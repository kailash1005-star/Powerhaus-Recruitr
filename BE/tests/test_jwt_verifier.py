"""
Tests for Auth0 access-token verification.

No Auth0 tenant and no network: we mint a local RSA keypair, serve it as a stub
JWKS, and sign our own tokens. That exercises the REAL verification path — the
same PyJWT calls, the same claim checks — so these tests are meaningful long
before any credentials exist.

The happy path is one test. The other fifteen are the point: a JWT library used
naively accepts tokens it should refuse, and every one of those is a way into
candidate PII.
"""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.security.jwt_verifier import AuthError, JwtVerifier

ISSUER = "https://recruitr-test.eu.auth0.com/"
AUDIENCE = "https://api.recruit.vanceltech.com"
KID = "test-key-1"


# ── local key material ───────────────────────────────────────────────────────
def _make_key(kid: str = KID):
    """An RSA keypair plus its JWKS entry — a local stand-in for Auth0's."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private, pem, jwk


@pytest.fixture(scope="module")
def keypair():
    return _make_key()


@pytest.fixture
def verifier(keypair, monkeypatch):
    """A JwtVerifier whose JWKS is our local key instead of Auth0's."""
    _, _, jwk = keypair
    verifier = JwtVerifier(
        jwks_url="https://recruitr-test.eu.auth0.com/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    _stub_jwks(verifier, monkeypatch, {"keys": [jwk]})
    return verifier


def _stub_jwks(verifier, monkeypatch, jwks: dict):
    """Point PyJWKClient's fetch at an in-memory JWKS (no network)."""
    from jwt import PyJWKSet

    monkeypatch.setattr(
        verifier._jwk_client,
        "fetch_data",
        lambda: jwks,
    )
    monkeypatch.setattr(
        verifier._jwk_client,
        "get_jwk_set",
        lambda refresh=False: PyJWKSet.from_dict(jwks),
    )


def _token(pem: str, *, kid: str = KID, alg: str = "RS256", **overrides) -> str:
    """A well-formed token, with claims overridable per-test."""
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "auth0|abc123",
        "iat": now,
        "exp": now + 3600,
        "azp": "client-id",
        "scope": "openid profile email",
    }
    claims.update(overrides)
    return jwt.encode(claims, pem, algorithm=alg, headers={"kid": kid})


# ── the happy path ───────────────────────────────────────────────────────────
def test_valid_token_is_accepted(verifier, keypair):
    _, pem, _ = keypair
    claims = verifier.verify(_token(pem))
    assert claims["sub"] == "auth0|abc123"
    assert claims["iss"] == ISSUER
    assert claims["aud"] == AUDIENCE


def test_custom_claims_survive_verification(verifier, keypair):
    """The post-login Action's namespaced claims must reach the caller intact."""
    _, pem, _ = keypair
    ns = "https://recruit.vanceltech.com/"
    claims = verifier.verify(
        _token(pem, **{f"{ns}tenant_id": "default", f"{ns}roles": ["admin"]})
    )
    assert claims[f"{ns}tenant_id"] == "default"
    assert claims[f"{ns}roles"] == ["admin"]


# ── rejections: the reason this file exists ──────────────────────────────────
def test_empty_token_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify("")


def test_garbage_token_rejected(verifier):
    with pytest.raises(AuthError):
        verifier.verify("not-a-jwt")


def test_expired_token_rejected(verifier, keypair):
    _, pem, _ = keypair
    now = int(time.time())
    with pytest.raises(AuthError) as e:
        verifier.verify(_token(pem, exp=now - 60, iat=now - 3600))
    assert "expired" in e.value.reason


def test_token_for_another_api_rejected(verifier, keypair):
    """Same tenant, same signing key, different audience.

    This token is genuinely, validly signed by Auth0 — it's just minted for a
    different API. Without an audience check we'd accept it.
    """
    _, pem, _ = keypair
    with pytest.raises(AuthError) as e:
        verifier.verify(_token(pem, aud="https://some-other-api"))
    assert "audience" in e.value.reason


def test_token_from_another_tenant_rejected(verifier, keypair):
    _, pem, _ = keypair
    with pytest.raises(AuthError) as e:
        verifier.verify(_token(pem, iss="https://evil.eu.auth0.com/"))
    assert "issuer" in e.value.reason


