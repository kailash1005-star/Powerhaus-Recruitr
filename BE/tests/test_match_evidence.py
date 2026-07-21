"""Evidence-pool regression evals (FC-29).

The fixture is the REAL production false negative, verbatim from the DB: Marina
W., a working SAP-HCM specialist whose enrichment returned `skills: []` and
whose evidence lives only in her title and experience bullet points. The old
scorer read neither → all 7 must-haves "missing" → coverage 0 → ceiling 25 →
score 16.2, displayed under reasons saying "No evidence of SAP-HCM experience
despite current title as SAP-Spezialistin HCM."

These tests pin the floor under that class of bug: titles/headline evidence
counts (all-terms rule), parenthesised abbreviations count ("Payroll (PY)" ↔
"PY"), free-text experience summaries count — and, just as important, the rules
that stop over-crediting (scatter across entries, substring-inside-word,
short-token noise) hold.
"""
from __future__ import annotations

import pytest

from app.services.matching_service import (
    _free_text_entries,
    _match_skill,
    _score_candidate,
    _skill_evidence_pool,
    _skill_variants,
)

# Verbatim from candidates/apifyEnrichment.profile (surname redacted in comments
# only; the data is what the scorer saw in production).
MARINA = {
    "fullName": "Marina W.",
    "headline": "SAP-Spezialistin HCM",
    "currentTitle": "SAP-Spezialistin HCM",
    "summary": "",
    "totalYears": 10.6,
    "skills": [],  # ← the trigger: HarvestAPI returned an empty skills array
    "titles": [
        "SAP-Spezialistin HCM",
        "Senior Ausbilderin kaufmännische Berufe",
        "Specialist for Learning- and Developmentsystems",
        "Sachbearbeiterin Personalentwicklung",
        "Ausbildung zur Industriekauffrau",
    ],
    "experience": [
        {
            "title": "SAP-Spezialistin HCM",
            "company_name": "Erzdiözese München und Freising",
            "summary": (
                "○ Betreuung und Weiterentwicklung von personalwirtschaftlichen Themen "
                "im SAP Umfeld mit den Schwerpunkten SAP HCM PA, PY, OM, ESS/MSS, "
                "Success Factors\n"
                "○ Sicherstellen der Betreuung und Verfügbarkeit der betroffenen SAP "
                "Module mit Hilfe von und durch Steuerung von externen Dienstleistern\n"
                "○ Begleitung von SAP-Rollout-Projekten inkl. Projektleitung, Beratung "
                "und Betreuung während der Implementierungsphase"
            ),
        },
        {
            "title": "Senior Ausbilderin kaufmännische Berufe",
            "company_name": "Stadtwerke München GmbH",
            "summary": (
                "○ Vermittlung von Fertigkeiten und Kenntnissen nach den IHK-Berufsbildern\n"
                "○ Recruiting für die genannten Berufe"
            ),
        },
        {
            "title": "Specialist for Learning- and Developmentsystems",
            "company_name": "Stadtwerke München GmbH",
            "summary": (
                "○ Fachverantwortung für das Learning Management System SAP Learning Solution\n"
                "○ Testmanagement für Releases und bei Einführung neuer Funktionen"
            ),
        },
    ],
}

MUST = ["SAP-HCM", "SAP HR", "Payroll (PY)", "SAP Customizing",
        "HR Process Optimization", "SAP HR Processes", "SAP Troubleshooting"]
JD = {"mustHaveSkills": MUST, "title": "SAP-HCM Specialist", "location": "München"}


# ── Variant expansion ────────────────────────────────────────────────────────

class TestSkillVariants:
    def test_parenthesised_abbreviation_becomes_variant(self):
        v = [x.lower() for x in _skill_variants("Payroll (PY)")]
        assert "payroll (py)" in v and "payroll" in v and "py" in v

    def test_slash_list_in_parens_splits(self):
        v = [x.lower() for x in _skill_variants("Time Management (PT/PY)")]
        assert "pt" in v and "py" in v

    def test_plain_skill_is_single_variant(self):
        assert _skill_variants("SAP Customizing") == ["SAP Customizing"]

    def test_empty(self):
        assert _skill_variants("") == []


# ── The Marina regression, end to end ────────────────────────────────────────

