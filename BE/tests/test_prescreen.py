"""Unit tests for the pre-enrichment gate.

The gate spends free signal (a search hit's title) to avoid paying to enrich
people who are obviously not the role. Its bias is precision: a false DROP is
unrecoverable, a false KEEP costs one scrape. These tests pin that bias.

Titles below are REAL ones sourced for the live "SAP Consultant" job.
"""
import importlib

import pytest

ps = importlib.import_module("app.services.prescreen_service")

SAP_TARGETS = ["SAP S/4HANA Consultant", "SAP Consultant", "SAP Berater"]
SAP_REQS = {"title": "SAP Consultant", "mustHaveSkills": ["SAP", "S/4HANA-Migrationen"]}

PAY_TARGETS = ["Entgeltabrechner", "Personalsachbearbeiter Entgeltabrechnung", "Payroll Specialist"]
PAY_REQS = {"title": "Sachbearbeiter Entgeltabrechnung",
            "mustHaveSkills": ["SAP HR3", "Entgeltabrechnung", "Lohnsteuerrecht"]}


def _screen(title, reqs, targets):
    return ps.screen({"currentTitle": title}, requirements=reqs,
                     target_titles=targets, min_score=25.0)


# ── keeps the people the search meant to find ────────────────────────────────
@pytest.mark.parametrize("title", [
    "SAP Inhouse Consultant",
    "Associate Consultant SAP FI/CO",
    "SAP MDG Lead Consultant",
    "SAP Senior Solution Consultant",
    "Advanced Consultant SAP SD",
])
def test_keeps_real_sap_consultants(title):
    keep, v = _screen(title, SAP_REQS, SAP_TARGETS)
    assert keep, f"{title!r} was dropped ({v})"


# ── drops the actor's slop ───────────────────────────────────────────────────
@pytest.mark.parametrize("title", ["CEO", "Geschäftsführer", "Interim Manager"])
def test_drops_executive_slop(title):
    keep, v = _screen(title, SAP_REQS, SAP_TARGETS)
    assert not keep
    assert v["decision"] == "drop"


def test_drops_sap_people_from_an_unrelated_payroll_role():
    keep, _ = _screen("SAP MDG Lead Consultant", PAY_REQS, PAY_TARGETS)
    assert not keep


# ── German morphology: the compound the whole thing turns on ─────────────────
def test_matches_german_compound_across_inflection():
    """'Entgeltabrechnung' (JD) vs 'Entgeltabrechner' (headline) is the same job."""
    keep, v = _screen("Personalsachbearbeiter Entgeltabrechnung", PAY_REQS, PAY_TARGETS)
    assert keep and v["score"] >= 50
    keep, v = _screen("Entgeltabrechner", PAY_REQS, PAY_TARGETS)
    assert keep and v["score"] >= 50


# ── never drop on absent or unjudgeable signal ───────────────────────────────
def test_missing_title_is_kept():
    keep, v = ps.screen({"currentTitle": ""}, requirements=SAP_REQS, target_titles=SAP_TARGETS)
    assert keep, "an absent title is not evidence of a bad candidate"


def test_no_role_spec_keeps_everything():
    """A missing spec must never silently empty a recruiter's pipeline."""
    keep, v = ps.screen({"currentTitle": "CEO"}, requirements={}, target_titles=[])
    assert keep
    assert "No role spec" in v["reasons"][0]


def test_verdict_explains_itself():
    _, v = _screen("SAP Inhouse Consultant", SAP_REQS, SAP_TARGETS)
    assert v["matchedVia"]
    assert "SAP Inhouse Consultant" in v["reasons"][0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
