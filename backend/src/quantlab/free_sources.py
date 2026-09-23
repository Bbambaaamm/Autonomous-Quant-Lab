"""Zero-cost-first market-data policy for paper research (#164).

This is an audited capability matrix, not a claim that every listed source is
configured, licensed for bulk automation, or sufficient for PIT research.
"""

from __future__ import annotations

from typing import Any

from quantlab.config import Settings

POLICY_VERSION = "zero-cost-first-2"
VERIFIED_ON = "2026-09-23"


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
                "id": "stooq",
                "cost": "FREE",
                "configured": False,
                "role": "OPTIONAL_GLOBAL_EOD",
                "scope": "international daily prices and bulk world history",
                "history": "source-dependent",
                "request_limit": "provider-enforced; exact quota not asserted",
                "feed": "free API key required",
                "broad_automatic_use": False,
                "limitations": [
                    "Bulk-world endpoint returned HTTP 401 without API key (staging, 2026-09-23)",
                    "No corporate-actions adapter",
                    "Bulk automation terms and complete exchange coverage are not verified",
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
                "configured": False,
                "role": "OPTIONAL_FREE_VALIDATION",
                "scope": "global symbol lookup and selected historical datasets",
                "history": "endpoint dependent",
                "request_limit": "25 requests/day standard free service",
                "feed": "API key required",
                "broad_automatic_use": False,
                "limitations": [
                    "Daily request limit is too small for broad recurring universe sync",
                    "Premium-only endpoints must never be assumed available",
                ],
            },
        ],
        "coverage": {
            "us_prices": "ACTIVE_FREE" if alpaca_ready else "FREE_SOURCE_NOT_CONFIGURED",
            "global_prices": "ZERO_COST_CREDENTIAL_REQUIRED",
            "global_catalog": "ZERO_COST_CREDENTIAL_REQUIRED",
            "historical_membership": "OBSERVED_US_LIFECYCLE_ONLY",
            "corporate_actions": "PARTIAL_US_CURRENT_ONLY",
            "research_pit_ready": False,
        },
    }
