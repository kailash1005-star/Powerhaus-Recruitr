"""Domain-anchored sourcing — the title must stop deciding who is qualified.

Client feedback (Kastell, Benedict, 2026-07-28), verbatim in substance: the
AI-suggested titles are *"quite dangerous"* because they restrict qualified
candidates out of the pool. His opening is titled "SAP Retail Consultant" but the
person is a SALESPERSON, and real candidates carry:

    Account Executive · Key Account Manager · Enterprise Account Manager
    Sales Director · Business Development Manager · Client Partner

...or nothing useful at all: **"Principal Consultant"**.

What identifies them is not the title, it is what they sell. His own LinkedIn
search — which works — is three AND-ed axes:

    ("SAP Retail" OR "S/4HANA Retail" OR "SAP CAR" OR "Consumer Goods")
    AND ("Account Executive" OR "Sales Manager" OR ...)
    AND (Germany OR Austria OR Switzerland)

Measured before the fix, against the real modules:

    "Principal Consultant"  ->  20.0  (threshold 25.0)  DROPPED, never enriched
    "Account Executive"     ->   0.0                    DROPPED, never enriched

These tests pin the three properties that make that impossible again, and — just
as importantly — pin that DELIVERY roles still behave exactly as they did, since
title anchoring genuinely works for them and this shipped default-ON to everyone.
"""
from __future__ import annotations

import importlib

import pytest

cp = importlib.import_module("app.services.sourcing.candidate_pipeline")
ps = importlib.import_module("app.services.sourcing.prescreen_service")

DOMAIN_GATE = '("SAP Retail" OR "S/4HANA Retail" OR "SAP CAR" OR "Consumer Goods")'

# The role model the JD parser now produces for Benedict's opening. Note the
# posting title is "SAP Retail Consultant" and appears nowhere in candidateTitles.
COMMERCIAL_REQS = {
    "title": "SAP Retail Consultant",
    "roleFamily": "commercial",
    "domainTerms": ["SAP Retail", "S/4HANA Retail", "SAP CAR", "Consumer Goods"],
    "candidateTitles": [
        "Account Executive", "Key Account Manager", "Enterprise Account Manager",
        "Sales Director", "Business Development Manager", "Client Partner",
    ],
    "mustHaveSkills": ["SAP Retail", "Consumer Goods", "Enterprise Sales"],
}

# A delivery role, for the regression half.
DELIVERY_REQS = {
    "title": "SAP EWM Consultant",
    "roleFamily": "delivery",
    "domainTerms": ["SAP EWM", "SAP LES"],
    "candidateTitles": ["SAP EWM Consultant", "SAP EWM Berater", "SAP Logistics Consultant"],
    "mustHaveSkills": ["SAP EWM"],
}


class TestDomainAnchorDetection:
    """Only a real domain query earns the relaxed screening."""

    def test_boolean_query_is_domain_anchored(self):
        assert cp.is_domain_anchored({"searchQuery": DOMAIN_GATE})

    def test_multiword_phrase_is_domain_anchored(self):
        assert cp.is_domain_anchored({"searchQuery": "SAP EWM"})

    def test_single_derived_word_is_NOT_domain_anchored(self):
        """The pre-fix bug produced exactly this: the whole domain gate collapsed
        to "Account". A single generic word is not evidence of anything, and must
        not buy a candidate past the screen."""
        assert not cp.is_domain_anchored({"searchQuery": "Account"})

    def test_empty_query_is_not_domain_anchored(self):
        assert not cp.is_domain_anchored({"searchQuery": ""})
        assert not cp.is_domain_anchored({})


