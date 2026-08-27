from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import AuditEventType, Bar, require_utc
from quantlab.market_data import (
    CorporateAction,
    CorporateActionKind,
    DatasetInvalid,
    Observation,
    XNYSCalendar,
    causal_adjusted_close,
)
from quantlab.market_data_service import _database_utc, _lock, _observation
from quantlab.multi_asset import (
    STRATEGY_REGISTRY,
    MultiAssetResult,
    RebalanceFrequency,
    run_multi_asset,
)
from quantlab.persistence import (
    CorporateActionRecord,
    DatasetSnapshotRecord,
    ExperimentRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    StrategyDeploymentRecord,
    StrategyRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase4 import (
    AuditEventRecord,
    PaperAccountRecord,
    PositionRecord,
    TradingCycleRecord,
    TradingCycleService,
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


@dataclass(frozen=True)
class Phase6ExperimentReplay:
    """Deterministický výsledek společné Phase 6 research evaluace."""

    selected_parameters: dict[str, object]
    train: MultiAssetMetrics
    validation: MultiAssetMetrics
    oos: MultiAssetMetrics
    oos_equity: tuple[tuple[datetime, Decimal], ...]
    oos_returns: tuple[Decimal, ...]
    oos_sessions: tuple[datetime, ...]


@dataclass(frozen=True)
class PaperExecutionTiming:
    """Auditovatelná hranice close-derived rozhodnutí a následujícího open."""

    signal_session: date
    decision_time: datetime
    execution_session: date
    execution_time: datetime


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
            git = shutil.which("git")
            if git is None:
                raise DatasetInvalid("Git executable nelze zjistit")
            try:
                value = subprocess.run(
                    [git, "rev-parse", "HEAD"],
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
        result = self._execute(request, persist=True)
        if not isinstance(result, ExperimentRecord):
            raise TypeError("Persistovaný experiment vrátil neplatný typ")
        return result

    def replay(self, request: Phase6ExperimentRequest) -> Phase6ExperimentReplay:
        result = self._execute(request, persist=False)
        if not isinstance(result, Phase6ExperimentReplay):
            raise TypeError("Replay experimentu vrátil neplatný typ")
        return result

    def _execute(
        self, request: Phase6ExperimentRequest, *, persist: bool
    ) -> ExperimentRecord | Phase6ExperimentReplay:
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
            if persist:
                existing = session.scalar(
                    select(ExperimentRecord).where(ExperimentRecord.idempotency_key == identity)
                )
                if existing is not None:
                    session.expunge(existing)
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
            if manifest.get("schema_version") != "3":
                raise DatasetInvalid("Snapshot manifest má nepodporovanou schema version")
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
            action_entries = manifest.get("corporate_actions")
            if not isinstance(action_entries, list):
                raise DatasetInvalid("Snapshot manifest neobsahuje immutable corporate actions")
            try:
                corporate_actions = tuple(
                    CorporateAction(
                        action_id=entry["action_id"],
                        instrument_id=entry["instrument_id"],
                        kind=CorporateActionKind(entry["kind"]),
                        effective_at=datetime.fromisoformat(entry["effective_at"]),
                        known_at=datetime.fromisoformat(entry["known_at"]),
                        value=Decimal(entry["value"]) if entry["value"] is not None else None,
                        new_symbol=entry["new_symbol"],
                    )
                    for entry in action_entries
                    if isinstance(entry, dict)
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise DatasetInvalid("Snapshot corporate actions nejsou konzistentní") from exc
            if len(corporate_actions) != len(action_entries):
                raise DatasetInvalid("Snapshot corporate actions nejsou konzistentní")
            action_ids = [item.action_id for item in corporate_actions]
            if len(set(action_ids)) != len(action_ids):
                raise DatasetInvalid("Snapshot corporate actions obsahují duplicity")
            persisted_actions = {
                item.action_id: item
                for item in session.scalars(
                    select(CorporateActionRecord).where(
                        CorporateActionRecord.action_id.in_(action_ids)
                    )
                )
            }
            if set(persisted_actions) != set(action_ids) or any(
                persisted_actions[item.action_id].instrument_id != item.instrument_id
                or persisted_actions[item.action_id].kind != item.kind.value
                or persisted_actions[item.action_id].effective_at != item.effective_at
                or persisted_actions[item.action_id].known_at != item.known_at
                or persisted_actions[item.action_id].value
                != (str(item.value) if item.value is not None else None)
                or persisted_actions[item.action_id].new_symbol != item.new_symbol
                for item in corporate_actions
            ):
                raise DatasetInvalid(
                    "Snapshot corporate actions neodpovídají persistentní evidence"
                )
            immutable_content = {"observations": entries, "corporate_actions": action_entries}
            manifest_hash = hashlib.sha256(self._canonical(immutable_content).encode()).hexdigest()
            if manifest_hash != snapshot.content_hash:
                raise DatasetInvalid("Snapshot manifest neodpovídá uloženému content hash")
            times = sorted({item.timestamp for item in observations})
            train_end = int(len(times) * float(request.train_fraction))
            validation_end = train_end + int(len(times) * float(request.validation_fraction))
            if train_end < 1 or validation_end <= train_end or validation_end >= len(times):
                raise DatasetInvalid("Každá chronologická část musí obsahovat data")
            definition_row = session.get(UniverseDefinitionRecord, snapshot.universe_id)
            if definition_row is None:
                raise RuntimeError("Universe definition pro snapshot nebyla nalezena")
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
                    _database_utc(definition_row.created_at),
                ),
                [
                    UniverseMembership(
                        row.universe_id,
                        row.instrument_id,
                        _database_utc(row.valid_from),
                        _database_utc(row.valid_to) if row.valid_to is not None else None,
                        _database_utc(row.known_at),
                    )
                    for row in membership_rows
                ],
            )
            instrument_ids = {item.instrument_id for item in observations}
            instrument_rows = tuple(
                session.scalars(
                    select(InstrumentRecord).where(
                        InstrumentRecord.instrument_id.in_(instrument_ids)
                    )
                )
            )
            currencies = {row.instrument_id: row.currency for row in instrument_rows}
            if set(currencies) != instrument_ids:
                raise DatasetInvalid("Snapshot odkazuje na chybějící instrument metadata")

            def evaluate(
                config: dict[str, object], selected_times: list[datetime]
            ) -> tuple[MultiAssetMetrics, MultiAssetResult]:
                strategy = strategy_type(**config)
                if strategy.version != request.strategy_version:
                    raise DatasetInvalid("Implementace strategy version neodpovídá registru")
                evaluation_start = selected_times[0]
                evaluation_end = selected_times[-1]
                result = run_multi_asset(
                    [item for item in observations if item.timestamp <= evaluation_end],
                    universe,
                    strategy,
                    request.initial_cash,
                    request.commission_bps,
                    currencies=currencies,
                    corporate_actions=corporate_actions,
                    evaluation_start=evaluation_start,
                )
                return multi_asset_metrics(result, request.initial_cash), result

            scored: list[tuple[Decimal, str, dict[str, object], MultiAssetMetrics]] = []
            for config in request.parameter_configs:
                evaluate(config, times[:train_end])
                validation, _ = evaluate(config, times[train_end:validation_end])
                scored.append(
                    (validation.risk_adjusted_return, self._canonical(config), config, validation)
                )
            _, _, selected, _ = max(scored, key=lambda item: (item[0], item[1]))
            train, _ = evaluate(selected, times[:train_end])
            validation, _ = evaluate(selected, times[train_end:validation_end])
            oos, oos_result = evaluate(selected, times[validation_end:])
            oos_equity = tuple(oos_result.equity)
            oos_returns = tuple(
                oos_equity[index][1] / oos_equity[index - 1][1] - 1
                for index in range(1, len(oos_equity))
            )
            replay = Phase6ExperimentReplay(
                selected,
                train,
                validation,
                oos,
                oos_equity,
                oos_returns,
                tuple(when for when, _ in oos_equity),
            )
            if not persist:
                return replay
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
                result_json=self._canonical(
                    {
                        "stage": "OOS",
                        "metrics": oos.__dict__,
                        "equity": oos_equity,
                        "returns": oos_returns,
                        "sessions": replay.oos_sessions,
                    }
                ),
            )
            session.add(experiment)
            session.flush()
            session.expunge(experiment)
            return experiment


class Phase6ExperimentReplayService:
    """Replay používající tutéž authoritative implementaci jako experiment runner."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._runner = Phase6ExperimentRunner(session_factory)

    def replay(self, request: Phase6ExperimentRequest) -> Phase6ExperimentReplay:
        return self._runner.replay(request)


def _control_plane_audit(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: dict[str, str],
    reason: str,
    correlation_id: str | None,
    evidence: dict[str, object],
) -> None:
    if not reason.strip():
        raise ValueError("Audit reason nesmí být prázdný")
    session.add(
        AuditEventRecord(
            id=str(uuid4()),
            timestamp=datetime.now(UTC),
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            trading_cycle_id=None,
            correlation_id=(correlation_id or str(uuid4()))[:64],
            payload_json=json.dumps(
                {"actor": actor, "reason": reason, **evidence}, sort_keys=True, default=str
            ),
        )
    )


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
        if not instrument_ids or len(set(instrument_ids)) != len(instrument_ids):
            raise DatasetInvalid("Požadavek na current data musí obsahovat unikátní instrumenty")
        candidate = self.calendar.latest_completed_session(now)
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
                        MarketObservationRecord.observed_at <= now,
                        MarketObservationRecord.timestamp <= now,
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
        ingestion_statuses: dict[str, str] = {}
        with self._sessions() as session:
            ingestion_statuses = {
                item.id: item.status
                for item in session.scalars(
                    select(MarketDataIngestionRecord).where(
                        MarketDataIngestionRecord.id.in_(
                            row.ingestion_id for row in latest.values()
                        )
                    )
                )
            }
        if any(ingestion_statuses.get(row.ingestion_id) != "SUCCEEDED" for row in latest.values()):
            raise DatasetInvalid("Nejnovější current data nepocházejí z úspěšné ingestion")
        return tuple(_observation(latest[item]) for item in sorted(latest))

    def for_execution_session(
        self, instrument_ids: Sequence[str], session_date: date, now: datetime
    ) -> tuple[Observation, ...]:
        """Vrátí raw data přesně určené session, pouze pokud už byla pozorována."""
        knowledge_cutoff = require_utc(now)
        if not instrument_ids or len(set(instrument_ids)) != len(instrument_ids):
            raise DatasetInvalid("Požadavek na current data musí obsahovat unikátní instrumenty")
        if not self.calendar.is_session(session_date):
            raise DatasetInvalid("Executable session není platná XNYS session")
        if knowledge_cutoff < self.calendar.session_open(session_date):
            raise DatasetInvalid("Executable session ještě nezačala")
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
                        == datetime.combine(session_date, datetime.min.time(), UTC),
                        MarketObservationRecord.timeframe == "open",
                        MarketObservationRecord.timestamp
                        == self.calendar.session_open(session_date),
                        MarketObservationRecord.observed_at <= knowledge_cutoff,
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
            raise DatasetInvalid(
                "Executable session nemá dostupný raw open pro všechny instrumenty"
            )
        with self._sessions() as session:
            ingestion_statuses = {
                item.id: item.status
                for item in session.scalars(
                    select(MarketDataIngestionRecord).where(
                        MarketDataIngestionRecord.id.in_(
                            row.ingestion_id for row in latest.values()
                        )
                    )
                )
            }
        if any(ingestion_statuses.get(row.ingestion_id) != "SUCCEEDED" for row in latest.values()):
            raise DatasetInvalid("Executable data nepocházejí z úspěšné ingestion")
        return tuple(_observation(latest[item]) for item in sorted(latest))

    def history(
        self,
        instrument_ids: Sequence[str],
        now: datetime,
        lookback: int,
        *,
        before_session: date | None = None,
        known_at: datetime | None = None,
    ) -> dict[str, tuple[Observation, ...]]:
        """Vrátí autoritativní revize; volitelně pouze před executable session."""
        if lookback < 1:
            raise DatasetInvalid("Strategy lookback musí být kladný")
        current = self.latest(instrument_ids, now)
        cutoff = current[0].session_date
        knowledge_cutoff = require_utc(known_at or now)
        if before_session is not None:
            cutoff = self.calendar.previous_session(before_session)
        with self._sessions() as session:
            rows = tuple(
                session.scalars(
                    select(MarketObservationRecord)
                    .join(MarketDataIngestionRecord)
                    .where(
                        MarketObservationRecord.instrument_id.in_(instrument_ids),
                        MarketObservationRecord.session_date
                        <= datetime.combine(cutoff, datetime.min.time(), UTC),
                        MarketObservationRecord.observed_at <= knowledge_cutoff,
                        MarketObservationRecord.timestamp <= knowledge_cutoff,
                        MarketDataIngestionRecord.status == "SUCCEEDED",
                    )
                    .order_by(
                        MarketObservationRecord.instrument_id,
                        MarketObservationRecord.session_date.desc(),
                        MarketObservationRecord.observed_at.desc(),
                        MarketObservationRecord.revision.desc(),
                    )
                )
            )
        selected: dict[str, list[Observation]] = {item: [] for item in instrument_ids}
        seen: set[tuple[str, object]] = set()
        for row in rows:
            key = (row.instrument_id, row.session_date)
            if key in seen or len(selected[row.instrument_id]) >= lookback:
                continue
            seen.add(key)
            selected[row.instrument_id].append(_observation(row))
        return {key: tuple(reversed(value)) for key, value in selected.items()}


class Phase6EligibilityService:
    """Explicitní strukturální eligibility gate; nikdy se nespouští z research runneru."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    def promote(
        self,
        experiment_id: str,
        *,
        actor: dict[str, str] | None = None,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> ExperimentRecord:
        with self._sessions() as session, session.begin():
            row = session.get(ExperimentRecord, experiment_id, with_for_update=True)
            if row is None:
                raise DatasetInvalid("Experiment neexistuje")
            DeploymentService.validate_experiment(session, row)
            if row.decision not in {"RESEARCH_ONLY", "PAPER_CANDIDATE"}:
                raise DatasetInvalid("Experiment je v nekonzistentním decision state")
            changed = row.decision == "RESEARCH_ONLY"
            row.decision = "PAPER_CANDIDATE"
            if changed and actor is not None and reason is not None:
                _control_plane_audit(
                    session,
                    "PHASE6_EXPERIMENT_PROMOTED",
                    "experiment",
                    row.id,
                    actor,
                    reason,
                    correlation_id,
                    {"resulting_decision": row.decision},
                )
            session.flush()
            session.expunge(row)
            return row


class DeploymentService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    def create(
        self,
        experiment_id: str,
        paper_account_id: str,
        *,
        currency: str = "USD",
        timeframe: str = "1d",
        created_at: datetime | None = None,
        actor: dict[str, str] | None = None,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> StrategyDeploymentRecord:
        created_at = require_utc(created_at or datetime.now(UTC))
        with self._sessions() as session, session.begin():
            experiment = session.get(ExperimentRecord, experiment_id)
            if experiment is None or experiment.decision != "PAPER_CANDIDATE":
                raise DatasetInvalid("Deployment lze vytvořit pouze z PAPER_CANDIDATE")
            snapshot, _, _ = self.validate_experiment(session, experiment)
            parameters = self._evidence(experiment.selected_parameters_json, "parameters")
            identity = hashlib.sha256(
                self._canonical(
                    {
                        "experiment_id": experiment.id,
                        "account_id": paper_account_id,
                        "currency": currency,
                        "timeframe": timeframe,
                    }
                ).encode()
            ).hexdigest()
            existing = session.get(StrategyDeploymentRecord, identity)
            if existing is not None:
                session.expunge(existing)
                return existing
            row = StrategyDeploymentRecord(
                deployment_id=identity,
                created_at=created_at,
                approved_at=None,
                status="PENDING_REVIEW",
                strategy_name=experiment.strategy_name,
                strategy_version=experiment.strategy_version,
                parameters_json=self._canonical(parameters),
                universe_id=snapshot.universe_id,
                paper_account_id=paper_account_id,
                experiment_id=experiment.id,
                snapshot_id=snapshot.snapshot_id,
                currency=currency,
                timeframe=timeframe,
            )
            session.add(row)
            if actor is not None and reason is not None:
                _control_plane_audit(
                    session,
                    "PHASE6_DEPLOYMENT_CREATED",
                    "deployment",
                    row.deployment_id,
                    actor,
                    reason,
                    correlation_id,
                    {"experiment_id": experiment.id, "status": row.status},
                )
            session.flush()
            session.expunge(row)
            return row

    def approve(
        self,
        deployment_id: str,
        approved_at: datetime,
        *,
        actor: dict[str, str] | None = None,
        reason: str | None = None,
        correlation_id: str | None = None,
        allow_already_approved: bool = False,
    ) -> None:
        approved_at = require_utc(approved_at)
        with self._sessions() as session, session.begin():
            row = session.get(StrategyDeploymentRecord, deployment_id, with_for_update=True)
            already_approved = row is not None and row.status == "APPROVED"
            if row is None or (
                row.status != "PENDING_REVIEW" and not (already_approved and allow_already_approved)
            ):
                raise ValueError("Deployment neexistuje nebo není čekající na ruční schválení")
            experiment = session.get(ExperimentRecord, row.experiment_id)
            if experiment is None:
                raise DatasetInvalid("Deployment experiment neexistuje")
            snapshot, strategy, parameters = self.validate_experiment(session, experiment)
            if experiment.decision != "PAPER_CANDIDATE":
                raise DatasetInvalid("Deployment vyžaduje dokončený PAPER_CANDIDATE experiment")
            account = session.get(PaperAccountRecord, row.paper_account_id)
            if (
                experiment.snapshot_id != row.snapshot_id
                or row.universe_id != snapshot.universe_id
                or row.strategy_name != experiment.strategy_name
                or row.strategy_version != experiment.strategy_version
                or strategy is None
                or strategy.strategy_name != row.strategy_name
                or strategy.strategy_version != row.strategy_version
            ):
                raise DatasetInvalid("Deployment strategy, universe nebo lineage nesouhlasí")
            if account is None or row.currency != "USD" or account.base_currency != row.currency:
                raise DatasetInvalid("Deployment vyžaduje existující kompatibilní paper účet")
            if row.timeframe != "1d" or snapshot.timeframe != row.timeframe:
                raise DatasetInvalid("Deployment timeframe není podporován")
            if self._evidence(row.parameters_json, "deployment parameters") != parameters:
                raise DatasetInvalid("Deployment parameters neodpovídají experiment evidence")
            if already_approved:
                return
            row.status = "APPROVED"
            row.approved_at = approved_at
            if actor is not None and reason is not None:
                _control_plane_audit(
                    session,
                    "PHASE6_DEPLOYMENT_APPROVED",
                    "deployment",
                    row.deployment_id,
                    actor,
                    reason,
                    correlation_id,
                    {"experiment_id": row.experiment_id, "status": row.status},
                )

    @classmethod
    def validate_experiment(
        cls, session: Session, experiment: ExperimentRecord
    ) -> tuple[DatasetSnapshotRecord, StrategyRecord, dict[str, object]]:
        if experiment.status != "COMPLETED" or experiment.failure_kind is not None:
            raise DatasetInvalid("Experiment není úspěšně COMPLETED")
        if not experiment.snapshot_id:
            raise DatasetInvalid("Experiment nemá snapshot lineage")
        snapshot = session.get(DatasetSnapshotRecord, experiment.snapshot_id)
        if snapshot is None or snapshot.status != "VALID":
            raise DatasetInvalid("Experiment snapshot neexistuje nebo není VALID")
        if (
            not experiment.strategy_identity
            or not experiment.strategy_name
            or not experiment.strategy_version
        ):
            raise DatasetInvalid("Experiment nemá přesnou strategy identity")
        strategy = session.get(StrategyRecord, experiment.strategy_identity)
        strategy_type = STRATEGY_REGISTRY.get(experiment.strategy_name)
        if (
            strategy is None
            or strategy_type is None
            or strategy.strategy_name != experiment.strategy_name
            or strategy.strategy_version != experiment.strategy_version
        ):
            raise DatasetInvalid("Experiment strategy identity neodpovídá registru")
        if experiment.code_sha is None:
            raise DatasetInvalid("Experiment nemá code SHA")
        cls._valid_sha(experiment.code_sha)
        cls._evidence(experiment.cost_model_json, "cost model")
        parameters = cls._evidence(experiment.selected_parameters_json, "parameters")
        try:
            implementation = strategy_type(**parameters)
        except (TypeError, ValueError) as exc:
            raise DatasetInvalid("Experiment parameters nejsou pro strategii platné") from exc
        if (
            implementation.name != experiment.strategy_name
            or implementation.version != experiment.strategy_version
        ):
            raise DatasetInvalid("Implementace strategy version neodpovídá experimentu")
        try:
            result: object = json.loads(experiment.result_json)
        except json.JSONDecodeError as exc:
            raise DatasetInvalid("Experiment nemá validní OOS result") from exc
        if (
            not isinstance(result, dict)
            or result.get("stage") != "OOS"
            or not isinstance(result.get("metrics"), dict)
        ):
            raise DatasetInvalid("Experiment nemá OOS result")
        return snapshot, strategy, parameters

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _valid_sha(value: str) -> None:
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise DatasetInvalid("Experiment code SHA není platná lineage")

    @staticmethod
    def _evidence(value: str | None, name: str) -> dict[str, object]:
        if value is None:
            raise DatasetInvalid(f"Experiment nemá {name} evidence")
        try:
            parsed: object = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DatasetInvalid(f"Experiment má neplatnou {name} evidence") from exc
        if not isinstance(parsed, dict):
            raise DatasetInvalid(f"Experiment má neplatnou {name} evidence")
        return parsed


class Phase6PaperExecutionService:
    """Autoritativní Phase 6 orchestrace nad jedinou ekonomickou cestou z Phase 4."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        current_data: ValidatedCurrentDataAccessor,
        trading_cycle: TradingCycleService,
    ) -> None:
        self._sessions = session_factory
        self.current_data = current_data
        self.trading_cycle = trading_cycle

    def _ensure_cycle_lineage(
        self,
        monitoring_id: str,
        deployment_id: str,
        cycle_id: str,
        session_date: date,
        linked_at: datetime,
    ) -> None:
        from quantlab.phase7 import PaperDeploymentCycleRecord

        lineage_id = hashlib.sha256(f"{monitoring_id}:{cycle_id}".encode()).hexdigest()
        with self._sessions() as session, session.begin():
            _lock(session, f"phase7-cycle-lineage:{cycle_id}")
            by_cycle = session.scalar(
                select(PaperDeploymentCycleRecord).where(
                    PaperDeploymentCycleRecord.trading_cycle_id == cycle_id
                )
            )
            if by_cycle is not None:
                if (
                    by_cycle.monitoring_id != monitoring_id
                    or by_cycle.deployment_id != deployment_id
                    or by_cycle.session_date != session_date
                ):
                    raise DatasetInvalid("Trading cycle má konfliktní monitoring lineage")
                return
            session.merge(
                PaperDeploymentCycleRecord(
                    lineage_id=lineage_id,
                    monitoring_id=monitoring_id,
                    deployment_id=deployment_id,
                    trading_cycle_id=cycle_id,
                    session_date=session_date,
                    linked_at=linked_at,
                )
            )

    def run(
        self,
        deployment_id: str,
        now: datetime,
        *,
        execution_intent_time: datetime | None = None,
    ) -> str:
        as_of = require_utc(now)
        if execution_intent_time is None:
            timing = self.execution_timing(self.current_data.calendar, as_of)
        else:
            intended_open = require_utc(execution_intent_time)
            intended_session = self.current_data.calendar.session_for_timestamp(intended_open)
            if (
                intended_session is None
                or self.current_data.calendar.session_open(intended_session) != intended_open
            ):
                raise DatasetInvalid("Persistent execution intent nemá platný XNYS open")
            signal_session = self.current_data.calendar.previous_session(intended_session)
            timing = PaperExecutionTiming(
                signal_session,
                self.current_data.calendar.session_close(signal_session),
                intended_session,
                intended_open,
            )
        executable_session = timing.execution_session
        execution_time = timing.execution_time
        if execution_intent_time is None and as_of != execution_time:
            state = "ještě nezačala" if as_of < execution_time else "už začala"
            raise DatasetInvalid(
                f"Signal je připraven, ale následující executable session {state}; "
                "bez persistentního intentu nelze zpětně fillovat její open"
            )
        if execution_intent_time is not None and (
            as_of < execution_time
            or as_of >= self.current_data.calendar.session_close(executable_session)
        ):
            raise DatasetInvalid("Persistent execution intent je před open nebo již missed")
        decision_time = timing.decision_time
        with self._sessions() as session:
            # Phase 7 je samostatná observation/control brána. Import je lokální, aby
            # monitoring mohl znovu použít validační Phase 6 služby bez importního cyklu.
            from quantlab.phase7 import OPEN_STATES, PaperMonitoringRunRecord

            deployment = session.get(StrategyDeploymentRecord, deployment_id)
            if deployment is None or deployment.status != "APPROVED":
                raise DatasetInvalid("Paper execution vyžaduje APPROVED deployment")
            monitoring = session.scalar(
                select(PaperMonitoringRunRecord).where(
                    PaperMonitoringRunRecord.deployment_id == deployment_id,
                    PaperMonitoringRunRecord.state.in_(OPEN_STATES),
                )
            )
            if monitoring is None or monitoring.state != "ACTIVE":
                raise DatasetInvalid("Paper execution vyžaduje právě jeden ACTIVE monitoring run")
            experiment = session.get(ExperimentRecord, deployment.experiment_id)
            if experiment is None:
                raise DatasetInvalid("Deployment experiment neexistuje")
            snapshot, strategy_row, selected = DeploymentService.validate_experiment(
                session, experiment
            )
            account = session.get(PaperAccountRecord, deployment.paper_account_id)
            if (
                experiment.decision != "PAPER_CANDIDATE"
                or deployment.snapshot_id != snapshot.snapshot_id
                or deployment.universe_id != snapshot.universe_id
                or deployment.strategy_name != strategy_row.strategy_name
                or deployment.strategy_version != strategy_row.strategy_version
                or DeploymentService._evidence(deployment.parameters_json, "deployment parameters")
                != selected
                or account is None
                or deployment.currency != "USD"
                or account.base_currency != "USD"
                or deployment.timeframe != "1d"
                or snapshot.timeframe != "1d"
            ):
                raise DatasetInvalid("Approved deployment evidence se od schválení změnila")
            definition = session.get(UniverseDefinitionRecord, deployment.universe_id)
            if definition is None or definition.kind != UniverseKind.POINT_IN_TIME_MEMBERSHIP:
                raise DatasetInvalid("Paper execution vyžaduje podporovaný PIT universe")
            memberships = tuple(
                session.scalars(
                    select(UniverseMembershipRecord).where(
                        UniverseMembershipRecord.universe_id == deployment.universe_id,
                        UniverseMembershipRecord.known_at <= decision_time,
                        UniverseMembershipRecord.valid_from <= decision_time,
                    )
                )
            )
            eligible = tuple(
                sorted(
                    {
                        item.instrument_id
                        for item in memberships
                        if item.valid_to is None or decision_time < item.valid_to
                    }
                )
            )
            if not eligible:
                raise DatasetInvalid("PIT universe nemá v decision time eligible instrument")
            held = {
                item.instrument_id
                for item in session.scalars(
                    select(PositionRecord).where(
                        PositionRecord.account_id == deployment.paper_account_id,
                        PositionRecord.quantity > 0,
                    )
                )
            }
            execution_instruments = tuple(sorted(set(eligible) | held))
            if any(len(instrument_id) > 40 for instrument_id in execution_instruments):
                raise DatasetInvalid("Paper execution instrument ID překračuje Phase 4 limit")
            instruments = {
                item.instrument_id: item
                for item in session.scalars(
                    select(InstrumentRecord).where(
                        InstrumentRecord.instrument_id.in_(execution_instruments)
                    )
                )
            }
            if set(instruments) != set(execution_instruments) or any(
                item.exchange != "XNYS"
                or item.calendar != "XNYS"
                or item.currency != "USD"
                or item.asset_type != "EQUITY"
                for item in instruments.values()
            ):
                raise DatasetInvalid("Current universe není podporovaný USD/XNYS equity scope")
            action_rows = tuple(
                session.scalars(
                    select(CorporateActionRecord).where(
                        CorporateActionRecord.instrument_id.in_(eligible),
                        CorporateActionRecord.known_at <= decision_time,
                    )
                )
            )
        strategy_type = STRATEGY_REGISTRY.get(deployment.strategy_name)
        if strategy_type is None:
            raise DatasetInvalid("Deployment strategy není v allowlisted registru")
        try:
            strategy = strategy_type(**selected)
        except (TypeError, ValueError) as exc:
            raise DatasetInvalid("Deployment parameters nejsou pro strategii platné") from exc
        if (
            strategy.version != deployment.strategy_version
            or strategy.name != deployment.strategy_name
        ):
            raise DatasetInvalid("Runtime strategy identity neodpovídá deploymentu")
        latest = self.current_data.for_execution_session(
            execution_instruments, executable_session, as_of
        )
        expected_sessions: list[date] = []
        history_session = executable_session
        for _ in range(strategy.required_lookback):
            history_session = self.current_data.calendar.previous_session(history_session)
            expected_sessions.append(history_session)
        expected_sessions.reverse()
        signal_time = self.current_data.calendar.session_close(expected_sessions[-1])
        history = self.current_data.history(
            eligible,
            decision_time,
            strategy.required_lookback,
            before_session=executable_session,
            known_at=signal_time,
        )
        if any(
            len(values) != strategy.required_lookback
            or [item.session_date for item in values] != expected_sessions
            for values in history.values()
        ):
            raise DatasetInvalid("Current strategy history je neúplná")
        actions = tuple(
            CorporateAction(
                item.action_id,
                item.instrument_id,
                CorporateActionKind(item.kind),
                _database_utc(item.effective_at),
                _database_utc(item.known_at),
                Decimal(item.value) if item.value is not None else None,
                item.new_symbol,
            )
            for item in action_rows
            if _database_utc(item.known_at) <= signal_time
        )
        signal_prices = {
            instrument: tuple(
                causal_adjusted_close(values, actions, decision_time)[item.session_date]
                for item in values
            )
            for instrument, values in history.items()
        }
        from quantlab.multi_asset import StrategyContext

        strategy_id = f"phase6:{deployment.deployment_id}"
        with self._sessions() as session:
            previous_cycle = session.scalar(
                select(TradingCycleRecord)
                .where(
                    TradingCycleRecord.account_id == deployment.paper_account_id,
                    TradingCycleRecord.strategy_id == strategy_id,
                    TradingCycleRecord.status == "COMPLETED",
                )
                .order_by(TradingCycleRecord.session_date.desc())
                .limit(1)
            )
        if previous_cycle is not None and previous_cycle.session_date == executable_session:
            self._ensure_cycle_lineage(
                monitoring.monitoring_id,
                deployment.deployment_id,
                previous_cycle.id,
                previous_cycle.session_date,
                as_of,
            )
            return previous_cycle.id
        if previous_cycle is not None and not self._rebalance_due(
            executable_session,
            previous_cycle.session_date,
            strategy.rebalance_frequency,
        ):
            raise DatasetInvalid("Deployment dnes nemá povolený rebalance")
        target = strategy.generate_targets(
            StrategyContext(signal_time, history, eligible, signal_prices)
        )
        if any(instrument not in eligible for instrument, _ in target.weights):
            raise DatasetInvalid("Strategy vytvořila target mimo deployment PIT universe")
        bars = [
            Bar(
                item.instrument_id,
                execution_time,
                item.open,
                item.high,
                item.low,
                item.close,
                item.volume,
                signal_prices[item.instrument_id][-1]
                if item.instrument_id in signal_prices
                else item.open,
                item.provider,
                item.timeframe,
            )
            for item in latest
        ]
        target_weights = {instrument: Decimal("0") for instrument in held}
        target_weights.update(dict(target.weights))
        from quantlab.phase7 import PaperCorporateActionService

        PaperCorporateActionService(self._sessions).apply(account.id, as_of)
        with self._sessions() as session:
            monitoring = session.get(PaperMonitoringRunRecord, monitoring.monitoring_id)
            if monitoring is None or monitoring.state != "ACTIVE":
                raise DatasetInvalid("Corporate action zablokovala paper execution")
        cycle_id = self.trading_cycle.run(
            deployment.paper_account_id,
            strategy_id,
            bars,
            target_weights,
            executable_session,
            decision_time,
        )
        self._ensure_cycle_lineage(
            monitoring.monitoring_id,
            deployment.deployment_id,
            cycle_id,
            executable_session,
            as_of,
        )
        with Session(self.trading_cycle.repository.engine) as session, session.begin():
            self.trading_cycle.repository.audit(
                session,
                AuditEventType.DATA_VALIDATED,
                "phase6_deployment",
                deployment.deployment_id,
                cycle_id,
                cycle_id,
                {
                    "deployment_id": deployment.deployment_id,
                    "experiment_id": deployment.experiment_id,
                    "snapshot_id": deployment.snapshot_id,
                    "signal_observation_ids": [
                        item.observation_id for values in history.values() for item in values
                    ],
                    "decision_time": decision_time.isoformat(),
                    "signal_through_session": expected_sessions[-1].isoformat(),
                    "executable_session": executable_session.isoformat(),
                    "execution_time": execution_time.isoformat(),
                    "current_observation_ids": [item.observation_id for item in latest],
                    "raw_open_by_instrument": {
                        item.instrument_id: str(item.open) for item in latest
                    },
                },
            )
        return cycle_id

    @staticmethod
    def execution_timing(calendar: XNYSCalendar, now: datetime) -> PaperExecutionTiming:
        as_of = require_utc(now)
        signal_session = calendar.latest_completed_session(as_of)
        execution_session = calendar.next_session(signal_session)
        return PaperExecutionTiming(
            signal_session,
            calendar.session_close(signal_session),
            execution_session,
            calendar.session_open(execution_session),
        )

    @staticmethod
    def _rebalance_due(current: date, previous: date, frequency: RebalanceFrequency) -> bool:
        if frequency is RebalanceFrequency.DAILY:
            return True
        if frequency is RebalanceFrequency.WEEKLY:
            return current.isocalendar()[:2] != previous.isocalendar()[:2]
        return (current.year, current.month) != (previous.year, previous.month)
