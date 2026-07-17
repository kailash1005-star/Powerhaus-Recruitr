"""
Request-facing auth: bearer token → Principal, or 401.

This is the layer FastAPI sees. jwt_verifier.py does the cryptography; this module
turns verified claims into something the app can reason about, and maps every
failure to a clean 401 with a `WWW-Authenticate` header.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.security.jwt_verifier import AuthError, get_verifier

logger = logging.getLogger(__name__)

# auto_error=False so we raise our own 401 (with WWW-Authenticate) rather than
# FastAPI's bare 403 for a missing header — 403 would be the wrong code, and
# wrong codes send clients down the wrong recovery path.
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    Frozen: nothing downstream should be able to edit who the caller is after
    the token said so.
    """

    sub: str                       # Auth0 user id — stable, the DB key
    email: str | None = None
    tenant_id: str = "default"
    roles: tuple[str, ...] = ()
    claims: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_authenticated(self) -> bool:
        return bool(self.sub)

    def has_role(self, role: str) -> bool:
        return role in self.roles


def _unauthorized(reason: str) -> HTTPException:
    """401 with the header the OAuth2 spec requires.

    `reason` is coarse by design (see AuthError) — enough for a developer to
    debug, not enough for an attacker to probe which check they tripped.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=reason,
        headers={"WWW-Authenticate": "Bearer"},
    )


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    """Map Auth0 claims onto a Principal.

    `tenant_id` is read from the TOKEN, never from the request body or a header.
    A client-supplied tenant id is just a request to read someone else's data.
    Today the post-login Action stamps "default" for everyone; when Organizations
    are switched on it stamps the real org and this code is unchanged.
    """
    ns = settings.AUTH0_CLAIM_NAMESPACE

    roles = claims.get(f"{ns}roles") or []
    if isinstance(roles, str):
        roles = [roles]

    return Principal(
        sub=claims.get("sub", ""),
        email=claims.get(f"{ns}email") or claims.get("email"),
        tenant_id=claims.get(f"{ns}tenant_id") or "default",
        roles=tuple(roles),
        claims=claims,
    )


# The principal used when AUTH_ENABLED=false. Local dev and tests only — the app
# refuses to boot in that state with a real Auth0 domain configured (see
# startup_checks.verify_auth_configuration).
DEV_PRINCIPAL = Principal(
    sub="dev|local",
    email="dev@localhost",
    tenant_id="default",
    roles=("admin",),
)


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """FastAPI dependency. Yields a Principal or raises 401.

    Also stashes the principal on request.state so non-dependency code (logging,
    middleware) can see who's calling without re-verifying the token.
    """
    if not settings.AUTH_ENABLED:
        request.state.principal = DEV_PRINCIPAL
        return DEV_PRINCIPAL

    if credentials is None or not credentials.credentials:
        raise _unauthorized("missing bearer token")

    # HTTPBearer already enforces the "Bearer" scheme, but it's case-insensitive
    # about it and we want the failure to be explicit rather than implicit.
    if credentials.scheme.lower() != "bearer":
        raise _unauthorized("invalid authorization scheme")

    try:
        claims = get_verifier().verify(credentials.credentials)
    except AuthError as e:
        raise _unauthorized(e.reason) from e
    except Exception as e:  # noqa: BLE001
        # Never let an unexpected verifier error (JWKS endpoint down, TLS
        # failure) surface as a 500 with a stack trace. Fail closed, log loudly.
        logger.exception("[auth] verifier error")
        raise _unauthorized("token verification unavailable") from e

    principal = principal_from_claims(claims)
    if not principal.is_authenticated:
        raise _unauthorized("token has no subject")

    request.state.principal = principal
    return principal


def require_roles(*required: str):
    """Dependency factory for role-gated routes.

    Unused today (no roles defined yet) but the claim is already plumbed, so
    gating a route later is a one-line change:
        @router.post("/x", dependencies=[Depends(require_roles("admin"))])
    """

    async def _check(principal: Principal = Depends(require_auth)) -> Principal:
        if not any(principal.has_role(r) for r in required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of: {', '.join(required)}",
            )
        return principal

    return _check
