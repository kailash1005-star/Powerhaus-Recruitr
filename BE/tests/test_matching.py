"""
Unit tests for the matching engine's deterministic core.

These cover the pure scoring/logic — no network, no Docling, no OpenAI — so they
run fast and prove the ranking math is correct and stable.
"""
import importlib

import pytest

ms = importlib.import_module("app.services.matching.matching_service")


# ── skill matching ───────────────────────────────────────────────────────────
def test_skill_present_exact_and_fuzzy():
    skills = ["Python", "React.js", "AWS"]
    assert ms._skill_present("python", skills)
    assert ms._skill_present("react", skills)        # candidate has the specific variant
    assert ms._skill_present("Amazon AWS", skills)   # candidate term is broader
    assert not ms._skill_present("Kubernetes", skills)


def test_skill_present_empty():
    assert not ms._skill_present("", ["Python"])
    assert not ms._skill_present("Python", [])


def test_short_skill_does_not_match_inside_a_word():
    """Regression: the old bare-substring rule credited the one-letter skill "R"
    against every German compound that merely contains the letter r, handing a
    Data Engineer 100% coverage on an SAP payroll JD."""
    skills = ["R", "Python", "SQL"]
    for must in ["SAP HR3", "Entgeltabrechnung", "Arbeitsrecht",
                 "Sozialversicherungsrecht", "Lohnsteuerrecht"]:
        assert not ms._skill_present(must, skills), f"{must!r} must not match on 'R'"


def test_standalone_short_skill_still_matches():
    assert ms._skill_present("R", ["R", "Python"])
    assert ms._skill_present("Go", ["Go", "Rust"])


def test_broader_candidate_skill_gets_half_credit():
    """Candidate lists "SAP"; the JD wants "SAP HR3". Related, but not proof."""
    ev = ms._match_skill("SAP HR3", ["SAP", "Python"])
    assert ev["credit"] == 0.5
    assert ev["method"] == "broader"
    assert ev["via"] == "SAP"


def test_specific_candidate_skill_gets_full_credit():
    ev = ms._match_skill("SAP", ["SAP HR3 Payroll"])
    assert ev["credit"] == 1.0
    assert ev["method"] == "specific"


# ── evidence pool: a title is evidence of a skill ────────────────────────────
# Sina Baumann, sourced live for the German payroll role. Her skills list never
# says "Entgeltabrechnung" — her JOB TITLE does. Scoring `skills` alone reported
# the must-have as missing for someone whose actual job is that requirement.
SINA = {
    "fullName": "Sina Baumann",
    "currentTitle": "Personalsachbearbeiterin Entgeltabrechnung",
    "titles": ["Personalsachbearbeiterin Entgeltabrechnung",
               "Ausbildung Kauffrau für Büromanagement"],
    "experience": [{"title": "Personalsachbearbeiterin Entgeltabrechnung"}],
    "totalYears": 4,
    "skills": ["Personaladministration", "Sozialversicherungsrecht", "Lohnabrechnung",
               "Lohnsteuer", "Lohn- und Gehaltsabrechnung", "SAP HR",
               "Arbeitsrecht (Grundkenntnisse)", "DATEV"],
}
PAYROLL_JD = {
    "mustHaveSkills": ["SAP HR3", "Entgeltabrechnung", "Arbeitsrecht",
                       "Sozialversicherungsrecht", "Lohnsteuerrecht"],
    "minYears": 3, "location": None,
}


def test_job_title_evidences_a_must_have_skill():
    ev = ms._match_skill("Entgeltabrechnung", ms._skill_evidence_pool(SINA))
    assert ev["credit"] == 1.0, "her job title IS Entgeltabrechnung"
    assert "Entgeltabrechnung" in str(ev["via"])


def test_german_compound_credits_the_requirement():
    """'Lohnsteuer' sits inside 'Lohnsteuerrecht' as a prefix, not a token."""
    ev = ms._match_skill("Lohnsteuerrecht", ["Lohnsteuer", "DATEV"])
    assert ev["credit"] == 0.5
    assert ev["via"] == "Lohnsteuer"


