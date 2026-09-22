"""Read-only explanation of blocked tasks from their pinned, immutable receipts.

This never grants readiness, changes a task, or contacts a provider. Text is static:
provider strings and exception payloads are never echoed into the operator UI.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantlab.current_actions import economic_date, stock_dividend_factor, unchanged_acquirer
from quantlab.market_data import AlpacaProvider, DatasetInvalid, InvalidProviderResponse

if TYPE_CHECKING:
    from quantlab.market_pipeline import MarketBatch, MarketTask

MAX_RECEIPT_CHARS = 262144
MAX_ROWS = 2000
TYPE_LABELS = {
    "stock_dividends": "akciová dividenda",
    "spin_offs": "oddělení části společnosti (spin-off)",
    "unit_splits": "rozdělení jednotek (unit split)",
    "cash_mergers": "fúze s peněžním vypořádáním",
    "stock_mergers": "fúze s akciovým vypořádáním",
    "stock_and_cash_mergers": "fúze s kombinovaným vypořádáním",
    "redemptions": "odkup nebo splacení instrumentu",
    "rights_distributions": "přidělení práv",
    "partial_calls": "částečné splacení",
    "reorganizations": "reorganizace",
    "capital_gains_distributions": "výplata kapitálového výnosu",
}
INVALID_RECEIPT = "Uložený inventář nelze ověřit; blokace dat zůstává zachována."


def inventory_blockage(
    payload_json: str,
    content_hash: str,
    *,
    symbol: str,
    instrument_id: str,
    start: date,
    end: date,
) -> str | None:
    """Explain a demonstrable inventory blocker, not guess the original exception."""
    if len(payload_json) > MAX_RECEIPT_CHARS:
        return "Inventář překračuje limit podrobné diagnostiky; blokace zůstává zachována."
    try:
        payload = json.loads(payload_json)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        if hashlib.sha256(encoded.encode()).hexdigest() != content_hash:
            return "Nesouhlasí obsahový otisk uloženého inventáře; blokace zůstává zachována."
        if not isinstance(payload, dict):
            return INVALID_RECEIPT
        expected = {
            "source": "alpaca_rest_current_inventory",
            "symbol": symbol,
            "instrument_id": instrument_id,
            "request_start": "1970-01-01",
            "request_end": "9999-12-31",
            "data_quality": "all",
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            return "Uložený inventář neodpovídá instrumentu nebo rozsahu kontroly."
        rows = payload.get("rows")
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            return INVALID_RECEIPT
        seen: set[str] = set()
        for entry in rows:
            if not isinstance(entry, list) or len(entry) != 2:
                return INVALID_RECEIPT
            collection, row = entry
            if not isinstance(collection, str) or not isinstance(row, dict):
                return INVALID_RECEIPT
            action_id = row.get("id")
            if not isinstance(action_id, str) or not action_id or action_id in seen:
                return "Uložený inventář obsahuje chybějící nebo duplicitní identitu události."
            seen.add(action_id)
            try:
                day = economic_date(row)
            except DatasetInvalid:
                return (
                    "Uložené události chybí ex_date, effective_date i process_date; "
                    "rozsah nelze ověřit."
                )
            except InvalidProviderResponse:
                return "Uložená událost obsahuje neplatné datum; rozsah nelze ověřit."
            if start <= day <= end and collection == "stock_dividends":
                try:
                    stock_dividend_factor(row, symbol)
                except (DatasetInvalid, InvalidProviderResponse):
                    return f"Událost akciová dividenda ({day}) nemá ověřený poměr nebo identitu."
                return f"Událost akciová dividenda ({day}) vyžaduje přehodnocení novým zpracováním."
            if start <= day <= end:
                try:
                    buyer = unchanged_acquirer(collection, row, symbol)
                except (DatasetInvalid, InvalidProviderResponse):
                    buyer = False
                if buyer:
                    return (
                        f"Událost fúze ({day}) se týká kupujícího; "
                        "lze přehodnotit uložený inventář."
                    )
            if start <= day <= end and collection not in AlpacaProvider._supported_collections:
                label = TYPE_LABELS.get(collection, "jiný dosud nepodporovaný typ")
                return (
                    f"Laboratoř neumí započítat událost: {label} ({day.isoformat()}). "
                    "Blokace se týká zpracování událostí."
                )
    except (ValueError, TypeError, RecursionError, OverflowError):
        return INVALID_RECEIPT
    # A supported inventory is not proof that the original failure has disappeared.
    return None


def blockage_detail(session: Session, task: MarketTask, batch: MarketBatch) -> str | None:
    """Inspect at most one pinned receipt per displayed blocked task, without writes."""
    from quantlab.market_pipeline import MarketActionReceipt

    if task.state != "DATA_BLOCKED" or not task.evidence_json:
        return task.detail
    if len(task.evidence_json) > MAX_RECEIPT_CHARS:
        return task.detail
    try:
        evidence: Any = json.loads(task.evidence_json)
    except (ValueError, RecursionError):
        return task.detail
    if not isinstance(evidence, dict):
        return task.detail
    receipt_id = evidence.get("current_action_receipt_id")
    if not isinstance(receipt_id, str) or re.fullmatch("[a-f0-9]{64}", receipt_id) is None:
        return task.detail
    receipt = session.execute(
        select(
            MarketActionReceipt.payload_json,
            MarketActionReceipt.content_hash,
            MarketActionReceipt.received_at,
        ).where(
            MarketActionReceipt.receipt_id == receipt_id,
            MarketActionReceipt.task_id == task.task_id,
            func.length(MarketActionReceipt.payload_json) <= MAX_RECEIPT_CHARS,
        )
    ).one_or_none()
    if receipt is None:
        return "Připnutý inventář není dostupný pro omezenou diagnostiku; blokace zůstává."
    received = receipt.received_at
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    if received > datetime.now(UTC):
        return "Čas přijetí inventáře je v budoucnosti; blokace zůstává zachována."
    return (
        inventory_blockage(
            receipt.payload_json,
            receipt.content_hash,
            symbol=task.symbol,
            instrument_id=task.instrument_id,
            start=batch.start,
            end=batch.end,
        )
        or task.detail
    )
