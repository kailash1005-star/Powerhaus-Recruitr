"""The Broadener may widen the net, never change the target.

The scenario below is the real one observed live on the payroll job: attempt 1
aimed at German payroll titles and returned zero, and the Broadener "generalised"
them into "SAP Consultant"/"HR Specialist" — a different profession — which
returned 12 SAP Finance consultants who could not do the job.
"""
import importlib

import pytest

b = importlib.import_module("app.services.sourcing.broadener")
SearchAttempt = importlib.import_module("app.services.sourcing.models").SearchAttempt
SearchFilters = importlib.import_module("app.services.sourcing.models").SearchFilters
BroadenDecision = importlib.import_module("app.services.sourcing.models").BroadenDecision

PAYROLL_INITIAL = SearchAttempt(
    attempt=1, action="initial", resultCount=0,
    filters={"currentJobTitles": [
        "Entgeltabrechnung Spezialist", "Payroll Specialist",
        "Personalsachbearbeiter Entgeltabrechnung", "Sachbearbeiter Lohnabrechnung",
        "Lohn- und Gehaltsbuchhalter"]},
)


def _decide(titles):
    return BroadenDecision(decision="broaden", action="generalise_titles",
                           reasoning="x", filters=SearchFilters(
                               searchQuery="SAP HR", currentJobTitles=titles))


def test_anchor_excludes_generic_role_words():
    anchor = b._domain_anchor([PAYROLL_INITIAL])
    assert "entgeltabrechnung" in anchor
    assert "payroll" in anchor
    # "Specialist"/"Sachbearbeiter" describe form, not profession — they would let
    # "HR Specialist" masquerade as a payroll role.
    assert "specialist" not in anchor
    assert "spezialist" not in anchor
    assert "sachbearbeiter" not in anchor


def test_strips_the_real_observed_drift():
    """The exact titles the Broadener drifted to, live."""
    d = b._enforce_domain(
        _decide(["SAP Consultant", "Payroll Consultant", "HR Specialist",
                 "HR Consultant", "HR Administrator"]),
        [PAYROLL_INITIAL],
    )
    assert d is not None
    assert d.filters.currentJobTitles == ["Payroll Consultant"]


def test_gives_up_to_the_ladder_when_domain_is_abandoned_entirely():
    d = b._enforce_domain(
        _decide(["SAP Consultant", "HR Specialist", "Business Analyst"]),
        [PAYROLL_INITIAL],
    )
    assert d is None, "a wholly off-domain proposal must fall back to the ladder"


def test_keeps_a_legitimate_within_domain_widening():
    d = b._enforce_domain(
        _decide(["Entgeltabrechner", "Payroll Specialist", "Lohnbuchhalter"]),
        [PAYROLL_INITIAL],
    )
    assert d is not None
    assert d.filters.currentJobTitles == ["Entgeltabrechner", "Payroll Specialist",
                                          "Lohnbuchhalter"]


def test_sap_alone_is_not_the_specialty():
    """FC-23 inverted the old semantics ON PURPOSE. 'SAP Consultant' used to
    count as a legitimate widening of 'SAP FICO' because both carry 'SAP' —
    which is exactly how an SAP-HCM search drifted into SAP FI-CO in
    production. Two-tier anchor: 'SAP' is the ecosystem, 'FICO' the specialty,
    and only the specialty satisfies the guard. A proposal that keeps the
    brand but drops the specialty is total drift → None (planned ladder)."""
    initial = SearchAttempt(attempt=1, action="initial", resultCount=0,
                            filters={"currentJobTitles": ["SAP FICO Consultant",
                                                          "SAP S/4HANA Consultant"]})
    d = b._enforce_domain(_decide(["SAP Consultant", "SAP Berater", "ERP Consultant"]),
                          [initial])
    assert d is None

    # Widening WITHIN the specialty still passes.
    d = b._enforce_domain(_decide(["SAP FICO Berater", "SAP Consultant"]), [initial])
    assert d is not None
    assert d.filters.currentJobTitles == ["SAP FICO Berater"]


def test_no_anchor_means_no_constraint():
    """A search with no initial titles has nothing to anchor to — never invent one."""
    initial = SearchAttempt(attempt=1, action="initial", resultCount=0,
                            filters={"searchQuery": "payroll"})
    d = b._enforce_domain(_decide(["Anything At All"]), [initial])
    assert d is not None
    assert d.filters.currentJobTitles == ["Anything At All"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
