"""
Whole-app authentication contract.

The unit tests prove the verifier and the dependency. This proves the thing we
actually care about: that the REAL app, with its real routers, is closed to
unauthenticated callers — and that the routes which must stay open still are.

Mongo is never touched: a 401 is raised by the dependency before any handler runs,
which is precisely the property under test.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings


@pytest.fixture
def client(monkeypatch):
    """The real app, with auth on and Auth0 configured to a dummy tenant.

    Startup events are skipped (TestClient isn't used as a context manager), so
    no Mongo connection is attempted.
    """
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-test.eu.auth0.com")
    monkeypatch.setattr(settings, "AUTH0_AUDIENCE", "https://api.recruit.vanceltech.com")

    from app.main import app

    # raise_server_exceptions=False so a handler that runs and then fails on the
    # (absent) Mongo connection returns 500 instead of exploding out of the test
    # client. We're asserting on the auth boundary, and a 500 is itself proof the
    # request got PAST auth into the handler.
    return TestClient(app, raise_server_exceptions=False)


# ── the routes that must stay open ───────────────────────────────────────────
def test_health_is_public(client):
    """Cloud Run's probe has no token. If this 401s, the revision never goes
    healthy and the deploy fails."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_health_reports_auth_state_without_leaking_secrets(client):
    body = client.get("/health").json()
    assert body["auth"]["enabled"] is True
    blob = str(body).lower()
    assert "client_secret" not in blob and "mongodb" not in blob


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/jobs/prospects/mobile-webhook",
        "/api/v1/outreach/webhooks/smartlead",
        "/api/v1/outreach/webhooks/calcom",
    ],
)
def test_provider_webhooks_are_not_behind_bearer_auth(client, path):
    """Apollo/Smartlead/Cal.com POST with no bearer token.

    Regression guard: putting auth on the router aggregate silently 401s these,
    and the symptom is remote — phone reveals hang on "pending" forever and
    nobody connects it to an auth change.

    A 500 here is a pass: it means auth let the request through and the handler
    ran, then tripped on the Mongo connection this test doesn't provide. The only
    failing status is 401.
    """
    r = client.post(path, json={})
    assert r.status_code != 401, f"{path} is behind auth — provider callbacks will fail"


# ── everything else must be closed ───────────────────────────────────────────
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/icp/config"),
        ("get", "/api/v1/runs"),
        ("get", "/api/v1/jobs"),
        ("get", "/api/v1/pipelines"),
        ("get", "/api/v1/analytics/jobs"),
        ("get", "/api/v1/analytics/companies"),
        ("get", "/api/v1/matching/runs"),
        ("get", "/api/v1/matching/cv"),
        ("get", "/api/v1/companies/507f1f77bcf86cd799439011"),
        ("get", "/api/v1/pipelines/candidates/507f1f77bcf86cd799439011"),
        ("post", "/api/v1/runs/start"),
        ("post", "/api/v1/matching/cv/upload"),
    ],
)
def test_data_routes_require_a_token(client, method, path):
    """Each of these served real candidate/company data to anyone, publicly,
    before auth landed. Paths are taken from the live route table."""
    r = getattr(client, method)(path)
    assert r.status_code == 401, f"{path} answered {r.status_code} without a token"


def test_garbage_token_is_rejected_by_the_real_app(client):
    r = client.get("/api/v1/icp/config", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# ── the sweep: nothing new slips through ─────────────────────────────────────
def test_every_v1_route_is_authenticated_or_explicitly_public(client):
    """Walks the real route table so a router added later can't quietly ship
    unauthenticated. If this fails, either add auth or add the path to
    EXPECTED_PUBLIC with a reason.
    """
    from app.main import app
    from app.security.deps import require_auth

    EXPECTED_PUBLIC = {
        # Provider callbacks — no bearer token possible; signature-verified.
        "/api/v1/jobs/prospects/mobile-webhook",
        "/api/v1/outreach/webhooks/smartlead",
        "/api/v1/outreach/webhooks/calcom",
    }

    unprotected = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1"):
            continue
        deps = getattr(route, "dependant", None)
        calls = {d.call for d in deps.dependencies} if deps else set()
        if require_auth not in calls and path not in EXPECTED_PUBLIC:
            unprotected.append(path)

    assert not unprotected, (
        "Unauthenticated /api/v1 routes found: "
        f"{sorted(set(unprotected))}. Add auth, or add to EXPECTED_PUBLIC with a reason."
    )
