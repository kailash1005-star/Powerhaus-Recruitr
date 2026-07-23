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
from app.services.sourcing.strategist import _sanitize


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
        out = _sanitize(_hallucinated(), _brief())
        titles = out.filters.currentJobTitles
        assert "SAP CO" not in titles
        assert "SAP PS" not in titles
        # Real titles survive.
        assert "SAP FICO Consultant" in titles

    def test_focus_title_is_not_the_posting_title(self):
        out = _sanitize(_hallucinated(), _brief())
        assert out.focusTitle != "Senior Inhouse Consultant SAP-CO/PS"
        assert not out.focusTitle.lower().startswith("senior inhouse consultant")


class TestLocationCanonicalisation:
    def test_apify_location_repaired(self):
        out = _sanitize(_hallucinated(), _brief())
        assert out.filters.locations == ["Koblenz, Germany"]

    def test_both_engines_get_identical_location(self):
        out = _sanitize(_hallucinated(), _brief())
        # The single-source guarantee: no 'Kolenz' vs 'Koblenz' divergence.
        assert out.apolloPlan.locations == out.filters.locations == ["Koblenz, Germany"]


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

    def test_seniority_carries_to_apollo_vocab(self):
        out = _sanitize(_hallucinated(), _brief())
        # Apify seniorityLevel 120 (Senior) → Apollo 'senior'.
        assert out.apolloPlan.seniorities == ["senior"]
