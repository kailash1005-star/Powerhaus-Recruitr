"""
Tests for just-in-time user provisioning.

No Mongo: build_upsert is pure, and upsert_user is exercised against a stub
collection that records exactly what it was asked to write. What we're checking is
mostly "what does a second login overwrite?" — which is where the bugs are.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.security.deps import Principal
from app.services.user_service import build_upsert, upsert_user

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _principal(**over) -> Principal:
    base = dict(
        sub="auth0|abc123",
        email="anna@agency.de",
        tenant_id="default",
        roles=("recruiter",),
        claims={"name": "Anna Schmidt", "picture": "https://cdn/a.png", "email_verified": True},
    )
    base.update(over)
    return Principal(**base)


# ── what gets written ────────────────────────────────────────────────────────
def test_upsert_is_keyed_on_sub_not_email():
    """Email is mutable and shared across connections; sub is the identity."""
    filter_, _ = build_upsert(_principal(), now=NOW)
    assert filter_ == {"auth0Sub": "auth0|abc123"}


def test_profile_fields_refresh_on_every_login():
    _, update = build_upsert(_principal(), now=NOW)
    s = update["$set"]
    assert s["email"] == "anna@agency.de"
    assert s["name"] == "Anna Schmidt"
    assert s["tenantId"] == "default"
    assert s["roles"] == ["recruiter"]
    assert s["lastLoginAt"] == NOW


def test_created_at_seeds_once():
    _, update = build_upsert(_principal(), now=NOW)
    assert update["$setOnInsert"]["createdAt"] == NOW
    assert "createdAt" not in update["$set"]


def test_login_count_increments():
    _, update = build_upsert(_principal(), now=NOW)
    assert update["$inc"] == {"loginCount": 1}


def test_disabled_user_is_not_reactivated_by_logging_in():
    """The one that matters. If isActive were in $set, an admin disabling someone
    would be undone the moment that person logged in again."""
    _, update = build_upsert(_principal(), now=NOW)
    assert "isActive" not in update["$set"]
    assert update["$setOnInsert"]["isActive"] is True


def test_absent_email_does_not_blank_a_stored_one():
    """Some connections omit email. Writing None would erase what we have."""
    _, update = build_upsert(_principal(email=None), now=NOW)
    assert "email" not in update["$set"]


def test_absent_optional_claims_are_omitted_not_nulled():
    _, update = build_upsert(_principal(claims={}), now=NOW)
    s = update["$set"]
    assert "name" not in s and "picture" not in s and "emailVerified" not in s


def test_tenant_comes_from_the_principal():
    _, update = build_upsert(_principal(tenant_id="acme-gmbh"), now=NOW)
    assert update["$set"]["tenantId"] == "acme-gmbh"


def test_roles_are_stored_as_a_list_not_a_tuple():
    """BSON can't encode a tuple; storing one raises at write time."""
    _, update = build_upsert(_principal(roles=("admin", "recruiter")), now=NOW)
    assert update["$set"]["roles"] == ["admin", "recruiter"]
    assert isinstance(update["$set"]["roles"], list)


# ── the IO wrapper ───────────────────────────────────────────────────────────
class _StubCollection:
    def __init__(self, *, raises: Exception | None = None):
        self.calls: list[dict] = []
        self._raises = raises

    async def find_one_and_update(self, filter_, update, **kwargs):
        if self._raises:
            raise self._raises
        self.calls.append({"filter": filter_, "update": update, "kwargs": kwargs})
        return {"auth0Sub": filter_["auth0Sub"], "email": "anna@agency.de"}


class _StubDb:
    def __init__(self, collection):
        self._c = collection

    def __getitem__(self, name):
        assert name == "users"
        return self._c


@pytest.mark.asyncio
async def test_upsert_user_upserts_and_returns_the_doc():
    col = _StubCollection()
    doc = await upsert_user(_StubDb(col), _principal())
    assert doc["auth0Sub"] == "auth0|abc123"
    assert col.calls[0]["kwargs"]["upsert"] is True


@pytest.mark.asyncio
async def test_upsert_failure_does_not_break_the_request():
    """A verified token means the caller is who they claim. If the bookkeeping
    write fails, that's a log line — not a 500 on an otherwise valid request."""
    col = _StubCollection(raises=RuntimeError("mongo down"))
    assert await upsert_user(_StubDb(col), _principal()) is None
