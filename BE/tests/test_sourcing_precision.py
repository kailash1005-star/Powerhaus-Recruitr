"""Sourcing-precision guarantees (FC-23..FC-27).

The contract under test, end to end:

  1. Widening a search may NEVER change the target profession. The Broadener's
     titles/query are clamped in code (lock_target); the domain guard that
     protects every other title path is two-tier — an ecosystem brand ("SAP")
     alone never proves a title is in-domain, only a CORE specialization term
     ("HCM") does. This is the exact bug the customer hit: an SAP-HCM search
     drifting into SAP FI-CO because both share "SAP".

  2. Recall comes from running MORE channels on the SAME specialty (title +
     keyword, merged and deduped), never from loosening the specialty.

  3. The prescreen gate is channel-aware: a keyword-channel hit is kept even
     when its title alone can't evidence the role, and a hit corroborated by
     both channels outranks a single-channel one.

All tests are offline — no Apify, no LLM, no Mongo.
"""
from __future__ import annotations

from app.services.sourcing.broadener import (
    _domain_anchor, _enforce_domain, lock_target, next_attempt,
)
from app.services.sourcing.common import derive_anchor_terms, title_in_domain
from app.services.sourcing.models import (
    BroadenDecision, BroadeningStep, DomainAnchor, SearchAttempt, SearchBrief,
    SearchFilters, SearchStrategy,
)
from app.services.sourcing.strategist import _sanitize


def _attempt(titles, query="", n=1, **kw):
    return SearchAttempt(
        attempt=n, action="initial" if n == 1 else "broaden",
        filters={"currentJobTitles": titles, "searchQuery": query, **kw},
        resultCount=0,
    )


# ── Two-tier anchor ──────────────────────────────────────────────────────────

class TestDomainAnchor:
    def test_ecosystem_brand_is_not_core(self):
        core, eco = derive_anchor_terms(["SAP HCM Consultant", "SAP HR Consultant"])
        assert "sap" in eco and "sap" not in core
        assert "hcm" in core

    def test_fico_fails_hcm_anchor(self):
        """THE customer bug: FI-CO must not pass as in-domain for an HCM role."""
        core, _ = derive_anchor_terms(["SAP HCM Consultant"])
        assert not title_in_domain("SAP FICO Consultant", core)
        assert not title_in_domain("SAP Application Manager", core)
        assert title_in_domain("SAP HCM Berater", core)

    def test_brand_only_domain_keeps_brand_as_core(self):
        """'SAP Consultant' alone: the brand IS the only signal — keep it."""
        core, eco = derive_anchor_terms(["SAP Consultant"])
        assert core == ["sap"] and eco == []

    def test_declared_anchor_wins_over_heuristic(self):
        anchor = _domain_anchor(
            [_attempt(["SAP HCM Consultant"])],
            strategy_anchor={"coreTerms": ["successfactors", "payroll"],
                             "ecosystemTerms": ["sap"]},
        )
        assert anchor == ["payroll", "successfactors"]

    def test_anchor_from_brief_when_no_titles(self):
        anchor = _domain_anchor(
            [_attempt([], query="SAP HCM")],
            brief=SearchBrief(jobTitle="SAP HCM Consultant"),
        )
        assert "hcm" in anchor and "sap" not in anchor


# ── The Broadener cannot change the target ───────────────────────────────────

class TestLockTarget:
    def test_titles_query_and_location_clamped(self):
        # Location has a state ceiling: a "Bamberg" search must never silently
        # widen to "Germany" — beyond the city's own state is the recruiter's
        # next run (found live on Kastell's first sourcing run).
        attempts = [_attempt(["SAP HCM Consultant", "SAP HR Consultant"], "SAP HCM",
                             locations=["Bamberg, Bavaria, Germany"])]
        drifted = BroadenDecision(
            action="generalise_titles", reasoning="",
            filters=SearchFilters(currentJobTitles=["SAP Consultant"],
                                  searchQuery="SAP", locations=["Germany"]),
        )
        locked = lock_target(drifted, attempts)
        assert locked.filters.currentJobTitles == ["SAP HCM Consultant", "SAP HR Consultant"]
        assert locked.filters.searchQuery == "SAP HCM"
        assert locked.filters.locations == ["Bamberg, Bavaria, Germany"]

    def test_widening_to_own_state_is_allowed(self):
        # The ONE legal location relaxation: city → its own federal state.
        attempts = [_attempt(["SAP HCM Consultant"], "SAP HCM",
                             locations=["Bamberg, Bavaria, Germany"])]
        proposal = BroadenDecision(
            action="widen_location", reasoning="",
            filters=SearchFilters(currentJobTitles=["SAP HCM Consultant"],
                                  searchQuery="SAP HCM",
                                  locations=["Bavaria, Germany"]),
        )
        locked = lock_target(proposal, attempts)
        assert locked.filters.locations == ["Bavaria, Germany"]

    async def test_ladder_fallback_is_clamped_too(self, monkeypatch):
        """A legacy persisted ladder with a generalise_titles step must run with
        the ORIGINAL titles — only its other relaxations apply."""
        monkeypatch.setattr(
            "app.services.sourcing.broadener.llm_available", lambda: False)
        attempts = [_attempt(["SAP HCM Consultant"], "SAP HCM", seniorityLevel="120")]
        ladder = [BroadeningStep(
            step=1, action="generalise_titles", detail="",
            filters=SearchFilters(currentJobTitles=["SAP Consultant"],
                                  searchQuery="SAP HCM"),
        )]
        decision = await next_attempt(SearchBrief(jobTitle="SAP HCM Consultant"),
                                      attempts, ladder)
        assert decision is not None
        assert decision.filters.currentJobTitles == ["SAP HCM Consultant"]

    async def test_exhausted_ladder_stops(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sourcing.broadener.llm_available", lambda: False)
        decision = await next_attempt(
            SearchBrief(jobTitle="X"), [_attempt(["X Specialist"])], ladder=[])
        assert decision is None


class TestEnforceDomain:
    def test_off_domain_titles_stripped(self):
        attempts = [_attempt(["SAP HCM Consultant"])]
        d = BroadenDecision(action="a", reasoning="", filters=SearchFilters(
            currentJobTitles=["SAP FICO Consultant", "SAP HCM Berater"]))
        out = _enforce_domain(d, attempts,
                              strategy_anchor={"coreTerms": ["hcm"]})
        assert out is not None
        assert out.filters.currentJobTitles == ["SAP HCM Berater"]

    def test_total_drift_returns_none(self):
        attempts = [_attempt(["SAP HCM Consultant"])]
        d = BroadenDecision(action="a", reasoning="", filters=SearchFilters(
            currentJobTitles=["SAP FICO Consultant", "SAP Basis Administrator"]))
        assert _enforce_domain(d, attempts,
                               strategy_anchor={"coreTerms": ["hcm"]}) is None


# ── Dual-channel merge ───────────────────────────────────────────────────────

class TestSearchChannels:
    async def test_merge_dedupe_and_corroboration(self, monkeypatch):
        from app.services.sourcing import candidate_pipeline as cp

        calls = []

        async def fake_run_search(pid, jid, filters, max_items):
            calls.append(dict(filters))
            if filters.get("currentJobTitles"):
                return [{"profileId": "a", "currentTitle": "SAP HCM Consultant"},
                        {"profileId": "b", "currentTitle": "SAP HR Consultant"}]
            return [{"profileId": "b", "currentTitle": "SAP HR Consultant"},
                    {"profileId": "c", "currentTitle": "IT-Consultant"}]

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        profiles, counts = await cp._run_search_channels(
            "p", "j",
            {"currentJobTitles": ["SAP HCM Consultant"], "searchQuery": "SAP HCM",
             "locations": ["Germany"]},
            25, include_keyword_channel=True,
        )
        assert counts == {"title": 2, "keyword": 2}
        by_id = {p["profileId"]: p for p in profiles}
        assert len(profiles) == 3                          # deduped
        assert by_id["a"]["channels"] == ["title"]
        assert by_id["b"]["channels"] == ["title", "keyword"]  # corroborated
        assert by_id["c"]["channels"] == ["keyword"]
        # The keyword channel must NOT carry the title filter.
        assert all("currentJobTitles" not in c or not c.get("currentJobTitles")
                   for c in calls[1:])

    async def test_keyword_channel_failure_is_not_fatal(self, monkeypatch):
        from app.services.sourcing import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items):
            if filters.get("currentJobTitles"):
                return [{"profileId": "a", "currentTitle": "T"}]
            raise RuntimeError("keyword page exploded")

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        profiles, counts = await cp._run_search_channels(
            "p", "j",
            {"currentJobTitles": ["T"], "searchQuery": "T kw"},
            25, include_keyword_channel=True,
        )
        assert [p["profileId"] for p in profiles] == ["a"]
        assert counts["keyword"] == 0

    async def test_retries_skip_keyword_channel(self, monkeypatch):
        from app.services.sourcing import candidate_pipeline as cp
        n = {"calls": 0}

        async def fake_run_search(pid, jid, filters, max_items):
            n["calls"] += 1
            return []

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        await cp._run_search_channels(
            "p", "j", {"currentJobTitles": ["T"], "searchQuery": "q"},
            25, include_keyword_channel=False,
        )
        assert n["calls"] == 1


