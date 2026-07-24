"""Offline location gazetteer — typeahead + canonicalisation.

These lock in the behaviour the discovery form and the gate depend on: a typed
prefix suggests real places, a mis-spelled/variant string canonicalises to one
label, and the STRICT resolver (used by the location gate) never repairs a typo
into a wrong-country match.
"""
from __future__ import annotations

from app.services import location_catalog as lc


class TestSuggest:
    def test_prefix_matches_city(self):
        # German cities canonicalise as "City, State, Country" — the exact shape
        # LinkedIn uses, so the actor's location facet matches reliably and the
        # gate's region check gets the state for free.
        labels = [s["label"] for s in lc.suggest("kobl")]
        assert "Koblenz, Rhineland-Palatinate, Germany" in labels

    def test_bamberg_full_label(self):
        # Kastell's first live search: "Bamberg" must suggest, with its state.
        labels = [s["label"] for s in lc.suggest("bamb")]
        assert "Bamberg, Bavaria, Germany" in labels

    def test_larger_pool_ranks_first(self):
        # Both Berlin and Bern prefix-match "ber"; the bigger talent pool wins.
        labels = [s["label"] for s in lc.suggest("ber")]
        assert labels[0] == "Berlin, Germany"
        assert "Bern, Switzerland" in labels

    def test_diacritic_folding(self):
        # A user who can't type the umlaut still finds the city.
        labels = [s["label"] for s in lc.suggest("zuri")]
        assert "Zurich, Switzerland" in labels

    def test_empty_query_returns_nothing(self):
        assert lc.suggest("") == []
        assert lc.suggest("   ") == []

    def test_limit_respected(self):
        assert len(lc.suggest("a", limit=3)) <= 3


class TestNormalize:
    def test_typo_repaired_to_canonical(self):
        # The exact friction-report failure: 'Kolenz' → 'Koblenz'.
        assert lc.normalize("Kolenz, Germany") == "Koblenz, Rhineland-Palatinate, Germany"

    def test_alias_and_suffix_collapse(self):
        assert lc.normalize("Frankfurt am Main") == "Frankfurt, Hesse, Germany"
        assert lc.normalize("Muenchen") == "Munich, Bavaria, Germany"

    def test_city_state_input_resolves(self):
        # A pasted LinkedIn-style three-part label round-trips to itself.
        assert lc.normalize("Bamberg, Bavaria, Germany") == "Bamberg, Bavaria, Germany"

    def test_country_kept(self):
        assert lc.normalize("Germany") == "Germany"

    def test_unrecognised_kept_as_none(self):
        assert lc.normalize("Somewhereville") is None


class TestStateWideningAndClamp:
    """The recruiter's location policy: a search may widen a city to its OWN
    federal state, never the country, never another state."""

    def test_state_widening_for_city(self):
        assert lc.state_widening("Bamberg, Bavaria, Germany") == "Bavaria, Germany"
        assert lc.state_widening("Bamberg") == "Bavaria, Germany"

    def test_no_widening_for_regions_countries_citystates(self):
        assert lc.state_widening("Bavaria, Germany") is None
        assert lc.state_widening("Germany") is None
        assert lc.state_widening("Berlin") is None       # city-state: no step up
        assert lc.state_widening("Somewhereville") is None

    def test_clamp_allows_own_state(self):
        out = lc.clamp_locations(["Bamberg, Bavaria, Germany"], ["Bavaria, Germany"])
        assert out == ["Bavaria, Germany"]
        # Variant spellings of the same state count as the same place.
        out = lc.clamp_locations(["Bamberg, Bavaria, Germany"], ["Bayern"])
        assert out == ["Bavaria, Germany"]

    def test_clamp_rejects_country_and_other_state(self):
        initial = ["Bamberg, Bavaria, Germany"]
        assert lc.clamp_locations(initial, ["Germany"]) == initial
        assert lc.clamp_locations(initial, ["Hesse, Germany"]) == initial
        # One legal + one illegal entry → all-or-nothing snap-back: a mixed set
        # ("Bavaria" + "Germany") is country-wide in effect while looking local.
        assert lc.clamp_locations(initial, ["Bavaria, Germany", "Germany"]) == initial

    def test_clamp_keeps_exact_and_empty_cases(self):
        initial = ["Bamberg, Bavaria, Germany"]
        assert lc.clamp_locations(initial, initial) == initial
        assert lc.clamp_locations(initial, []) == initial
        # No recruiter location → nothing to protect.
        assert lc.clamp_locations([], ["Germany"]) == ["Germany"]


class TestStrictCountryOf:
    def test_bare_city_resolves(self):
        # The FN fix: a bare city (no country word) still resolves for the gate.
        assert lc.country_of("Koblenz") == "Germany"

    def test_strict_does_not_repair_typos(self):
        # Strict (gate) mode must NOT turn a typo into a confident country — that
        # is how a wrong-country reject would be manufactured.
        assert lc.country_of("Kolenz", fuzzy=False) is None
        # …but the input-normalisation path (fuzzy) still repairs it.
        assert lc.country_of("Kolenz", fuzzy=True) == "Germany"

    def test_last_segment_country_wins(self):
        assert lc.country_of("Kelmis, Belgium") == "Belgium"
