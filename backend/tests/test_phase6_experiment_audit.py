from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from phase6_audit_helpers import CALENDAR, seed_phase6_snapshot
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import quantlab.phase6_runtime as runtime
from quantlab import api
from quantlab.market_data import DatasetInvalid
from quantlab.market_data_service import (
    CorporateActionRevisionCanonicalizationRecord,
    CorporateActionRevisionRecord,
    DatasetSnapshotService,
)
from quantlab.persistence import (
    CorporateActionEventRecord,
    CorporateActionRecord,
    DatasetSnapshotRecord,
    ExperimentRecord,
    StrategyRecord,
)
from quantlab.phase6_runtime import (
    Phase6ExperimentRequest,
    Phase6ExperimentRunner,
    normalize_strategy_config,
)


def factory():
    engine = create_engine("sqlite://")
    from quantlab.persistence import Base

    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_phase6_runner_is_sequentially_exactly_once_and_never_auto_promotes() -> None:
    sessions = factory()
    _, _, _, _, request = seed_phase6_snapshot(sessions)
    runner = Phase6ExperimentRunner(sessions)
    first = runner.run(request)
    second = runner.run(request)
    assert first.id == second.id
    assert first.idempotency_key == second.idempotency_key
    assert first.selected_parameters_json == second.selected_parameters_json
    assert first.result_json == second.result_json
    assert first.decision == second.decision == "RESEARCH_ONLY"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExperimentRecord)) == 1


def test_phase6_runner_oos_isolation() -> None:
    sessions = factory()
    prefix = [Decimal(100 + index) for index in range(12)]
    _, _, _, _, request_one = seed_phase6_snapshot(
        sessions, suffix="oos-one", closes=prefix + [Decimal(112), Decimal(113), Decimal(114)]
    )
    _, _, _, _, request_two = seed_phase6_snapshot(
        sessions,
        suffix="oos-two",
        closes=prefix + [Decimal(112), Decimal(40), Decimal(10)],
    )
    runner = Phase6ExperimentRunner(sessions)
    first, second = runner.run(request_one), runner.run(request_two)
    assert first.selected_parameters_json == second.selected_parameters_json
    assert first.result_json != second.result_json


def test_mean_reversion_decimal_transport_has_one_canonical_identity() -> None:
    sessions = factory()
    _, _, _, snapshot, _ = seed_phase6_snapshot(sessions, suffix="mean-reversion")
    with sessions() as session, session.begin():
        session.add(
            StrategyRecord(
                strategy_identity="mean-reversion-1",
                strategy_name="multi_asset_mean_reversion",
                strategy_version="1.0.0",
                created_at=runtime.datetime.now(runtime.UTC),
                metadata_json="{}",
            )
        )
    runner = Phase6ExperimentRunner(sessions)

    def request(threshold: object) -> Phase6ExperimentRequest:
        return Phase6ExperimentRequest(
            snapshot.snapshot_id,
            "multi_asset_mean_reversion",
            "1.0.0",
            ({"lookback": 20, "threshold": threshold},),
            code_sha="a" * 40,
        )

    from_string = runner.run(request("0.950"))
    from_number = runner.run(request(0.95))
    with_default_omitted = runner.run(
        Phase6ExperimentRequest(
            snapshot.snapshot_id,
            "multi_asset_mean_reversion",
            "1.0.0",
            ({"threshold": "0.95"},),
            code_sha="a" * 40,
        )
    )
    replay = runner.replay(request(0.95))
    assert from_string.id == from_number.id == with_default_omitted.id
    assert from_string.selected_parameters_json == (
        '{"lookback":20,"rebalance_frequency":"WEEKLY","threshold":"0.95"}'
    )
    assert replay.selected_parameters == {
        "lookback": 20,
        "rebalance_frequency": runtime.RebalanceFrequency.WEEKLY,
        "threshold": Decimal("0.95"),
    }


