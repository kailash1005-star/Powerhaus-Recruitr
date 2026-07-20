"""Regression evals for the location-gate false-negative (production 2026-07-20).

The "Lead Consultant SAP SuccessFactors EC" sourcing run silently DROPPED 5
in-country candidates — including a perfect "Senior Consultant SAP SuccessFactors"
match — because their LinkedIn location was the metro label "Frankfurt Rhine-Main
Metropolitan Area" (no comma). The old resolver took the whole commaless string as
the "country", found it != "germany", and rejected. A false negative is the most
expensive error class, so these pin the fix hard:

  * the exact 5 production strings now resolve to Germany → decision "match";
  * DACH cities / states / metros / "… Area" labels resolve positively;
  * a location that resolves to NO known country is KEPT ("unknown"), never
    rejected — the gate's documented invariant;
  * genuine wrong-country rejects (the Bavaria→India leak) STILL fire, even when
    the string omits the word "India".

Offline: pure function, no DB / network / LLM.
"""
from __future__ import annotations

import pytest

from app.services import location_resolver as lr


# The five candidates the production run dropped on location — all in Germany.
PROD_DROPPED = [
    "Frankfurt Rhine-Main Metropolitan Area",
]


class TestProductionMetroLabelNowKept:
    @pytest.mark.parametrize("cand", PROD_DROPPED)
    def test_frankfurt_metro_matches_germany(self, cand):
        v = lr.location_verdict("Germany", cand)
        assert v["decision"] == "match", v
        assert v["candidateCountry"] == "germany"

    def test_the_five_are_not_country_mismatch(self):
        # Whatever else, none of them may be a hard reject anymore.
        for cand in PROD_DROPPED + ["Frankfurt Rhine-Main Metropolitan Area"]:
            assert lr.location_verdict("Germany", cand)["decision"] != "country_mismatch"


class TestDachResolvesPositively:
    @pytest.mark.parametrize("cand", [
        "Munich Area", "Greater Munich Metropolitan Area", "Berlin Metropolitan Area",
        "Hamburg und Umgebung", "Cologne Bonn Region", "Stuttgart Region",
        "Munich, Bavaria", "Frankfurt, Hesse", "Rhineland",
        "Nürnberg", "Düsseldorf", "Bayern",
    ])
    def test_german_places_match(self, cand):
        v = lr.location_verdict("Germany", cand)
        assert v["decision"] == "match", (cand, v)

    def test_austria_and_switzerland(self):
        assert lr.location_verdict("Austria", "Vienna Metropolitan Area")["decision"] == "match"
        assert lr.location_verdict("Switzerland", "Greater Zurich Area")["decision"] == "match"


class TestNeverRejectOnUnresolvable:
    @pytest.mark.parametrize("cand", [
        "Remote", "EMEA", "Europe", "Metropolitan Area", "Springfield Area",
    ])
    def test_unresolvable_is_kept(self, cand):
        # No known country on the candidate side → unknown → KEPT, never a reject.
        assert lr.location_verdict("Germany", cand)["decision"] == "unknown"


class TestGenuineWrongCountryStillRejects:
    def test_bavaria_to_india_word_present(self):
        v = lr.location_verdict("Bavaria, Germany", "Bengaluru, Karnataka, India")
        assert v["decision"] == "country_mismatch"
        assert v["candidateCountry"] == "india"

    def test_india_metro_without_the_word_india_still_rejects(self):
        # The leak must stay shut even when the string omits "India".
        assert lr.location_verdict("Germany", "Bengaluru Area")["decision"] == "country_mismatch"
        assert lr.location_verdict("Germany", "Greater Mumbai")["decision"] == "country_mismatch"

    def test_other_eu_country_rejects_against_dach(self):
        assert lr.location_verdict("Germany", "Paris, France")["decision"] == "country_mismatch"
        assert lr.location_verdict("Germany", "Greater Paris Metropolitan Region")["decision"] == "country_mismatch"
        assert lr.location_verdict("Germany", "London Area, United Kingdom")["decision"] == "country_mismatch"


class TestExistingContractPreserved:
    def test_same_region_matches(self):
        assert lr.location_verdict("Bavaria, Germany", "Munich, Bavaria, Germany")["decision"] == "match"

    def test_other_region_is_soft_flag(self):
        assert lr.location_verdict("Bavaria, Germany", "Hamburg, Germany")["decision"] == "region_mismatch"

    def test_aliases_resolve(self):
        assert lr.location_verdict("Germany", "München, Deutschland")["decision"] == "match"
        assert lr.location_verdict("USA", "New York, United States")["decision"] == "match"

    def test_missing_location_is_unknown(self):
        assert lr.location_verdict("Bavaria, Germany", "")["decision"] == "unknown"
        assert lr.location_verdict("", "Munich, Germany")["decision"] == "unknown"