def test_compound_rule_does_not_resurrect_the_R_bug():
    """The min-length floor is what separates 'Lohnsteuer' from 'R'."""
    for must in ["SAP HR3", "Entgeltabrechnung", "Arbeitsrecht", "Lohnsteuerrecht"]:
        assert not ms._skill_present(must, ["R", "Python", "SQL"]), must


def test_real_payroll_candidate_is_no_longer_wrongly_capped():
    score, sub, gaps, bd = ms._score_candidate(PAYROLL_JD, SINA, sim=0.715)
    # Was 55% coverage → capped at 65 before titles counted as evidence.
    assert sub["skillCoverage"] >= 75, f"coverage {sub['skillCoverage']} — {gaps}"
    assert score > 65
    assert "Entgeltabrechnung" not in gaps


def test_evidence_pool_prefers_a_real_skill_over_a_title():
    """A skills-list hit should be named as the source, not the title."""
    pool = ms._skill_evidence_pool(SINA)
    assert pool[0] == "Personaladministration"  # skills come first
    ev = ms._match_skill("Sozialversicherungsrecht", pool)
    assert ev["via"] == "Sozialversicherungsrecht" and ev["method"] == "exact"


# ── candidate scoring ────────────────────────────────────────────────────────
def test_score_full_match_is_high():
    jd = {"mustHaveSkills": ["Python", "AWS"], "minYears": 3, "location": "Berlin"}
    profile = {"skills": ["Python", "AWS", "Docker"], "totalYears": 5, "location": "Berlin, DE"}
    score, sub, gaps, bd = ms._score_candidate(jd, profile, sim=0.9)
    assert gaps == []
    assert sub["skillCoverage"] == 100.0
    assert sub["experience"] == 100.0
    assert score > 85
    assert bd["cappedBy"] is None


def test_score_missing_skill_lists_gap_and_lowers():
    jd = {"mustHaveSkills": ["Python", "Kubernetes"], "minYears": None, "location": None}
    profile = {"skills": ["Python"], "totalYears": 4, "location": "Remote"}
    score, sub, gaps, bd = ms._score_candidate(jd, profile, sim=0.8)
    assert "Kubernetes" in gaps
    assert sub["skillCoverage"] == 50.0


def test_score_underqualified_years():
    jd = {"mustHaveSkills": [], "minYears": 10, "location": None}
    profile = {"skills": [], "totalYears": 5, "location": None}
    score, sub, gaps, bd = ms._score_candidate(jd, profile, sim=0.5)
    assert sub["experience"] == 50.0   # 5/10


def test_score_no_requirements_defaults_to_full():
    jd = {"mustHaveSkills": [], "minYears": None, "location": None}
    profile = {"skills": [], "totalYears": None, "location": None}
    score, sub, gaps, bd = ms._score_candidate(jd, profile, sim=1.0)
    assert sub["skillCoverage"] == 100.0
    assert sub["experience"] == 100.0
    assert sub["location"] == 100.0
    assert score == 100.0


def test_ranking_orders_by_score():
    jd = {"mustHaveSkills": ["Python"], "minYears": None, "location": None}
    strong = ms._score_candidate(jd, {"skills": ["Python"], "totalYears": 8}, sim=0.95)[0]
    weak = ms._score_candidate(jd, {"skills": ["Java"], "totalYears": 1}, sim=0.2)[0]
    assert strong > weak


# ── weight redistribution & gating ───────────────────────────────────────────
def test_unstated_requirement_is_not_free_points():
    """A JD naming no location must not hand every candidate location points —
    the weight is redistributed over what the JD actually stated."""
    jd = {"mustHaveSkills": ["Python"], "minYears": None, "location": None}
    profile = {"skills": ["Python"], "totalYears": 4, "location": None}
    _, _, _, bd = ms._score_candidate(jd, profile, sim=0.5)
    comps = {c["key"]: c for c in bd["components"]}
    assert comps["location"]["applicable"] is False
    assert comps["location"]["maxPoints"] == 0.0
    assert comps["experience"]["applicable"] is False
    # semantic .35 + skillCoverage .40 = .75 live → renormalised to .4667 / .5333
    assert comps["semantic"]["weight"] == pytest.approx(0.35 / 0.75, abs=1e-3)
    assert comps["skillCoverage"]["weight"] == pytest.approx(0.40 / 0.75, abs=1e-3)
    assert sum(c["maxPoints"] for c in bd["components"]) == pytest.approx(100.0, abs=0.2)


