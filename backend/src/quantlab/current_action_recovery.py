"""Reasoned, bounded re-evaluation using stored receipts; no external requests."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantlab.current_actions import NORMALIZATION_VERSION
from quantlab.domain import require_utc
from quantlab.market_data import AlpacaProvider, DatasetInvalid, InvalidProviderResponse
from quantlab.market_data_service import _lock
from quantlab.market_diagnostics import MAX_RECEIPT_CHARS, MAX_ROWS
from quantlab.market_pipeline import (
    MarketActionReceipt,
    MarketActionReview,
    MarketBatch,
    MarketPipeline,
    MarketTask,
    _utc,
)
from quantlab.market_screening import MarketScreenRun, canonical, identity


def _require(value: bool, reason: str) -> None:
    if not value:
        raise DatasetInvalid(reason)


def _receipt(
    session: Session, task: MarketTask, batch: MarketBatch, now: datetime
) -> tuple[MarketActionReceipt, dict[str, Any]]:
    _require(
        bool(task.evidence_json) and len(task.evidence_json or "") <= MAX_RECEIPT_CHARS,
        "TASK_EVIDENCE_UNAVAILABLE",
    )
    evidence = json.loads(task.evidence_json or "{}")
    _require(isinstance(evidence, dict), "TASK_EVIDENCE_INVALID")
    receipt_id = evidence.get("current_action_receipt_id")
    _require(
        isinstance(receipt_id, str) and re.fullmatch("[a-f0-9]{64}", receipt_id) is not None,
        "RECEIPT_ID_INVALID",
    )
    receipt = session.scalar(
        select(MarketActionReceipt).where(
            MarketActionReceipt.receipt_id == receipt_id,
            MarketActionReceipt.task_id == task.task_id,
            func.length(MarketActionReceipt.payload_json) <= MAX_RECEIPT_CHARS,
        )
    )
    if receipt is None:
        raise DatasetInvalid("RECEIPT_UNAVAILABLE")
    _require(_utc(receipt.received_at) <= now, "FUTURE_RECEIPT")
    payload = json.loads(receipt.payload_json)
    _require(
        isinstance(payload, dict) and identity(payload) == receipt.content_hash,
        "RECEIPT_HASH_INVALID",
    )
    expected = {
        "source": "alpaca_rest_current_inventory",
        "symbol": task.symbol,
        "instrument_id": task.instrument_id,
        "request_start": "1970-01-01",
        "request_end": "9999-12-31",
        "data_quality": "all",
    }
    _require(all(payload.get(k) == v for k, v in expected.items()), "RECEIPT_SCOPE_INVALID")
    rows = payload.get("rows")
    _require(isinstance(rows, list) and len(rows) <= MAX_ROWS, "RECEIPT_ROWS_INVALID")
    _require(
        all(
            isinstance(r, list) and len(r) == 2 and isinstance(r[0], str) and isinstance(r[1], dict)
            for r in rows
        ),
        "RECEIPT_ROWS_INVALID",
    )
    _require(batch.provider.startswith("alpaca:"), "PROVIDER_SCOPE_INVALID")
    return receipt, payload


def recheck_current_receipts(
    sessions: Callable[[], Session],
    batch_id: str,
    symbols: list[str],
    actor: str,
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    now = require_utc(now)
    if not re.fullmatch("[a-f0-9]{64}", batch_id) or not 1 <= len(symbols) <= 50:
        raise ValueError("Neplatná dávka nebo počet titulů")
    if len(set(symbols)) != len(symbols) or not actor or not 3 <= len(reason.strip()) <= 1000:
        raise ValueError("Chybí jednoznačný rozsah nebo důvod")
    results: list[dict[str, str]] = []
    pipeline = MarketPipeline(sessions)
    with sessions() as session, session.begin():
        _lock(session, f"market-screen:{batch_id}")
        batch = session.get(MarketBatch, batch_id)
        if batch is None or _utc(batch.created_at) > now:
            raise ValueError("Dávka není dostupná")
        if session.scalar(
            select(MarketScreenRun.run_id).where(MarketScreenRun.batch_id == batch_id).limit(1)
        ):
            raise ValueError("Dokončený výběr se nemění; použijte novou dávku")
        tasks = list(
            session.scalars(
                select(MarketTask)
                .where(
                    MarketTask.batch_id == batch_id,
                    MarketTask.symbol.in_(symbols),
                )
                .order_by(MarketTask.symbol)
                .with_for_update()
            )
        )
        if len(tasks) != len(symbols):
            raise ValueError("Požadované symboly neodpovídají dávce")
        for task in tasks:
            if task.state != "DATA_BLOCKED":
                results.append({"symbol": task.symbol, "result": "UNCHANGED"})
                continue
            try:
                receipt, payload = _receipt(session, task, batch, now)
                at = _utc(receipt.received_at)
                actions = AlpacaProvider.normalize_current_rows(
                    task.symbol,
                    task.instrument_id,
                    payload["rows"],
                    batch.start,
                    batch.end,
                    at,
                )
                after = pipeline.screen(
                    task.instrument_id,
                    batch.provider,
                    batch.start,
                    batch.end,
                    at,
                    actions=actions,
                    inventory_id=receipt.receipt_id,
                    inventory_received_at=at,
                )
                previous = json.loads(task.evidence_json or "{}")
                _require(
                    task.bars > 0 and after["bars"] == task.bars, "PRICE_EVIDENCE_NOT_REPRODUCIBLE"
                )
                _require(
                    after["observation_ids"] == previous.get("observation_ids"),
                    "PRICE_MANIFEST_CHANGED",
                )
            except (DatasetInvalid, InvalidProviderResponse, ValueError, TypeError, KeyError):
                results.append({"symbol": task.symbol, "result": "STILL_BLOCKED"})
                continue
            review_id = identity(
                {
                    "task": task.task_id,
                    "receipt": receipt.receipt_id,
                    "normalization_version": NORMALIZATION_VERSION,
                }
            )
            if session.get(MarketActionReview, review_id) is not None:
                results.append({"symbol": task.symbol, "result": "ALREADY_REVIEWED"})
                continue
            after["normalization_version"] = NORMALIZATION_VERSION
            after["recovery_source"] = "PINNED_RECEIPT_NO_PROVIDER_REFRESH"
            after["current_action_receipt_id"] = receipt.receipt_id
            audit = {
                "actor": actor,
                "reason": reason,
                "reviewed_at": now,
                "normalization_version": NORMALIZATION_VERSION,
                "previous_state": task.state,
                "next_state": "DONE",
                "previous_evidence": json.loads(task.evidence_json or "{}"),
                "next_evidence": after,
            }
            session.add(
                MarketActionReview(
                    review_id=review_id,
                    task_id=task.task_id,
                    receipt_id=receipt.receipt_id,
                    reviewed_at=now,
                    content_hash=identity(audit),
                    payload_json=canonical(audit),
                )
            )
            task.state = "DONE"
            task.detail = str(after["reason"])
            task.bars, task.coverage = after["bars"], Decimal(after["coverage"])
            task.momentum, task.trend = after["momentum"], after["trend"]
            task.mean_reversion = after["mean_reversion"]
            task.evidence_json = canonical(after)
            results.append({"symbol": task.symbol, "result": "RESOLVED", "review_id": review_id})
    return {
        "batch_id": batch_id,
        "normalization_version": NORMALIZATION_VERSION,
        "resolved": sum(r["result"] == "RESOLVED" for r in results),
        "items": results,
    }
