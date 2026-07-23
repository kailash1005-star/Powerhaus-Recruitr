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
        labels = [s["label"] for s in lc.suggest("kobl")]
        assert "Koblenz, Germany" in labels

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
        assert lc.normalize("Kolenz, Germany") == "Koblenz, Germany"

    def test_alias_and_suffix_collapse(self):
        assert lc.normalize("Frankfurt am Main") == "Frankfurt, Germany"
        assert lc.normalize("Muenchen") == "Munich, Germany"

    def test_country_kept(self):
        assert lc.normalize("Germany") == "Germany"

    def test_unrecognised_kept_as_none(self):
        assert lc.normalize("Somewhereville") is None


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
