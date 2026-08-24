from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantlab.phase7 import (
    DEFAULT_POLICY,
    _entitled_quantity,
    deterministic_block_bootstrap,
    validate_policy,
)


def test_default_policy_is_fail_closed_and_valid() -> None:
    validate_policy(DEFAULT_POLICY.copy())
    unsafe = DEFAULT_POLICY | {"hard_suspend_on_halted": False}
    with pytest.raises(ValueError, match="nelze vypnout"):
        validate_policy(unsafe)


def test_policy_rejects_hidden_and_malformed_values() -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        validate_policy(DEFAULT_POLICY | {"broker": "live"})
    with pytest.raises(ValueError, match="integer"):
        validate_policy(DEFAULT_POLICY | {"minimum_sessions": True})


def test_block_bootstrap_is_deterministic_and_horizon_aware() -> None:
    returns = [Decimal("0.01"), Decimal("-0.02"), Decimal("0.03")]
    first = deterministic_block_bootstrap(returns, 7, 200, 2, "monitor:policy:7:v1")
    second = deterministic_block_bootstrap(returns, 7, 200, 2, "monitor:policy:7:v1")
    assert first == second
    assert len(first) == 200
    assert first == sorted(first)


def test_no_synthetic_bootstrap_without_baseline_series() -> None:
    assert deterministic_block_bootstrap([], 20, 100, 5, "seed") == []


@pytest.mark.parametrize(
    ("returns", "horizon", "block_size"),
    [
        ([Decimal("0")], 1, 10),
        ([Decimal("0")], 20, 50),
        ([Decimal("0.01")], 25, 3),
        ([Decimal("0.01"), Decimal("-0.01")], 40, 20),
    ],
)
def test_bootstrap_handles_short_and_zero_series_without_non_finite_values(
    returns: list[Decimal], horizon: int, block_size: int
) -> None:
    distribution = deterministic_block_bootstrap(returns, horizon, 50, block_size, "safe")
    assert len(distribution) == 50
    assert all(value.is_finite() for value in distribution)


def test_bootstrap_distribution_changes_with_paper_horizon() -> None:
    returns = [Decimal("0.01"), Decimal("-0.02"), Decimal("0.03")]
    short = deterministic_block_bootstrap(returns, 3, 100, 2, "monitor:3")
    long = deterministic_block_bootstrap(returns, 8, 100, 2, "monitor:8")
    assert short != long


def test_entitlement_applies_split_to_older_fills_only() -> None:
    bought_at = datetime(2026, 1, 2, tzinfo=UTC)
    split_at = bought_at + timedelta(days=5)
    sold_at = split_at + timedelta(days=1)

    quantity = _entitled_quantity(
        [
            (bought_at, Decimal("10"), "BUY"),
            (sold_at, Decimal("5"), "SELL"),
        ],
        [(split_at, "2")],
    )

    assert quantity == Decimal("15")


def test_entitlement_compounds_multiple_splits_without_drift() -> None:
    bought_at = datetime(2026, 1, 2, tzinfo=UTC)

    quantity = _entitled_quantity(
        [(bought_at, Decimal("10"), "BUY")],
        [
            (bought_at + timedelta(days=2), "2"),
            (bought_at + timedelta(days=4), "1.5"),
        ],
    )

    assert quantity == Decimal("30")


def test_entitlement_rejects_invalid_applied_split_evidence() -> None:
    bought_at = datetime(2026, 1, 2, tzinfo=UTC)

    with pytest.raises(ValueError, match="Split ratio"):
        _entitled_quantity(
            [(bought_at, Decimal("10"), "BUY")],
            [(bought_at + timedelta(days=1), "0")],
        )
