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


# ── module-code discrimination: the exact "everyone scores 100%" regression ──
#
# The audited SAP-CO/PS run scored a student, an SD/MM/EWM consultant and a bare
# "Senior sap Consultant" all at 100% roleFit, because the 2-letter "CO"/"PS"
# were dropped (too short) AND "co" matched as a substring of "Consultant". A
# CO/PS search must separate real CO/PS people from generic/wrong-domain ones.
COPS_TARGETS = [
    "Senior SAP CO Consultant", "SAP CO/PS Consultant",
    "SAP Controlling Consultant", "SAP PS Consultant",
    "SAP CO-PS Inhouse Consultant",
]


@pytest.mark.parametrize("title", [
    "Senior Consultant SAP Applications Sd/mm/ewm",   # wrong SAP domain
    "Senior SAP Consultant - Connectivity & Development",  # BTP/integration
    "Senior sap Consultant",                          # generic, no evidence
    "Senior Consultant SAP",                          # generic
])
def test_generic_or_wrong_domain_no_longer_scores_as_a_perfect_match(title):
    v = ps.score_profile({"currentTitle": title}, target_titles=COPS_TARGETS)
    # Used to be 100 / 1.0; the module-code cap holds these well below a match.
    assert v["roleFit"] <= 0.5, f"{title!r} still inflated: {v}"


@pytest.mark.parametrize("title", [
    "SAP Inhouse Senior Consultant CO/PS",
    "SAP CO Berater",
    "SAP PS Projektsystem Consultant",
])
def test_real_cops_specialists_still_rank_high(title):
    v = ps.score_profile({"currentTitle": title}, target_titles=COPS_TARGETS)
    assert v["roleFit"] >= 0.7, f"real specialist under-scored: {v}"


def test_short_module_code_is_not_a_substring_of_consultant():
    # The bug's mechanism: "co" must not be found inside "consultant".
    assert not ps.token_present("co", ps.tokens("Senior Consultant"))
    # …but a real standalone "CO" token is matched.
    assert ps.token_present("co", ps.tokens("SAP CO Berater"))


def test_module_code_survives_tokenisation():
    assert "co" in ps.tokens("SAP CO/PS Consultant")
    assert "ps" in ps.tokens("SAP CO/PS Consultant")


# ── executive/owner detection: the "cofounder as SAP consultant" leak ────────
@pytest.mark.parametrize("title", [
    "Owner", "Geschäftsführer", "Co-Founder & CEO", "Inhaber",
    "Gründer", "Selbständig", "Freiberufler", "CEO",
])
def test_executive_titles_detected(title):
    assert ps.is_executive_title(title)


@pytest.mark.parametrize("title", [
    "SAP CO Consultant", "Senior SAP Berater",
    "SAP CO/PS Inhouse Consultant", "Head of SAP Controlling",
])
def test_real_specialists_are_not_executives(title):
    assert not ps.is_executive_title(title)


# ── deterministic seniority scoring: a junior must not tie with a senior ──────
HCM_TARGETS = ["SAP HCM Consultant", "SAP HR Consultant", "SAP PY Consultant"]
HCM_SPECIALIST_REQS = {"title": "SAP-HCM Specialist"}   # role wants experience


def test_role_with_specialist_wants_experience():
    assert ps._role_wants_experience(HCM_TARGETS, HCM_SPECIALIST_REQS)
    # A plain consultant role names no seniority → does not trigger the penalty.
    assert not ps._role_wants_experience(HCM_TARGETS, {"title": "SAP HCM Consultant"})


@pytest.mark.parametrize("title", [
    "Junior Process Consultant SAP HCM & SuccessFactors",
    "Werkstudent SAP HCM",
    "Key User HCM und SAP Projekt",
    "SAP HCM Trainee",
])
def test_junior_or_enduser_is_scored_down_for_a_specialist_role(title):
    v = ps.score_profile({"currentTitle": title},
                         target_titles=HCM_TARGETS, requirements=HCM_SPECIALIST_REQS)
    # It still matches the specialty, but the level caps it well below a senior.
    assert v["score"] <= 55, f"{title!r} not scored down: {v}"


@pytest.mark.parametrize("title", [
    "Senior SAP HCM Consultant",
    "SAP HCM Consultant",
    "SAP-Spezialistin HCM",
])
def test_real_specialists_keep_full_score(title):
    v = ps.score_profile({"currentTitle": title},
                         target_titles=HCM_TARGETS, requirements=HCM_SPECIALIST_REQS)
    assert v["score"] >= 90, f"{title!r} under-scored: {v}"


def test_junior_not_penalised_when_role_names_no_seniority():
    # A "Consultant" role (no senior/specialist) accepts juniors — no cut.
    v = ps.score_profile({"currentTitle": "Junior SAP HCM Consultant"},
                         target_titles=HCM_TARGETS, requirements={"title": "SAP HCM Consultant"})
    assert v["score"] >= 90


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
