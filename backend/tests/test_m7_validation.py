import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from phase6_audit_helpers import CALENDAR, MappingProvider, daily_bar
from quantlab.market_data import (
    AssetType,
    CorporateAction,
    CorporateActionKind,
    Instrument,
)
from quantlab.market_data_service import DatasetSnapshotService, PersistentMarketDataService
from quantlab.m7_validation import EqualWeightMonthly, _request, run_m7_validation
from quantlab.multi_asset import StrategyContext
from quantlab.persistence import (
    DatasetSnapshotRecord,
    ExperimentRecord,
    StrategyDeploymentRecord,
    StrategyRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase4 import Phase4Repository
from quantlab.phase6_runtime import Phase6ExperimentRequest, Phase6ExperimentRunner


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
        db.add(
            StrategyDeploymentRecord(
                deployment_id="m7-approved",
                created_at=cutoff,
                approved_at=cutoff,
                status="APPROVED",
                strategy_name="multi_asset_trend",
                strategy_version="1.0.0",
                parameters_json=experiment.selected_parameters_json or "{}",
                universe_id=universe_id,
                paper_account_id="paper-main",
                experiment_id=experiment.id,
                snapshot_id=snapshot.snapshot_id,
                currency="USD",
                timeframe="1d",
            )
        )
    return factory, experiment, snapshot


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
    factory, experiment, _ = _seed(tmp_path)
    monkeypatch.setattr(
        Phase6ExperimentRunner,
        "_code_sha",
        staticmethod(lambda explicit: "b" * 40 if explicit is None else explicit),
    )
    with factory() as db:
        persisted = db.get(ExperimentRecord, experiment.id)
        request = _request(persisted, "b" * 40)
    assert request.code_sha == "b" * 40
    report = run_m7_validation(factory, deployment_id="m7-approved")
    assert report["validator_code_sha"] == "b" * 40
    assert report["experiment_original_code_sha"] == "a" * 40


def test_static_or_backdated_ad_hoc_universe_cannot_pass_m7(tmp_path):
    factory, _, snapshot = _seed(tmp_path)
    with factory() as db, db.begin():
        universe = db.get(UniverseDefinitionRecord, "m7-pit")
        universe.kind = "STATIC"
    with pytest.raises(ValueError, match="M7_REQUIRES_PERSISTED_PIT_UNIVERSE"):
        run_m7_validation(factory, deployment_id="m7-approved")
    with factory() as db:
        assert db.get(DatasetSnapshotRecord, snapshot.snapshot_id) is not None


def test_snapshot_manifest_tamper_fails_closed(tmp_path):
    factory, _, snapshot = _seed(tmp_path)
    with factory() as db, db.begin():
        row = db.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        manifest["observations"] = manifest["observations"][:-1]
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="M7_SNAPSHOT_CONTENT_HASH_MISMATCH"):
        run_m7_validation(factory, deployment_id="m7-approved")


def test_replay_is_persisted_and_benchmark_receives_corporate_actions(tmp_path, monkeypatch):
    import quantlab.m7_validation as module

    factory, experiment, _ = _seed(tmp_path)
    original = module.run_multi_asset
    captured = []

    def wrapped(*args, **kwargs):
        captured.extend(kwargs.get("corporate_actions", ()))
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_multi_asset", wrapped)
    report = run_m7_validation(factory, deployment_id="m7-approved")
    assert report["experiment_id"] == experiment.id
    assert report["replay"]["matches_persisted_oos"] is True
    assert report["guards"]["causal_adjusted_signal_prices"] is True
    assert report["guards"]["raw_prices_for_fills"] is True
    assert any(action.kind is CorporateActionKind.SPLIT for action in captured)


def test_validator_is_read_only_for_deployment_and_experiment(tmp_path):
    factory, experiment, _ = _seed(tmp_path)
    before = None
    with factory() as db:
        deployment = db.get(StrategyDeploymentRecord, "m7-approved")
        before = (deployment.status, deployment.approved_at, experiment.decision)
    first = run_m7_validation(factory, deployment_id="m7-approved")
    second = run_m7_validation(factory, deployment_id="m7-approved")
    assert first["report_hash"] == second["report_hash"]
    with factory() as db:
        deployment = db.get(StrategyDeploymentRecord, "m7-approved")
        persisted = db.get(ExperimentRecord, experiment.id)
        assert (deployment.status, deployment.approved_at, persisted.decision) == before
