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
    assert sources["stooq"]["configured"] is False
    assert sources["stooq"]["broad_automatic_use"] is False
    assert sources["stooq"]["feed"] == "free API key required"
    assert sources["twelve-data-basic"]["configured"] is False
    assert sources["alpha-vantage-free"]["configured"] is False
    assert policy["coverage"]["global_catalog"] == "ZERO_COST_CREDENTIAL_REQUIRED"
    assert policy["coverage"]["global_prices"] == "ZERO_COST_CREDENTIAL_REQUIRED"
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
