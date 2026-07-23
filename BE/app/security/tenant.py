"""Tenant scoping — company-level data isolation.

Every client company is a tenant. Users in the same company share its data; the
three operators (admins) bypass scoping and see everything. Which tenant a user
belongs to is resolved from CONFIG keyed on their verified identity (email, email
domain, or Auth0 sub) — NEVER from the request body or a header, because a
client-supplied tenant id is just a request to read someone else's candidates.

Fail-closed by design: a user who is NOT mapped to a company gets a tenant unique
to themselves (``u:<sub>``), so they can only ever see their own data until an
operator explicitly groups them into a company. Under-sharing is a annoyance;
over-sharing is a data breach.

Configuration (all comma-separated, ``key=tenant``):
  * TENANT_ASSIGNMENTS — exact email OR email-domain OR sub → tenant id.
      e.g. "castle-personal.de=castle, benedict@gmail.com=castle, auth0|abc=castle"

The three admins (ADMIN_EMAILS / ADMIN_SUBS / the ``admin`` role) are matched
first and get an all-tenant bypass.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException

from app.config import settings
from app.security.deps import Principal, require_auth


# ── admin detection (single source of truth; qa.py delegates here) ───────────

def _admin_emails() -> set[str]:
    return {e.strip().lower() for e in (settings.ADMIN_EMAILS or "").split(",") if e.strip()}


def _admin_subs() -> set[str]:
    return {s.strip() for s in (getattr(settings, "ADMIN_SUBS", "") or "").split(",") if s.strip()}


def is_admin(principal: Principal) -> bool:
    if principal.has_role("admin"):
        return True
    if principal.sub and principal.sub in _admin_subs():
        return True
    email = (principal.email or "").strip().lower()
    return bool(email) and email in _admin_emails()


# ── tenant resolution ────────────────────────────────────────────────────────

def _assignments() -> dict[str, str]:
    """Parse TENANT_ASSIGNMENTS into {key(lower): tenant}. Keys are emails,
    domains or subs — resolution tries them most-specific first."""
    out: dict[str, str] = {}
    for pair in (getattr(settings, "TENANT_ASSIGNMENTS", "") or "").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, _, tenant = pair.partition("=")
        key, tenant = key.strip().lower(), tenant.strip()
        if key and tenant:
            out[key] = tenant
    return out


def resolve_tenant(principal: Principal) -> str:
    """The tenant a caller's data is scoped to. Most-specific match wins:
    exact email → email domain → sub → self-isolated ``u:<sub>``."""
    amap = _assignments()
    email = (principal.email or "").strip().lower()
    if email and email in amap:
        return amap[email]
    if "@" in email:
        domain = email.rsplit("@", 1)[1]
        if domain in amap:
            return amap[domain]
    sub = (principal.sub or "").strip()
    if sub and sub.lower() in amap:
        return amap[sub.lower()]
    # Unmapped → isolated to just this user. Fail-closed.
    return f"u:{sub}" if sub else "u:anonymous"


@dataclass(frozen=True)
class TenantContext:
    """The resolved data-access scope for a request."""
    tenant_id: str
    is_admin: bool
    sub: str
    email: str | None = None

    def read_filter(self, base: dict | None = None) -> dict:
        """A Mongo filter that restricts a query to this tenant. Admins get the
        base filter unchanged (all tenants)."""
        f = dict(base or {})
        if not self.is_admin:
            f["tenantId"] = self.tenant_id
        return f

    def owns(self, doc: dict | None) -> bool:
        """True if this caller may see ``doc``. Admins may see anything; a
        missing doc is never owned."""
        if doc is None:
            return False
        if self.is_admin:
            return True
        return doc.get("tenantId") == self.tenant_id

    def stamp(self, doc: dict) -> dict:
        """Tag a to-be-created doc with the owning tenant (in place)."""
        doc["tenantId"] = self.tenant_id
        return doc


async def tenant_scope(principal: Principal = Depends(require_auth)) -> TenantContext:
    """FastAPI dependency: the caller's data-access scope."""
    return TenantContext(
        tenant_id=resolve_tenant(principal),
        is_admin=is_admin(principal),
        sub=principal.sub,
        email=principal.email,
    )


async def require_admin(principal: Principal = Depends(require_auth)) -> Principal:
    """Gate a route (or whole router) to operators only. 403 for everyone else.

    For endpoints that expose cross-tenant / platform-wide data with no per-tenant
    attribution (cost ledger, QA metrics): admin-gating is what stops one client
    seeing another's numbers, since there is no tenant to scope the aggregate to."""
    if not is_admin(principal):
        raise HTTPException(status_code=403, detail="admin access required")
    return principal
