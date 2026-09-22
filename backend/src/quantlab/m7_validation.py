"""Authoritative M7 replay for issue #164.

The validator never invents a universe. It reuses one already approved PAPER
deployment, its immutable Phase 6 snapshot, persisted PIT memberships, immutable
corporate actions, original experiment budget, and chronological split. It is
read-only and compares a current-code replay with the persisted OOS result before
running a predeclared equal-weight benchmark on the same evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import require_utc
from quantlab.market_data import CorporateAction, CorporateActionKind
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
    Phase6ExperimentRequest,
    Phase6ExperimentRunner,
    multi_asset_metrics,
)
from quantlab.universe import (
    PointInTimeUniverse,
    UniverseDefinition,
    UniverseKind,
    UniverseMembership,
)

SCHEMA_VERSION = 2
BENCHMARK_ID = "equal-weight-monthly-same-pit-universe-v1"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


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
        return TargetPortfolio(tuple((item, weight) for item in members), "equal weight")


def _metrics(metrics: Any) -> dict[str, object]:
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


def _request(experiment: ExperimentRecord, current_code_sha: str) -> Phase6ExperimentRequest:
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
            code_sha=current_code_sha,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("M7_EXPERIMENT_CONFIG_INVALID") from exc


def _persisted_signature(experiment: ExperimentRecord) -> dict[str, object]:
    try:
        result = json.loads(experiment.result_json)
        selected = json.loads(experiment.selected_parameters_json or "{}")
        metrics = result["metrics"]
        return {
            "selected_parameters": selected,
            "oos": {
                "total_return": str(metrics["total_return"]),
                "annualized_return": str(metrics["annualized_return"]),
                "volatility": str(metrics["volatility"]),
                "sharpe": str(metrics["sharpe"]),
                "max_drawdown": str(metrics["max_drawdown"]),
                "turnover": str(metrics["turnover"]),
                "time_weighted_exposure": str(metrics["time_weighted_exposure"]),
                "trade_count": int(metrics["trade_count"]),
                "total_costs": str(metrics["total_costs"]),
            },
            "equity": [[str(when), str(value)] for when, value in result["equity"]],
            "returns": [str(value) for value in result["returns"]],
            "sessions": [str(value) for value in result["sessions"]],
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("M7_PERSISTED_RESULT_INVALID") from exc


def _replay_signature(replay: Any) -> dict[str, object]:
    return {
        "selected_parameters": replay.selected_parameters,
        "oos": _metrics(replay.oos),
        "equity": [[str(when), str(value)] for when, value in replay.oos_equity],
        "returns": [str(value) for value in replay.oos_returns],
        "sessions": [str(value) for value in replay.oos_sessions],
    }


def _load_snapshot(
    session: Session, snapshot: DatasetSnapshotRecord
) -> tuple[
    dict[str, Any],
    tuple[Any, ...],
    tuple[CorporateAction, ...],
    PointInTimeUniverse,
    dict[str, str],
]:
    try:
        manifest = json.loads(snapshot.manifest_json)
    except json.JSONDecodeError as exc:
        raise ValueError("M7_SNAPSHOT_MANIFEST_INVALID") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "4":
        raise ValueError("M7_SNAPSHOT_MANIFEST_UNSUPPORTED")
    entries = manifest.get("observations")
    action_entries = manifest.get("corporate_actions")
    membership_entries = manifest.get("universe_memberships")
    if not isinstance(entries, list) or not entries:
        raise ValueError("M7_OBSERVATION_MANIFEST_EMPTY")
    if not isinstance(action_entries, list):
        raise ValueError("M7_ACTION_MANIFEST_INVALID")
    if not isinstance(membership_entries, list) or len(membership_entries) < 2:
        raise ValueError("M7_REQUIRES_MULTI_INSTRUMENT_PIT_UNIVERSE")

    immutable = {
        "observations": entries,
        "corporate_actions": action_entries,
        "universe_memberships": membership_entries,
    }
    if digest(immutable) != snapshot.content_hash:
        raise ValueError("M7_SNAPSHOT_CONTENT_HASH_MISMATCH")

    parsed_entries: list[tuple[str, int, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("M7_OBSERVATION_MANIFEST_INVALID")
        identity, revision, source_hash = entry.get("id"), entry.get("revision"), entry.get("hash")
        if (
            not isinstance(identity, str)
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision <= 0
            or not isinstance(source_hash, str)
        ):
            raise ValueError("M7_OBSERVATION_MANIFEST_INVALID")
        parsed_entries.append((identity, revision, source_hash))
    if len({item[0] for item in parsed_entries}) != len(parsed_entries):
        raise ValueError("M7_OBSERVATION_MANIFEST_INVALID")
    rows = tuple(
        session.scalars(
            select(MarketObservationRecord).where(
                MarketObservationRecord.observation_id.in_([item[0] for item in parsed_entries])
            )
        )
    )
    by_id = {row.observation_id: row for row in rows}
    if set(by_id) != {item[0] for item in parsed_entries}:
        raise ValueError("M7_OBSERVATION_MISSING")
    if any(
        by_id[identity].revision != revision or by_id[identity].source_hash != source_hash
        for identity, revision, source_hash in parsed_entries
    ):
        raise ValueError("M7_OBSERVATION_REVISION_MISMATCH")
    observations = tuple(_observation(by_id[identity]) for identity, _, _ in parsed_entries)

    definition = session.get(UniverseDefinitionRecord, snapshot.universe_id)
    if definition is None or definition.kind != UniverseKind.POINT_IN_TIME_MEMBERSHIP:
        raise ValueError("M7_REQUIRES_PERSISTED_PIT_UNIVERSE")
    memberships: list[UniverseMembership] = []
    for entry in membership_entries:
        if not isinstance(entry, dict):
            raise ValueError("M7_MEMBERSHIP_MANIFEST_INVALID")
        try:
            memberships.append(
                UniverseMembership(
                    snapshot.universe_id,
                    str(entry["instrument_id"]),
                    require_utc(datetime.fromisoformat(str(entry["valid_from"]))),
                    require_utc(datetime.fromisoformat(str(entry["valid_to"])))
                    if entry.get("valid_to")
                    else None,
                    require_utc(datetime.fromisoformat(str(entry["known_at"]))),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("M7_MEMBERSHIP_MANIFEST_INVALID") from exc
    persisted = tuple(
        session.scalars(
            select(UniverseMembershipRecord).where(
                UniverseMembershipRecord.universe_id == snapshot.universe_id
            )
        )
    )
    persisted_keys = {
        (
            row.instrument_id,
            _database_utc(row.valid_from),
            _database_utc(row.valid_to) if row.valid_to is not None else None,
            _database_utc(row.known_at),
        )
        for row in persisted
    }
    if any(
        (item.instrument_id, item.valid_from, item.valid_to, item.known_at) not in persisted_keys
        for item in memberships
    ):
        raise ValueError("M7_MEMBERSHIP_NOT_PERSISTED")
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
    for entry in action_entries:
        if not isinstance(entry, dict):
            raise ValueError("M7_ACTION_MANIFEST_INVALID")
        try:
            actions.append(
                CorporateAction(
                    action_id=str(entry["action_id"]),
                    instrument_id=str(entry["instrument_id"]),
                    kind=CorporateActionKind(str(entry["kind"])),
                    effective_at=require_utc(datetime.fromisoformat(str(entry["effective_at"]))),
                    known_at=require_utc(datetime.fromisoformat(str(entry["known_at"]))),
                    value=Decimal(str(entry["value"])) if entry.get("value") is not None else None,
                    new_symbol=str(entry["new_symbol"])\n                    if entry.get("new_symbol") is not None\n                    else None,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("M7_ACTION_MANIFEST_INVALID") from exc

    instrument_ids = {row.instrument_id for row in observations}
    currencies = {
        row.instrument_id: row.currency
        for row in session.scalars(
            select(InstrumentRecord).where(InstrumentRecord.instrument_id.in_(instrument_ids))
        )
    }
    if set(currencies) != instrument_ids:
        raise ValueError("M7_INSTRUMENT_METADATA_MISSING")
    return manifest, observations, tuple(actions), universe, currencies


def _select_deployment(session: Session, deployment_id: str | None) -> StrategyDeploymentRecord:
    if deployment_id is not None:
        deployment = session.get(StrategyDeploymentRecord, deployment_id)
    else:
        deployment = session.scalar(
            select(StrategyDeploymentRecord)
            .where(
                StrategyDeploymentRecord.paper_account_id == "paper-main",
                StrategyDeploymentRecord.status == "APPROVED",
            )
            .order_by(
                StrategyDeploymentRecord.approved_at.desc(),
                StrategyDeploymentRecord.created_at.desc(),
            )
            .limit(1)
        )
    if deployment is None or deployment.status != "APPROVED":
        raise ValueError("M7_APPROVED_DEPLOYMENT_NOT_FOUND")
    return deployment


def run_m7_validation(
    sessions: Callable[[], Session], *, deployment_id: str | None = None
) -> dict[str, object]:
    current_code_sha = Phase6ExperimentRunner._code_sha(None)
    with sessions() as session:
        deployment = _select_deployment(session, deployment_id)
        experiment = session.get(ExperimentRecord, deployment.experiment_id)
        snapshot = session.get(DatasetSnapshotRecord, deployment.snapshot_id)
        if (
            experiment is None
            or experiment.status != "COMPLETED"
            or snapshot is None
            or snapshot.status != "VALID"
            or experiment.snapshot_id != snapshot.snapshot_id
        ):
            raise ValueError("M7_LINEAGE_NOT_READY")
        request = _request(experiment, current_code_sha)
        if request.snapshot_id != snapshot.snapshot_id:
            raise ValueError("M7_LINEAGE_MISMATCH")
        manifest, observations, actions, universe, currencies = _load_snapshot(session, snapshot)
        persisted = _persisted_signature(experiment)
        original_code_sha = experiment.code_sha

    replay = Phase6ExperimentRunner(sessions).replay(request)
    replay_signature = _replay_signature(replay)
    if canonical(replay_signature) != canonical(persisted):
        raise ValueError("M7_REPLAY_MISMATCH")
    oos_times = list(replay.oos_sessions)
    if len(oos_times) < 2:
        raise ValueError("M7_OOS_TOO_SHORT")

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
        "schema_version": SCHEMA_VERSION,
        "deployment_id": deployment.deployment_id,
        "experiment_id": experiment.id,
        "experiment_original_code_sha": original_code_sha,
        "validator_code_sha": current_code_sha,
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "content_hash": snapshot.content_hash,
            "provider": snapshot.provider,
            "as_of": _database_utc(snapshot.as_of).isoformat(),
            "universe_id": snapshot.universe_id,
            "manifest_schema_version": manifest["schema_version"],
            "observation_count": len(manifest["observations"]),
            "membership_count": len(manifest["universe_memberships"]),
            "corporate_action_count": len(manifest["corporate_actions"]),
            "immutable_reference_verified": True,
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
            "persisted_pit_universe_only": True,
            "no_backdated_membership_created": True,
            "immutable_snapshot_reference_verified": True,
            "validator_sha_from_runtime": True,
            "causal_adjusted_signal_prices": True,
            "raw_prices_for_fills": True,
            "oos_used_for_selection": False,
            "paper_deployment_modified": False,
            "research_promotion_modified": False,
            "live_trading_used": False,
        },
    }
    report["report_hash"] = digest(report)
    return report
