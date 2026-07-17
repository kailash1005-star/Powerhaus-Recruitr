"""
Tests for the startup auth guard and config derivation.

AUTH_ENABLED is a foot-gun: a flag that silently switches authentication off, whose
failure mode is invisible (the app works perfectly — it just serves everyone). These
tests are what make it safe to have.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.startup_checks import AuthMisconfigured, auth_readiness, verify_auth_configuration


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Each test sets the whole auth config explicitly."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "")
    monkeypatch.setattr(settings, "AUTH0_AUDIENCE", "")
    monkeypatch.setattr(settings, "AUTH0_ISSUER", "")


# ── issuer derivation ────────────────────────────────────────────────────────
def test_issuer_derives_with_scheme_and_trailing_slash(monkeypatch):
    """`iss` is compared character-for-character. Auth0 mints it with https AND a
    trailing slash; get either wrong and every token 401s with 'invalid issuer'."""
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-prod.eu.auth0.com")
    assert settings.auth0_issuer == "https://recruitr-prod.eu.auth0.com/"


def test_issuer_derivation_survives_a_pasted_trailing_slash(monkeypatch):
    """People paste the domain with a slash. Don't produce '...com//'."""
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-prod.eu.auth0.com/")
    assert settings.auth0_issuer == "https://recruitr-prod.eu.auth0.com/"


def test_explicit_issuer_overrides_derivation(monkeypatch):
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-prod.eu.auth0.com")
    monkeypatch.setattr(settings, "AUTH0_ISSUER", "https://custom.example.com/")
    assert settings.auth0_issuer == "https://custom.example.com/"


def test_jwks_url_shape(monkeypatch):
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-prod.eu.auth0.com")
    assert settings.auth0_jwks_url == (
        "https://recruitr-prod.eu.auth0.com/.well-known/jwks.json"
    )


def test_unconfigured_domain_yields_empty_issuer_not_a_broken_url():
    assert settings.auth0_issuer == ""
    assert settings.auth0_jwks_url == ""


# ── the guard ────────────────────────────────────────────────────────────────
def test_auth_on_and_configured_is_fine(monkeypatch):
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-prod.eu.auth0.com")
    monkeypatch.setattr(settings, "AUTH0_AUDIENCE", "https://api.recruit.vanceltech.com")
    verify_auth_configuration()  # must not raise


def test_auth_on_but_unconfigured_refuses_to_boot():
    """Otherwise every request 401s and you debug it after deploy instead of at
    startup."""
    with pytest.raises(AuthMisconfigured) as e:
        verify_auth_configuration()
    assert "AUTH0_DOMAIN" in str(e.value)


def test_auth_disabled_against_a_real_tenant_refuses_to_boot(monkeypatch):
    """THE important one.

    Auth off while an Auth0 tenant is configured means someone disabled auth on
    what is almost certainly production. The app must not start — the failure is
    otherwise invisible, because everything works, for everyone, including people
    who shouldn't be there.
    """
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-prod.eu.auth0.com")
    monkeypatch.setattr(settings, "AUTH0_AUDIENCE", "https://api.recruit.vanceltech.com")
    with pytest.raises(AuthMisconfigured) as e:
        verify_auth_configuration()
    assert "Refusing to start" in str(e.value)


def test_auth_disabled_with_no_tenant_is_allowed(monkeypatch):
    """The one legitimate way to run unauthenticated: local dev, no Auth0 at all."""
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    verify_auth_configuration()  # must not raise


# ── /health payload ──────────────────────────────────────────────────────────
def test_readiness_reports_state_without_leaking_secrets(monkeypatch):
    monkeypatch.setattr(settings, "AUTH0_DOMAIN", "recruitr-prod.eu.auth0.com")
    monkeypatch.setattr(settings, "AUTH0_AUDIENCE", "https://api.recruit.vanceltech.com")
    r = auth_readiness()
    assert r["enabled"] is True and r["configured"] is True
    assert "secret" not in str(r).lower()


# ── CORS ─────────────────────────────────────────────────────────────────────
def test_stale_project_origin_is_gone():
    """job-hunt-kappa-two.vercel.app belonged to a different project. Every allowed
    origin can read authenticated responses, so a stale one is a real hole."""
    assert not any("job-hunt" in o for o in settings.CORS_ORIGINS)
