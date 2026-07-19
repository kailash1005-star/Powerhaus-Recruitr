"""
Tests for match-scoring-5: calibrated semantic component, the anchored-rubric
judge blend, the LLM boundary-validation models, and the sourcing filter fixes
shipped alongside (profileLanguage plumbing, broadener anchor fallback).

Pure logic — no network, no OpenAI, no Mongo.
"""
import importlib

import pytest

ms = importlib.import_module("app.services.matching_service")
llm = importlib.import_module("app.services.llm_extraction_service")


# ── semantic calibration ─────────────────────────────────────────────────────
def test_calibration_endpoints():
    assert ms.calibrate_similarity(ms.SIM_CALIBRATION_FLOOR) == 0.0
    assert ms.calibrate_similarity(ms.SIM_CALIBRATION_CEIL) == 1.0
    assert ms.calibrate_similarity(-0.5) == 0.0
    assert ms.calibrate_similarity(0.99) == 1.0


def test_calibration_is_continuous_at_zero():
    """Regression: the old normalisation mapped sim=-0.001 to ~0.4995 but
    sim=+0.001 to 0.001 — a 50-point cliff at exactly zero."""
    below = ms.calibrate_similarity(-0.001)
    above = ms.calibrate_similarity(0.001)
    assert abs(below - above) < 0.01


def test_calibration_is_monotone():
    sims = [-0.2, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8]
    vals = [ms.calibrate_similarity(s) for s in sims]
    assert vals == sorted(vals)


def test_mid_range_cosine_now_earns_mid_range_points():
    """A typical 'related pair' cosine (~0.35) used to enter the 50%-weight
    component as 0.35; calibrated it reads as the midpoint it empirically is."""
    assert ms.calibrate_similarity(0.35) == pytest.approx(0.5, abs=0.01)


# ── judge blend ──────────────────────────────────────────────────────────────
def _scored(score: float, ceiling: float) -> dict:
    return {"score": score, "breakdown": {"ceiling": ceiling}}


def test_judge_blend_moves_the_score():
    s = _scored(60.0, 100.0)
    ms.apply_judge(s, {"fitScore": 90, "verdict": "Strong"})
    w = ms.settings.MATCH_JUDGE_WEIGHT
    assert s["score"] == pytest.approx(round((1 - w) * 60 + w * 90, 1))
    assert s["breakdown"]["judge"]["verdict"] == "Strong"
    assert s["breakdown"]["judge"]["deterministicScore"] == 60.0


def test_judge_cannot_lift_past_the_coverage_ceiling():
    """Prose never outranks missing must-haves: the blend is re-capped."""
    s = _scored(60.0, 65.0)
    ms.apply_judge(s, {"fitScore": 100, "verdict": "Ready now"})
    assert s["score"] == 65.0
    assert s["breakdown"]["judge"]["cappedByCeiling"] is True


def test_judge_can_pull_a_score_down():
    s = _scored(80.0, 100.0)
    ms.apply_judge(s, {"fitScore": 40, "verdict": "Stretch"})
    assert s["score"] < 80.0


def test_missing_judge_leaves_deterministic_score_marked():
    s = _scored(72.5, 100.0)
    ms.apply_judge(s, None)
    assert s["score"] == 72.5
    assert s["breakdown"]["judge"] is None


# ── boundary validation models ───────────────────────────────────────────────
def test_jd_requirements_coerces_llm_sloppiness():
    req = llm.JDRequirements.model_validate({
        "title": "Payroll Specialist",
        "mustHaveSkills": "SAP HR",          # string, not list
        "niceToHaveSkills": None,             # null, not list
        "minYears": "5+ Jahre",               # prose, not number
        "location": None, "seniority": None,
        "responsibilities": ["", "  ", "Run payroll"],
    }).model_dump()
    assert req["mustHaveSkills"] == ["SAP HR"]
    assert req["niceToHaveSkills"] == []
    assert req["minYears"] == 5.0
    assert req["responsibilities"] == ["Run payroll"]


def test_jd_requirements_rejects_absurd_years():
    assert llm.JDRequirements.model_validate({"minYears": 250}).minYears is None


def test_judge_item_clamps_out_of_range_scores():
    item = llm.JudgeItem.model_validate({"id": "x", "fitScore": 140})
    assert item.fitScore == 100.0
    item = llm.JudgeItem.model_validate({"id": "x", "fitScore": -3})
    assert item.fitScore == 0.0
    item = llm.JudgeItem.model_validate({"id": "x", "fitScore": "not-a-number"})
    assert item.fitScore == 0.0


def test_cv_fields_coerces_years_and_lists():
    cv = llm.CvFields.model_validate({
        "totalYears": "ca. 7 Jahre",
        "skills": ["Python", "", None],
    })
    assert cv.totalYears == 7.0
    assert cv.skills == ["Python"]


# ── profileLanguage plumbing (the dead questionnaire field) ──────────────────
def test_singular_profile_language_reaches_the_actor():
    from app.services.apify_search_service import _build_input
    run_input = _build_input({"profileLanguage": "German"}, max_items=10)
    assert run_input.get("profileLanguages") == ["German"]


def test_plural_profile_languages_still_win_over_singular():
    from app.services.apify_search_service import _build_input
    run_input = _build_input(
        {"profileLanguage": "French", "profileLanguages": ["German"]}, max_items=10)
    assert run_input.get("profileLanguages") == ["German"]


# ── broadener: anchor fallback for searchQuery-only searches ─────────────────
def test_broadener_anchor_falls_back_to_brief_title():
    from app.services.sourcing import broadener as b
    from app.services.sourcing.models import SearchAttempt, SearchBrief

    query_only = SearchAttempt(attempt=1, filters={"searchQuery": "payroll sap"})
    brief = SearchBrief(jobTitle="Entgeltabrechner SAP")
    # Without the brief there is nothing to anchor to (the old unguarded case);
    # with it, the job title supplies the domain. Since FC-23 the anchor is
    # CORE terms only — "sap" is the ecosystem brand every neighbouring
    # profession shares, so it is deliberately NOT in the anchor.
    assert b._domain_anchor([query_only]) == []
    anchor = b._domain_anchor([query_only], brief)
    assert "entgeltabrechner" in anchor and "payroll" in anchor
    assert "sap" not in anchor


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