class TestBenedictsCandidatesSurvive:
    """The reproduction. Each of these was dropped before enrichment."""

    @pytest.mark.parametrize("title", [
        "Principal Consultant",          # measured 20.0 — the headline case
        "Account Executive",             # measured 0.0
        "Key Account Manager",
        "Sales Director",
        "Business Development Manager",
        "Vertriebsleiter Handel",
    ])
    def test_survives_when_domain_anchored(self, title):
        keep, verdict = ps.screen(
            {"currentTitle": title},
            requirements=COMMERCIAL_REQS,
            target_titles=COMMERCIAL_REQS["candidateTitles"],
            min_score=25.0,
        )
        keep, verdict = cp._channel_screen_policy(
            keep, verdict, ["title"], title=title, company="Acme GmbH",
            domain_anchored=True,
            candidate_titles=COMMERCIAL_REQS["candidateTitles"],
            domain_terms=COMMERCIAL_REQS["domainTerms"],
        )
        assert keep, f"{title!r} was dropped — this is the reported bug"
        assert verdict["score"] > 0

    def test_posting_title_is_not_used_as_a_target_for_commercial_roles(self):
        """"SAP Retail Consultant" describes the opening, not the person.

        Scoring an Account Executive against it is what produced 0.0 and the
        rejection. `candidateTitles` is the correct signal.
        """
        _, verdict = ps.screen(
            {"currentTitle": "Account Executive"},
            requirements=COMMERCIAL_REQS,
            target_titles=COMMERCIAL_REQS["candidateTitles"],
            min_score=25.0,
        )
        assert verdict.get("matchedVia") != "SAP Retail Consultant"

    def test_executive_veto_stays_a_hard_drop_even_with_domain_evidence(self):
        """Decided 2026-07-30: the owner/executive/freelance veto is an
        unconditional policy reject, not a ranking signal — domain evidence
        does not soften it. This matches the same policy shipped for freelance/
        self-employed candidates (`is_freelance_or_self_employed`), which is
        absolute for the same reason: a query matching profile TEXT cannot tell
        us someone stopped running their own business."""
        title = "Geschäftsführer"
        keep, verdict = ps.screen(
            {"currentTitle": title}, requirements=COMMERCIAL_REQS,
            target_titles=COMMERCIAL_REQS["candidateTitles"], min_score=25.0)
        keep, verdict = cp._channel_screen_policy(
            keep, verdict, ["title"], title=title, company="Retail SAP Partner GmbH",
            domain_anchored=True,
            candidate_titles=COMMERCIAL_REQS["candidateTitles"],
            domain_terms=COMMERCIAL_REQS["domainTerms"])
        assert not keep


class TestRankingIsNotFlat:
    """Relaxing the gate is only safe if the list still has an order.

    The old rescue floored everything at exactly 30.0. Applied to most of the
    list that produces one undifferentiated bucket and the recruiter's sort stops
    meaning anything — the gate stops rejecting and the ranking stops working.
    """

    def _rank(self, title, company, channels):
        keep, verdict = ps.screen(
            {"currentTitle": title}, requirements=COMMERCIAL_REQS,
            target_titles=COMMERCIAL_REQS["candidateTitles"], min_score=25.0)
        _, verdict = cp._channel_screen_policy(
            keep, verdict, channels, title=title, company=company,
            domain_anchored=True,
            candidate_titles=COMMERCIAL_REQS["candidateTitles"],
            domain_terms=COMMERCIAL_REQS["domainTerms"])
        return float(verdict["score"])

    def test_function_match_outranks_a_generic_title(self):
        assert (self._rank("Account Executive", "Acme", ["title"])
                > self._rank("Principal Consultant", "Acme", ["title"]))

    def test_both_channels_outranks_one(self):
        assert (self._rank("Principal Consultant", "Acme", ["title", "keyword"])
                > self._rank("Principal Consultant", "Acme", ["title"]))

    def test_domain_employer_outranks_an_unknown_one(self):
        assert (self._rank("Principal Consultant", "SAP Retail Solutions GmbH", ["title"])
                > self._rank("Principal Consultant", "Müller GmbH", ["title"]))

    def test_scores_are_not_all_identical(self):
        scores = {
            self._rank("Account Executive", "SAP Retail GmbH", ["title", "keyword"]),
            self._rank("Account Executive", "Acme", ["title"]),
            self._rank("Principal Consultant", "Acme", ["title"]),
            self._rank("Geschäftsführer", "Acme", ["title"]),
        }
        assert len(scores) > 1, "ranking collapsed into a single bucket"

    def test_score_is_capped(self):
        assert self._rank("Account Executive", "SAP Retail GmbH", ["title", "keyword"]) <= 95.0


