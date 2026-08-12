from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import require_utc
from quantlab.market_data import DatasetInvalid, Observation, XNYSCalendar
from quantlab.market_data_service import _observation
from quantlab.multi_asset import MultiAssetResult
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    StrategyDeploymentRecord,
)


@dataclass(frozen=True)
class MultiAssetMetrics:
    total_return: Decimal
    annualized_return: Decimal
    volatility: Decimal
    sharpe: Decimal
    max_drawdown: Decimal
    turnover: Decimal
    time_weighted_exposure: Decimal
    trade_count: int
    total_costs: Decimal


def multi_asset_metrics(result: MultiAssetResult, initial_cash: Decimal) -> MultiAssetMetrics:
    values = [value for _, value in result.equity]
    if not values:
        raise ValueError("Metrics vyžadují neprázdnou equity curve")
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values))]
    total_return = values[-1] / initial_cash - 1
    periods = max(len(returns), 1)
    annualized = Decimal(str((float(values[-1] / initial_cash) ** (252 / periods)) - 1))
    mean = sum(returns, Decimal(0)) / len(returns) if returns else Decimal(0)
    variance = (
        sum(((item - mean) ** 2 for item in returns), Decimal(0)) / len(returns)
        if returns
        else Decimal(0)
    )
    volatility = variance.sqrt() * Decimal(str(math.sqrt(252)))
    sharpe = mean * Decimal(252) / volatility if volatility else Decimal(0)
    peak = values[0]
    max_drawdown = Decimal(0)
    for value in values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    traded = sum((fill.quantity * fill.price for fill in result.fills), Decimal(0))
    turnover = traded / initial_cash
    costs = sum((fill.commission for fill in result.fills), Decimal(0))
    # Equity body jsou konce stejně dlouhých sessions; exposure je vážená intervaly mezi body.
    fill_index = 0
    cash = initial_cash
    weighted, duration = Decimal(0), Decimal(0)
    for index in range(len(result.equity) - 1):
        when, equity = result.equity[index]
        while fill_index < len(result.fills) and result.fills[fill_index].timestamp <= when:
            fill = result.fills[fill_index]
            amount = fill.quantity * fill.price
            cash += (
                amount - fill.commission if fill.side.value == "SELL" else -amount - fill.commission
            )
            fill_index += 1
        seconds = Decimal(str((result.equity[index + 1][0] - when).total_seconds()))
        exposure = max(Decimal(0), min(Decimal(1), (equity - cash) / equity))
        weighted += exposure * seconds
        duration += seconds
    return MultiAssetMetrics(
        total_return,
        annualized,
        volatility,
        sharpe,
        max_drawdown,
        turnover,
        weighted / duration if duration else Decimal(0),
        len(result.fills),
        costs,
    )


class ValidatedCurrentDataAccessor:
    """Oddělený mutable paper pohled; nikdy nevrací research snapshot."""

    def __init__(
        self, session_factory: Callable[[], Session], calendar: XNYSCalendar | None = None
    ) -> None:
        self._sessions = session_factory
        self.calendar = calendar or XNYSCalendar()

    def latest(self, instrument_ids: Sequence[str], now: datetime) -> tuple[Observation, ...]:
        now = require_utc(now)
        candidate = now.date()
        if not self.calendar.is_session(candidate) or now < self.calendar.session_close(candidate):
            candidate = self.calendar.previous_session(candidate)
        with self._sessions() as session:
            rows = tuple(
                session.scalars(
                    select(MarketObservationRecord)
                    .join(
                        MarketDataIngestionRecord,
                        MarketObservationRecord.ingestion_id == MarketDataIngestionRecord.id,
                    )
                    .where(
                        MarketObservationRecord.instrument_id.in_(instrument_ids),
                        MarketObservationRecord.session_date
                        == datetime.combine(candidate, datetime.min.time(), UTC),
                        MarketDataIngestionRecord.status == "SUCCEEDED",
                    )
                    .order_by(
                        MarketObservationRecord.instrument_id,
                        MarketObservationRecord.observed_at.desc(),
                        MarketObservationRecord.revision.desc(),
                    )
                )
            )
        latest: dict[str, MarketObservationRecord] = {}
        for row in rows:
            latest.setdefault(row.instrument_id, row)
        if set(latest) != set(instrument_ids):
            raise DatasetInvalid("Poslední dokončená session má missing nebo stale data")
        return tuple(_observation(latest[item]) for item in sorted(latest))


class DeploymentService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    def approve(self, deployment_id: str, approved_at: datetime) -> None:
        with self._sessions() as session, session.begin():
            row = session.get(StrategyDeploymentRecord, deployment_id, with_for_update=True)
            if row is None or row.status != "PENDING_REVIEW":
                raise ValueError("Deployment neexistuje nebo není čekající na ruční schválení")
            snapshot = session.get(DatasetSnapshotRecord, row.snapshot_id)
            experiment = session.get(ExperimentRecord, row.experiment_id)
            if snapshot is None or snapshot.status != "VALID" or experiment is None:
                raise DatasetInvalid("Deployment evidence není VALID")
            if experiment.snapshot_id != snapshot.snapshot_id:
                raise DatasetInvalid("Experiment a deployment nepoužívají stejný snapshot")
            row.status = "APPROVED"
            row.approved_at = approved_at.astimezone(UTC)
