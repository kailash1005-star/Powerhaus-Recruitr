"""Tenant-isolation contract tests.

These lock in the rules that keep one client from ever reading another's scraped
candidate PII: how a caller is resolved to a tenant, how reads are filtered, what
a caller may own, and that GDPR erase/export stay inside the caller's tenant.
Pure-logic (no live DB) so they run in the normal suite.
"""
from __future__ import annotations

import asyncio

import pytest

from app.security import tenant as T
from app.security.deps import Principal
from app.security.tenant import TenantContext


def _principal(sub="auth0|u1", email=None, roles=None):
    return Principal(sub=sub, email=email, roles=roles or [], claims={})


# ── resolve_tenant ───────────────────────────────────────────────────────────

def test_resolve_tenant_exact_email(monkeypatch):
    monkeypatch.setattr(T.settings, "TENANT_ASSIGNMENTS", "benedict@castle-personal.de=castle")
    assert T.resolve_tenant(_principal(email="benedict@castle-personal.de")) == "castle"


def test_resolve_tenant_by_domain(monkeypatch):
    monkeypatch.setattr(T.settings, "TENANT_ASSIGNMENTS", "castle-personal.de=castle")
    assert T.resolve_tenant(_principal(email="anyone@castle-personal.de")) == "castle"


def test_resolve_tenant_unmapped_is_self_isolated(monkeypatch):
    monkeypatch.setattr(T.settings, "TENANT_ASSIGNMENTS", "castle-personal.de=castle")
    # A user matching nothing must be isolated to only themselves — fail closed.
    assert T.resolve_tenant(_principal(sub="auth0|stranger", email="x@gmail.com")) == "u:auth0|stranger"


def test_resolve_tenant_most_specific_wins(monkeypatch):
    monkeypatch.setattr(
        T.settings, "TENANT_ASSIGNMENTS",
        "castle-personal.de=castle, vip@castle-personal.de=vip")
    assert T.resolve_tenant(_principal(email="vip@castle-personal.de")) == "vip"


# ── is_admin ─────────────────────────────────────────────────────────────────

def test_is_admin_by_role():
    assert T.is_admin(_principal(roles=["admin"]))


def test_is_admin_by_email(monkeypatch):
    monkeypatch.setattr(T.settings, "ADMIN_EMAILS", "kailash@vanceltech.com")
    assert T.is_admin(_principal(email="Kailash@Vanceltech.com"))  # case-insensitive


def test_non_admin_is_not_admin(monkeypatch):
    monkeypatch.setattr(T.settings, "ADMIN_EMAILS", "kailash@vanceltech.com")
    monkeypatch.setattr(T.settings, "ADMIN_SUBS", "")
    assert not T.is_admin(_principal(email="benedict@castle-personal.de"))


# ── read_filter / owns / stamp ───────────────────────────────────────────────

def _ctx(tenant="castle", is_admin=False):
    return TenantContext(tenant_id=tenant, is_admin=is_admin, sub="auth0|u", email="u@x")


def test_read_filter_scopes_non_admin():
    assert _ctx().read_filter() == {"tenantId": "castle"}
    assert _ctx().read_filter({"status": "x"}) == {"status": "x", "tenantId": "castle"}


def test_read_filter_admin_sees_all():
    assert _ctx(is_admin=True).read_filter() == {}
    assert _ctx(is_admin=True).read_filter({"status": "x"}) == {"status": "x"}


def test_owns_semantics():
    ctx = _ctx()
    assert ctx.owns({"tenantId": "castle"}) is True
    assert ctx.owns({"tenantId": "other"}) is False
    # Legacy/unstamped doc → a non-admin owns nothing (clean slate for new tenants).
    assert ctx.owns({"_id": 1}) is False
    assert ctx.owns(None) is False


def test_admin_owns_everything():
    admin = _ctx(is_admin=True)
    assert admin.owns({"tenantId": "other"}) is True
    assert admin.owns({"_id": 1}) is True  # legacy null-tenant doc
    assert admin.owns(None) is False       # ...but a missing doc is still not owned


def test_stamp_tags_tenant():
    doc = {}
    _ctx().stamp(doc)
    assert doc["tenantId"] == "castle"


# ── GDPR: subject matching stays tenant-scoped ───────────────────────────────

def test_gdpr_match_filters_are_tenant_scoped():
    from app.services import gdpr_service

    async def go():
        # No DB is touched: _match_filters only builds the query dicts.
        return await gdpr_service._match_filters(
            db=None,
            tenant_scope={"tenantId": "castle"},
            subject={"email": "jane@doe.com"},
        )

    filters = asyncio.run(go())
    # Every collection's filter must AND-in the tenant scope.
    for col, f in filters.items():
        assert "$and" in f, col
        assert {"tenantId": "castle"} in f["$and"], col


def test_gdpr_admin_scope_is_unrestricted():
    from app.services import gdpr_service

    async def go():
        return await gdpr_service._match_filters(
            db=None, tenant_scope={}, subject={"email": "jane@doe.com"})

    filters = asyncio.run(go())
    for col, f in filters.items():
        # Admin (empty scope) → plain $or, no tenant AND-wrap.
        assert "$and" not in f, col
        assert "$or" in f, col


def test_gdpr_linkedin_slug_extraction():
    from app.services import gdpr_service
    assert gdpr_service._linkedin_slug("https://www.linkedin.com/in/jane-doe/") == "jane-doe"
    assert gdpr_service._linkedin_slug("jane-doe") == "jane-doe"


def test_gdpr_subject_key_prefers_stable_id():
    from app.services import gdpr_service
    assert gdpr_service._subject_key({"candidateId": "abc"}) == "candidateId:abc"
    assert gdpr_service._subject_key({"email": "a@b.com"}) == "email:a@b.com"
    assert gdpr_service._subject_key({}) == "unknown"


# ── GDPR API: empty subject is rejected (never matches everyone) ─────────────

def test_gdpr_subject_request_requires_an_identifier():
    from app.api.v1.gdpr import SubjectRequest
    with pytest.raises(ValueError):
        SubjectRequest()  # no identifiers → must raise, not select all
    # One identifier is enough.
    assert SubjectRequest(email="a@b.com").as_subject() == {"email": "a@b.com"}