class TestDeliveryRolesUnchanged:
    """BLOCKING REGRESSION GUARD.

    Title anchoring works WELL for delivery roles, and this shipped default-ON to
    every tenant with no staged rollout — so a recall or precision regression here
    would be silent until a client complained. These pin both directions.
    """

    def test_right_specialty_still_scores_high(self):
        keep, verdict = ps.screen(
            {"currentTitle": "Senior SAP EWM Consultant"},
            requirements=DELIVERY_REQS,
            target_titles=DELIVERY_REQS["candidateTitles"], min_score=25.0)
        assert keep and verdict["score"] >= 75.0

    @pytest.mark.parametrize("title", ["Kraftfahrer", "Marketing Manager", "Krankenpfleger"])
    def test_unrelated_titles_are_still_dropped_without_a_domain_gate(self, title):
        """No domain evidence ⇒ the title screen is the only gate, and it must
        still reject. This is what stops the fix degenerating into "keep
        everyone", which would just move the cost problem downstream.

        Note what is deliberately NOT asserted here: a same-platform neighbour
        like "SAP FICO Consultant" scores 35 and is KEPT, because prescreen is
        intentionally lopsided ("a false DROP is unrecoverable, a false KEEP
        costs one scrape"). Catching wrong-specialty-within-platform is the
        sourcing QA auditor's job, not this gate's.
        """
        keep, verdict = ps.screen(
            {"currentTitle": title}, requirements=DELIVERY_REQS,
            target_titles=DELIVERY_REQS["candidateTitles"], min_score=25.0)
        keep, _ = cp._channel_screen_policy(
            keep, verdict, ["title"], title=title, company="Acme",
            domain_anchored=False,
            candidate_titles=DELIVERY_REQS["candidateTitles"],
            domain_terms=DELIVERY_REQS["domainTerms"])
        assert not keep

    def test_same_platform_neighbour_is_capped_not_promoted(self):
        """"SAP FICO Consultant" for an SAP EWM role is kept but must stay
        clearly below a genuine match, so ranking still separates them."""
        _, fico = ps.screen(
            {"currentTitle": "SAP FICO Consultant"}, requirements=DELIVERY_REQS,
            target_titles=DELIVERY_REQS["candidateTitles"], min_score=25.0)
        _, ewm = ps.screen(
            {"currentTitle": "Senior SAP EWM Consultant"}, requirements=DELIVERY_REQS,
            target_titles=DELIVERY_REQS["candidateTitles"], min_score=25.0)
        assert fico["score"] < ewm["score"]

    def test_executive_veto_still_hard_drops_without_domain_evidence(self):
        """The cofounder leak this was written for is unchanged when there is no
        domain gate to vouch for the profile."""
        title = "Geschäftsführer"
        keep, verdict = ps.screen(
            {"currentTitle": title}, requirements=DELIVERY_REQS,
            target_titles=DELIVERY_REQS["candidateTitles"], min_score=25.0)
        keep, _ = cp._channel_screen_policy(
            keep, verdict, ["keyword"], title=title, company="Acme",
            domain_anchored=False,
            candidate_titles=DELIVERY_REQS["candidateTitles"],
            domain_terms=DELIVERY_REQS["domainTerms"])
        assert not keep

    def test_delivery_role_still_uses_the_posting_title_as_a_fallback(self):
        """Only COMMERCIAL roles suppress the posting title, because only there
        is it systematically misleading."""
        _, verdict = ps.screen(
            {"currentTitle": "SAP EWM Consultant"},
            requirements={"title": "SAP EWM Consultant", "roleFamily": "delivery",
                          "mustHaveSkills": ["SAP EWM"]},
            target_titles=[], min_score=25.0)
        assert verdict["score"] > 0
