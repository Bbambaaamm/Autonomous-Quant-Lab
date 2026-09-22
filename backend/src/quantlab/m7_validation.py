"""Read-only reproducible M7 validation for issue #164.

The validation intentionally measures PRICE returns. Cash distributions are ignored
consistently for strategy and benchmark because historical announcement knowledge
is unavailable. Any split/symbol/lifecycle event inside the interval fails closed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import require_utc
from quantlab.market_data import Observation, XNYSCalendar
from quantlab.market_data_service import _database_utc, _observation
from quantlab.market_pipeline import MarketActionReceipt, MarketBatch, MarketTask
from quantlab.multi_asset import (
    CrossSectionalMomentumStrategy,
    ObservationKnowledgeMode,
    RebalanceFrequency,
    StrategyContext,
    TargetPortfolio,
    run_multi_asset,
)
from quantlab.persistence import MarketObservationRecord
from quantlab.phase6_runtime import multi_asset_metrics
from quantlab.universe import (
    PointInTimeUniverse,
    UniverseDefinition,
    UniverseKind,
    UniverseMembership,
)

UNIVERSE_ID = "m7-broad-etf-price-return-v1"
SYMBOLS = ("DIA", "IWM", "QQQ", "SPY")
START = date(2025, 8, 18)
END = date(2026, 9, 21)
INITIAL_CASH = Decimal("100000")
COMMISSION_BPS = Decimal("1")
SPLIT = (Decimal("0.60"), Decimal("0.20"), Decimal("0.20"))
PARAMETER_BUDGET = (
    {"lookback": 63, "top_n": 1},
    {"lookback": 63, "top_n": 2},
    {"lookback": 126, "top_n": 1},
    {"lookback": 126, "top_n": 2},
)
MEMBERSHIP_EVIDENCE = {
    "DIA": (
        "1998-01-14",
        "https://www.ssga.com/us/en/institutional/etfs/state-street-spdr-dow-jones-industrial-average-etf-trust-dia",
    ),
    "IWM": ("2000-05-22", "https://www.ishares.com/us/products/239710/"),
    "QQQ": ("1999-03-10", "https://www.invesco.com/qqq-etf/en/home.html"),
    "SPY": (
        "1993-01-22",
        "https://www.ssga.com/us/en/institutional/etfs/state-street-spdr-sp-500-etf-trust-spy",
    ),
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class EqualWeightMonthly:
    name: str = "equal_weight_monthly_price_return"
    version: str = "1.0.0"
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.MONTHLY

    @property
    def required_lookback(self) -> int:
        return 1

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        members = tuple(sorted(context.eligible_instruments))
        weight = Decimal("1") / len(members) if members else Decimal("0")
        return TargetPortfolio(tuple((item, weight) for item in members), "equal weight")


def _metric_dict(metrics: Any) -> dict[str, object]:
    return {
        "total_return": str(metrics.total_return),
        "annualized_return": str(metrics.annualized_return),
        "volatility": str(metrics.volatility),
        "sharpe": str(metrics.sharpe),
        "max_drawdown": str(metrics.max_drawdown),
        "turnover": str(metrics.turnover),
        "time_weighted_exposure": str(metrics.time_weighted_exposure),
        "trade_count": metrics.trade_count,
        "total_costs": str(metrics.total_costs),
    }


def _receipt_rows(
    receipt: MarketActionReceipt, task: MarketTask, batch: MarketBatch
) -> list[list[Any]]:
    if hashlib.sha256(receipt.payload_json.encode()).hexdigest() != receipt.content_hash:
        raise ValueError("ACTION_RECEIPT_HASH_MISMATCH")
    payload = json.loads(receipt.payload_json)
    expected = {
        "source": "alpaca_rest_current_inventory",
        "symbol": task.symbol,
        "instrument_id": task.instrument_id,
        "request_start": "1970-01-01",
        "request_end": "9999-12-31",
        "data_quality": "all",
    }
    if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in expected.items()):
        raise ValueError("ACTION_RECEIPT_SCOPE_MISMATCH")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ACTION_RECEIPT_INVALID")
    for entry in rows:
        if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[1], dict):
            raise ValueError("ACTION_RECEIPT_INVALID")
        kind, row = entry
        raw = row.get("ex_date") or row.get("effective_date") or row.get("process_date")
        if raw is None:
            raise ValueError("ACTION_RECEIPT_DATE_MISSING")
        day = date.fromisoformat(str(raw))
        if batch.start <= day <= batch.end and kind != "cash_dividends":
            raise ValueError(f"PRICE_RETURN_DISCONTINUITY:{kind}")
    return rows


def _memberships(tasks: dict[str, MarketTask]) -> list[UniverseMembership]:
    result = []
    for symbol in SYMBOLS:
        raw, _ = MEMBERSHIP_EVIDENCE[symbol]
        when = datetime.combine(date.fromisoformat(raw), datetime.min.time(), UTC)
        result.append(
            UniverseMembership(UNIVERSE_ID, tasks[symbol].instrument_id, when, None, when)
        )
    return result


def _authoritative_observations(
    session: Session, tasks: dict[str, MarketTask], provider: str, as_of: datetime
) -> tuple[Observation, ...]:
    instrument_ids = [tasks[s].instrument_id for s in SYMBOLS]
    rows = tuple(
        session.scalars(
            select(MarketObservationRecord)
            .where(
                MarketObservationRecord.instrument_id.in_(instrument_ids),
                MarketObservationRecord.provider == provider,
                MarketObservationRecord.timeframe == "1d",
                MarketObservationRecord.session_date
                >= datetime.combine(START, datetime.min.time(), UTC),
                MarketObservationRecord.session_date
                <= datetime.combine(END, datetime.min.time(), UTC),
                MarketObservationRecord.observed_at <= as_of,
                MarketObservationRecord.timestamp <= as_of,
            )
            .order_by(
                MarketObservationRecord.instrument_id,
                MarketObservationRecord.session_date,
                MarketObservationRecord.observed_at,
                MarketObservationRecord.revision,
            )
        )
    )
    latest: dict[tuple[str, date], MarketObservationRecord] = {}
    for row in rows:
        latest[(row.instrument_id, row.session_date.date())] = row
    calendar = XNYSCalendar()
    expected = calendar.sessions_between(START, END)
    for task in tasks.values():
        missing = [day for day in expected if (task.instrument_id, day) not in latest]
        if missing:
            raise ValueError(f"MISSING_COMMON_HISTORY:{task.symbol}:{len(missing)}")
    return tuple(
        _observation(latest[(tasks[symbol].instrument_id, day)])
        for day in expected
        for symbol in SYMBOLS
    )


def _evaluate(
    observations: tuple[Observation, ...],
    universe: PointInTimeUniverse,
    strategy: Any,
    evaluation_times: list[datetime],
) -> tuple[dict[str, object], Any]:
    result = run_multi_asset(
        [row for row in observations if row.timestamp <= evaluation_times[-1]],
        universe,
        strategy,
        INITIAL_CASH,
        COMMISSION_BPS,
        currencies={m.instrument_id: "USD" for m in universe._memberships},
        corporate_actions=(),
        evaluation_start=evaluation_times[0],
        observation_knowledge_mode=ObservationKnowledgeMode.SNAPSHOT_PINNED,
    )
    return _metric_dict(multi_asset_metrics(result, INITIAL_CASH)), result


def run_m7_validation(
    sessions: Callable[[], Session], *, batch_id: str, as_of: datetime, code_sha: str
) -> dict[str, object]:
    cutoff = require_utc(as_of)
    if len(code_sha) != 40 or any(c not in "0123456789abcdef" for c in code_sha):
        raise ValueError("INVALID_CODE_SHA")
    with sessions() as session:
        batch = session.get(MarketBatch, batch_id)
        if (
            batch is None
            or batch.provider != "alpaca:iex"
            or batch.start != date(2025, 8, 17)
            or batch.end != END
        ):
            raise ValueError("UNEXPECTED_BATCH")
        task_rows = tuple(
            session.scalars(
                select(MarketTask).where(
                    MarketTask.batch_id == batch_id,
                    MarketTask.symbol.in_(SYMBOLS),
                )
            )
        )
        tasks = {task.symbol: task for task in task_rows}
        if set(tasks) != set(SYMBOLS) or any(
            task.state != "DONE" or task.coverage != 1 for task in tasks.values()
        ):
            raise ValueError("M7_TASKS_NOT_READY")
        receipts: dict[str, MarketActionReceipt] = {}
        receipt_evidence = {}
        for symbol, task in tasks.items():
            evidence = json.loads(task.evidence_json or "{}")
            screening = evidence.get("screening")
            receipt_id = (
                screening.get("current_action_receipt_id") if isinstance(screening, dict) else None
            )
            receipt = (
                session.get(MarketActionReceipt, receipt_id)
                if isinstance(receipt_id, str)
                else None
            )
            if (
                receipt is None
                or receipt.task_id != task.task_id
                or _database_utc(receipt.received_at) > cutoff
            ):
                raise ValueError("M7_ACTION_RECEIPT_NOT_READY")
            rows = _receipt_rows(receipt, task, batch)
            receipts[symbol] = receipt
            receipt_evidence[symbol] = {
                "receipt_id": receipt.receipt_id,
                "content_hash": receipt.content_hash,
                "received_at": _database_utc(receipt.received_at).isoformat(),
                "in_interval_kinds": sorted(
                    {
                        kind
                        for kind, item in rows
                        if batch.start
                        <= date.fromisoformat(
                            str(
                                item.get("ex_date")
                                or item.get("effective_date")
                                or item.get("process_date")
                            )
                        )
                        <= batch.end
                    }
                ),
            }
        observations = _authoritative_observations(session, tasks, batch.provider, cutoff)

    memberships = _memberships(tasks)
    universe = PointInTimeUniverse(
        UniverseDefinition(
            UNIVERSE_ID,
            "Broad US ETF price-return validation",
            UniverseKind.POINT_IN_TIME_MEMBERSHIP,
        ),
        memberships,
    )
    times = sorted({row.timestamp for row in observations})
    train_end = int(len(times) * float(SPLIT[0]))
    validation_end = train_end + int(len(times) * float(SPLIT[1]))
    if train_end < 127 or validation_end >= len(times):
        raise ValueError("INSUFFICIENT_M7_HISTORY")
    train_times = times[:train_end]
    validation_times = times[train_end:validation_end]
    oos_times = times[validation_end:]

    scored = []
    evaluations = {}
    for config in PARAMETER_BUDGET:
        strategy = CrossSectionalMomentumStrategy(
            lookback=int(config["lookback"]), top_n=int(config["top_n"])
        )
        train_metrics, _ = _evaluate(observations, universe, strategy, train_times)
        validation_metrics, _ = _evaluate(observations, universe, strategy, validation_times)
        key = canonical(config)
        evaluations[key] = {"train": train_metrics, "validation": validation_metrics}
        scored.append((Decimal(str(validation_metrics["sharpe"])), key, config))
    _, _, selected = max(scored, key=lambda item: (item[0], item[1]))
    selected_strategy = CrossSectionalMomentumStrategy(
        lookback=int(selected["lookback"]), top_n=int(selected["top_n"])
    )
    oos_metrics, oos_result = _evaluate(observations, universe, selected_strategy, oos_times)
    benchmark_metrics, benchmark_result = _evaluate(
        observations, universe, EqualWeightMonthly(), oos_times
    )
    observation_manifest = [
        {"id": row.observation_id, "revision": row.revision, "hash": row.source_hash}
        for row in observations
    ]
    membership_manifest = [
        {
            "symbol": symbol,
            "instrument_id": tasks[symbol].instrument_id,
            "valid_from": membership.valid_from.isoformat(),
            "known_at": membership.known_at.isoformat(),
            "source": MEMBERSHIP_EVIDENCE[symbol][1],
        }
        for symbol, membership in zip(SYMBOLS, memberships, strict=True)
    ]
    inputs = {
        "batch_id": batch_id,
        "as_of": cutoff.isoformat(),
        "provider": "alpaca:iex",
        "period": [START.isoformat(), END.isoformat()],
        "return_basis": "RAW_PRICE_RETURN_DIVIDENDS_IGNORED",
        "commission_bps": str(COMMISSION_BPS),
        "initial_cash": str(INITIAL_CASH),
        "split": [str(x) for x in SPLIT],
        "parameter_budget": list(PARAMETER_BUDGET),
        "memberships": membership_manifest,
        "action_receipts": receipt_evidence,
        "observation_hash": digest(observation_manifest),
        "observation_count": len(observation_manifest),
        "code_sha": code_sha,
    }
    report = {
        "schema_version": 1,
        "experiment_id": digest(inputs),
        "inputs": inputs,
        "selected_parameters": selected,
        "pre_oos_evaluations": evaluations,
        "oos": {
            "strategy": oos_metrics,
            "benchmark_equal_weight_monthly": benchmark_metrics,
            "sessions": len(oos_times),
            "strategy_fills": len(oos_result.fills),
            "benchmark_fills": len(benchmark_result.fills),
        },
        "guards": {
            "oos_used_for_selection": False,
            "only_cash_dividends_in_interval": True,
            "corporate_actions_applied": False,
            "paper_deployment_modified": False,
            "live_trading_used": False,
            "research_promotion_allowed": False,
        },
        "limitations": [
            "Price-return validation intentionally ignores cash distributions.",
            (
                "Current REST receipts prove no split/symbol/lifecycle discontinuity "
                "in the interval but do not backdate dividend announcement knowledge."
            ),
            (
                "This M7 validation is infrastructure evidence, not investment approval "
                "or whole-market historical membership."
            ),
        ],
    }
    report["report_hash"] = digest(report)
    return report
