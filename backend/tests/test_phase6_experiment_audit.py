from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from decimal import Decimal

import pytest
from phase6_audit_helpers import seed_phase6_snapshot
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import quantlab.phase6_runtime as runtime
from quantlab.market_data import DatasetInvalid
from quantlab.persistence import DatasetSnapshotRecord, ExperimentRecord
from quantlab.phase6_runtime import Phase6ExperimentRunner


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


def _canonical_hash(manifest: dict[str, object]) -> str:
    immutable = {
        "observations": manifest["observations"],
        "corporate_actions": manifest["corporate_actions"],
    }
    return hashlib.sha256(
        json.dumps(immutable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
