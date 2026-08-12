from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import require_utc
from quantlab.market_data import DatasetInvalid, Observation, XNYSCalendar
from quantlab.market_data_service import _lock, _observation
from quantlab.multi_asset import STRATEGY_REGISTRY, MultiAssetResult, run_multi_asset
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    StrategyDeploymentRecord,
    StrategyRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.universe import (
    PointInTimeUniverse,
    UniverseDefinition,
    UniverseKind,
    UniverseMembership,
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

    @property
    def risk_adjusted_return(self) -> Decimal:
        return self.sharpe


@dataclass(frozen=True)
class Phase6ExperimentRequest:
    snapshot_id: str
    strategy_name: str
    strategy_version: str
    parameter_configs: tuple[dict[str, object], ...]
    train_fraction: Decimal = Decimal("0.6")
    validation_fraction: Decimal = Decimal("0.2")
    initial_cash: Decimal = Decimal("100000")
    commission_bps: Decimal = Decimal("1")
    seed: int = 42
    code_sha: str | None = None


class Phase6ExperimentRunner:
    """Snapshot-only Phase 6 runner s persistentní, exactly-once OOS identitou."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _code_sha(explicit: str | None) -> str:
        value = explicit
        if value is None:
            try:
                value = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            except (OSError, subprocess.CalledProcessError) as exc:
                raise DatasetInvalid("Git SHA nelze zjistit") from exc
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise DatasetInvalid("Code SHA musí být plný lowercase Git SHA")
        return value

    def run(self, request: Phase6ExperimentRequest) -> ExperimentRecord:
        if not request.parameter_configs:
            raise ValueError("Parameter space nesmí být prázdný")
        if not Decimal(0) < request.train_fraction < Decimal(1) or not Decimal(
            0
        ) < request.validation_fraction < Decimal(1):
            raise ValueError("Chronologický split má neplatné poměry")
        code_sha = self._code_sha(request.code_sha)
        identity_payload = {
            "snapshot_id": request.snapshot_id,
            "strategy": [request.strategy_name, request.strategy_version],
            "parameters": request.parameter_configs,
            "train_fraction": request.train_fraction,
            "validation_fraction": request.validation_fraction,
            "initial_cash": request.initial_cash,
            "commission_bps": request.commission_bps,
            "seed": request.seed,
            "code_sha": code_sha,
        }
        identity = hashlib.sha256(self._canonical(identity_payload).encode()).hexdigest()
        with self._sessions() as session, session.begin():
            _lock(session, f"phase6-experiment:{identity}")
            existing = session.scalar(
                select(ExperimentRecord).where(ExperimentRecord.idempotency_key == identity)
            )
            if existing is not None:
                return existing
            snapshot = session.get(DatasetSnapshotRecord, request.snapshot_id)
            if (
                snapshot is None
                or snapshot.status != "VALID"
                or not snapshot.content_hash
                or session.get(UniverseDefinitionRecord, snapshot.universe_id) is None
            ):
                raise DatasetInvalid("Experiment vyžaduje existující VALID snapshot s universe")
            strategy_row = session.scalar(
                select(StrategyRecord).where(
                    StrategyRecord.strategy_name == request.strategy_name,
                    StrategyRecord.strategy_version == request.strategy_version,
                )
            )
            strategy_type = STRATEGY_REGISTRY.get(request.strategy_name)
            if strategy_row is None or strategy_type is None:
                raise DatasetInvalid("Přesná strategy version není registrována")
            try:
                manifest: object = json.loads(snapshot.manifest_json)
            except json.JSONDecodeError as exc:
                raise DatasetInvalid("Snapshot manifest není validní JSON") from exc
            if not isinstance(manifest, dict):
                raise DatasetInvalid("Snapshot manifest musí být objekt")
            entries = manifest.get("observations")
            if not isinstance(entries, list) or not entries:
                raise DatasetInvalid("Snapshot manifest neobsahuje observations")
            parsed_entries: list[tuple[str, int, str]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise DatasetInvalid("Snapshot manifest není interně konzistentní")
                observation_id = entry.get("id")
                revision = entry.get("revision")
                source_hash = entry.get("hash")
                if (
                    not isinstance(observation_id, str)
                    or not isinstance(revision, int)
                    or isinstance(revision, bool)
                    or revision <= 0
                    or not isinstance(source_hash, str)
                ):
                    raise DatasetInvalid("Snapshot manifest není interně konzistentní")
                parsed_entries.append((observation_id, revision, source_hash))
            ids = [entry[0] for entry in parsed_entries]
            if len(set(ids)) != len(ids):
                raise DatasetInvalid("Snapshot manifest není interně konzistentní")
            rows = tuple(
                session.scalars(
                    select(MarketObservationRecord).where(
                        MarketObservationRecord.observation_id.in_(ids)
                    )
                )
            )
            by_id = {row.observation_id: row for row in rows}
            if set(by_id) != set(ids) or any(
                by_id[observation_id].revision != revision
                or by_id[observation_id].source_hash != source_hash
                for observation_id, revision, source_hash in parsed_entries
            ):
                raise DatasetInvalid("Snapshot manifest odkazuje na změněná nebo chybějící data")
            observations = tuple(_observation(by_id[item]) for item in ids)
            times = sorted({item.timestamp for item in observations})
            train_end = int(len(times) * float(request.train_fraction))
            validation_end = train_end + int(len(times) * float(request.validation_fraction))
            if train_end < 1 or validation_end <= train_end or validation_end >= len(times):
                raise DatasetInvalid("Každá chronologická část musí obsahovat data")
            definition_row = session.get(UniverseDefinitionRecord, snapshot.universe_id)
            assert definition_row is not None
            membership_rows = tuple(
                session.scalars(
                    select(UniverseMembershipRecord).where(
                        UniverseMembershipRecord.universe_id == snapshot.universe_id,
                        UniverseMembershipRecord.known_at <= snapshot.as_of,
                    )
                )
            )
            universe = PointInTimeUniverse(
                UniverseDefinition(
                    definition_row.universe_id,
                    definition_row.name,
                    UniverseKind(definition_row.kind),
                    definition_row.created_at,
                ),
                [
                    UniverseMembership(
                        row.universe_id,
                        row.instrument_id,
                        row.valid_from,
                        row.valid_to,
                        row.known_at,
                    )
                    for row in membership_rows
                ],
            )

            def evaluate(
                config: dict[str, object], selected_times: list[datetime]
            ) -> MultiAssetMetrics:
                strategy = strategy_type(**config)
                if strategy.version != request.strategy_version:
                    raise DatasetInvalid("Implementace strategy version neodpovídá registru")
                result = run_multi_asset(
                    [item for item in observations if item.timestamp in selected_times],
                    universe,
                    strategy,
                    request.initial_cash,
                    request.commission_bps,
                )
                return multi_asset_metrics(result, request.initial_cash)

            scored: list[tuple[Decimal, str, dict[str, object], MultiAssetMetrics]] = []
            for config in request.parameter_configs:
                evaluate(config, times[:train_end])
                validation = evaluate(config, times[train_end:validation_end])
                scored.append(
                    (validation.risk_adjusted_return, self._canonical(config), config, validation)
                )
            _, _, selected, _ = max(scored, key=lambda item: (item[0], item[1]))
            oos = evaluate(selected, times[validation_end:])
            experiment = ExperimentRecord(
                id=identity,
                idempotency_key=identity,
                created_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                status="COMPLETED",
                snapshot_id=snapshot.snapshot_id,
                strategy_identity=strategy_row.strategy_identity,
                strategy_name=request.strategy_name,
                strategy_version=request.strategy_version,
                parameter_space_id=hashlib.sha256(
                    self._canonical(request.parameter_configs).encode()
                ).hexdigest(),
                decision="RESEARCH_ONLY",
                total_return=float(oos.total_return),
                annualized_return=float(oos.annualized_return),
                volatility=float(oos.volatility),
                sharpe=float(oos.risk_adjusted_return),
                max_drawdown=float(oos.max_drawdown),
                turnover=float(oos.turnover),
                time_weighted_exposure=float(oos.time_weighted_exposure),
                trade_count=oos.trade_count,
                total_costs=float(oos.total_costs),
                code_sha=code_sha,
                seed=request.seed,
                cost_model_json=self._canonical(
                    {"commission_bps": request.commission_bps, "slippage_model": "none"}
                ),
                selected_parameters_json=self._canonical(selected),
                config_json=self._canonical(identity_payload),
                result_json=self._canonical({"stage": "OOS", "metrics": oos.__dict__}),
            )
            session.add(experiment)
            session.flush()
            return experiment


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
    if len(result.exposure) != len(result.equity) or any(
        exposure_time != equity_time
        for (exposure_time, _), (equity_time, _) in zip(result.exposure, result.equity, strict=True)
    ):
        raise ValueError("Výsledek neobsahuje konzistentní portfolio exposure series")
    # Exposure vzniká přímo ze stavu portfolia, takže zahrnuje i dividendovou hotovost.
    weighted, duration = Decimal(0), Decimal(0)
    for index in range(len(result.equity) - 1):
        when, _ = result.equity[index]
        seconds = Decimal(str((result.equity[index + 1][0] - when).total_seconds()))
        weighted += result.exposure[index][1] * seconds
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
            if experiment.status != "COMPLETED" or experiment.decision != "PAPER_CANDIDATE":
                raise DatasetInvalid("Deployment vyžaduje dokončený PAPER_CANDIDATE experiment")
            row.status = "APPROVED"
            row.approved_at = approved_at.astimezone(UTC)
