"""Strategist output clamps — the title-hallucination fixes.

Feeds ``_sanitize`` the exact bad output the friction report captured for
"Senior Inhouse Consultant SAP-CO/PS" and asserts the cures:

  * the verbatim posting title is never the searchQuery (the #1 zero-result cause),
  * brand+module fragments ("SAP CO", "SAP PS") are dropped from the titles,
  * every location is canonicalised, so the two engines get the SAME spelling
    (the 'Koblenz' vs 'Kolenz' divergence, fixed at the source),
  * the Apollo plan is DERIVED from the cleaned Apify plan (one source of truth),
  * a seniority set on the Apify side carries over to Apollo's own vocabulary.
"""
from __future__ import annotations

from app.services.sourcing.models import (
    ApolloPlan, DomainAnchor, SearchBrief, SearchFilters, SearchStrategy,
)
from app.services.sourcing.strategist import (
    _clean_posting_title, _fallback, _jd_states_years, _sanitize,
)


def _hallucinated() -> SearchStrategy:
    """The stored bad output for the SAP-CO/PS run, verbatim from the report."""
    return SearchStrategy(
        interpretedRole="Inhouse SAP CO/PS consultant",
        focusTitle="Senior Inhouse Consultant SAP-CO/PS",
        titleReasoning="...",
        filters=SearchFilters(
            searchQuery="Senior Inhouse Consultant SAP-CO/PS",
            currentJobTitles=[
                "Senior Inhouse Consultant SAP-CO/PS", "SAP Consultant CO",
                "Senior consultant CO/PS", "SAP CO", "SAP PS",
                "SAP FICO Consultant", "SAP Controlling Berater",
            ],
            locations=["Kolenz, Germany"],
            seniorityLevel="120",  # Senior
        ),
        apolloPlan=ApolloPlan(titles=["Senior Inhouse Consultant SAP-CO/PS"],
                              locations=["Kolenz,Germany"]),
        domainAnchor=DomainAnchor(coreTerms=["inhouse"], ecosystemTerms=["sap"]),
    )


def _brief() -> SearchBrief:
    return SearchBrief(
        jobTitle="Senior Inhouse Consultant SAP-CO/PS",
        jobLocation="Koblenz, Germany",
        mustHaveSkills=["SAP CO", "SAP PS"],
    )


class TestSearchQueryClamp:
    def test_verbatim_title_query_is_shortened(self):
        out = _sanitize(_hallucinated(), _brief())
        # No longer the full posting title, and short (≤4 tokens).
        assert out.filters.searchQuery != "Senior Inhouse Consultant SAP-CO/PS"
        assert len(out.filters.searchQuery.split()) <= 4

    def test_too_long_query_is_shortened(self):
        s = _hallucinated()
        s.filters.searchQuery = "a b c d e f"
        out = _sanitize(s, _brief())
        assert len(out.filters.searchQuery.split()) <= 4


class TestTitleClamp:
    def test_brand_module_fragments_dropped(self):
        # currentJobTitles is always empty now (2026-07-31 redesign) — the
        # fragment-dropping protection surfaces through focusTitle instead:
        # a bare brand+module fragment ("SAP CO", "SAP PS") must never win the
        # slot, only a real, plausible profile title.
        out = _sanitize(_hallucinated(), _brief())
        assert out.filters.currentJobTitles == []
        assert out.focusTitle not in ("SAP CO", "SAP PS")
        assert out.focusTitle != "Senior Inhouse Consultant SAP-CO/PS"

    def test_focus_title_is_not_the_posting_title(self):
        out = _sanitize(_hallucinated(), _brief())
        assert out.focusTitle != "Senior Inhouse Consultant SAP-CO/PS"
        assert not out.focusTitle.lower().startswith("senior inhouse consultant")


class TestLocationCanonicalisation:
    def test_apify_location_repaired(self):
        out = _sanitize(_hallucinated(), _brief())
        # Canonical German city labels carry the federal state.
        assert out.filters.locations == ["Koblenz, Rhineland-Palatinate, Germany"]

    def test_both_engines_get_identical_location(self):
        out = _sanitize(_hallucinated(), _brief())
        # The single-source guarantee: no 'Kolenz' vs 'Koblenz' divergence.
        assert (out.apolloPlan.locations == out.filters.locations
                == ["Koblenz, Rhineland-Palatinate, Germany"])


class TestApolloDerivation:
    def test_apollo_titles_match_apify_titles(self):
        out = _sanitize(_hallucinated(), _brief())
        # Apollo reuses the cleaned Apify title family (+ focus), not an
        # independently-hallucinated set.
        for t in out.filters.currentJobTitles:
            assert t in out.apolloPlan.titles

    def test_apollo_qkeywords_capped_at_three(self):
        s = _hallucinated()
        brief = _brief()
        brief.mustHaveSkills = ["a", "b", "c", "d", "e"]
        out = _sanitize(s, brief)
        assert len(out.apolloPlan.qKeywords) <= 3

    def test_inferred_seniority_is_forced_to_any(self):
        out = _sanitize(_hallucinated(), _brief())
        # seniorityLevel is an inferred filter — an AI-set value is dropped, so
        # nothing carries to Apollo either. Recruiter narrows by hand if needed.
        assert out.filters.seniorityLevel is None
        assert out.apolloPlan.seniorities == []