def test_zero_coverage_caps_the_score():
    """A strong semantic read cannot carry a candidate who has none of the must-haves."""
    jd = {"mustHaveSkills": ["SAP HR3", "Entgeltabrechnung"], "minYears": None, "location": None}
    profile = {"skills": ["Python", "R"], "totalYears": 9}
    score, sub, gaps, bd = ms._score_candidate(jd, profile, sim=0.99)
    assert sub["skillCoverage"] == 0.0
    assert score <= ms.NO_COVERAGE_CEILING
    assert bd["cappedBy"] is not None


def test_breakdown_points_sum_to_the_base_score():
    jd = {"mustHaveSkills": ["Python", "AWS"], "minYears": 3, "location": "Berlin"}
    profile = {"skills": ["Python", "AWS"], "totalYears": 5, "location": "Berlin"}
    _, _, _, bd = ms._score_candidate(jd, profile, sim=0.7)
    assert sum(c["points"] for c in bd["components"]) == pytest.approx(bd["base"], abs=0.2)


def test_breakdown_records_skill_evidence():
    jd = {"mustHaveSkills": ["SAP HR3"], "minYears": None, "location": None}
    _, _, _, bd = ms._score_candidate(jd, {"skills": ["SAP"]}, sim=0.4)
    comps = {c["key"]: c for c in bd["components"]}
    ev = comps["skillCoverage"]["skills"][0]
    assert ev["skill"] == "SAP HR3" and ev["via"] == "SAP" and ev["method"] == "broader"


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


def test_strongest_rule_wins_not_first_rule():
    """'SAP HR' vs 'SAP HR3' is both a compound-prefix hit (0.5) and a 92% fuzzy
    hit (0.75). Short-circuiting on the compound silently downgraded real
    candidates — evaluate every rule and take the best."""
    ev = ms._match_skill("SAP HR3", ["SAP HR"])
    assert ev["credit"] == 0.75, f"got {ev}"
    assert ev["method"] == "fuzzy"


def test_adding_evidence_never_lowers_credit():
    """A bigger evidence pool must be monotonic — more to match against can only
    help. Guards the regression where a title crowded out a better skills hit."""
    skills_only = ms._match_skill("SAP HR3", SINA["skills"])["credit"]
    full_pool = ms._match_skill("SAP HR3", ms._skill_evidence_pool(SINA))["credit"]
    assert full_pool >= skills_only


# ── gaps vs partials: the data must agree with itself ────────────────────────
def test_a_partial_match_is_not_reported_as_a_gap():
    """Regression: 'SAP HR3' scored 0.75 via 'SAP HR' yet was listed under gaps,
    which the reasoning LLM then rendered as "Missing specific experience with
    SAP HR3" — directly contradicting the breakdown shown beside it."""
    score, sub, gaps, bd = ms._score_candidate(PAYROLL_JD, SINA, sim=0.715)
    assert "SAP HR3" not in gaps, "0.75 credit is not an absence"
    assert "Lohnsteuerrecht" not in gaps, "0.5 credit is not an absence"
    partial = {p["skill"] for p in bd["partialMustHave"]}
    assert partial == {"SAP HR3", "Lohnsteuerrecht"}


def test_gaps_are_only_the_wholly_unevidenced():
    jd = {"mustHaveSkills": ["Python", "Kubernetes"], "minYears": None, "location": None}
    _, _, gaps, bd = ms._score_candidate(jd, {"skills": ["Python"]}, sim=0.5)
    assert gaps == ["Kubernetes"]
    assert bd["partialMustHave"] == []


def test_every_must_have_is_exactly_one_of_credited_partial_or_missing():
    """No must-have may fall through the cracks or be double-counted."""
    _, _, gaps, bd = ms._score_candidate(PAYROLL_JD, SINA, sim=0.7)
    comp = next(c for c in bd["components"] if c["key"] == "skillCoverage")
    full = {e["skill"] for e in comp["skills"] if e["credit"] >= 1.0}
    partial = {p["skill"] for p in bd["partialMustHave"]}
    missing = set(gaps)
    assert full | partial | missing == set(PAYROLL_JD["mustHaveSkills"])
    assert not (full & partial) and not (full & missing) and not (partial & missing)


