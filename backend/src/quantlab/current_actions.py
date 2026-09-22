"""Current receipt-only economic cases; never backdate research/SSE knowledge."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from quantlab.market_data import DatasetInvalid, InvalidProviderResponse

NORMALIZATION_VERSION = "current-actions-2"
MERGERS = frozenset({"cash_mergers", "stock_mergers", "stock_and_cash_mergers"})


def positive(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidProviderResponse("Neplatný poměr firemní události") from exc
    if not number.is_finite() or number <= 0:
        raise InvalidProviderResponse("Neplatný poměr firemní události")
    return number


def economic_date(row: dict[str, Any]) -> date:
    raw = row.get("ex_date") or row.get("effective_date") or row.get("process_date")
    if raw is None:
        raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise InvalidProviderResponse("Neplatné ekonomické datum události") from exc


def stock_dividend_factor(row: dict[str, Any], symbol: str) -> Decimal:
    if row.get("symbol") != symbol or row.get("currency") not in (None, "", "USD"):
        raise DatasetInvalid("CORPORATE_ACTION_IDENTITY_UNVERIFIED")
    if not row.get("ex_date"):
        raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
    # Alpaca defines rate as EXTRA shares received for each share already held.
    return Decimal(1) + positive(row.get("rate"))


def unchanged_acquirer(collection: str, row: dict[str, Any], symbol: str) -> bool:
    if collection not in MERGERS:
        return False
    acquirer, acquiree = row.get("acquirer_symbol"), row.get("acquiree_symbol")
    if not isinstance(acquiree, str) or not acquiree or acquiree == acquirer:
        raise DatasetInvalid("CORPORATE_ACTION_IDENTITY_UNVERIFIED")
    if acquirer != symbol:
        return False  # Target-side conversion requires a multi-security ledger.
    if row.get("currency") not in (None, "", "USD") or not row.get("effective_date"):
        raise DatasetInvalid("CORPORATE_ACTION_IDENTITY_UNVERIFIED")
    economic_date({"effective_date": row["effective_date"]})
    if collection != "cash_mergers":
        positive(row.get("acquirer_rate"))
        positive(row.get("acquiree_rate"))
    if collection == "cash_mergers":
        positive(row.get("rate"))
    if collection == "stock_and_cash_mergers":
        positive(row.get("cash_rate"))
    # Existing buyer shares are unchanged: this is not a split or a cash dividend.
    return True
