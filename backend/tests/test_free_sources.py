from quantlab.config import Settings
from quantlab.free_sources import zero_cost_source_matrix


def test_zero_cost_policy_never_requires_paid_subscription():
    policy = zero_cost_source_matrix(Settings())
    assert policy["monthly_data_budget_usd"] == 0
    assert policy["paid_subscription_required"] is False
    assert all(source["cost"] == "FREE" for source in policy["sources"])
    assert policy["coverage"]["research_pit_ready"] is False


def test_configured_alpaca_basic_is_active_free_us_source():
    settings = Settings(
        market_data_provider="alpaca",
        alpaca_key_id="key",
        alpaca_secret_key="secret",
    )
    policy = zero_cost_source_matrix(settings)
    alpaca = next(source for source in policy["sources"] if source["id"] == "alpaca-basic")
    assert alpaca["configured"] is True
    assert alpaca["broad_automatic_use"] is True
    assert policy["coverage"]["us_prices"] == "ACTIVE_FREE"


def test_optional_free_sources_do_not_pretend_global_completion():
    policy = zero_cost_source_matrix(Settings())
    sources = {source["id"]: source for source in policy["sources"]}
    assert sources["stooq-per-symbol"]["broad_automatic_use"] is False
    assert sources["stooq-bulk-world-candidate"]["configured"] is False
    assert sources["stooq-bulk-world-candidate"]["configured"] is False
    assert sources["twelve-data-basic"]["configured"] is False
    # alpha-vantage-free is now user-authorized + live-verified (non-US).
    av = sources["alpha-vantage-free"]
    assert av["configured"] is True
    assert av["broad_automatic_use"] is True
    assert av["role"] == "VERIFIED_NON_US_VALIDATION"
    # Global-prices coverage is measured (not inferred from a plan name).
    assert policy["coverage"]["global_catalog"] == "NOT_VERIFIED_INTEGRATION_REQUIRED"
    assert policy["coverage"]["global_prices"] == "MEASURED_PARTIAL_FREE"
    assert policy["coverage"]["historical_membership"] == "OBSERVED_US_LIFECYCLE_ONLY"


def test_policy_never_exposes_credentials():
    settings = Settings(
        market_data_provider="alpaca",
        alpaca_key_id="private-key",
        alpaca_secret_key="private-secret",
    )
    rendered = str(zero_cost_source_matrix(settings))
    assert "private-key" not in rendered
    assert "private-secret" not in rendered


def test_alpha_vantage_non_us_coverage_is_measured_not_inferred():
    """Issue #187 DoD: non-US coverage is measured from real receipts, never
    inferred from a provider plan name or a single symbol."""
    policy = zero_cost_source_matrix(Settings())
    av = next(source for source in policy["sources"] if source["id"] == "alpha-vantage-free")
    assert av["configured"] is True
    assert av["role"] == "VERIFIED_NON_US_VALIDATION"
    assert len(av["measured_coverage"]) == 3
    receipts = {r["symbol"]: r for r in av["measured_coverage"]}
    assert receipts["TSCO.LON"]["exchange"] == "LSE"
    assert receipts["MBG.DEX"]["exchange"] == "XETRA"
    assert receipts["SHOP.TRT"]["exchange"] == "TSX"
    for r in av["measured_coverage"]:
        assert r["bars"] == 100
        assert r["last_date"] == "2026-09-25"
        assert len(r["series_sha256"]) == 64  # immutable provenance receipt
    # Coverage reflects measured evidence, not "NOT_VERIFIED".
    assert policy["coverage"]["global_prices"] == "MEASURED_PARTIAL_FREE"


def test_zero_cost_policy_never_exposes_credentials():
    settings = Settings(
        market_data_provider="alpaca",
        alpaca_key_id="private-key",
        alpaca_secret_key="private-secret",
    )
    policy = zero_cost_source_matrix(settings)
    assert "alpha_vantage_ready" not in str(policy)
    assert "private-key" not in str(policy)
    assert "private-secret" not in str(policy)


def test_alpha_vantage_corporate_actions_stay_fail_closed():
    """Free-tier CA is not exposed; adapter must not fabricate CA evidence."""
    policy = zero_cost_source_matrix(Settings())
    av = next(source for source in policy["sources"] if source["id"] == "alpha-vantage-free")
    assert any("fail-closed" in limitation for limitation in av["limitations"])
