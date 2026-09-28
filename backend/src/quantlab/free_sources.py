"""Zero-cost-first market-data policy for paper research (#164).

This is an audited capability matrix, not a claim that every listed source is
configured, licensed for bulk automation, or sufficient for PIT research.
"""

from __future__ import annotations

from typing import Any

from quantlab.config import Settings

POLICY_VERSION = "zero-cost-first-3"
VERIFIED_ON = "2026-09-26"


def zero_cost_source_matrix(settings: Settings) -> dict[str, Any]:
    alpaca_ready = bool(
        settings.market_data_provider == "alpaca"
        and settings.alpaca_key_id.strip()
        and settings.alpaca_secret_key.strip()
    )
    return {
        "policy_version": POLICY_VERSION,
        "verified_on": VERIFIED_ON,
        "monthly_data_budget_usd": 0,
        "paid_subscription_required": False,
        "sources": [
            {
                "id": "alpaca-basic",
                "cost": "FREE",
                "configured": alpaca_ready,
                "role": "PRIMARY_US",
                "scope": "US stocks and ETFs",
                "history": "since 2016",
                "request_limit": "200 historical requests/min",
                "feed": settings.alpaca_feed if alpaca_ready else "iex",
                "broad_automatic_use": alpaca_ready,
                "limitations": [
                    "Free real-time equities feed is IEX, not consolidated SIP",
                    "Current active directory is not historical market membership",
                ],
            },
            {
                "id": "stooq-per-symbol",
                "cost": "FREE",
                "configured": settings.market_data_provider == "stooq",
                "role": "SECONDARY_EOD_VALIDATION",
                "scope": "daily prices for individually mapped symbols",
                "history": "source-dependent",
                "request_limit": "not asserted",
                "feed": "credential-free per-symbol adapter",
                "broad_automatic_use": False,
                "limitations": [
                    "Current per-symbol adapter is not a global catalog",
                    "No corporate-actions adapter",
                ],
            },
            {
                "id": "stooq-bulk-world-candidate",
                "cost": "FREE",
                "configured": False,
                "role": "UNVERIFIED_GLOBAL_CANDIDATE",
                "scope": "bulk world history candidate",
                "history": "not verified",
                "request_limit": "not verified",
                "feed": "authentication/integration not established",
                "broad_automatic_use": False,
                "limitations": [
                    "Bulk-world endpoint returned HTTP 401 without API key (staging, 2026-09-23)",
                    "Credentialed access and complete global coverage are not established",
                    "Provider factory has no bulk-world integration",
                ],
            },
            {
                "id": "twelve-data-basic",
                "cost": "FREE",
                "configured": False,
                "role": "OPTIONAL_FREE_VALIDATION",
                "scope": "Basic plan plus global trial symbols",
                "history": "plan/symbol dependent",
                "request_limit": "8 credits/min; 800/day",
                "feed": "API key required",
                "broad_automatic_use": False,
                "limitations": [
                    "Free Basic is not proof of full-world symbol coverage",
                    "Requires a user-created free API key before live verification",
                ],
            },
            {
                "id": "alpha-vantage-free",
                "cost": "FREE",
                "configured": True,
                "role": "VERIFIED_NON_US_VALIDATION",
                "scope": "global symbol lookup and selected historical datasets",
                "history": "measured (LSE/XETRA/TSX)",
                "request_limit": "25 requests/day standard free service",
                "feed": "user-authorized free credential (non-US); key via ALPHAVANTAGE_API_KEY env",  # noqa: E501
                "broad_automatic_use": True,
                "measured_coverage": [
                    {
                        "exchange": "LSE",
                        "symbol": "TSCO.LON",
                        "bars": 100,
                        "first_date": "2026-05-07",
                        "last_date": "2026-09-25",
                        "series_sha256": "699875c1976ac3c92b99e1104e2baeea4f42e93c79a55226bdb25767c7004c7c",  # noqa: E501
                    },
                    {
                        "exchange": "XETRA",
                        "symbol": "MBG.DEX",
                        "bars": 100,
                        "first_date": "2026-05-11",
                        "last_date": "2026-09-25",
                        "series_sha256": "630a38031352efc24ce6b83a96257337093a3072308d8cfdc061884633cad47d",  # noqa: E501
                    },
                    {
                        "exchange": "TSX",
                        "symbol": "SHOP.TRT",
                        "bars": 100,
                        "first_date": "2026-05-05",
                        "last_date": "2026-09-25",
                        "series_sha256": "76552b6ddd4522f23ffba2512078d24a30cfea0f058f933d512e5b8e98c9bf4f",  # noqa: E501
                    },
                ],
                "limitations": [
                    "Daily request limit is too small for broad recurring universe sync",
                    "Premium-only endpoints must never be assumed available",
                    "Free-tier corporate actions are not exposed — CA adapter is fail-closed",
                ],
            },
        ],
        "coverage": {
            "us_prices": "ACTIVE_FREE" if alpaca_ready else "FREE_SOURCE_NOT_CONFIGURED",
            "global_prices": "MEASURED_PARTIAL_FREE",
            "global_catalog": "NOT_VERIFIED_INTEGRATION_REQUIRED",
            "historical_membership": "OBSERVED_US_LIFECYCLE_ONLY",
            "corporate_actions": "PARTIAL_US_CURRENT_ONLY",
            "research_pit_ready": False,
        },
    }
