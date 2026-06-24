"""
Unit tests for the matching engine's deterministic core.

These cover the pure scoring/logic — no network, no Docling, no OpenAI — so they
run fast and prove the ranking math is correct and stable.
"""
import importlib

import pytest

ms = importlib.import_module("app.services.matching_service")


# ── skill matching ───────────────────────────────────────────────────────────
def test_skill_present_exact_and_fuzzy():
    skills = ["Python", "React.js", "AWS"]
    assert ms._skill_present("python", skills)
    assert ms._skill_present("react", skills)        # substring
    assert ms._skill_present("Amazon AWS", skills)   # fuzzy / contains
    assert not ms._skill_present("Kubernetes", skills)


def test_skill_present_empty():
    assert not ms._skill_present("", ["Python"])
    assert not ms._skill_present("Python", [])


# ── candidate scoring ────────────────────────────────────────────────────────
def test_score_full_match_is_high():
    jd = {"mustHaveSkills": ["Python", "AWS"], "minYears": 3, "location": "Berlin"}
    profile = {"skills": ["Python", "AWS", "Docker"], "totalYears": 5, "location": "Berlin, DE"}
    score, sub, gaps = ms._score_candidate(jd, profile, sim=0.9)
    assert gaps == []
    assert sub["skillCoverage"] == 100.0
    assert sub["experience"] == 100.0
    assert score > 85


def test_score_missing_skill_lists_gap_and_lowers():
    jd = {"mustHaveSkills": ["Python", "Kubernetes"], "minYears": None, "location": None}
    profile = {"skills": ["Python"], "totalYears": 4, "location": "Remote"}
    score, sub, gaps = ms._score_candidate(jd, profile, sim=0.8)
    assert "Kubernetes" in gaps
    assert sub["skillCoverage"] == 50.0


def test_score_underqualified_years():
    jd = {"mustHaveSkills": [], "minYears": 10, "location": None}
    profile = {"skills": [], "totalYears": 5, "location": None}
    score, sub, gaps = ms._score_candidate(jd, profile, sim=0.5)
    assert sub["experience"] == 50.0   # 5/10


def test_score_no_requirements_defaults_to_full():
    jd = {"mustHaveSkills": [], "minYears": None, "location": None}
    profile = {"skills": [], "totalYears": None, "location": None}
    score, sub, gaps = ms._score_candidate(jd, profile, sim=1.0)
    assert sub["skillCoverage"] == 100.0
    assert sub["experience"] == 100.0
    assert sub["location"] == 100.0
    assert score == 100.0


def test_ranking_orders_by_score():
    jd = {"mustHaveSkills": ["Python"], "minYears": None, "location": None}
    strong = ms._score_candidate(jd, {"skills": ["Python"], "totalYears": 8}, sim=0.95)[0]
    weak = ms._score_candidate(jd, {"skills": ["Java"], "totalYears": 1}, sim=0.2)[0]
    assert strong > weak


# ── embed-text composition ───────────────────────────────────────────────────
def test_embed_text_prefers_profile():
    profile = {"currentTitle": "Data Engineer", "skills": ["Python", "Spark"],
               "titles": ["ETL Developer"], "experience": [], "education": [], "certifications": []}
    text = ms._embed_text_from_profile(profile, "raw markdown")
    assert "Data Engineer" in text and "Spark" in text


def test_embed_text_falls_back_to_markdown_when_thin():
    text = ms._embed_text_from_profile({}, "X" * 100)
    assert text.startswith("X")


# ── fallback reasons ─────────────────────────────────────────────────────────
def test_fallback_reasons_nonempty():
    jd = {"mustHaveSkills": ["Python", "AWS"]}
    profile = {"totalYears": 6, "currentTitle": "Backend Engineer"}
    scored = {"gaps": ["AWS"]}
    reasons = ms._fallback_reasons(jd, profile, scored)
    assert any("1/2" in r for r in reasons)
    assert any("6 years" in r for r in reasons)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
