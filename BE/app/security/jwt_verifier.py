"""
Auth0 access-token verification.

RS256 only, verified against Auth0's published JWKS. This module is deliberately
narrow: it turns a bearer string into verified claims, or raises. It knows nothing
about FastAPI or Mongo — see deps.py for the request-facing layer.

Why PyJWT + PyJWKClient rather than Auth0's own `auth0-fastapi-api`: the latter is
still a beta (1.0.0b5) at the time of writing, and this is the code path that
decides who may read candidate PII. See docs/engineering/AUTH0_SETUP.md.

The security of this module rests on three checks that are easy to get wrong:

  1. ALGORITHM PINNING. We pass algorithms=["RS256"] explicitly. Without it, a
     JWT library will honour the `alg` header the *attacker* controls — including
     `none` (no signature at all), or HS256, where the library would treat our
     PUBLIC key as an HMAC shared secret. The public key is public, so an attacker
     could then sign their own valid-looking tokens. That's the classic algorithm
     confusion attack, and pinning is what closes it.
  2. AUDIENCE. A token minted for a *different* API in the same Auth0 tenant is
     validly signed by the same key. Without an audience check it would be
     accepted here.
  3. ISSUER. Pins tokens to our tenant.
"""
from __future__ import annotations

import logging
from typing import Any

import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

# The ONLY signature algorithm we accept. See the module docstring — this is a
# security control, not a default.
ALLOWED_ALGORITHMS = ["RS256"]


class AuthError(Exception):
    """Token could not be verified. Carries a short, safe reason.

    The reason is intentionally coarse ("token expired", "invalid signature").
    Detailed crypto errors go to the log, not to the caller: telling an attacker
    precisely which check failed helps them and helps nobody else.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class JwtVerifier:
    """Verifies Auth0 RS256 access tokens.

    One instance per process. PyJWKClient caches the JWKS in-memory and refetches
    on an unknown `kid`, so Auth0 key rotation is handled without a redeploy.
    """

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        *,
        leeway: int = 10,
        cache_ttl: int = 600,
    ):
        if not jwks_url or not issuer or not audience:
            raise ValueError("JwtVerifier requires jwks_url, issuer and audience")

        self.issuer = issuer
        self.audience = audience
        self.leeway = leeway
        # lifespan caps how long a rotated-away key stays trusted in this process.
        self._jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=cache_ttl)

    def verify(self, token: str) -> dict[str, Any]:
        """Return verified claims, or raise AuthError.

        Every failure path funnels to AuthError so callers can map the whole
        module to a 401 without catching library-specific exception types.
        """
        if not token:
            raise AuthError("missing token")

        # Resolve the signing key from the token's `kid`. This reads an UNVERIFIED
        # header, which is safe: `kid` only selects which public key to try, and a
        # wrong/forged kid simply fails to match or fails the signature check below.
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token).key
        except jwt.exceptions.PyJWKClientError as e:
            logger.warning("[auth] no signing key for token: %s", e)
            raise AuthError("invalid token") from e
        except jwt.exceptions.DecodeError as e:
            # Malformed token — couldn't even read the header.
            logger.warning("[auth] malformed token: %s", e)
            raise AuthError("invalid token") from e

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=ALLOWED_ALGORITHMS,  # ← pinning; see module docstring
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway,
                options={
                    "require": ["exp", "iat", "iss", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthError("token expired") from e
        except jwt.InvalidAudienceError as e:
            logger.warning("[auth] wrong audience (expected %s)", self.audience)
            raise AuthError("invalid audience") from e
        except jwt.InvalidIssuerError as e:
            logger.warning("[auth] wrong issuer (expected %s)", self.issuer)
            raise AuthError("invalid issuer") from e
        except jwt.MissingRequiredClaimError as e:
            logger.warning("[auth] missing claim: %s", e)
            raise AuthError("invalid token") from e
        except jwt.InvalidSignatureError as e:
            logger.warning("[auth] bad signature")
            raise AuthError("invalid signature") from e
        except jwt.InvalidAlgorithmError as e:
            # An `alg` we don't accept (none / HS256 / …). Worth a louder log:
            # legitimate clients never do this, so it usually means someone is
            # probing for algorithm confusion.
            logger.warning("[auth] rejected algorithm: %s", e)
            raise AuthError("invalid token") from e
        except jwt.InvalidTokenError as e:
            # Catch-all for the rest of PyJWT's tree. Must stay LAST — the
            # specific errors above are all subclasses of this.
            logger.warning("[auth] invalid token: %s", e)
            raise AuthError("invalid token") from e

        return claims


_verifier: JwtVerifier | None = None


def get_verifier() -> JwtVerifier:
    """Process-wide verifier, built on first use.

    Lazy rather than import-time so the module can be imported (and tested) in an
    environment with no Auth0 configuration.
    """
    global _verifier
    if _verifier is None:
        from app.config import settings

        _verifier = JwtVerifier(
            jwks_url=settings.auth0_jwks_url,
            issuer=settings.auth0_issuer,
            audience=settings.AUTH0_AUDIENCE,
            leeway=settings.AUTH0_LEEWAY,
            cache_ttl=settings.AUTH0_JWKS_CACHE_TTL,
        )
    return _verifier


def reset_verifier() -> None:
    """Drop the cached verifier. For tests."""
    global _verifier
    _verifier = None