def test_strategy_config_materializes_defaults_canonically() -> None:
    omitted = normalize_strategy_config(
        "multi_asset_mean_reversion", "1.0.0", {"threshold": "0.9500"}
    )
    explicit = normalize_strategy_config(
        "multi_asset_mean_reversion",
        "1.0.0",
        {"lookback": 20, "threshold": Decimal("0.95")},
    )
    assert omitted == explicit
    assert omitted == {
        "lookback": 20,
        "rebalance_frequency": runtime.RebalanceFrequency.WEEKLY,
        "threshold": Decimal("0.95"),
    }


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"lookback": 20, "threshold": "abc"}, "desetinné číslo"),
        ({"lookback": True, "threshold": "0.95"}, "celé číslo"),
        ({"lookback": 20.5, "threshold": "0.95"}, "celé číslo"),
        ({"lookback": 20.0, "threshold": "0.95"}, "celé číslo"),
        ({"lookback": 9007199254740993.0, "threshold": "0.95"}, "celé číslo"),
        (
            {"lookback": 20, "threshold": "0.95", "threshhold": "0.9"},
            "Neznámé strategy parametry",
        ),
    ],
)
def test_strategy_config_rejects_invalid_operator_values(
    config: dict[str, object], message: str
) -> None:
    with pytest.raises(DatasetInvalid, match=message):
        normalize_strategy_config("multi_asset_mean_reversion", "1.0.0", config)


def test_runner_rejects_bad_config_before_strategy_construction() -> None:
    sessions = factory()
    runner = Phase6ExperimentRunner(sessions)
    request = Phase6ExperimentRequest(
        "unused",
        "multi_asset_mean_reversion",
        "1.0.0",
        ({"lookback": 20, "threshold": "abc"},),
        code_sha="a" * 40,
    )
    with pytest.raises(DatasetInvalid, match="desetinné číslo"):
        runner.run(request)


@pytest.mark.parametrize(
    "config",
    [
        {"lookback": 20, "threshold": "abc"},
        {"lookback": True, "threshold": "0.95"},
        {"lookback": 20.5, "threshold": "0.95"},
        {"lookback": 20, "threshold": "0.95", "threshhold": "0.9"},
    ],
)
def test_experiment_api_returns_domain_error_for_invalid_config(
    config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api.control_plane_registry, "ensure_strategy", lambda *args: None)
    response = TestClient(api.app).post(
        "/operator/research/experiments",
        json={
            "snapshot_id": "unused",
            "strategy_name": "multi_asset_mean_reversion",
            "strategy_version": "1.0.0",
            "parameter_configs": [config],
            "code_sha": "a" * 40,
            "reason": "regression test",
        },
    )
    assert response.status_code == 409, response.text


def test_trend_and_momentum_configs_remain_typed() -> None:
    assert normalize_strategy_config("multi_asset_trend", "1.0.0", {"fast": 2, "slow": 3}) == {
        "fast": 2,
        "rebalance_frequency": runtime.RebalanceFrequency.MONTHLY,
        "slow": 3,
    }
    assert normalize_strategy_config(
        "cross_sectional_momentum", "1.0.0", {"lookback": 20, "top_n": 3}
    ) == {
        "lookback": 20,
        "rebalance_frequency": runtime.RebalanceFrequency.MONTHLY,
        "top_n": 3,
    }


