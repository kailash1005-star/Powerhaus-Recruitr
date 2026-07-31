"""Apify profile-enrichment result correlation — the identity/photo-mixup fix.

Postmortem (2026-07-31, "Sales Manager SAP Retail" run): candidates' photos
(and full profiles) came back swapped after bulk enrichment. Root cause was in
`_run_chunk`'s request->result correlation:

  * The actor DOES echo the original query URL back, in `originalQuery.query`
    — but the code read a top-level `query` field that never exists, so this
    reliable signal silently contributed nothing.
  * With nothing else fully bridging the member-URN (search actor) / profile-
    URN (scraper) gap, EVERY match fell back to trusting that dataset row
    order matches request order — an assumption not guaranteed by the vendor,
    and one dropped/reordered row scrambles every candidate after it.

These tests pin: (1) the echo is read from the right field, (2) it is
AUTHORITATIVE — it wins even when it disagrees with position, and (3) the
positional fallback only fills slots nothing has already claimed, so a reorder
degrades to a loud warning + a few unmatched profiles, never a swapped one.

Offline: no real Apify — `client`/`call_actor_bounded` are stubs.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.sourcing.apify_profile_service import (
    ApifyProfileService, _result_keys, echoed_query_identifier,
)


def _item(ident: str, **extra: Any) -> Dict[str, Any]:
    """A minimal realistic actor item for `ident`, echo included."""
    base = {
        "id": f"ACo{ident}",
        "publicIdentifier": ident,
        "linkedinUrl": f"https://www.linkedin.com/in/{ident}",
        "originalQuery": {"query": f"https://www.linkedin.com/in/{ident}"},
        "headline": f"Headline for {ident}",
    }
    base.update(extra)
    return base


class _FakeDataset:
    def __init__(self, items: List[Dict[str, Any]]):
        self._items = items

    def iterate_items(self):
        return iter(self._items)


class _FakeClient:
    def __init__(self, items: List[Dict[str, Any]]):
        self._items = items

    def dataset(self, dataset_id: str) -> _FakeDataset:
        return _FakeDataset(self._items)


def _run_chunk_with(monkeypatch, chunk: List[str], items: List[Dict[str, Any]]):
    """Exercise the real `_run_chunk` against a stubbed actor run."""
    import app.services.sourcing.apify_profile_service as mod

    monkeypatch.setattr(
        mod, "call_actor_bounded",
        lambda client, actor_id, run_input, *, timeout_secs: {"status": "ok"},
    )
    monkeypatch.setattr(
        mod, "_run_to_dict",
        lambda run: {"status": "SUCCEEDED", "defaultDatasetId": "ds1"},
    )
    service = ApifyProfileService(token="fake-token", actor="fake-actor")
    client = _FakeClient(items)
    results: Dict[str, Dict[str, Any]] = {}
    service._run_chunk(client, chunk, results)
    return results


# ── echoed_query_identifier ──────────────────────────────────────────────────

class TestEchoedQueryIdentifier:
    def test_reads_the_real_nested_shape(self):
        item = {"originalQuery": {"query": "https://www.linkedin.com/in/Jane-Doe"}}
        assert echoed_query_identifier(item) == "jane-doe"

    def test_none_when_originalquery_missing(self):
        assert echoed_query_identifier({}) is None

    def test_none_when_originalquery_is_not_a_dict(self):
        assert echoed_query_identifier({"originalQuery": "not-a-dict"}) is None

    def test_included_in_result_keys(self):
        item = _item("someone")
        assert "someone" in _result_keys(item)


# ── _run_chunk correlation ───────────────────────────────────────────────────

class TestRunChunkCorrelation:
    def test_in_order_results_match_by_identity(self, monkeypatch):
        chunk = ["alice", "bob", "carol"]
        items = [_item("alice"), _item("bob"), _item("carol")]
        results = _run_chunk_with(monkeypatch, chunk, items)
        for ident in chunk:
            assert results[ident]["publicIdentifier"] == ident

    def test_out_of_order_results_still_match_correctly(self, monkeypatch):
        """The actor returns bob's profile before alice's (parallel scraping) —
        this is exactly the reordering that used to swap their photos."""
        chunk = ["alice", "bob", "carol"]
        items = [_item("bob"), _item("alice"), _item("carol")]
        results = _run_chunk_with(monkeypatch, chunk, items)
        assert results["alice"]["publicIdentifier"] == "alice"
        assert results["bob"]["publicIdentifier"] == "bob"
        assert results["carol"]["publicIdentifier"] == "carol"

    def test_dropped_row_does_not_cascade_misassignment(self, monkeypatch):
        """bob's row never arrives (no error placeholder either) — the OLD
        positional code would then pair carol's identifier with... nothing
        reliable, and shift every subsequent candidate by one. The fix must
        leave bob unmatched and still get carol right."""
        chunk = ["alice", "bob", "carol"]
        items = [_item("alice"), _item("carol")]  # bob silently missing
        results = _run_chunk_with(monkeypatch, chunk, items)
        assert results["alice"]["publicIdentifier"] == "alice"
        assert results["carol"]["publicIdentifier"] == "carol"
        assert "bob" not in results

    def test_echo_wins_even_if_it_disagrees_with_position(self, monkeypatch):
        """A pathological case: an item arrives in bob's SLOT but its own
        echo says it's actually carol. Identity must win — never position."""
        chunk = ["alice", "bob", "carol"]
        items = [_item("alice"), _item("carol"), _item("bob")]
        results = _run_chunk_with(monkeypatch, chunk, items)
        assert results["bob"]["publicIdentifier"] == "bob"
        assert results["carol"]["publicIdentifier"] == "carol"

    def test_positional_fallback_never_overwrites_a_confirmed_match(self, monkeypatch):
        """An item with no echo at all still falls into a slot — but only one
        nothing has already claimed via identity."""
        chunk = ["alice", "bob"]
        no_echo_item = {"publicIdentifier": "bob", "headline": "no echo here"}
        items = [_item("alice"), no_echo_item]
        results = _run_chunk_with(monkeypatch, chunk, items)
        assert results["alice"]["publicIdentifier"] == "alice"
        # No echo, but publicIdentifier + position both point at "bob" — and
        # slot "bob" was unclaimed, so the fallback is allowed to fill it.
        assert results["bob"] is no_echo_item