def test_fallback_reasons_counts_partial_credit_not_gap_length():
    jd = {"mustHaveSkills": ["SAP HR3", "Entgeltabrechnung", "Arbeitsrecht",
                             "Sozialversicherungsrecht", "Lohnsteuerrecht"]}
    score, sub, gaps, bd = ms._score_candidate(PAYROLL_JD, SINA, sim=0.7)
    reasons = ms._fallback_reasons(jd, SINA, {"gaps": gaps, "breakdown": bd})
    # 4.25/5 credited — NOT 5/5 just because nothing is wholly missing.
    assert any("4.25/5" in r for r in reasons), reasons


# ── Kastell's manual-screening patterns (2026-07-30, "Sales Manager SAP
# Retail") ──────────────────────────────────────────────────────────────────
# Six real verdicts from a client's manual LinkedIn review; none of these
# reasoning patterns existed in the scorer before. Fixtures reproduce his
# stated reasons as regression tests — see the "Evidence-grounded screening"
# plan. Historical experience dates (2002-2020) are fixed in the past on
# purpose: they must always read as historical, today and in the future.
cm = importlib.import_module("app.services.sourcing.candidate_merge")

SAP_RETAIL_SALES_JD = {
    "title": "Senior Sales Manager SAP Retail",
    "mustHaveSkills": ["SAP Retail"],
}


def test_dietmar_stale_domain_evidence_is_discounted_not_credited():
    """"Correct topic but was a sales manager 2002-2005, then developed
    himself in the technical presales niche... therefore not relevant."
    SAP Retail Sales is real in his profile, but only in a role that ended
    two decades ago — the current role must not inherit that credit."""
    profile = {
        "currentTitle": "Technical Presales Consultant",
        "skills": ["Presales", "Solution Architecture"],
        "experience": [
            {"title": "Technical Presales Consultant",
             "summary": "Solution demos for enterprise clients.",
             "starts_at": "2006-01", "ends_at": None, "is_current": True},
            {"title": "Sales Manager SAP Retail",
             "summary": "Sold SAP Retail solutions to retail chains.",
             "starts_at": "2002-01", "ends_at": "2005-12", "is_current": False},
        ],
    }
    score, sub, gaps, bd = ms._score_candidate(SAP_RETAIL_SALES_JD, profile, sim=0.5)
    assert gaps == []  # credited, not missing...
    stale = bd["staleEvidence"]
    assert len(stale) == 1 and stale[0]["skill"] == "SAP Retail"
    # ...but discounted: the coverage math used the discounted credit.
    skill_row = next(c for c in bd["components"] if c["key"] == "skillCoverage")
    assert skill_row["skills"][0]["credit"] == pytest.approx(ms._STALE_EVIDENCE_DISCOUNT)


def test_diana_junior_historical_role_also_flagged_stale():
    """"2009 she was some kind of clerk for the sales department but then
    went on to other topics at sap." Same pattern as Dietmar."""
    profile = {
        "currentTitle": "Data Protection Officer",
        "skills": ["GDPR", "Compliance"],
        "experience": [
            {"title": "Data Protection Officer",
             "summary": "Privacy compliance program.",
             "starts_at": "2015-01", "ends_at": None, "is_current": True},
            {"title": "Sales Clerk SAP Retail",
             "summary": "Administrative support for the SAP Retail sales team.",
             "starts_at": "2009-01", "ends_at": "2009-12", "is_current": False},
        ],
    }
    _, _, _, bd = ms._score_candidate(SAP_RETAIL_SALES_JD, profile, sim=0.4)
    assert len(bd["staleEvidence"]) == 1


def test_melanie_current_domain_evidence_is_not_flagged_stale():
    """Positive control — Melanie Hagen, "Top candidate!" — the domain match
    is in her CURRENT role, so nothing should be discounted."""
    profile = {
        "currentTitle": "Senior Sales Manager SAP Retail",
        "skills": ["SAP Retail", "S/4HANA Retail", "Enterprise Sales"],
        "experience": [
            {"title": "Senior Sales Manager SAP Retail",
             "summary": "Selling SAP Retail transformation deals.",
             "starts_at": "2020-01", "ends_at": None, "is_current": True},
        ],
    }
    score, sub, gaps, bd = ms._score_candidate(SAP_RETAIL_SALES_JD, profile, sim=0.6)
    assert bd["staleEvidence"] == []
    assert gaps == []


