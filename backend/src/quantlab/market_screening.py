"""Versioned current-universe screening; never authorizes a trade or a backtest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.domain import require_utc
from quantlab.market_data import CorporateAction, Observation, causal_adjusted_close
from quantlab.market_data_service import _lock
from quantlab.persistence import Base

POLICY: dict[str, Any] = {
    "version": "us-current-universe-1",
    "minimum_sessions": 127,
    "minimum_coverage": "0.98",
    "minimum_price_usd": "5",
    "minimum_feed_dollar_volume_20": "1000000",
    "volume_scope": "configured_feed_only",
    "price_basis": "causal_adjusted_close",
    "research_eligible": False,
}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def identity(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def evaluate_screen(
    observations: Sequence[Observation],
    actions: Sequence[CorporateAction],
    expected: Sequence[date],
    as_of: datetime,
    readiness_id: str | None,
) -> dict[str, Any]:
    cutoff = require_utc(as_of)
    known = [o for o in observations if o.observed_at <= cutoff and o.timestamp <= cutoff]
    by_day = {
        o.session_date: o
        for o in sorted(
            known, key=lambda x: (x.session_date, x.observed_at, x.revision, x.observation_id)
        )
    }
    ordered = [by_day[d] for d in expected if d in by_day]
    coverage = Decimal(len(ordered)) / len(expected) if expected else Decimal(0)
    reasons = []
    if not readiness_id:
        reasons.append("ACTIONS_NOT_VERIFIED")
    if len(ordered) < POLICY["minimum_sessions"]:
        reasons.append("SHORT_HISTORY")
    if coverage < Decimal(str(POLICY["minimum_coverage"])):
        reasons.append("LOW_COVERAGE")
    if not expected or any(d not in by_day for d in expected[-127:]):
        reasons.append("RECENT_GAPS")
    adjusted = causal_adjusted_close(ordered, actions, cutoff)
    closes = [adjusted[o.session_date] for o in ordered]
    if any(not p.is_finite() or p <= 0 for p in closes):
        reasons.append("INVALID_ADJUSTED_PRICE")
    if not ordered or ordered[-1].close < Decimal(str(POLICY["minimum_price_usd"])):
        reasons.append("LOW_PRICE")
    turnover = (
        (sum((o.close * o.volume for o in ordered[-20:]), Decimal(0)) / 20)
        if len(ordered) >= 20
        else None
    )
    if turnover is None or turnover < Decimal(str(POLICY["minimum_feed_dollar_volume_20"])):
        reasons.append("LOW_FEED_LIQUIDITY")
    sufficient = not reasons
    return {
        "policy": POLICY,
        "policy_hash": identity(POLICY),
        "as_of": cutoff,
        "eligible": sufficient,
        "research_eligible": False,
        "reasons": reasons,
        "coverage": str(coverage),
        "expected_sessions": len(expected),
        "bars": len(ordered),
        "feed_dollar_volume_20": str(turnover) if turnover is not None else None,
        "momentum": str(closes[-1] / closes[-127] - 1) if sufficient else None,
        "trend": str(
            sum(closes[-20:], Decimal(0)) / 20 / (sum(closes[-100:], Decimal(0)) / 100) - 1
        )
        if sufficient
        else None,
        "mean_reversion": str(1 - closes[-1] / (sum(closes[-20:], Decimal(0)) / 20))
        if sufficient
        else None,
        "observation_ids": [o.observation_id for o in ordered],
        "action_readiness_id": readiness_id,
        "actions": [
            {"id": a.action_id, "payload_hash": a.payload_hash, "known_at": a.known_at}
            for a in actions
            if a.known_at <= cutoff and a.effective_at <= cutoff
        ],
    }


class MarketScreenRun(Base):
    __tablename__ = "market_screen_runs"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("market_batches.batch_id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    policy_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    total: Mapped[int] = mapped_column(Integer)
    eligible: Mapped[int] = mapped_column(Integer)


class MarketScreenItem(Base):
    __tablename__ = "market_screen_items"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("market_screen_runs.run_id", ondelete="RESTRICT"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    eligible: Mapped[int] = mapped_column(Integer)
    momentum: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    trend: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    mean_reversion: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    evidence_json: Mapped[str] = mapped_column(Text)


class MarketScreening:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def finalize(self, batch_id: str, now: datetime) -> str | None:
        from quantlab.market_pipeline import MarketBatch, MarketTask, _utc

        now = require_utc(now)
        with self.sessions() as session, session.begin():
            _lock(session, f"market-screen:{batch_id}")
            previous = session.scalar(
                select(MarketScreenRun).where(MarketScreenRun.batch_id == batch_id)
            )
            if previous:
                return previous.run_id
            batch = session.get(MarketBatch, batch_id)
            if batch is None or _utc(batch.created_at) > now:
                raise ValueError("Dávka není dostupná k zadanému času")
            active = session.scalar(
                select(func.count())
                .select_from(MarketTask)
                .where(
                    MarketTask.batch_id == batch_id,
                    MarketTask.state.in_(("PENDING", "RUNNING", "RETRY")),
                )
            )
            if active:
                return None
            query = (
                select(
                    MarketTask.asset_id,
                    MarketTask.symbol,
                    MarketTask.state,
                    MarketTask.evidence_json,
                )
                .where(MarketTask.batch_id == batch_id)
                .order_by(MarketTask.asset_id)
                .execution_options(yield_per=100)
            )

            def items() -> Iterator[dict[str, Any]]:
                for asset_id, symbol, state, evidence_json in session.execute(query):
                    screened = json.loads(evidence_json or "{}").get("screening")
                    if screened is None or screened.get("policy_hash") != identity(POLICY):
                        screened = {
                            "eligible": False,
                            "reasons": [state if state != "DONE" else "SCREENING_NOT_VERIFIED"],
                            "research_eligible": False,
                        }
                    if "as_of" in screened and datetime.fromisoformat(screened["as_of"]) > now:
                        raise ValueError("Evidence screeningu pochází z budoucnosti")
                    yield {"asset_id": asset_id, "symbol": symbol, "evidence": screened}

            content = hashlib.sha256()
            total = eligible_count = 0
            for item in items():
                content.update(canonical(item).encode() + b"\n")
                total += 1
                eligible_count += int(bool(item["evidence"]["eligible"]))
            if not total:
                return None
            digest = content.hexdigest()
            run_id = identity({"batch": batch_id, "policy": POLICY, "content": digest})
            session.add(
                MarketScreenRun(
                    run_id=run_id,
                    batch_id=batch_id,
                    created_at=now,
                    policy_json=canonical(POLICY),
                    content_hash=digest,
                    total=total,
                    eligible=eligible_count,
                )
            )
            session.flush()
            verification = hashlib.sha256()
            for index, item in enumerate(items(), 1):
                verification.update(canonical(item).encode() + b"\n")
                evidence = item["evidence"]
                eligible = bool(evidence["eligible"])
                session.add(
                    MarketScreenItem(
                        run_id=run_id,
                        asset_id=item["asset_id"],
                        symbol=item["symbol"],
                        eligible=int(eligible),
                        momentum=Decimal(evidence["momentum"]) if eligible else None,
                        trend=Decimal(evidence["trend"]) if eligible else None,
                        mean_reversion=Decimal(evidence["mean_reversion"]) if eligible else None,
                        evidence_json=canonical(evidence),
                    )
                )
                if index % 100 == 0:
                    session.flush()
            if verification.hexdigest() != digest:
                raise ValueError("Evidence se během uzavírání dávky změnila")
            return run_id

    def read(
        self, limit: int = 50, offset: int = 0, query: str = "", rank: str = "momentum"
    ) -> dict[str, Any]:
        if (
            not 1 <= limit <= 200
            or offset < 0
            or len(query) > 100
            or rank not in {"momentum", "trend", "mean_reversion"}
        ):
            raise ValueError("Neplatný filtr screeningu")
        with self.sessions() as session:
            run = session.scalar(
                select(MarketScreenRun)
                .order_by(MarketScreenRun.created_at.desc(), MarketScreenRun.run_id)
                .limit(1)
            )
            if not run:
                return {"run": None, "policy": POLICY, "items": [], "matched": 0}
            filters = [MarketScreenItem.run_id == run.run_id]
            if query.strip():
                filters.append(MarketScreenItem.symbol.icontains(query.strip(), autoescape=True))
            matched = session.scalar(
                select(func.count()).select_from(MarketScreenItem).where(*filters)
            )
            rows = session.scalars(
                select(MarketScreenItem)
                .where(*filters)
                .order_by(
                    getattr(MarketScreenItem, rank).desc().nulls_last(),
                    MarketScreenItem.symbol,
                    MarketScreenItem.asset_id,
                )
                .offset(offset)
                .limit(limit)
            )
            return {
                "run": {
                    "id": run.run_id,
                    "batch_id": run.batch_id,
                    "created_at": run.created_at,
                    "total": run.total,
                    "eligible": run.eligible,
                    "content_hash": run.content_hash,
                },
                "policy": json.loads(run.policy_json),
                "matched": matched,
                "items": [
                    {
                        "symbol": row.symbol,
                        "eligible": bool(row.eligible),
                        "momentum": row.momentum,
                        "trend": row.trend,
                        "mean_reversion": row.mean_reversion,
                        "reasons": json.loads(row.evidence_json)["reasons"],
                    }
                    for row in rows
                ],
            }
