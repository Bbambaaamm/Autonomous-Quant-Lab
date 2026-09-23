import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from phase6_audit_helpers import CALENDAR, MappingProvider, daily_bar
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from quantlab.m7_validation import (
    EqualWeightMonthly,
    _request,
    _runtime_code_sha,
    _select_deployment,
    digest,
    run_m7_validation,
)
from quantlab.market_data import (
    AssetType,
    CorporateAction,
    CorporateActionKind,
    Instrument,
)
from quantlab.market_data_service import DatasetSnapshotService, PersistentMarketDataService
from quantlab.multi_asset import StrategyContext
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    InstrumentRecord,
    MarketObservationRecord,
    StrategyDeploymentRecord,
    StrategyRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase4 import Phase4Repository
from quantlab.phase6_runtime import (
    DeploymentService,
    Phase6ExperimentRequest,
    Phase6ExperimentRunner,
)


def _seed(tmp_path):
    repository = Phase4Repository(f"sqlite:///{tmp_path / 'm7.db'}")
    repository.seed_account()
    factory = sessionmaker(repository.engine)
    sessions = list(CALENDAR.sessions_between(date(2026, 1, 2), date(2026, 2, 13)))[:24]
    instruments = (
        Instrument("m7-a", "M7A", "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2020, 1, 1)),
        Instrument("m7-b", "M7B", "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2020, 1, 1)),
    )
    split = CorporateAction(
        "m7-split",
        "m7-a",
        CorporateActionKind.SPLIT,
        CALENDAR.session_close(sessions[10]),
        datetime(2020, 1, 1, tzinfo=UTC),
        Decimal("2"),
    )
    provider = MappingProvider(
        "m7-provider",
        {
            "M7A": [daily_bar(day, Decimal(100 + index)) for index, day in enumerate(sessions)],
            "M7B": [
                daily_bar(day, Decimal(110 - index) + Decimal("0.5"))
                for index, day in enumerate(sessions)
            ],
        },
        {"M7A": [split], "M7B": []},
    )
    service = PersistentMarketDataService(factory)
    for instrument in instruments:
        for day in sessions:
            result = service.ingest(
                provider,
                instrument,
                day,
                day,
                CALENDAR.session_close(day),
            )
            assert result.status == "SUCCEEDED"
        service.verify_corporate_action_readiness(
            provider,
            instrument,
            sessions[0],
            sessions[-1],
            CALENDAR.session_close(sessions[-1]),
        )

    universe_id = "m7-pit"
    with factory() as db, db.begin():
        db.add(
            UniverseDefinitionRecord(
                universe_id=universe_id,
                name=universe_id,
                kind="POINT_IN_TIME_MEMBERSHIP",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        for instrument in instruments:
            db.add(
                UniverseMembershipRecord(
                    universe_id=universe_id,
                    instrument_id=instrument.instrument_id,
                    valid_from=datetime(2020, 1, 1, tzinfo=UTC),
                    valid_to=None,
                    known_at=datetime(2020, 1, 1, tzinfo=UTC),
                )
            )
        db.add(
            StrategyRecord(
                strategy_identity="m7-trend",
                strategy_name="multi_asset_trend",
                strategy_version="1.0.0",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
                metadata_json="{}",
            )
        )

    cutoff = CALENDAR.session_close(sessions[-1])
    snapshot = DatasetSnapshotService(factory).build(
        as_of=cutoff,
        provider=provider.metadata.name,
        universe_id=universe_id,
        start=sessions[0],
        end=sessions[-1],
        minimum_coverage=Decimal("1"),
    )
    request = Phase6ExperimentRequest(
        snapshot.snapshot_id,
        "multi_asset_trend",
        "1.0.0",
        (
            {"fast": 2, "slow": 3},
            {"fast": 3, "slow": 5},
        ),
        code_sha="a" * 40,
    )
    experiment = Phase6ExperimentRunner(factory).run(request)
    with factory() as db, db.begin():
        persisted = db.get(ExperimentRecord, experiment.id)
        persisted.decision = "PAPER_CANDIDATE"
    deployments = DeploymentService(factory)
    deployment = deployments.create(
        experiment.id,
        "paper-main",
        created_at=cutoff,
    )
    deployments.approve(deployment.deployment_id, cutoff)
    return factory, experiment, snapshot, deployment.deployment_id


def test_equal_weight_benchmark_is_deterministic():
    strategy = EqualWeightMonthly()
    context = StrategyContext(
        datetime(2026, 1, 2, 21, tzinfo=UTC),
        {},
        ("m7-a", "m7-b"),
        {},
    )
    target = strategy.generate_targets(context)
    assert target.weights == (("m7-a", Decimal("0.5")), ("m7-b", Decimal("0.5")))


def test_runtime_sha_replaces_caller_or_historical_sha(tmp_path, monkeypatch):
    import quantlab.m7_validation as module

    factory, experiment, _, deployment_id = _seed(tmp_path)
    monkeypatch.setattr(module, "_runtime_code_sha", lambda: "b" * 40)
    with factory() as db:
        persisted = db.get(ExperimentRecord, experiment.id)
        request = _request(persisted, "b" * 40)
    assert request.code_sha == "b" * 40
    report = run_m7_validation(factory, deployment_id=deployment_id)
    assert report["validator_code_sha"] == "b" * 40
    assert report["experiment_original_code_sha"] == "a" * 40


def test_static_or_backdated_ad_hoc_universe_cannot_pass_m7(tmp_path):
    factory, _, snapshot, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        universe = db.get(UniverseDefinitionRecord, "m7-pit")
        universe.kind = "STATIC"
    with pytest.raises(ValueError, match="M7_REQUIRES_PERSISTED_PIT_UNIVERSE"):
        run_m7_validation(factory, deployment_id=deployment_id)
    with factory() as db:
        assert db.get(DatasetSnapshotRecord, snapshot.snapshot_id) is not None


def test_snapshot_manifest_tamper_fails_closed(tmp_path):
    factory, _, snapshot, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        row = db.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        manifest["observations"] = manifest["observations"][:-1]
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="M7_SNAPSHOT_CONTENT_HASH_MISMATCH"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_replay_is_persisted_and_benchmark_receives_corporate_actions(tmp_path, monkeypatch):
    import quantlab.m7_validation as module

    factory, experiment, _, deployment_id = _seed(tmp_path)
    original = module.run_multi_asset
    captured = []

    def wrapped(*args, **kwargs):
        captured.extend(kwargs.get("corporate_actions", ()))
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_multi_asset", wrapped)
    report = run_m7_validation(factory, deployment_id=deployment_id)
    assert report["experiment_id"] == experiment.id
    assert report["replay"]["matches_persisted_oos"] is True
    assert report["guards"]["causal_adjusted_signal_prices"] is True
    assert report["guards"]["raw_prices_for_fills"] is True
    assert any(action.kind is CorporateActionKind.SPLIT for action in captured)


def test_validator_is_read_only_for_deployment_and_experiment(tmp_path):
    factory, experiment, _, deployment_id = _seed(tmp_path)
    before = None
    with factory() as db:
        deployment = db.get(StrategyDeploymentRecord, deployment_id)
        persisted = db.get(ExperimentRecord, experiment.id)
        before = (deployment.status, deployment.approved_at, persisted.decision)
    first = run_m7_validation(factory, deployment_id=deployment_id)
    second = run_m7_validation(factory, deployment_id=deployment_id)
    assert first["report_hash"] == second["report_hash"]
    with factory() as db:
        deployment = db.get(StrategyDeploymentRecord, deployment_id)
        persisted = db.get(ExperimentRecord, experiment.id)
        assert (deployment.status, deployment.approved_at, persisted.decision) == before


def test_manual_approved_row_without_runtime_evidence_is_rejected(tmp_path):
    factory, experiment, snapshot, _ = _seed(tmp_path)
    with factory() as db, db.begin():
        db.add(
            StrategyDeploymentRecord(
                deployment_id="manual-approved",
                created_at=snapshot.as_of,
                approved_at=snapshot.as_of,
                status="APPROVED",
                strategy_name=experiment.strategy_name,
                strategy_version=experiment.strategy_version,
                parameters_json=experiment.selected_parameters_json or "{}",
                universe_id=snapshot.universe_id,
                paper_account_id="paper-main",
                experiment_id=experiment.id,
                snapshot_id=snapshot.snapshot_id,
                currency="USD",
                timeframe="1d",
            )
        )
    with pytest.raises(ValueError, match="RUNTIME_CONFIG"):
        run_m7_validation(factory, deployment_id="manual-approved")


def test_deployment_selection_has_stable_unique_tie_break(tmp_path):
    factory, experiment, snapshot, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        original = db.get(StrategyDeploymentRecord, deployment_id)
        clone = StrategyDeploymentRecord(
            deployment_id="f" * 64,
            created_at=original.created_at,
            approved_at=original.approved_at,
            status="APPROVED",
            strategy_name=original.strategy_name,
            strategy_version=original.strategy_version,
            parameters_json=original.parameters_json,
            universe_id=original.universe_id,
            paper_account_id=original.paper_account_id,
            experiment_id=experiment.id,
            snapshot_id=snapshot.snapshot_id,
            currency=original.currency,
            timeframe=original.timeframe,
            runtime_manifest_json=original.runtime_manifest_json,
            runtime_manifest_hash=original.runtime_manifest_hash,
            runtime_manifest_version=original.runtime_manifest_version,
        )
        db.add(clone)
    with factory() as db:
        assert _select_deployment(db, None).deployment_id == "f" * 64


def test_single_observed_instrument_cannot_be_reported_as_multi_asset(tmp_path):
    factory, _, snapshot, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        row = db.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        first_id = manifest["universe_memberships"][0]["instrument_id"]
        observed = []
        for item in manifest["observations"]:
            observation = db.scalar(
                select(MarketObservationRecord).where(
                    MarketObservationRecord.observation_id == item["id"]
                )
            )
            if observation is not None and observation.instrument_id == first_id:
                observed.append(item)
        manifest["observations"] = observed
        immutable = {
            "observations": manifest["observations"],
            "corporate_actions": manifest["corporate_actions"],
            "universe_memberships": manifest["universe_memberships"],
        }
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        row.content_hash = digest(immutable)
    with pytest.raises(ValueError, match="M7_REQUIRES_MULTI_INSTRUMENT_PIT_UNIVERSE"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_loaded_observation_payload_is_rehashed(tmp_path):
    factory, _, _, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        observation = db.scalar(select(MarketObservationRecord))
        observation.high = str(Decimal(observation.high) + Decimal("1"))
    with pytest.raises(ValueError, match="M7_OBSERVATION_PAYLOAD_HASH_MISMATCH"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_snapshot_id_is_revalidated_from_logical_identity(tmp_path):
    factory, _, snapshot, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        row = db.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        manifest["logical_identity"] += "|tampered"
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="M7_SNAPSHOT_ID_MISMATCH"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_experiment_config_is_bound_to_persisted_identity(tmp_path):
    factory, experiment, _, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        row = db.get(ExperimentRecord, experiment.id)
        config = json.loads(row.config_json)
        config["seed"] = int(config["seed"]) + 1
        row.config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="M7_EXPERIMENT_IDENTITY_MISMATCH"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_observation_knowledge_time_cannot_move_past_snapshot_cutoff(tmp_path):
    factory, _, snapshot, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        observation = db.scalar(select(MarketObservationRecord))
        observation.observed_at = snapshot.as_of + timedelta(seconds=1)
    with pytest.raises(ValueError, match="M7_OBSERVATION_TIME_INCONSISTENT"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_dirty_checkout_is_rejected_for_validator_sha(monkeypatch):
    import quantlab.m7_validation as module

    sha = "b" * 40
    monkeypatch.setattr(
        Phase6ExperimentRunner,
        "_code_sha",
        staticmethod(lambda explicit: sha),
    )
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/git")

    def fake_run(args, **kwargs):
        if args[1:] == ["rev-parse", "--is-inside-work-tree"]:
            return SimpleNamespace(returncode=0, stdout="true\n")
        if args[1:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout=sha + "\n")
        if args[1:4] == ["status", "--porcelain", "--untracked-files=all"]:
            return SimpleNamespace(
                returncode=0, stdout="?? backend/src/quantlab/untracked_runtime.py\n"
            )
        raise AssertionError(args)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="M7_VALIDATOR_CHECKOUT_DIRTY"):
        _runtime_code_sha()


def test_snapshot_instrument_currency_must_match_paper_account(tmp_path):
    factory, _, _, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        instrument = db.scalar(select(InstrumentRecord))
        instrument.currency = "EUR"
    with pytest.raises(ValueError, match="M7_INSTRUMENT_CURRENCY_MISMATCH"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_observation_cannot_be_known_before_daily_close(tmp_path):
    factory, _, _, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        observation = db.scalar(select(MarketObservationRecord))
        observation.observed_at = observation.timestamp - timedelta(seconds=1)
    with pytest.raises(ValueError, match="M7_OBSERVATION_TIME_INCONSISTENT"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_snapshot_row_metadata_is_bound_to_logical_identity(tmp_path):
    factory, _, snapshot, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        row = db.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        row.as_of = row.as_of + timedelta(seconds=1)
    with pytest.raises(ValueError, match="M7_SNAPSHOT_LOGICAL_IDENTITY_MISMATCH"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_experiment_seed_column_must_match_precommitted_config(tmp_path):
    factory, experiment, _, deployment_id = _seed(tmp_path)
    with factory() as db, db.begin():
        row = db.get(ExperimentRecord, experiment.id)
        row.seed = int(row.seed) + 1
    with pytest.raises(ValueError, match="M7_EXPERIMENT_SEED_MISMATCH"):
        run_m7_validation(factory, deployment_id=deployment_id)


def test_loader_keeps_currency_for_member_without_observation(tmp_path):
    import quantlab.m7_validation as module

    factory, _, snapshot, _ = _seed(tmp_path)
    with factory() as db, db.begin():
        instrument = db.get(InstrumentRecord, "m7-b")
        instrument.currency = "EUR"
        row = db.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        kept = []
        for item in manifest["observations"]:
            observation = db.scalar(
                select(MarketObservationRecord).where(
                    MarketObservationRecord.observation_id == item["id"]
                )
            )
            if observation.instrument_id != "m7-b":
                kept.append(item)
        manifest["observations"] = kept
        immutable = {
            "observations": kept,
            "corporate_actions": manifest["corporate_actions"],
            "universe_memberships": manifest["universe_memberships"],
        }
        row.content_hash = digest(immutable)
        logical = manifest["logical_identity"]
        row.snapshot_id = module.hashlib.sha256(
            f"{logical}|{row.content_hash}".encode()
        ).hexdigest()
        manifest["logical_identity"] = logical
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        db.flush()
        _, _, _, _, currencies = module._load_snapshot(db, row)
        assert currencies["m7-b"] == "EUR"