def test_ralf_executive_title_is_penalized_as_overqualified():
    """"Product Owner/Manager/Chairman. Is 5 Levels higher than the one we
    are looking for and no sales person." """
    profile = {"currentTitle": "Product Owner / Chairman", "skills": ["SAP Retail"]}
    mult, reason = ms.seniority_band_fit(profile, SAP_RETAIL_SALES_JD)
    assert mult < 1.0 and reason
    entry = {"score": 80.0, "breakdown": {}}
    ms.apply_screening_signals(entry, seniority_band=(mult, reason), inactive=None)
    assert entry["score"] < 40.0


def test_seniority_band_fit_does_not_penalize_a_non_executive_title():
    profile = {"currentTitle": "Senior Sales Manager SAP Retail"}
    mult, reason = ms.seniority_band_fit(profile, SAP_RETAIL_SALES_JD)
    assert mult == 1.0 and reason is None


def test_seniority_band_fit_allows_executive_titles_for_an_executive_role():
    """The penalty only fires when the ROLE itself isn't executive-level."""
    profile = {"currentTitle": "Chief Revenue Officer"}
    jd = {"title": "Chief Revenue Officer", "mustHaveSkills": []}
    mult, reason = ms.seniority_band_fit(profile, jd)
    assert mult == 1.0 and reason is None


def test_christian_inactive_since_a_long_gap_is_flagged():
    """"He is in pension and seems to be out since 2020." No headline marker
    needed — the date gap alone is the signal here."""
    profile = {
        "experience": [
            {"title": "Sales Manager SAP Retail", "starts_at": "2010-01",
             "ends_at": "2020-01", "is_current": False},
        ],
    }
    flag = cm.inactive_candidate_flag(profile)
    assert flag and flag["inactive"] is True


def test_retirement_headline_is_flagged_even_with_recent_dates():
    profile = {
        "headline": "Im Ruhestand",
        "experience": [{"title": "Sales Manager", "starts_at": "2023-01",
                        "ends_at": "2024-01", "is_current": False}],
    }
    flag = cm.inactive_candidate_flag(profile)
    assert flag and flag["inactive"] is True


def test_active_current_role_is_not_flagged_inactive():
    profile = {
        "experience": [{"title": "Sales Manager", "starts_at": "2022-01",
                        "ends_at": None, "is_current": True}],
    }
    assert cm.inactive_candidate_flag(profile) is None


def test_missing_dates_never_flag_inactive():
    """Absence of evidence is not evidence of retirement."""
    assert cm.inactive_candidate_flag({"currentTitle": "Sales Manager"}) is None


def test_ron_porcello_job_hop_pattern_is_detected():
    """"Changed quite often in his career... if a candidate has an average
    moving rate of >2years, he might be a b candidate." Kastell's own
    proposed detector and threshold, computed from his actual work history."""
    profile = {
        "experience": [
            {"title": "IT Security Consultant", "starts_at": "2019-01",
             "ends_at": "2020-01", "is_current": False},
            {"title": "SAP Consultant", "starts_at": "2017-06",
             "ends_at": "2018-12", "is_current": False},
            {"title": "SAP MDG / Retail Consultant", "starts_at": "2015-01",
             "ends_at": "2017-05", "is_current": False},
        ],
    }
    avg = cm.average_tenure_years(profile)
    assert avg is not None and avg < ms._JOB_HOP_THRESHOLD_YEARS


def test_single_dated_role_does_not_evidence_a_hopping_pattern():
    """One data point can't evidence a pattern — must stay neutral (None),
    never treated as either stable or hopping."""
    profile = {"experience": [{"title": "Sales Manager", "starts_at": "2015-01",
                               "ends_at": "2020-01", "is_current": False}]}
    assert cm.average_tenure_years(profile) is None


