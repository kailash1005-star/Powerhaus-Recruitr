"""
User provisioning.

Auth0 authenticates; this file makes sure a matching local record exists. There is
no signup endpoint and no user-creation UI: the first authenticated request from a
new `sub` creates the row (just-in-time provisioning). That's the whole flow.

Anything a caller could lie about is taken from the verified token, never the
request body.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.security.deps import Principal

logger = logging.getLogger(__name__)

COLLECTION = "users"


def build_upsert(principal: Principal, *, now: datetime | None = None) -> tuple[dict, dict]:
    """Build the (filter, update) for a just-in-time user upsert.

    Split out from the IO so the interesting part — what we write, and what we
    refuse to overwrite — is directly testable.

    `$setOnInsert` vs `$set` matters here:
      • createdAt / loginCount seed once, on insert.
      • Profile fields refresh every login, because Auth0 is the source of truth
        and a changed name or picture should follow through.
      • isActive is NOT touched on login. If an admin disables someone, the next
        login must not silently re-enable them — which is exactly what putting it
        in $set would do.
    """
    now = now or datetime.now(timezone.utc)

    profile: dict[str, Any] = {
        "tenantId": principal.tenant_id,
        "roles": list(principal.roles),
        "lastLoginAt": now,
    }
    # Only overwrite profile fields the token actually carried. A token without
    # an email (some connections omit it) must not blank an email we already have.
    if principal.email is not None:
        profile["email"] = principal.email
    if principal.claims.get("name"):
        profile["name"] = principal.claims["name"]
    if principal.claims.get("picture"):
        profile["picture"] = principal.claims["picture"]
    if "email_verified" in principal.claims:
        profile["emailVerified"] = bool(principal.claims["email_verified"])

    filter_ = {"auth0Sub": principal.sub}
    update = {
        "$set": profile,
        "$setOnInsert": {
            "auth0Sub": principal.sub,
            "createdAt": now,
            "isActive": True,
        },
        "$inc": {"loginCount": 1},
    }
    return filter_, update


async def upsert_user(db, principal: Principal) -> dict | None:
    """Create-or-refresh the local user record. Returns the stored document.

    Never raises: a provisioning hiccup must not turn an otherwise valid request
    into a 500. The token was already verified, so the caller IS who they say
    they are — the local row is bookkeeping, not authorization.
    """
    filter_, update = build_upsert(principal)
    try:
        return await db[COLLECTION].find_one_and_update(
            filter_, update, upsert=True, return_document=True
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[auth] user upsert failed for %s: %s", principal.sub, e)
        return None


async def ensure_indexes(db) -> None:
    """Indexes for the users collection. Called from connect_to_mongo()."""
    users = db[COLLECTION]
    # Unique: one row per Auth0 identity. This is the constraint that makes JIT
    # provisioning safe under concurrent requests — two simultaneous first-calls
    # race to upsert, and the index guarantees one row rather than two.
    await users.create_index("auth0Sub", name="idx_auth0Sub", unique=True)
    await users.create_index("tenantId", name="idx_users_tenantId")
    await users.create_index(
        "email", name="idx_users_email",
        partialFilterExpression={"email": {"$type": "string", "$gt": ""}},
    )