def test_token_signed_by_a_different_key_rejected(verifier):
    """Attacker mints their own keypair, reuses our kid, signs a perfect token.

    Claims all check out; only the signature is wrong. This is the single most
    important assertion in the file.
    """
    _, other_pem, _ = _make_key(kid=KID)
    with pytest.raises(AuthError):
        verifier.verify(_token(other_pem))


def test_unsigned_alg_none_token_rejected(verifier, keypair):
    """`alg: none` — no signature at all.

    PyJWT won't mint one, so we hand-assemble it exactly as an attacker would.
    Algorithm pinning is what refuses it.
    """
    import base64
    import json

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    now = int(time.time())
    header = b64(json.dumps({"alg": "none", "typ": "JWT", "kid": KID}).encode())
    payload = b64(
        json.dumps(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "auth0|attacker",
             "iat": now, "exp": now + 3600}
        ).encode()
    )
    with pytest.raises(AuthError):
        verifier.verify(f"{header}.{payload}.")


def test_hs256_algorithm_confusion_rejected(verifier, keypair):
    """The classic: sign with HS256 using our PUBLIC key as the HMAC secret.

    The public key is, by definition, public. A verifier that trusts the token's
    own `alg` header would treat it as a shared secret and happily validate an
    attacker-minted token. Pinning to RS256 is the fix, and this proves it.

    Built by hand with raw HMAC, because PyJWT's `encode` refuses to use a PEM as
    an HMAC secret — it defends the minting side. An attacker has no such
    scruples and would just do this, so we test what they'd actually send.
    """
    import base64
    import hashlib
    import hmac
    import json

    private, _, _ = keypair
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    now = int(time.time())
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    payload = b64(
        json.dumps(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "auth0|attacker",
             "iat": now, "exp": now + 3600}
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())

    with pytest.raises(AuthError):
        verifier.verify(f"{header}.{payload}.{sig}")


def test_unknown_kid_rejected(verifier, keypair):
    _, pem, _ = keypair
    with pytest.raises(AuthError):
        verifier.verify(_token(pem, kid="a-kid-we-never-published"))


@pytest.mark.parametrize("missing", ["exp", "iat", "sub"])
def test_tokens_missing_required_claims_rejected(verifier, keypair, missing):
    _, pem, _ = keypair
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "auth0|abc",
              "iat": now, "exp": now + 3600}
    del claims[missing]
    token = jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(AuthError):
        verifier.verify(token)


# ── clock skew ───────────────────────────────────────────────────────────────
def test_token_expired_within_leeway_is_accepted(keypair, monkeypatch):
    """Cloud Run and Auth0 clocks drift; a few seconds shouldn't 401 a user."""
    _, pem, jwk = keypair
    v = JwtVerifier(
        jwks_url="https://x/.well-known/jwks.json",
        issuer=ISSUER, audience=AUDIENCE, leeway=30,
    )
    _stub_jwks(v, monkeypatch, {"keys": [jwk]})
    now = int(time.time())
    assert v.verify(_token(pem, exp=now - 5, iat=now - 3600))["sub"] == "auth0|abc123"


def test_token_expired_beyond_leeway_is_rejected(keypair, monkeypatch):
    _, pem, jwk = keypair
    v = JwtVerifier(
        jwks_url="https://x/.well-known/jwks.json",
        issuer=ISSUER, audience=AUDIENCE, leeway=30,
    )
    _stub_jwks(v, monkeypatch, {"keys": [jwk]})
    now = int(time.time())
    with pytest.raises(AuthError):
        v.verify(_token(pem, exp=now - 120, iat=now - 3600))


# ── construction ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "kwargs",
    [
        {"jwks_url": "", "issuer": ISSUER, "audience": AUDIENCE},
        {"jwks_url": "https://x", "issuer": "", "audience": AUDIENCE},
        {"jwks_url": "https://x", "issuer": ISSUER, "audience": ""},
    ],
)
def test_verifier_refuses_incomplete_config(kwargs):
    """Fail at construction, not at request time with a confusing 401."""
    with pytest.raises(ValueError):
        JwtVerifier(**kwargs)