class TestMarinaRegression:
    def test_pool_includes_headline(self):
        pool = _skill_evidence_pool(MARINA)
        assert "SAP-Spezialistin HCM" in pool

    def test_free_text_carries_experience_summaries(self):
        entries = _free_text_entries(MARINA)
        assert any("SAP HCM PA, PY, OM" in e for e in entries)

    def test_sap_hcm_credited_from_title(self):
        """THE bug: 'SAP-HCM' vs title 'SAP-Spezialistin HCM' scored 0.0."""
        ev = _match_skill("SAP-HCM", _skill_evidence_pool(MARINA), _free_text_entries(MARINA))
        assert ev["credit"] == 1.0
        assert ev["method"] in ("all-terms", "specific", "profile-text")

    def test_payroll_py_credited_from_summary(self):
        """'Payroll (PY)' evidenced by the standalone module code 'PY' in the
        experience bullet — the abbreviation variant + free-text tier."""
        ev = _match_skill("Payroll (PY)", _skill_evidence_pool(MARINA), _free_text_entries(MARINA))
        assert ev["credit"] == 1.0
        assert "PY" in (ev["via"] or "")

    def test_score_escapes_the_no_coverage_ceiling(self):
        score, subscores, gaps, breakdown = _score_candidate(JD, MARINA, 0.45)
        # She evidences 2 of 7 must-haves (SAP-HCM, Payroll) → coverage lifts the
        # ceiling off the zero-coverage floor (8) into the 0.25 band (50). Under
        # match-scoring-8's evidence-led weights a 2-of-7 partial specialist lands
        # in the high-40s — nowhere near the 16.2 false negative this guards, and
        # far above the wrong-domain weak cap (22).
        assert breakdown["ceiling"] > 25.0, "coverage>0 must lift the zero-coverage cap"
        assert score >= 48.0
        assert "SAP-HCM" not in gaps
        assert "Payroll (PY)" not in gaps

    def test_reasons_input_no_longer_all_missing(self):
        _, _, gaps, _ = _score_candidate(JD, MARINA, 0.45)
        # The judge/reasoner is handed `gaps` as authoritative — at most the
        # genuinely-unevidenced skills may remain there.
        assert len(gaps) < len(MUST)


# ── Over-crediting guards (the flip side must NOT regress) ───────────────────

class TestNoOverCredit:
    def test_unrelated_skill_stays_uncredited(self):
        ev = _match_skill("Kubernetes", _skill_evidence_pool(MARINA), _free_text_entries(MARINA))
        assert ev["credit"] == 0.0

    def test_abbreviation_needs_token_boundaries(self):
        """'PY' must not fire inside 'Python' — module codes match as whole
        tokens only."""
        texts = ["Senior Python Developer — built pipelines in Python and Typescript"]
        ev = _match_skill("Payroll (PY)", [], texts)
        assert ev["credit"] == 0.0

    def test_terms_scattered_across_entries_do_not_cooccur(self):
        """'SAP Customizing': 'SAP' in one job, 'Customizing' in another —
        different roles, no co-occurrence credit."""
        texts = [
            "Worked with SAP FI and MM modules",
            "Customizing of the in-house CRM platform",
        ]
        ev = _match_skill("SAP Customizing", [], texts)
        assert ev["credit"] == 0.0

    def test_cooccurrence_in_one_entry_is_partial_not_full(self):
        texts = ["Verantwortlich für SAP Systeme inklusive Customizing der HR-Module"]
        ev = _match_skill("SAP Customizing", [], texts)
        assert 0 < ev["credit"] < 1.0
        assert ev["method"] == "profile-text-terms"

    def test_contiguous_phrase_in_entry_is_full(self):
        texts = ["Schwerpunkt SAP Customizing und Rollouts"]
        ev = _match_skill("SAP Customizing", [], texts)
        assert ev["credit"] == 1.0
        assert ev["method"] == "profile-text"

    def test_short_items_still_win_the_evidence_race(self):
        """A skills-list hit must be named as the source even when free text
        would also match — provenance quality matters to the recruiter."""
        ev = _match_skill("SAP Customizing", ["SAP Customizing"],
                          ["Schwerpunkt SAP Customizing und Rollouts"])
        assert ev["method"] == "exact"


# ── forced_credits (the QA correction channel) ──────────────────────────────

class TestForcedCredits:
    def test_verified_credit_lifts_score_through_real_math(self):
        base_score, _, base_gaps, _ = _score_candidate(JD, MARINA, 0.45)
        forced = {"SAP HR": {"quote": "SAP HCM PA, PY, OM"},
                  "SAP HR Processes": {"quote": "personalwirtschaftlichen Themen im SAP Umfeld"}}
        score, _, gaps, breakdown = _score_candidate(JD, MARINA, 0.45, forced_credits=forced)
        assert score > base_score
        assert "SAP HR" not in gaps and "SAP HR Processes" not in gaps
        ev = {e["skill"]: e for c in breakdown["components"] if c["key"] == "skillCoverage"
              for e in c["skills"]}
        assert ev["SAP HR"]["method"] == "qa_verified"

    def test_forced_credit_never_downgrades_an_earned_full_credit(self):
        forced = {"SAP-HCM": {"quote": "irrelevant"}}
        _, _, _, breakdown = _score_candidate(JD, MARINA, 0.45, forced_credits=forced)
        ev = {e["skill"]: e for c in breakdown["components"] if c["key"] == "skillCoverage"
              for e in c["skills"]}
        # Already fully credited deterministically — the forced entry must not
        # replace the stronger native evidence.
        assert ev["SAP-HCM"]["method"] != "qa_verified"

    def test_forced_credit_ignores_skills_not_in_the_jd(self):
        forced = {"Underwater Basket Weaving": {"quote": "x"}}
        score, _, _, _ = _score_candidate(JD, MARINA, 0.45, forced_credits=forced)
        base, _, _, _ = _score_candidate(JD, MARINA, 0.45)
        assert score == base


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