class TestBroadeningPreservesExcludeSeniorityLevel:
    """A recruiter's manual `excludeSeniorityLevel` (never AI-set — see
    strategist._ENUM_INFERRED_FIELDS) must survive the auto-broaden loop the
    same way titles/query/locations do via lock_target. SearchFilters keeps
    this field single-value on purpose (so the Broadener's own LLM can't grow
    its surface here), so the recruiter's real list is restored directly onto
    the dict handed to the next attempt, bypassing that model."""

    async def test_recruiter_exclude_seniority_survives_a_broadened_retry(self, monkeypatch):
        from app.services.sourcing import candidate_pipeline as cp
        import app.services.sourcing as sourcing_pkg

        calls = []

        async def fake_run_search_channels(pid, jid, filters, max_items, *, include_keyword_channel):
            calls.append(dict(filters))
            if len(calls) == 1:
                return [], {}  # first attempt: zero results, triggers broadening
            return [{"profileId": "a", "channels": ["title"]}], {"title": 1}

        async def fake_build_brief(pid, jid, hints):
            return SearchBrief(jobTitle="Sales Manager SAP Retail")

        async def fake_next_attempt(brief, attempts, ladder, strategy_anchor=None):
            # Simulate the Broadener's own proposal NOT carrying the
            # recruiter's exclude-seniority choice at all.
            return BroadenDecision(
                decision="broaden", action="widen_query", reasoning="widen",
                filters=SearchFilters(searchQuery="SAP Retail", currentJobTitles=[]),
            )

        async def fake_record_attempts(pid, jid, attempts):
            return None

        monkeypatch.setattr(cp, "_run_search_channels", fake_run_search_channels)
        monkeypatch.setattr(cp, "_record_attempts", fake_record_attempts)
        monkeypatch.setattr(sourcing_pkg, "build_brief", fake_build_brief)
        monkeypatch.setattr(sourcing_pkg, "next_attempt", fake_next_attempt)

        profiles, attempts, winning_filters = await cp._search_with_broadening(
            "p", "j",
            {"searchQuery": '("SAP Retail") AND ("Sales Manager")',
             "excludeSeniorityLevel": ["100", "110"]},
            30, auto_broaden=True, hints=None, ladder=None,
        )
        assert len(calls) == 2  # confirms a retry actually happened
        assert winning_filters.get("excludeSeniorityLevel") == ["100", "110"]


class TestPrimaryChannelLabeling:
    """FC-sourcing-precision (2026-08-01): once the strategist redesign made
    `currentJobTitles` always empty, labeling the primary channel off that
    field alone made EVERY search "keyword" — which silently waives the
    title/seniority screen for 100% of candidates (a live search reproduced
    28 students/trainees out of 30 under the bug). The label must instead
    reflect whether the boolean `searchQuery` itself carries a real title
    requirement (a top-level AND)."""

    async def test_compound_and_query_labels_as_title_even_with_empty_current_titles(self, monkeypatch):
        from app.services.sourcing import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items):
            return [{"profileId": "a", "currentTitle": "Working Student"}]

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        # include_keyword_channel=False isolates this test to the labeling
        # question alone — whether the SECOND channel runs at all is a
        # separate concern, covered by TestKeywordChannelRevival below.
        profiles, counts = await cp._run_search_channels(
            "p", "j",
            {"currentJobTitles": [], "searchQuery": '("SAP Retail") AND ("Sales Manager")'},
            30, include_keyword_channel=False,
        )
        assert counts == {"title": 1}
        assert profiles[0]["channels"] == ["title"]

    async def test_plain_or_only_query_still_labels_as_keyword(self, monkeypatch):
        from app.services.sourcing import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items):
            return [{"profileId": "a", "currentTitle": "SAP HCM Consultant"}]

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        profiles, counts = await cp._run_search_channels(
            "p", "j",
            {"currentJobTitles": [], "searchQuery": '"SAP HCM" OR "SuccessFactors"'},
            30, include_keyword_channel=True,
        )
        assert counts == {"keyword": 1}
        assert profiles[0]["channels"] == ["keyword"]

    async def test_populated_current_titles_still_labels_as_title(self, monkeypatch):
        from app.services.sourcing import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items):
            return [{"profileId": "a", "currentTitle": "T"}]

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        profiles, counts = await cp._run_search_channels(
            "p", "j", {"currentJobTitles": ["T"], "searchQuery": "kw"},
            30, include_keyword_channel=True,
        )
        assert counts["title"] == 1


