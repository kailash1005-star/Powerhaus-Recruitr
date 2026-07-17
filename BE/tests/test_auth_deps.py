"""
Tests for the request-facing auth dependency.

Where test_jwt_verifier.py proves the cryptography, this proves the HTTP contract:
the right status codes, the right headers, and claims landing on the Principal the
way the rest of the app expects.
"""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.security import jwt_verifier as jv
from app.security.deps import Principal, principal_from_claims, require_auth, require_roles

ISSUER = "https://recruitr-test.eu.auth0.com/"
AUDIENCE = "https://api.recruit.vanceltech.com"
NS = "https://recruit.vanceltech.com/"
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return pem, jwk


@pytest.fixture
def app(keypair, monkeypatch):
    """A throwaway app with one public and one protected route."""
    pem, jwk = keypair
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH0_CLAIM_NAMESPACE", NS)

    verifier = jv.JwtVerifier(
        jwks_url="https://recruitr-test.eu.auth0.com/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    from jwt import PyJWKSet

    monkeypatch.setattr(verifier._jwk_client, "fetch_data", lambda: {"keys": [jwk]})
    monkeypatch.setattr(
        verifier._jwk_client, "get_jwk_set",
        lambda refresh=False: PyJWKSet.from_dict({"keys": [jwk]}),
    )
    monkeypatch.setattr(jv, "_verifier", verifier)

    api = FastAPI()

    @api.get("/open")
    async def open_route():
        return {"ok": True}

    @api.get("/protected")
    async def protected(p: Principal = Depends(require_auth)):
        return {"sub": p.sub, "tenant_id": p.tenant_id, "roles": list(p.roles), "email": p.email}

    @api.get("/admin", dependencies=[Depends(require_roles("admin"))])
    async def admin_only():
        return {"ok": True}

    yield api
    jv.reset_verifier()


@pytest.fixture
def client(app):
    return TestClient(app)


def _token(pem: str, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": "auth0|abc123",
        "iat": now, "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


def _auth(pem: str, **overrides) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(pem, **overrides)}"}


# ── the HTTP contract ────────────────────────────────────────────────────────
def test_public_route_needs_no_token(client):
    assert client.get("/open").status_code == 200


def test_missing_token_is_401_not_403(client):
    """403 would mean "authenticated but not allowed" and send clients to the
    wrong recovery path. A missing credential is 401."""
    r = client.get("/protected")
    assert r.status_code == 401


def test_401_carries_www_authenticate_header(client):
    """Required by the OAuth2 spec; it's how a client knows to go get a token."""
    r = client.get("/protected")
    assert r.headers.get("www-authenticate") == "Bearer"


def test_valid_token_passes_and_yields_principal(client, keypair):
    pem, _ = keypair
    r = client.get("/protected", headers=_auth(pem))
    assert r.status_code == 200
    assert r.json()["sub"] == "auth0|abc123"


def test_garbage_token_is_401(client):
    r = client.get("/protected", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


def test_expired_token_is_401(client, keypair):
    pem, _ = keypair
    now = int(time.time())
    r = client.get("/protected", headers=_auth(pem, exp=now - 60, iat=now - 3600))
    assert r.status_code == 401


def test_wrong_audience_is_401(client, keypair):
    pem, _ = keypair
    r = client.get("/protected", headers=_auth(pem, aud="https://other-api"))
    assert r.status_code == 401


def test_non_bearer_scheme_is_401(client):
    r = client.get("/protected", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401


def test_error_detail_does_not_leak_internals(client, keypair):
    """The 401 body should say "invalid token", not hand back a stack trace or
    name the exact check that failed."""
    pem, _ = keypair
    r = client.get("/protected", headers=_auth(pem, aud="https://other-api"))
    body = r.json()["detail"].lower()
    assert "traceback" not in body and "jwt" not in body


# ── claims → Principal ───────────────────────────────────────────────────────
def test_namespaced_claims_map_onto_principal(client, keypair):
    pem, _ = keypair
    r = client.get("/protected", headers=_auth(
        pem, **{f"{NS}tenant_id": "acme-gmbh", f"{NS}roles": ["admin", "recruiter"]}
    ))
    assert r.json()["tenant_id"] == "acme-gmbh"
    assert r.json()["roles"] == ["admin", "recruiter"]


def test_tenant_defaults_when_action_has_not_run(client, keypair):
    """No tenant claim (Action not yet in the flow) must not crash — it falls
    back to "default", which is what the single-tenant deployment expects."""
    pem, _ = keypair
    assert client.get("/protected", headers=_auth(pem)).json()["tenant_id"] == "default"


def test_client_cannot_supply_its_own_tenant(client, keypair):
    """The whole ballgame: tenant comes from the signed token, never the request.

    Here the caller passes a header AND a query param claiming another tenant.
    Both are ignored.
    """
    pem, _ = keypair
    r = client.get(
        "/protected?tenant_id=victim-gmbh",
        headers={**_auth(pem, **{f"{NS}tenant_id": "acme-gmbh"}),
                 "X-Tenant-Id": "victim-gmbh"},
    )
    assert r.json()["tenant_id"] == "acme-gmbh"


def test_roles_as_bare_string_is_normalized():
    """Auth0 Actions can stamp a string instead of a list; don't let that become
    a per-character role tuple."""
    p = principal_from_claims({"sub": "auth0|x", f"{NS}roles": "admin"})
    assert p.roles == ("admin",)


def test_principal_is_immutable():
    p = Principal(sub="auth0|x")
    with pytest.raises(Exception):
        p.sub = "auth0|someone-else"  # type: ignore[misc]


# ── role gating ──────────────────────────────────────────────────────────────
def test_role_gate_allows_matching_role(client, keypair):
    pem, _ = keypair
    r = client.get("/admin", headers=_auth(pem, **{f"{NS}roles": ["admin"]}))
    assert r.status_code == 200


def test_role_gate_403s_wrong_role(client, keypair):
    """Authenticated but not permitted — 403 here is correct, unlike a missing
    token."""
    pem, _ = keypair
    r = client.get("/admin", headers=_auth(pem, **{f"{NS}roles": ["recruiter"]}))
    assert r.status_code == 403


def test_role_gate_401s_when_unauthenticated(client):
    assert client.get("/admin").status_code == 401


# ── the kill switch ──────────────────────────────────────────────────────────
def test_auth_disabled_yields_dev_principal(app, monkeypatch):
    """AUTH_ENABLED=false must be an explicit, obvious dev-only bypass."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    r = TestClient(app).get("/protected")
    assert r.status_code == 200
    assert r.json()["sub"] == "dev|local"


# ── fail closed ──────────────────────────────────────────────────────────────
def test_verifier_blowing_up_is_401_not_500(client, keypair, monkeypatch):
    """If JWKS is unreachable we must fail CLOSED. A 500 here would be a bad day;
    an open door would be worse."""
    pem, _ = keypair
    monkeypatch.setattr(
        jv.get_verifier(), "verify",
        lambda t: (_ for _ in ()).throw(RuntimeError("JWKS endpoint down")),
    )
    r = client.get("/protected", headers=_auth(pem))
    assert r.status_code == 401