class TestBooleanAndExperienceClamps:
    def test_boolean_query_is_preserved(self):
        s = _hallucinated()
        bool_query = '("SAP Retail" OR "S/4HANA Retail") AND ("Account Executive" OR "Sales Manager") AND (Germany OR Austria)'
        s.filters.searchQuery = bool_query
        out = _sanitize(s, _brief())
        assert out.filters.searchQuery == bool_query

    def test_malformed_boolean_degrades_instead_of_shipping(self):
        """An unbalanced query makes the actor reject the whole run, which reads
        downstream as "no candidates match" — worse than a verbatim title, since
        `_is_boolean_query` skips the length/full-title clamp for it."""
        for broken in ['("SAP Retail" OR "SAP CAR"', '"SAP Retail OR "SAP CAR"']:
            s = _hallucinated()
            s.filters.searchQuery = broken
            out = _sanitize(s, _brief())
            assert out.filters.searchQuery != broken
            assert out.filters.searchQuery.strip()

    def test_overlong_boolean_degrades(self):
        s = _hallucinated()
        s.filters.searchQuery = '("a" OR "b") ' * 40  # > 300 chars, actor limit
        out = _sanitize(s, _brief())
        assert len(out.filters.searchQuery) <= 300

    def test_min_years_maps_to_experience_enum(self):
        s = _hallucinated()
        s.filters.yearsOfExperience = None
        brief = _brief()
        brief.minYears = 5.0
        out = _sanitize(s, brief)
        assert out.filters.yearsOfExperience == "3"  # 3 to 5 years


class TestInferredEnumsForcedToAny:
    def test_function_headcount_tenure_are_dropped(self):
        s = _hallucinated()
        s.filters.function = "1"
        s.filters.companyHeadcount = "1"
        s.filters.yearsAtCurrentCompany = "1"
        out = _sanitize(s, _brief())
        assert out.filters.function is None
        assert out.filters.companyHeadcount is None
        assert out.filters.yearsAtCurrentCompany is None


class TestYearsOfExperienceGate:
    def test_ai_years_dropped_without_explicit_basis(self):
        s = _hallucinated()
        s.filters.yearsOfExperience = "4"  # model assumed "senior => 6-10y"
        brief = _brief()
        brief.jobDescription = "We need a strong SAP consultant to join the team."
        brief.minYears = None
        out = _sanitize(s, brief)
        assert out.filters.yearsOfExperience is None

    def test_ai_years_kept_when_jd_states_years(self):
        s = _hallucinated()
        s.filters.yearsOfExperience = "3"
        brief = _brief()
        brief.jobDescription = "Requires at least 3 years of experience in SAP CO."
        brief.minYears = None
        out = _sanitize(s, brief)
        assert out.filters.yearsOfExperience == "3"

    def test_recruiter_min_years_always_wins(self):
        s = _hallucinated()
        s.filters.yearsOfExperience = None
        brief = _brief()
        brief.jobDescription = "No tenure mentioned here."
        brief.minYears = 8.0
        out = _sanitize(s, brief)
        assert out.filters.yearsOfExperience == "4"  # 6 to 10 years


class TestJdStatesYears:
    def test_detects_english_and_german(self):
        assert _jd_states_years("5+ years of experience")
        assert _jd_states_years("mindestens 3 Jahre Berufserfahrung")
        assert _jd_states_years("3-5 years in a similar role")

    def test_ignores_seniority_without_number(self):
        assert not _jd_states_years("Senior consultant, deep SAP expertise")
        assert not _jd_states_years("")


class TestCleanPostingTitle:
    def test_strips_grade_gender_and_employment(self):
        assert _clean_posting_title("Senior Java Developer II (m/w/d)") == "Senior Java Developer"
        assert _clean_posting_title("SAP FICO Consultant - Contract") == "SAP FICO Consultant"

    def test_non_empty_input_never_empties(self):
        assert _clean_posting_title("Consultant") == "Consultant"


class TestFallbackIsSearchable:
    def test_fallback_query_is_not_the_verbatim_posting_title(self):
        brief = SearchBrief(
            jobTitle="Senior Java Developer II (m/w/d)",
            jobLocation="Munich, Germany",
        )
        out = _fallback(brief)
        assert out.filters.searchQuery.lower() != brief.jobTitle.lower()
        assert out.filters.currentJobTitles  # a real title family exists
        assert "(m/w/d)" not in out.filters.currentJobTitles[0]
        assert out.confidence == 0.0

    def test_fallback_sets_no_inferred_enums(self):
        brief = SearchBrief(jobTitle="SAP FICO Consultant", jobLocation="Germany")
        out = _fallback(brief)
        assert out.filters.seniorityLevel is None
        assert out.filters.function is None
        assert out.filters.yearsOfExperience is None

