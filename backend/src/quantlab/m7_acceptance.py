"""Read-only M7 acceptance validation over the already-approved PAPER lineage."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import require_utc
from quantlab.market_data import CorporateAction, CorporateActionKind, DatasetInvalid
from quantlab.market_data_service import _database_utc, _observation
from quantlab.multi_asset import (
    ObservationKnowledgeMode,
    RebalanceFrequency,
    StrategyContext,
    TargetPortfolio,
    run_multi_asset,
)
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    InstrumentRecord,
    MarketObservationRecord,
    StrategyDeploymentRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase6_runtime import (
    Phase6ExperimentReplay,
    Phase6ExperimentRequest,
    Phase6ExperimentRunner,
    multi_asset_metrics,
)
from quantlab.universe import PointInTimeUniverse, UniverseDefinition, UniverseKind, UniverseMembership

BENCHMARK_ID = "equal_weight_monthly_same_pit_v1"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class EqualWeightMonthly:
    name: str = BENCHMARK_ID
    version: str = "1.0.0"
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.MONTHLY

    @property
    def required_lookback(self) -> int:
        return 1

    def generate_targets(self, context: StrategyContext) -> TargetPortfolio:
        members = tuple(sorted(context.eligible_instruments))
        weight = Decimal("1") / len(members) if members else Decimal(0)
        return TargetPortfolio(tuple((instrument, weight) for instrument in members), "equal weight")


def _metrics(value: Any) -> dict[str, object]:
    return {
        "total_return": str(value.total_return),
        "annualized_return": str(value.annualized_return),
        "volatility": str(value.volatility),
        "sharpe": str(value.sharpe),
        "max_drawdown": str(value.max_drawdown),
        "turnover": str(value.turnover),
        "time_weighted_exposure": str(value.time_weighted_exposure),
        "trade_count": value.trade_count,
        "total_costs": str(value.total_costs),
    }


def _request(experiment: ExperimentRecord) -> Phase6ExperimentRequest:
    try:
        config = json.loads(experiment.config_json)
        strategy = config["strategy"]
        parameters = config["parameters"]
        if not isinstance(strategy, list) or len(strategy) != 2:
            raise TypeError
        if not isinstance(parameters, list) or not parameters:
            raise TypeError
        return Phase6ExperimentRequest(
            snapshot_id=str(config["snapshot_id"]),
            strategy_name=str(strategy[0]),
            strategy_version=str(strategy[1]),
            parameter_configs=tuple(dict(item) for item in parameters),
            train_fraction=Decimal(str(config["train_fraction"])),
            validation_fraction=Decimal(str(config["validation_fraction"])),
            initial_cash=Decimal(str(config["initial_cash"])),
            commission_bps=Decimal(str(config["commission_bps"])),
            seed=int(config["seed"]),
            code_sha=str(config["code_sha"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DatasetInvalid("M7 persisted experiment config is invalid") from exc


def _approved_lineage(session: Session) -> tuple[StrategyDeploymentRecord, ExperimentRecord, DatasetSnapshotRecord]:
    deployment = session.scalar(
        select(StrategyDeploymentRecord)
        .where(
            StrategyDeploymentRecord.paper_account_id == "paper-main",
            StrategyDeploymentRecord.status == "APPROVED",
        )
        .order_by(StrategyDeploymentRecord.approved_at.desc(), StrategyDeploymentRecord.deployment_id)
        .limit(1)
    )
    if deployment is None:
        raise DatasetInvalid("M7 requires an already-approved PAPER deployment")
    experiment = session.get(ExperimentRecord, deployment.experiment_id)
    snapshot = session.get(DatasetSnapshotRecord, deployment.snapshot_id)
    if experiment is None or experiment.status != "COMPLETED" or snapshot is None or snapshot.status != "VALID":
        raise DatasetInvalid("M7 approved lineage is incomplete")
    if experiment.snapshot_id != snapshot.snapshot_id:
        raise DatasetInvalid("M7 deployment/experiment snapshot lineage mismatch")
    return deployment, experiment, snapshot


def _load_snapshot(
    session: Session, snapshot: DatasetSnapshotRecord
) -> tuple[dict[str, Any], tuple[Any, ...], tuple[CorporateAction, ...], PointInTimeUniverse, dict[str, str]]:
    try:
        manifest = json.loads(snapshot.manifest_json)
    except json.JSONDecodeError as exc:
        raise DatasetInvalid("M7 snapshot manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "4":
        raise DatasetInvalid("M7 requires immutable Phase6 snapshot schema 4")
    observations_meta = manifest.get("observations")
    membership_meta = manifest.get("universe_memberships")
    action_meta = manifest.get("corporate_actions")
    if not isinstance(observations_meta, list) or not observations_meta:
        raise DatasetInvalid("M7 snapshot has no observations")
    if not isinstance(membership_meta, list) or not isinstance(action_meta, list):
        raise DatasetInvalid("M7 snapshot lineage is incomplete")

    immutable = {
        "observations": observations_meta,
        "corporate_actions": action_meta,
        "universe_memberships": membership_meta,
    }
    if _digest(immutable) != snapshot.content_hash:
        raise DatasetInvalid("M7 snapshot content hash mismatch")

    parsed_ids: list[tuple[str, int, str]] = []
    for item in observations_meta:
        if not isinstance(item, dict):
            raise DatasetInvalid("M7 observation manifest is malformed")
        observation_id, revision, source_hash = item.get("id"), item.get("revision"), item.get("hash")
        if (
            not isinstance(observation_id, str)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision <= 0
            or not isinstance(source_hash, str)
        ):
            raise DatasetInvalid("M7 observation manifest is malformed")
        parsed_ids.append((observation_id, revision, source_hash))
    if len({item[0] for item in parsed_ids}) != len(parsed_ids):
        raise DatasetInvalid("M7 observation manifest contains duplicates")

    rows = tuple(
        session.scalars(
            select(MarketObservationRecord).where(
                MarketObservationRecord.observation_id.in_([item[0] for item in parsed_ids])
            )
        )
    )
    by_id = {row.observation_id: row for row in rows}
    if set(by_id) != {item[0] for item in parsed_ids}:
        raise DatasetInvalid("M7 snapshot observation is missing")
    for observation_id, revision, source_hash in parsed_ids:
        row = by_id[observation_id]
        if row.revision != revision or row.source_hash != source_hash:
            raise DatasetInvalid("M7 snapshot observation revision mismatch")
    observations = tuple(_observation(by_id[item[0]]) for item in parsed_ids)

    definition = session.get(UniverseDefinitionRecord, snapshot.universe_id)
    if definition is None or definition.kind != UniverseKind.POINT_IN_TIME_MEMBERSHIP:
        raise DatasetInvalid("M7 requires persisted POINT_IN_TIME_MEMBERSHIP")
    memberships: list[UniverseMembership] = []
    for item in membership_meta:
        if not isinstance(item, dict):
            raise DatasetInvalid("M7 membership manifest is malformed")
        memberships.append(
            UniverseMembership(
                snapshot.universe_id,
                str(item["instrument_id"]),
                require_utc(datetime.fromisoformat(str(item["valid_from"]))),
                require_utc(datetime.fromisoformat(str(item["valid_to"]))) if item.get("valid_to") else None,
                require_utc(datetime.fromisoformat(str(item["known_at"]))),
            )
        )
    if len({item.instrument_id for item in memberships}) < 2:
        raise DatasetInvalid("M7 requires at least two persisted PIT instruments")
    persisted = tuple(
        session.scalars(
            select(UniverseMembershipRecord).where(
                UniverseMembershipRecord.universe_id == snapshot.universe_id
            )
        )
    )
    persisted_keys = {
        (
            item.instrument_id,
            _database_utc(item.valid_from),
            _database_utc(item.valid_to) if item.valid_to is not None else None,
            _database_utc(item.known_at),
        )
        for item in persisted
    }
    if any((m.instrument_id, m.valid_from, m.valid_to, m.known_at) not in persisted_keys for m in memberships):
        raise DatasetInvalid("M7 membership was not persisted before validation")
    universe = PointInTimeUniverse(
        UniverseDefinition(
            definition.universe_id,
            definition.name,
            UniverseKind(definition.kind),
            _database_utc(definition.created_at),
        ),
        memberships,
    )

    actions: list[CorporateAction] = []
    for item in action_meta:
        if not isinstance(item, dict):
            raise DatasetInvalid("M7 corporate-action manifest is malformed")
        actions.append(
            CorporateAction(
                action_id=str(item["action_id"]),
                instrument_id=str(item["instrument_id"]),
                kind=CorporateActionKind(str(item["kind"])),
                effective_at=require_utc(datetime.fromisoformat(str(item["effective_at"]))),
                known_at=require_utc(datetime.fromisoformat(str(item["known_at"]))),
                value=Decimal(str(item["value"])) if item.get("value") is not None else None,
                new_symbol=str(item["new_symbol"]) if item.get("new_symbol") is not None else None,
            )
        )

    instrument_ids = {item.instrument_id for item in observations}
    currencies = {
        row.instrument_id: row.currency
        for row in session.scalars(
            select(InstrumentRecord).where(InstrumentRecord.instrument_id.in_(instrument_ids))
        )
    }
    if set(currencies) != instrument_ids:
        raise DatasetInvalid("M7 snapshot instrument metadata is incomplete")
    return manifest, observations, tuple(actions), universe, currencies


def _persisted_oos_signature(experiment: ExperimentRecord) -> dict[str, object]:
    try:
        result = json.loads(experiment.result_json)
        selected = json.loads(experiment.selected_parameters_json or "{}")
        return {
            "selected_parameters": selected,
            "metrics": result["metrics"],
            "equity": result["equity"],
            "returns": result["returns"],
            "sessions": result["sessions"],
        }
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise DatasetInvalid("M7 persisted OOS result is invalid") from exc


def _replay_oos_signature(replay: Phase6ExperimentReplay) -> dict[str, object]:
    return {
        "selected_parameters": replay.selected_parameters,
        "metrics": _metrics(replay.oos),
        "equity": [[str(when), str(value)] for when, value in replay.oos_equity],
        "returns": [str(value) for value in replay.oos_returns],
        "sessions": [str(value) for value in replay.oos_sessions],
    }


def run_m7_acceptance(sessions: Callable[[], Session]) -> dict[str, object]:
    validator_sha = Phase6ExperimentRunner._code_sha(None)
    with sessions() as session:
        deployment, experiment, snapshot = _approved_lineage(session)
        request = _request(experiment)
        manifest, observations, actions, universe, currencies = _load_snapshot(session, snapshot)
        persisted_signature = _persisted_oos_signature(experiment)

    replay = Phase6ExperimentRunner(sessions).replay(request)
    replay_signature = _replay_oos_signature(replay)
    if _canonical(replay_signature) != _canonical(persisted_signature):
        raise DatasetInvalid("M7 replay does not match persisted untouched OOS")

    oos_times = list(replay.oos_sessions)
    if len(oos_times) < 2:
        raise DatasetInvalid("M7 untouched OOS is too short")
    benchmark = run_multi_asset(
        [row for row in observations if row.timestamp <= oos_times[-1]],
        universe,
        EqualWeightMonthly(),
        request.initial_cash,
        request.commission_bps,
        currencies=currencies,
        corporate_actions=actions,
        evaluation_start=oos_times[0],
        observation_knowledge_mode=ObservationKnowledgeMode.SNAPSHOT_PINNED,
    )
    benchmark_metrics = multi_asset_metrics(benchmark, request.initial_cash)

    report: dict[str, object] = {
        "schema_version": 2,
        "validator_code_sha": validator_sha,
        "original_experiment_code_sha": request.code_sha,
        "deployment_id": deployment.deployment_id,
        "experiment_id": experiment.id,
        "snapshot_reference": {
            "snapshot_id": snapshot.snapshot_id,
            "content_hash": snapshot.content_hash,
            "as_of": _database_utc(snapshot.as_of).isoformat(),
            "provider": snapshot.provider,
            "universe_id": snapshot.universe_id,
            "manifest_hash_verified": True,
            "observation_count": len(manifest["observations"]),
            "membership_count": len(manifest["universe_memberships"]),
            "corporate_action_count": len(manifest["corporate_actions"]),
        },
        "precommitted_experiment": {
            "strategy": [request.strategy_name, request.strategy_version],
            "parameter_budget": list(request.parameter_configs),
            "train_fraction": str(request.train_fraction),
            "validation_fraction": str(request.validation_fraction),
            "initial_cash": str(request.initial_cash),
            "commission_bps": str(request.commission_bps),
            "seed": request.seed,
        },
        "replay": {
            "matches_persisted_oos": True,
            "selected_parameters": replay.selected_parameters,
            "train": _metrics(replay.train),
            "validation": _metrics(replay.validation),
            "oos": _metrics(replay.oos),
            "oos_sessions": len(oos_times),
        },
        "benchmark": {
            "id": BENCHMARK_ID,
            "oos": _metrics(benchmark_metrics),
            "fills": len(benchmark.fills),
        },
        "guards": {
            "approved_paper_lineage_preexists_validation": True,
            "persisted_pit_membership_only": True,
            "immutable_snapshot_manifest_verified": True,
            "validator_sha_derived_from_runtime": True,
            "causal_adjusted_signal_prices": True,
            "raw_ohlc_fills": True,
            "oos_used_for_selection": False,
            "paper_deployment_modified": False,
            "live_trading_used": False,
        },
    }
    report["report_hash"] = _digest(report)
    return report