def test_dietmar_real_title_flags_function_mismatch_not_stale():
    """His REAL scraped title — "Product Manager, Business Solution Architect
    for SAP SCE Applications in Retail" — contains "Retail" and is CURRENT, so
    staleEvidence correctly stays silent (verified live, 2026-07-31). The
    actual mismatch Kastell named is the JOB, not the domain or its recency."""
    profile = {"currentTitle": "Product Manager, Business Solution Architect for SAP SCE Applications in Retail"}
    mult, reason = ms.function_mismatch_fit(profile, SAP_RETAIL_SALES_JD)
    assert mult < 1.0 and "product manager" in reason.lower()


def test_ralf_real_title_flags_function_mismatch():
    """His real current title has no executive marker at all ("SAP Ecosystem
    Development in BTP, BDC..."), so seniority_band_fit stays silent — this is
    what actually catches him."""
    profile = {"currentTitle": "SAP Ecosystem Development in BTP, BDC and SAP Business AI, SAP Toolchain Growth"}
    mult, reason = ms.seniority_band_fit(profile, SAP_RETAIL_SALES_JD)
    assert mult == 1.0
    mult, reason = ms.function_mismatch_fit(profile, SAP_RETAIL_SALES_JD)
    assert mult < 1.0 and "ecosystem" in reason.lower()


def test_diana_real_title_flags_function_mismatch():
    """Her real current title — "Operations Business Partner for Partner
    Ecosystem Success" — names a different function via TWO independent
    markers (business partner, ecosystem)."""
    profile = {"currentTitle": "Operations Business Partner for Partner Ecosystem Success"}
    mult, reason = ms.function_mismatch_fit(profile, SAP_RETAIL_SALES_JD)
    assert mult < 1.0


@pytest.mark.parametrize("title", [
    "Client Partner",
    "Client Director | OpenText Observability & Service Management (OSM)",
    "Key-Account-Manager",                       # hyphenated German — must normalize
    "Key-Account-Managerin",
    "Sales Director",
    "Leiter Fachvertrieb SAP",                    # "vertrieb" embedded in a German compound
    "International Sales and business development",
    "Enterprise Account Executive, Google Cloud, Apigee",
])
def test_real_commercial_titles_are_never_flagged(title):
    """False-positive guard, built from the OTHER 25 candidates in the same
    real run Kastell did not flag — a hybrid/commercial title must never be
    penalized just because it also touches an adjacent-function word."""
    mult, reason = ms.function_mismatch_fit({"currentTitle": title}, SAP_RETAIL_SALES_JD)
    assert mult == 1.0 and reason is None


@pytest.mark.parametrize("title", [None, "", "UPS Healthcare - Europe", "Enabling the Intelligent Enterprise"])
def test_missing_or_uninformative_titles_are_never_flagged(title):
    """Absence or vagueness is not evidence of the wrong function — the same
    "missing data is neutral" rule the other three signals already follow."""
    mult, reason = ms.function_mismatch_fit({"currentTitle": title}, SAP_RETAIL_SALES_JD)
    assert mult == 1.0 and reason is None


def test_function_mismatch_is_scoped_to_commercial_roles():
    """A Solution Architect JD must never penalize a Solution Architect
    candidate — the check only applies when the ROLE itself is commercial."""
    jd = {"title": "Senior Solution Architect SAP Retail", "mustHaveSkills": ["SAP Retail"]}
    profile = {"currentTitle": "Solution Architect SAP Retail"}
    mult, reason = ms.function_mismatch_fit(profile, jd)
    assert mult == 1.0 and reason is None


def test_saurabh_clean_current_match_has_no_flags():
    """Positive control — "Nearby perfect candidate!" — none of the four new
    signals should fire for a straightforward current-role match."""
    profile = {
        "currentTitle": "Sales Manager SAP Retail",
        "skills": ["SAP Retail"],
        "experience": [{"title": "Sales Manager SAP Retail",
                        "summary": "SAP Retail solution sales.",
                        "starts_at": "2021-01", "ends_at": None, "is_current": True}],
    }
    _, _, gaps, bd = ms._score_candidate(SAP_RETAIL_SALES_JD, profile, sim=0.6)
    assert gaps == [] and bd["staleEvidence"] == []
    mult, reason = ms.seniority_band_fit(profile, SAP_RETAIL_SALES_JD)
    assert mult == 1.0 and reason is None
    assert cm.inactive_candidate_flag(profile) is None