def _canonical_hash(manifest: dict[str, object]) -> str:
    immutable = {
        "observations": manifest["observations"],
        "corporate_actions": manifest["corporate_actions"],
        "universe_memberships": manifest["universe_memberships"],
    }
    return hashlib.sha256(
        json.dumps(immutable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _historical_action_fixture(
    sessions, *, include_legacy: bool = True, matching_provider: bool = True
):
    instrument, provider, _, snapshot, request = seed_phase6_snapshot(
        sessions, suffix=f"historical-action-{include_legacy}"
    )
    action_id = "a" * 64
    # Akce je stejně jako stagingová dividenda známa až po effective_at, ale ještě
    # před research intervalem; enrollment test tak izoluje replay od výnosové policy.
    canonical_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    legacy_at = canonical_at + timedelta(hours=1)
    effective_at = datetime(2025, 12, 30, tzinfo=UTC)
    revision_provider = provider.metadata.name if matching_provider else "different-lineage"
    action_entry = {
        "action_id": action_id,
        "instrument_id": instrument.instrument_id,
        "kind": "CASH_DIVIDEND",
        "effective_at": effective_at.isoformat(),
        "known_at": legacy_at.isoformat(),
        "value": "1.69",
        "new_symbol": None,
    }
    with sessions() as session, session.begin():
        row = session.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        manifest["corporate_actions"] = [action_entry]
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        row.content_hash = _canonical_hash(manifest)
        session.add(
            CorporateActionEventRecord(
                event_id="legacy-canonicalization-event",
                provider=revision_provider,
                occurred_at=legacy_at,
                action="update",
                provider_action_id="provider-action",
                payload_hash="b" * 64,
            )
        )
        revisions = [
            CorporateActionRevisionRecord(
                revision_id="1" * 64,
                action_id=action_id,
                provider=revision_provider,
                provider_action_id="provider-action",
                payload_hash="b" * 64,
                instrument_id=instrument.instrument_id,
                kind="CASH_DIVIDEND",
                effective_at=effective_at,
                known_at=canonical_at,
                value="1.69",
                new_symbol=None,
            )
        ]
        if include_legacy:
            revisions.append(
                CorporateActionRevisionRecord(
                    revision_id="2" * 64,
                    action_id=action_id,
                    provider=revision_provider,
                    provider_action_id="provider-action",
                    payload_hash="b" * 64,
                    instrument_id=instrument.instrument_id,
                    kind="CASH_DIVIDEND",
                    effective_at=effective_at,
                    known_at=legacy_at,
                    value="1.69",
                    new_symbol=None,
                )
            )
        session.add_all(revisions)
        session.add(
            CorporateActionRecord(
                action_id=action_id,
                instrument_id=instrument.instrument_id,
                kind="CASH_DIVIDEND",
                effective_at=effective_at,
                known_at=canonical_at,
                value="1.69",
                new_symbol=None,
            )
        )
        if include_legacy:
            # PostgreSQL musí před sidecarem vidět obě FK revision evidence i source event.
            session.flush()
            session.add(
                CorporateActionRevisionCanonicalizationRecord(
                    superseded_revision_id="2" * 64,
                    canonical_revision_id="1" * 64,
                    provider=revision_provider,
                    provider_action_id="provider-action",
                    reason="LEGACY_DUPLICATE_SAME_SSE_INCARNATION",
                    source_event_id="legacy-canonicalization-event",
                    repaired_at=legacy_at,
                )
            )
    return snapshot, request, canonical_at, legacy_at


def test_historical_superseded_revision_remains_replayable_and_immutable() -> None:
    sessions = factory()
    snapshot, request, canonical_at, legacy_at = _historical_action_fixture(sessions)
    runner = Phase6ExperimentRunner(sessions)

    assert runner.replay(request) == runner.replay(request)
    with sessions() as session:
        persisted_snapshot = session.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        legacy = session.get(CorporateActionRevisionRecord, "2" * 64)
        current = session.get(CorporateActionRecord, "a" * 64)
        assert persisted_snapshot.content_hash == _canonical_hash(
            json.loads(persisted_snapshot.manifest_json)
        )
        assert legacy is not None and runtime._database_utc(legacy.known_at) == legacy_at
        assert current is not None and runtime._database_utc(current.known_at) == canonical_at


def test_historical_snapshot_without_exact_legacy_revision_fails_closed() -> None:
    sessions = factory()
    _, request, _, _ = _historical_action_fixture(sessions, include_legacy=False)

    with pytest.raises(DatasetInvalid, match="persistentní evidence"):
        Phase6ExperimentRunner(sessions).replay(request)


def test_historical_snapshot_requires_revision_from_its_provider_lineage() -> None:
    sessions = factory()
    _, request, _, _ = _historical_action_fixture(sessions, matching_provider=False)

    with pytest.raises(DatasetInvalid, match="persistentní evidence"):
        Phase6ExperimentRunner(sessions).replay(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instrument_id", "different-instrument"),
        ("kind", "SPLIT"),
        ("effective_at", "2025-12-31T00:00:00+00:00"),
        ("known_at", "2026-01-01T14:00:00+00:00"),
        ("value", "1.70"),
        ("new_symbol", "IBM2"),
    ],
)
def test_historical_snapshot_requires_every_semantic_field(field: str, value: object) -> None:
    sessions = factory()
    snapshot, request, _, _ = _historical_action_fixture(sessions)
    with sessions() as session, session.begin():
        row = session.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        manifest["corporate_actions"][0][field] = value
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        row.content_hash = _canonical_hash(manifest)

    with pytest.raises(DatasetInvalid, match="persistentní evidence"):
        Phase6ExperimentRunner(sessions).replay(request)


@pytest.mark.parametrize(("field", "value"), [("instrument_id", []), ("new_symbol", {})])
def test_historical_snapshot_rejects_unhashable_field_types(field: str, value: object) -> None:
    sessions = factory()
    snapshot, request, _, _ = _historical_action_fixture(sessions)
    with sessions() as session, session.begin():
        row = session.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        manifest = json.loads(row.manifest_json)
        manifest["corporate_actions"][0][field] = value
        row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        row.content_hash = _canonical_hash(manifest)

    with pytest.raises(DatasetInvalid, match="nejsou konzistentní"):
        Phase6ExperimentRunner(sessions).replay(request)


def test_new_snapshot_rejects_current_action_without_immutable_revision() -> None:
    sessions = factory()
    instrument, provider, days, snapshot, _ = seed_phase6_snapshot(
        sessions, suffix="missing-new-snapshot-revision"
    )
    with sessions() as session, session.begin():
        session.add(
            CorporateActionRecord(
                action_id="legacy-current-only",
                instrument_id=instrument.instrument_id,
                kind="CASH_DIVIDEND",
                effective_at=CALENDAR.session_open(days[-1]),
                known_at=CALENDAR.session_close(days[-2]),
                value="1.69",
                new_symbol=None,
            )
        )

    with pytest.raises(DatasetInvalid, match="immutable corporate-action revision evidence"):
        DatasetSnapshotService(sessions).build(
            as_of=snapshot.as_of,
            provider=provider.metadata.name,
            universe_id=snapshot.universe_id,
            start=snapshot.start,
            end=snapshot.end,
            minimum_coverage=Decimal("1"),
        )


@pytest.mark.parametrize(
    "case",
    [
        "malformed_json",
        "unsupported_schema",
        "missing_observations",
        "empty_observations",
        "duplicate_observation",
        "missing_referenced_observation",
        "revision_mismatch",
        "source_hash_mismatch",
        "missing_corporate_actions",
        "altered_universe_lineage",
        "malformed_corporate_action",
        "altered_corporate_action",
        "missing_corporate_action_evidence",
        "content_hash_mismatch",
    ],
)
def test_phase6_manifest_tampering_fails_before_research(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = factory()
    _, _, _, snapshot, request = seed_phase6_snapshot(sessions, suffix=f"tamper-{case}")
    research_called = False

    def forbidden_research(*args, **kwargs):
        nonlocal research_called
        research_called = True
        raise AssertionError("Research se po invalidním manifestu nesmí spustit")

    monkeypatch.setattr(runtime, "run_multi_asset", forbidden_research)
    with sessions() as session, session.begin():
        row = session.get(DatasetSnapshotRecord, snapshot.snapshot_id)
        assert row is not None
        if case == "malformed_json":
            row.manifest_json = "{"
        else:
            manifest = json.loads(row.manifest_json)
            observations = manifest["observations"]
            if case == "unsupported_schema":
                manifest["schema_version"] = "999"
            elif case == "missing_observations":
                del manifest["observations"]
            elif case == "empty_observations":
                manifest["observations"] = []
                row.content_hash = _canonical_hash(manifest)
            elif case == "duplicate_observation":
                observations.append(deepcopy(observations[0]))
                row.content_hash = _canonical_hash(manifest)
            elif case == "missing_referenced_observation":
                observations[0]["id"] = "missing"
                row.content_hash = _canonical_hash(manifest)
            elif case == "revision_mismatch":
                observations[0]["revision"] += 1
                row.content_hash = _canonical_hash(manifest)
            elif case == "source_hash_mismatch":
                observations[0]["hash"] = "f" * 64
                row.content_hash = _canonical_hash(manifest)
            elif case == "missing_corporate_actions":
                del manifest["corporate_actions"]
            elif case == "altered_universe_lineage":
                manifest["universe"]["survivorship_bias_status"] = "BIAS_PRONE_STATIC"
            elif case == "malformed_corporate_action":
                manifest["corporate_actions"] = [{"kind": "SPLIT"}]
                row.content_hash = _canonical_hash(manifest)
            elif case in {"altered_corporate_action", "missing_corporate_action_evidence"}:
                manifest["corporate_actions"] = [
                    {
                        "action_id": "missing-action",
                        "instrument_id": "wrong"
                        if case == "altered_corporate_action"
                        else observations[0]["id"],
                        "kind": "SPLIT",
                        "effective_at": "2026-01-20T21:00:00+00:00",
                        "known_at": "2026-01-19T21:00:00+00:00",
                        "value": "2",
                        "new_symbol": None,
                    }
                ]
                row.content_hash = _canonical_hash(manifest)
            elif case == "content_hash_mismatch":
                row.content_hash = "0" * 64
            row.manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    with pytest.raises(DatasetInvalid):
        Phase6ExperimentRunner(sessions).run(request)
    assert research_called is False
