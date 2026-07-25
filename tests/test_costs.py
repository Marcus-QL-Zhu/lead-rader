from ht_lead_radar.costs import (
    METASO_CONSERVATIVE_POINTS_PER_SEARCH,
    METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT,
    SearchBudgetLedger,
)


def test_budget_ledger_is_idempotent_and_never_exceeds_configured_limit(tmp_path):
    ledger = SearchBudgetLedger(tmp_path / "costs.sqlite")

    assert ledger.charge("lead-a", 6, configured_limit=12)
    assert not ledger.charge("lead-a", 6, configured_limit=12)
    assert ledger.charge("lead-b", 6, configured_limit=12)
    assert not ledger.charge("lead-c", 6, configured_limit=12)

    status = ledger.status(configured_limit=12)
    assert status.spent_points == 12
    assert status.available_points == 0


def test_budget_provider_cap_is_always_enforced(tmp_path):
    ledger = SearchBudgetLedger(tmp_path / "costs.sqlite")

    assert ledger.charge("a", 6, configured_limit=999, provider_limit=6)
    assert not ledger.charge("b", 1, configured_limit=999, provider_limit=6)


def test_budget_provider_cap_cannot_be_raised_above_500(tmp_path):
    ledger = SearchBudgetLedger(tmp_path / "costs.sqlite")

    assert ledger.charge(
        "bulk",
        METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT,
        configured_limit=99_999,
        provider_limit=99_999,
    )
    assert not ledger.charge(
        "overflow",
        1,
        configured_limit=99_999,
        provider_limit=99_999,
    )
    status = ledger.status(configured_limit=99_999, provider_limit=99_999)
    assert status.provider_limit == METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT
    assert status.spent_points == METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT
    assert status.available_points == 0


def test_metaso_cannot_be_undercharged_below_conservative_search_cost(tmp_path):
    ledger = SearchBudgetLedger(tmp_path / "costs.sqlite")

    assert ledger.charge(
        "understated-search",
        1,
        configured_limit=METASO_CONSERVATIVE_POINTS_PER_SEARCH,
        provider_limit=99_999,
    )
    assert not ledger.charge(
        "second-search",
        1,
        configured_limit=METASO_CONSERVATIVE_POINTS_PER_SEARCH,
        provider_limit=99_999,
    )
    status = ledger.status(
        configured_limit=METASO_CONSERVATIVE_POINTS_PER_SEARCH,
        provider_limit=99_999,
    )
    assert status.spent_points == METASO_CONSERVATIVE_POINTS_PER_SEARCH