class TestAutoQuerySegmentation:
    """Live-confirmed 2026-08-01: the identical compound `(domain) AND
    (title)` query returned clean senior-sales candidates at ≤25 results and
    reproduced 28 students/trainees out of 30 the moment `autoQuerySegmentation`
    auto-enabled past 25 — it appears to search the AND's pieces
    independently, so a profile only has to satisfy ONE side. A plain
    OR-only query isn't at risk this way (each alternative is independently
    meaningful), so segmentation must still turn on for those."""

    def test_compound_and_query_never_gets_segmentation(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input(
            {"searchQuery": '("SAP Retail") AND ("Sales Manager")', "locations": ["Germany"]},
            60,
        )
        assert "autoQuerySegmentation" not in run_input

    def test_plain_or_query_still_gets_segmentation_past_25(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input(
            {"searchQuery": '"SAP HCM" OR "SuccessFactors"', "locations": ["Germany"]},
            60,
        )
        assert run_input.get("autoQuerySegmentation") is True

    def test_compound_and_query_under_25_needs_no_segmentation_anyway(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input(
            {"searchQuery": '("SAP Retail") AND ("Sales Manager")', "locations": ["Germany"]},
            25,
        )
        assert "autoQuerySegmentation" not in run_input


class TestPaginationSupport:
    """2026-08-02: `_meta.pagination` (totalElements/totalPages/pageNumber) is
    real data the actor stamps on every item — confirmed live, not
    documentation guesswork — and `startPage` lets a caller resume past pages
    already fetched. Segmentation restructures a query into independent
    sub-runs, each with its own page count, so it must never be active at the
    same time as `startPage` or page numbers stop meaning anything."""

    def test_parse_short_profile_extracts_pagination_metadata(self):
        from app.services.sourcing.apify_search_service import parse_short_profile

        item = {
            "id": "abc123",
            "linkedinUrl": "https://www.linkedin.com/in/abc123",
            "firstName": "Jane",
            "lastName": "Doe",
            "currentPositions": [{"title": "Sales Director", "companyName": "Acme"}],
            "_meta": {"pagination": {"totalElements": 37, "totalPages": 2,
                                      "pageNumber": 1, "pageSize": 25,
                                      "previousElements": 0}},
        }
        p = parse_short_profile(item)
        assert p["pagination"] == {"totalElements": 37, "totalPages": 2,
                                    "pageNumber": 1, "pageSize": 25}

    def test_parse_short_profile_missing_meta_is_none_not_a_crash(self):
        from app.services.sourcing.apify_search_service import parse_short_profile

        item = {"id": "abc123", "firstName": "Jane", "lastName": "Doe"}
        p = parse_short_profile(item)
        assert p["pagination"] is None

    def test_start_page_is_sent_when_past_page_one(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input(
            {"searchQuery": '"SAP Retail" OR "SAP CAR"', "locations": ["Germany"]},
            25, start_page=3,
        )
        assert run_input["startPage"] == 3

    def test_start_page_one_or_none_is_never_sent(self):
        """Page 1 is the actor's own default — sending it explicitly would
        just be noise, and `None` obviously means "not paginating"."""
        from app.services.sourcing.apify_search_service import _build_input

        for sp in (None, 1):
            run_input = _build_input(
                {"searchQuery": '"SAP Retail" OR "SAP CAR"', "locations": ["Germany"]},
                25, start_page=sp,
            )
            assert "startPage" not in run_input

    def test_start_page_forces_segmentation_off_even_past_25_with_or_query(self):
        """Without start_page, this exact plain-OR query at 60 items DOES get
        segmentation (TestAutoQuerySegmentation above) — proving this is
        start_page suppressing it, not the query shape."""
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input(
            {"searchQuery": '"SAP HCM" OR "SuccessFactors"', "locations": ["Germany"]},
            60, start_page=2,
        )
        assert "autoQuerySegmentation" not in run_input
        assert run_input["startPage"] == 2


class TestDomainOnlyQuerySplit:
    """`strategist.domain_only_query` / `split_domain_and_title_groups`
    (2026-08-02): the piece that lets the keyword channel drop the TITLE
    GROUP under the post-redesign `(DOMAIN) AND (TITLE)` shape. Must only
    ever act on a clean, unambiguous two-group Boolean — anything else comes
    back None, never a guess, because dropping the wrong half would narrow a
    search instead of widening it."""

    def test_splits_the_real_audited_query(self):
        from app.services.sourcing.strategist import domain_only_query

        q = ('("SAP Retail" OR "SAP S/4HANA Retail" OR "SAP CAR" OR "Retail Solutions") AND '
             '("SAP Retail Sales" OR "SAP Retail Account Executive" OR "Senior Sales Executive" OR '
             '"Account Executive" OR "Business Development Manager" OR "Sales Director")')
        assert domain_only_query(q) == (
            '("SAP Retail" OR "SAP S/4HANA Retail" OR "SAP CAR" OR "Retail Solutions")'
        )

    def test_works_regardless_of_which_side_the_titles_are_on(self):
        """Classifies by content (title-shaped phrases), not position — still
        correct if a caller ever hands it (TITLE) AND (DOMAIN) instead."""
        from app.services.sourcing.strategist import domain_only_query

        q = '("Sales Manager" OR "Account Executive") AND ("SAP Retail" OR "SAP CAR")'
        assert domain_only_query(q) == '("SAP Retail" OR "SAP CAR")'

    def test_quoted_phrase_containing_the_word_and_is_not_mistaken_for_the_join(self):
        from app.services.sourcing.strategist import domain_only_query

        q = '("SAP Retail" OR "Retail and Commerce Solutions") AND ("Sales Manager" OR "Account Executive")'
        assert domain_only_query(q) == '("SAP Retail" OR "Retail and Commerce Solutions")'

    def test_plain_or_only_query_returns_none(self):
        """Already effectively a keyword search — nothing to drop."""
        from app.services.sourcing.strategist import domain_only_query

        assert domain_only_query('"SAP Retail" OR "SAP S/4HANA Retail" OR "SAP CAR"') is None

    def test_non_boolean_text_returns_none(self):
        from app.services.sourcing.strategist import domain_only_query

        assert domain_only_query("Sales Manager SAP Retail") is None

    def test_malformed_boolean_returns_none_never_guesses(self):
        from app.services.sourcing.strategist import domain_only_query

        assert domain_only_query('("SAP Retail" OR "SAP CAR" AND ("Sales Manager")') is None

    def test_ambiguous_classification_returns_none(self):
        """Neither half reads as titles (no GENERIC_ROLE_WORDS anywhere) — must
        not guess which half to drop."""
        from app.services.sourcing.strategist import domain_only_query

        q = '("SAP Retail" OR "SAP CAR") AND ("Commerce" OR "S/4HANA")'
        assert domain_only_query(q) is None


class TestYearsOfExperienceAtLeastSemantics:
    """`_build_input`'s yearsOfExperienceIds expansion (2026-08-02): a JD
    stating "at least 5-8 years" for a Senior role mapped to a single closed
    band ("6 to 10 years"), silently excluding every candidate with 10+
    years — the most senior slice, for a senior role. Confirmed live
    2026-08-01. "At least N" must mean N and up, not a narrow window."""

    def test_middle_band_expands_up_to_the_top(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input({"yearsOfExperience": "4", "locations": ["Germany"]}, 25)
        assert run_input["yearsOfExperienceIds"] == ["4", "5"]

    def test_lowest_band_expands_to_every_band(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input({"yearsOfExperience": "1", "locations": ["Germany"]}, 25)
        assert run_input["yearsOfExperienceIds"] == ["1", "2", "3", "4", "5"]

    def test_top_band_is_unchanged(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input({"yearsOfExperience": "5", "locations": ["Germany"]}, 25)
        assert run_input["yearsOfExperienceIds"] == ["5"]

    def test_unset_years_of_experience_stays_absent(self):
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input({"locations": ["Germany"]}, 25)
        assert "yearsOfExperienceIds" not in run_input

    def test_other_enum_filters_are_not_affected(self):
        """This must only touch yearsOfExperienceIds — seniorityLevelIds and
        the rest of the enum machinery are untouched."""
        from app.services.sourcing.apify_search_service import _build_input

        run_input = _build_input(
            {"yearsOfExperience": "3", "excludeSeniorityLevel": ["100", "110"],
             "locations": ["Germany"]}, 25,
        )
        assert run_input["yearsOfExperienceIds"] == ["3", "4", "5"]
        assert run_input["excludeSeniorityLevelIds"] == ["100", "110"]


class TestPaginationDecisionLogic:
    """`_next_pagination_page` / `pagination_stop_reason` / `_count_free_verified`
    (2026-08-02) — the pure logic behind "page deeper into the SAME query
    instead of relaxing it". Three independent stop conditions; getting any
    one wrong either burns budget chasing an unreachable target or gives up
    with real results still on the table."""

    def test_target_already_met_stops_immediately(self):
        from app.services.sourcing.candidate_pipeline import _next_pagination_page

        assert _next_pagination_page(
            verified_count=40, target_count=40, pages_fetched=1, total_pages=10,
        ) is None

    def test_target_not_met_and_pages_remain_continues(self):
        from app.services.sourcing.candidate_pipeline import _next_pagination_page

        assert _next_pagination_page(
            verified_count=10, target_count=40, pages_fetched=1, total_pages=10,
        ) == 2

    def test_page_cap_stops_even_with_pages_and_target_remaining(self):
        """The hard ceiling — this is what actually bounds cost when a
        query's true valid rate makes 80% unreachable."""
        from app.services.sourcing.candidate_pipeline import _next_pagination_page

        assert _next_pagination_page(
            verified_count=10, target_count=40, pages_fetched=5, total_pages=50, page_cap=5,
        ) is None

    def test_total_pages_exhausted_stops_even_under_the_cap(self):
        """A genuinely niche query with only 2 total pages must not try to
        fetch a 3rd page that doesn't exist."""
        from app.services.sourcing.candidate_pipeline import _next_pagination_page

        assert _next_pagination_page(
            verified_count=5, target_count=40, pages_fetched=2, total_pages=2, page_cap=5,
        ) is None

    def test_unknown_total_pages_never_stops_the_loop_on_that_basis_alone(self):
        """total_pages=None (never learned, e.g. an empty first page) must
        not be treated as "0 pages exist" — only a real known total can end
        the loop this way; the page cap is still the backstop."""
        from app.services.sourcing.candidate_pipeline import _next_pagination_page

        assert _next_pagination_page(
            verified_count=0, target_count=40, pages_fetched=1, total_pages=None, page_cap=5,
        ) == 2

    def test_stop_reason_matches_target_reached(self):
        from app.services.sourcing.candidate_pipeline import pagination_stop_reason

        assert pagination_stop_reason(
            verified_count=40, target_count=40, pages_fetched=3, total_pages=10,
        ) == "target_reached"

    def test_stop_reason_matches_pages_exhausted(self):
        from app.services.sourcing.candidate_pipeline import pagination_stop_reason

        assert pagination_stop_reason(
            verified_count=5, target_count=40, pages_fetched=2, total_pages=2, page_cap=5,
        ) == "pages_exhausted"

    def test_stop_reason_matches_page_cap_reached(self):
        from app.services.sourcing.candidate_pipeline import pagination_stop_reason

        assert pagination_stop_reason(
            verified_count=10, target_count=40, pages_fetched=5, total_pages=50, page_cap=5,
        ) == "page_cap_reached"

    def test_stop_reason_never_claims_the_cap_when_it_was_not_actually_reached(self):
        """Regression guard for the exact bug the linter caught while this
        was being built: reporting "page_cap_reached" as a blind fallback
        would misreport why a niche, pages-exhausted search actually stopped."""
        from app.services.sourcing.candidate_pipeline import pagination_stop_reason

        assert pagination_stop_reason(
            verified_count=5, target_count=40, pages_fetched=2, total_pages=2, page_cap=10,
        ) == "pages_exhausted"

    def test_count_free_verified_excludes_dropped_and_specialization_only(self):
        from app.services.sourcing.candidate_pipeline import _count_free_verified

        verdicts = [
            {"decision": "keep", "domainEvidenceSignal": "both"},
            {"decision": "keep", "domainEvidenceSignal": "neither"},
            {"decision": "keep", "domainEvidenceSignal": "specialization_only"},
            {"decision": "drop", "domainEvidenceSignal": "both"},
            {"decision": "keep"},  # no signal computed at all (e.g. no domain query) still counts
        ]
        assert _count_free_verified(verdicts) == 3

    def test_count_free_verified_of_empty_list_is_zero(self):
        from app.services.sourcing.candidate_pipeline import _count_free_verified

        assert _count_free_verified([]) == 0


class TestPaginatePrimaryChannelToTarget:
    """`_paginate_primary_channel_to_target` end to end (offline — `_run_search`
    and `_store_profiles` monkeypatched, no real Apify/Mongo calls)."""

    def _verdict(self, n, signal="both"):
        return [{"decision": "keep", "domainEvidenceSignal": signal} for _ in range(n)]

    async def test_stops_early_once_target_is_reached(self, monkeypatch):
        """max_items=10 -> target = 8 (80%). Page 1 already has 8 verified —
        must not fetch page 2 at all."""
        from app.services.sourcing import candidate_pipeline as cp

        run_search_calls = []

        async def fake_run_search(pid, jid, filters, max_items, *, start_page=None):
            run_search_calls.append(start_page)
            return []

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        result = await cp._paginate_primary_channel_to_target(
            "p", "j", {"searchQuery": '("SAP Retail") AND ("Sales Manager")', "currentJobTitles": []},
            10, initial_verdicts=self._verdict(8), initial_pagination={"totalPages": 5, "totalElements": 120},
            requirements={}, target_titles=[], requested_location=None,
        )
        assert run_search_calls == []  # never even tried a second page
        assert result["stopReason"] == "target_reached"
        assert result["pagesFetched"] == 1
        assert result["verifiedCount"] == 8

    async def test_fetches_additional_pages_until_target_or_cap(self, monkeypatch):
        """Each page contributes 2 more verified candidates; target=8 needs
        page 1 (2) + pages 2,3,4 (2 each) = 8 by page 4."""
        from app.services.sourcing import candidate_pipeline as cp

        fetched_pages = []

        async def fake_run_search(pid, jid, filters, max_items, *, start_page=None):
            fetched_pages.append(start_page)
            return [{"profileId": f"p{start_page}", "currentTitle": "x",
                     "pagination": {"totalPages": 6, "totalElements": 150}}]

        async def fake_store_profiles(profiles, **kwargs):
            return [p["profileId"] for p in profiles], self._verdict(2)

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        monkeypatch.setattr(cp, "_store_profiles", fake_store_profiles)
        result = await cp._paginate_primary_channel_to_target(
            "p", "j", {"searchQuery": '("SAP Retail") AND ("Sales Manager")', "currentJobTitles": []},
            10, initial_verdicts=self._verdict(2), initial_pagination={"totalPages": 6, "totalElements": 150},
            requirements={}, target_titles=[], requested_location=None,
        )
        assert fetched_pages == [2, 3, 4]
        assert result["stopReason"] == "target_reached"
        assert result["verifiedCount"] == 8
        assert result["pagesFetched"] == 4

    async def test_stops_at_page_cap_when_target_unreachable(self, monkeypatch):
        """A genuinely low-yield query (0 new verified per page) must stop at
        the 5-page cap, never loop indefinitely chasing an unreachable target."""
        from app.services.sourcing import candidate_pipeline as cp

        fetched_pages = []

        async def fake_run_search(pid, jid, filters, max_items, *, start_page=None):
            fetched_pages.append(start_page)
            return [{"profileId": f"p{start_page}", "currentTitle": "x",
                     "pagination": {"totalPages": 200, "totalElements": 5000}}]

        async def fake_store_profiles(profiles, **kwargs):
            return [p["profileId"] for p in profiles], self._verdict(0)  # never contributes a verified hit

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        monkeypatch.setattr(cp, "_store_profiles", fake_store_profiles)
        result = await cp._paginate_primary_channel_to_target(
            "p", "j", {"searchQuery": '("SAP Retail") AND ("Sales Manager")', "currentJobTitles": []},
            10, initial_verdicts=self._verdict(0), initial_pagination={"totalPages": 200, "totalElements": 5000},
            requirements={}, target_titles=[], requested_location=None,
        )
        assert fetched_pages == [2, 3, 4, 5]  # pages 2-5; page 1 was the initial call, not fetched here
        assert result["pagesFetched"] == 5
        assert result["stopReason"] == "page_cap_reached"

    async def test_stops_when_a_page_returns_nothing(self, monkeypatch):
        """A niche query exhausting itself early (empty page) must stop
        cleanly rather than keep asking for pages that don't exist."""
        from app.services.sourcing import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items, *, start_page=None):
            return []

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        result = await cp._paginate_primary_channel_to_target(
            "p", "j", {"searchQuery": '("SAP Retail") AND ("Sales Manager")', "currentJobTitles": []},
            10, initial_verdicts=self._verdict(3), initial_pagination={"totalPages": 6, "totalElements": 150},
            requirements={}, target_titles=[], requested_location=None,
        )
        assert result["pagesFetched"] == 2
        assert result["stopReason"] == "pages_exhausted"

    async def test_fetch_error_stops_gracefully_never_raises(self, monkeypatch):
        """Pagination is an enhancement to an already-successful search — a
        transient failure on page 2 must not blow up the whole discovery
        run; it must just stop with what was already found."""
        from app.services.sourcing import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items, *, start_page=None):
            raise RuntimeError("transient Apify error")

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        result = await cp._paginate_primary_channel_to_target(
            "p", "j", {"searchQuery": '("SAP Retail") AND ("Sales Manager")', "currentJobTitles": []},
            10, initial_verdicts=self._verdict(2), initial_pagination={"totalPages": 6, "totalElements": 150},
            requirements={}, target_titles=[], requested_location=None,
        )
        assert result["pagesFetched"] == 1
        assert result["verifiedCount"] == 2


class TestDomainEvidenceSignal:
    """`prescreen_service.domain_evidence_signal` (2026-08-02), calibrated
    directly against every real case confirmed live that day: 3 real failures
    (specialization word alone) and 6 real genuine matches (evidence
    elsewhere, current title shows neither word). Getting either wrong means
    either letting Mirko back in, or rejecting Volker Krause — this is the
    ground truth, not a synthetic one."""

    DOMAIN_QUERY = '("SAP Retail" OR "S/4HANA Retail" OR "SAP CAR")'

    def test_mirko_muller_title_is_specialization_only(self):
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal(
            "Business Development Manager Retail", self.DOMAIN_QUERY,
        ) == "specialization_only"

    def test_hendrik_jansen_title_is_specialization_only(self):
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal("Retail Account Executive", self.DOMAIN_QUERY) == "specialization_only"

    def test_dieter_kosancic_title_is_specialization_only(self):
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal("Director Sales Retail", self.DOMAIN_QUERY) == "specialization_only"

    def test_volker_krause_current_title_alone_is_neither_not_risky(self):
        """His real evidence (a past "Senior SAP Retail Consultant" role) is
        NOT in his current title — the free check must say "neither", not
        flag him, since flagging "neither" would demote every genuine match
        found 2026-08-02."""
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal(
            "Lecturer (Lehrbeauftragter) for Digital Transformation", self.DOMAIN_QUERY,
        ) == "neither"

    def test_completely_unrelated_title_is_neither(self):
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal("Marketing Manager", self.DOMAIN_QUERY) == "neither"

    def test_title_with_both_words_is_both(self):
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal("SAP Retail Sales Manager", self.DOMAIN_QUERY) == "both"

    def test_full_profile_text_finds_evidence_in_a_past_role(self):
        """Same function, fed the FULL enriched text instead of just the
        title (the post-enrichment use) — Andreas Wueck's current title has
        neither word, but his real past title does."""
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        blob = "Enterprise Account Executive, Google Cloud, Apigee. SAP Retail Account Executive at SAP."
        assert domain_evidence_signal(blob, self.DOMAIN_QUERY) == "both"

    def test_different_word_order_still_counts_as_both(self):
        """Richard Deuschle's real profile says 'SAP for retail
        implementation', not the contiguous phrase 'SAP Retail' — this must
        still register as evidence, since we check word presence, not
        phrase adjacency (an exact-phrase check would wrongly fail him)."""
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        blob = "participate in various SAP for retail implementation projects"
        assert domain_evidence_signal(blob, self.DOMAIN_QUERY) == "both"

    def test_skill_tag_counts_same_as_title_or_description(self):
        """Elena Held's evidence is a bare tagged skill, "SAP Retail" —
        not a sentence."""
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal("SAP Retail", self.DOMAIN_QUERY) == "both"

    def test_ecosystem_word_alone_is_its_own_bucket(self):
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal("SAP Basis Administrator", self.DOMAIN_QUERY) == "ecosystem_only"

    def test_empty_domain_query_is_neither_not_a_crash(self):
        from app.services.sourcing.prescreen_service import domain_evidence_signal

        assert domain_evidence_signal("Some Title", "") == "neither"


class TestDomainEvidenceDemotion:
    """`candidate_pipeline._apply_domain_evidence_demotion` — wires the free
    pre-enrichment classifier into the sourcing verdict. Demotes, never
    rejects; only fires on the one confirmed-risky pattern."""

    DOMAIN_QUERY = '("SAP Retail" OR "SAP CAR")'

    def test_specialization_only_demotes_score_but_keeps_the_candidate(self):
        from app.services.sourcing.candidate_pipeline import _apply_domain_evidence_demotion

        keep, verdict = _apply_domain_evidence_demotion(
            True, {"score": 100.0, "reasons": ["Title matches."]},
            title="Business Development Manager Retail", domain_query=self.DOMAIN_QUERY,
        )
        assert keep is True  # never a hard reject
        assert verdict["score"] == 30.0
        assert verdict["domainEvidenceSignal"] == "specialization_only"
        assert "false match" in verdict["reasons"][0]

    def test_neither_is_left_completely_untouched(self):
        """The majority case, and where every real genuine match lived —
        must not be demoted just for lacking a title-level signal."""
        from app.services.sourcing.candidate_pipeline import _apply_domain_evidence_demotion

        original = {"score": 75.0, "reasons": ["Title matches Sales Director."]}
        keep, verdict = _apply_domain_evidence_demotion(
            True, dict(original), title="Chief Revenue Officer", domain_query=self.DOMAIN_QUERY,
        )
        assert verdict["score"] == 75.0
        assert verdict["reasons"] == original["reasons"]
        assert verdict["domainEvidenceSignal"] == "neither"

    def test_both_present_is_left_untouched(self):
        from app.services.sourcing.candidate_pipeline import _apply_domain_evidence_demotion

        keep, verdict = _apply_domain_evidence_demotion(
            True, {"score": 90.0, "reasons": []},
            title="SAP Retail Sales Manager", domain_query=self.DOMAIN_QUERY,
        )
        assert verdict["score"] == 90.0
        assert verdict["domainEvidenceSignal"] == "both"

    def test_no_domain_query_is_a_complete_no_op(self):
        """Manual/plain searches with nothing to check against must pass
        through byte-for-byte unchanged."""
        from app.services.sourcing.candidate_pipeline import _apply_domain_evidence_demotion

        verdict_in = {"score": 50.0, "reasons": ["x"]}
        keep, verdict = _apply_domain_evidence_demotion(
            True, verdict_in, title="Anything", domain_query="",
        )
        assert verdict is verdict_in

    def test_never_flips_a_drop_decision_either_direction(self):
        from app.services.sourcing.candidate_pipeline import _apply_domain_evidence_demotion

        keep, verdict = _apply_domain_evidence_demotion(
            False, {"score": 10.0, "reasons": []},
            title="Retail Account Executive", domain_query=self.DOMAIN_QUERY,
        )
        assert keep is False


class TestPrimaryChannelFilters:
    """`_primary_channel_filters` (2026-08-02): the actor's `searchQuery` is
    fuzzy relevance search, not a strict filter (confirmed live — Mirko
    Muller, whose title shares only the word "Retail" with the domain
    phrase, still surfaces on the combined AND query with segmentation off).
    `currentJobTitles` is the actor's documented deterministic filter, so the
    primary channel derives a real one instead of relying on searchQuery to
    enforce the title half."""

    def test_derives_title_filter_and_domain_only_query(self):
        from app.services.sourcing.candidate_pipeline import _primary_channel_filters

        out = _primary_channel_filters({
            "currentJobTitles": [], "pastJobTitles": [],
            "searchQuery": '("SAP Retail" OR "SAP CAR") AND ("Sales Manager" OR "Account Executive")',
            "locations": ["Germany"],
        })
        assert out["searchQuery"] == '("SAP Retail" OR "SAP CAR")'
        assert set(out["currentJobTitles"]) == {"Sales Manager", "Account Executive"}
        assert out["locations"] == ["Germany"]

    def test_legacy_populated_current_titles_is_left_untouched(self):
        """A manual/legacy search that already set its own currentJobTitles
        must never be silently overridden."""
        from app.services.sourcing.candidate_pipeline import _primary_channel_filters

        filters = {"currentJobTitles": ["Sales Director"], "searchQuery": "SAP Retail"}
        assert _primary_channel_filters(filters) is filters

    def test_non_boolean_query_falls_back_unchanged(self):
        from app.services.sourcing.candidate_pipeline import _primary_channel_filters

        filters = {"currentJobTitles": [], "searchQuery": "Sales Manager SAP Retail"}
        assert _primary_channel_filters(filters) is filters

    def test_plain_or_only_query_falls_back_unchanged(self):
        """Already effectively a keyword search — nothing to split."""
        from app.services.sourcing.candidate_pipeline import _primary_channel_filters

        filters = {"currentJobTitles": [], "searchQuery": '"SAP Retail" OR "SAP CAR"'}
        assert _primary_channel_filters(filters) is filters

    def test_ambiguous_classification_falls_back_unchanged(self):
        """Neither half reads as titles — domain_only_query already refuses
        to guess; this must propagate, not crash or half-apply."""
        from app.services.sourcing.candidate_pipeline import _primary_channel_filters

        filters = {"currentJobTitles": [], "searchQuery": '("SAP Retail") AND ("Commerce")'}
        assert _primary_channel_filters(filters) is filters

    def test_derived_filters_preserve_every_other_constraint(self):
        """excludeSeniorityLevel, locations, etc. must survive the split —
        only searchQuery/currentJobTitles change."""
        from app.services.sourcing.candidate_pipeline import _primary_channel_filters

        out = _primary_channel_filters({
            "currentJobTitles": [],
            "searchQuery": '("SAP Retail") AND ("Sales Director")',
            "locations": ["Germany"], "excludeSeniorityLevel": ["100", "110"],
            "profileLanguages": ["German"],
        })
        assert out["locations"] == ["Germany"]
        assert out["excludeSeniorityLevel"] == ["100", "110"]
        assert out["profileLanguages"] == ["German"]


class TestKeywordChannelRevival:
    """FC-sourcing-precision (2026-08-02): the 2026-07-31 strategist redesign
    made `currentJobTitles`/`pastJobTitles` permanently empty, and
    `_keyword_channel_filters`'s only branch required one of those to be
    populated — so the keyword channel silently stopped running for every
    AI-built search from that day on (confirmed live: a real run's
    `searchAttempts[0].channelCounts` carried only `{"title": N}`, never a
    `"keyword"` key). `_keyword_channel_filters` now also derives a
    domain-only variant straight from searchQuery when the legacy fields are
    empty, so the second channel runs again."""

    def test_legacy_populated_titles_path_is_unchanged(self):
        from app.services.sourcing.candidate_pipeline import _keyword_channel_filters

        out = _keyword_channel_filters({
            "currentJobTitles": ["Sales Manager"], "searchQuery": "SAP Retail",
            "locations": ["Germany"],
        })
        assert out == {"searchQuery": "SAP Retail", "locations": ["Germany"]}

    def test_empty_titles_with_and_query_derives_domain_only_variant(self):
        from app.services.sourcing.candidate_pipeline import _keyword_channel_filters

        out = _keyword_channel_filters({
            "currentJobTitles": [], "pastJobTitles": [],
            "searchQuery": '("SAP Retail") AND ("Sales Manager")',
            "locations": ["Germany"], "excludeSeniorityLevel": ["110"],
        })
        assert out is not None
        assert out["searchQuery"] == '("SAP Retail")'
        # every other recruiter constraint survives untouched
        assert out["locations"] == ["Germany"]
        assert out["excludeSeniorityLevel"] == ["110"]

    def test_empty_titles_with_non_boolean_query_returns_none(self):
        from app.services.sourcing.candidate_pipeline import _keyword_channel_filters

        out = _keyword_channel_filters({
            "currentJobTitles": [], "searchQuery": "SAP Retail Sales Manager",
        })
        assert out is None

    def test_no_query_at_all_returns_none(self):
        from app.services.sourcing.candidate_pipeline import _keyword_channel_filters

        assert _keyword_channel_filters({"currentJobTitles": [], "searchQuery": ""}) is None

    async def test_second_channel_now_actually_runs_and_finds_a_distinct_candidate(self, monkeypatch):
        """End-to-end through `_run_search_channels`: a candidate the title
        channel's query would never surface (domain mentioned, no title-group
        term in sight) is found by the revived keyword channel and merged in
        as its own, single-channel hit — proving this isn't dead code again.

        Since the primary channel now derives its own real `currentJobTitles`
        filter (`_primary_channel_filters`, 2026-08-02) instead of sending the
        combined AND query verbatim, both channels now share the SAME
        domain-only searchQuery — they're distinguished by whether
        `currentJobTitles` is populated, not by searchQuery text."""
        from app.services.sourcing import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items):
            assert filters["searchQuery"] == '("SAP Retail")'
            if filters.get("currentJobTitles"):
                assert filters["currentJobTitles"] == ["Sales Manager"]
                return [{"profileId": "title-hit", "currentTitle": "Sales Manager SAP Retail"}]
            assert not filters.get("currentJobTitles")
            return [{"profileId": "keyword-hit", "currentTitle": "IT-Consultant bei X"}]

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        profiles, counts = await cp._run_search_channels(
            "p", "j",
            {"currentJobTitles": [], "searchQuery": '("SAP Retail") AND ("Sales Manager")'},
            30, include_keyword_channel=True,
        )
        assert counts == {"title": 1, "keyword": 1}
        by_id = {p["profileId"]: p for p in profiles}
        assert by_id["title-hit"]["channels"] == ["title"]
        assert by_id["keyword-hit"]["channels"] == ["keyword"]


# ── Strategist sanitize: anchor hygiene + adjacent titles ────────────────────

class TestStrategistSanitize:
    def _strategy(self, **kw):
        base = dict(
            interpretedRole="SAP HCM Consultant",
            filters=SearchFilters(
                searchQuery="SAP HCM",
                currentJobTitles=["SAP HCM Consultant", "SAP SuccessFactors Consultant"],
            ),
            domainAnchor=DomainAnchor(coreTerms=["hcm", "successfactors"],
                                      ecosystemTerms=["sap"]),
        )
        base.update(kw)
        return SearchStrategy(**base)

    def test_generic_core_terms_stripped(self):
        s = self._strategy(domainAnchor=DomainAnchor(
            coreTerms=["hcm", "Consultant", "Manager"], ecosystemTerms=[]))
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert out.domainAnchor.coreTerms == ["hcm"]

    def test_empty_anchor_derived_from_titles(self):
        s = self._strategy(domainAnchor=DomainAnchor())
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert "hcm" in out.domainAnchor.coreTerms
        assert "sap" in out.domainAnchor.ecosystemTerms

    def test_anchor_rejecting_own_titles_is_rebuilt(self):
        # currentJobTitles is always empty now — the anchor's self-consistency
        # check reads its title family back out of searchQuery's TITLE GROUP.
        s = self._strategy(
            filters=SearchFilters(
                searchQuery='("Payroll") AND ("SAP HCM Consultant" OR "SAP SuccessFactors Consultant")',
                currentJobTitles=[],
            ),
            domainAnchor=DomainAnchor(coreTerms=["entgeltabrechnung"], ecosystemTerms=[]),
        )
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert "hcm" in out.domainAnchor.coreTerms

    def test_adjacent_titles_deduped_and_capped(self):
        s = self._strategy(
            filters=SearchFilters(
                searchQuery='("SAP HCM") AND ("SAP HCM Consultant" OR "SAP SuccessFactors Consultant")',
                currentJobTitles=[],
            ),
            adjacentTitles=[
                "SAP HCM Consultant",   # dupe of a query title → dropped
                "HRIS Consultant", "Workday HCM Consultant", "  ", "A", "B", "C", "D", "E",
            ],
        )
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert "SAP HCM Consultant" not in out.adjacentTitles
        assert "HRIS Consultant" in out.adjacentTitles
        assert len(out.adjacentTitles) <= 6

    def test_ladder_titles_locked(self):
        s = self._strategy(broadeningLadder=[BroadeningStep(
            step=1, action="generalise_titles", detail="",
            filters=SearchFilters(currentJobTitles=["SAP Consultant"],
                                  searchQuery="SAP"),
        )])
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        step = out.broadeningLadder[0]
        assert step.filters.currentJobTitles == out.filters.currentJobTitles
        assert step.filters.searchQuery == out.filters.searchQuery

    # ── focusTitle + apolloPlan (unified discovery) ─────────────────────────
    def test_focus_title_defaults_to_strongest_title(self):
        s = self._strategy()  # no focusTitle set
        out = _sanitize(s, SearchBrief(jobTitle="Senior SAP HCM Consultant"))
        assert out.focusTitle == "SAP HCM Consultant"  # first currentJobTitle

    def test_apollo_plan_derived_when_omitted(self):
        s = self._strategy()  # apolloPlan left at its empty default
        out = _sanitize(
            s, SearchBrief(jobTitle="SAP HCM Consultant",
                           mustHaveSkills=["SAP SuccessFactors", "Payroll", "EC", "Time"]))
        # Titles fall back to the LinkedIn family; q_keywords capped at 3.
        assert out.apolloPlan.titles
        assert len(out.apolloPlan.qKeywords) <= 3

    def test_apollo_seniorities_coerced_to_vocab(self):
        from app.services.sourcing.models import ApolloPlan
        s = self._strategy(apolloPlan=ApolloPlan(
            titles=["SAP HCM Consultant"],
            seniorities=["senior", "Wizard", "director"]))  # "Wizard" is invalid
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert "wizard" not in [x.lower() for x in out.apolloPlan.seniorities]
        assert all(x in {"senior", "director"} for x in out.apolloPlan.seniorities)

    def test_enum_filters_left_untouched_when_null(self):
        """The prompt biases the 4 inferred filters to Any; sanitize must not
        invent them."""
        s = self._strategy()
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert out.filters.seniorityLevel is None
        assert out.filters.yearsOfExperience is None
        assert out.filters.function is None
        assert out.filters.companyHeadcount is None


# ── Channel-aware prescreen policy ───────────────────────────────────────────

class TestChannelScreenPolicy:
    def test_keyword_hit_survives_title_only_gate(self):
        from app.services.sourcing.candidate_pipeline import _channel_screen_policy
        keep, verdict = _channel_screen_policy(
            False, {"score": 0.0, "decision": "drop", "reasons": ["no overlap"]},
            ["keyword"],
        )
        assert keep is True
        assert verdict["decision"] == "keep"
        assert verdict["score"] >= 30.0

    def test_title_only_drop_stays_dropped(self):
        from app.services.sourcing.candidate_pipeline import _channel_screen_policy
        keep, verdict = _channel_screen_policy(
            False, {"score": 0.0, "decision": "drop", "reasons": ["no overlap"]},
            ["title"],
        )
        assert keep is False

    def test_corroborated_hit_gets_rank_bonus(self):
        from app.services.sourcing.candidate_pipeline import _channel_screen_policy
        keep, verdict = _channel_screen_policy(
            True, {"score": 80.0, "decision": "keep", "reasons": []},
            ["title", "keyword"],
        )
        assert keep and verdict["score"] == 85.0

    def test_bonus_capped(self):
        from app.services.sourcing.candidate_pipeline import _channel_screen_policy
        _, verdict = _channel_screen_policy(
            True, {"score": 93.0, "decision": "keep", "reasons": []},
            ["title", "keyword"],
        )
        assert verdict["score"] == 95.0


# ── Enrichment selection size ────────────────────────────────────────────────
# The endpoint used to hard-reject a selection over 10 (JOB_ENRICH_SELECTION_MAX)
# with a 400 before even queuing. That artificial ceiling is gone — the
# recruiter enriches however many candidates they manually selected; the
# vendor-call safety net now lives one layer down, in `enrich_candidates`
# chunking the fetch into APIFY_ENRICH_MAX-sized groups (see
# tests/test_candidate_enrichment.py).

class TestEnrichSelectionSize:
    async def test_large_selection_is_not_rejected(self, monkeypatch):
        import app.api.v1.pipelines as pipelines_mod
        from app.api.v1.pipelines import BulkEnrichSchema, enrich_job_candidates

        seen = {}

        async def fake_enqueue(pipeline_id, job_id, candidate_ids):
            seen["candidate_ids"] = candidate_ids
            return {"queued": True}

        monkeypatch.setattr(pipelines_mod, "enqueue_job_enrich", fake_enqueue)

        body = BulkEnrichSchema(candidateIds=[f"c{i}" for i in range(37)])
        result = await enrich_job_candidates("p", "j", body)

        assert result["success"] is True
        # Every selected id reached the queueing step — nothing was trimmed
        # or rejected for exceeding the old 10-candidate cap.
        assert seen["candidate_ids"] == body.candidateIds
        assert len(seen["candidate_ids"]) == 37
