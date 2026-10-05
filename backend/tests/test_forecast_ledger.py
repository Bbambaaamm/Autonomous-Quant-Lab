"""Immutable Forecast Ledger — unit, PostgreSQL and causality regression tests (#266).

Covers every acceptance criterion of issue #266 plus the independent
architecture/statistical review blockers A–C of both reviews.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from quantlab.forecast_ledger import (
    FORECAST_AUTHORITY,
    FORECAST_SCHEMA_VERSION,
    BaselineProbability,
    CalibratedProbability,
    CanonicalTargetSpec,
    CommitOutcome,
    CoverageReport,
    DecisionForecastReference,
    DegradedReason,
    ForecastAuthorityError,
    ForecastConflictError,
    ForecastGateError,
    ForecastIntegrityError,
    ForecastLedger,
    ForecastLedgerError,
    ForecastLedgerRecord,
    ForecastLineage,
    ForecastRecord,
    ForecastStatus,
    InvalidForecastError,
    OutcomeKind,
    ProbabilityDistribution,
    ScopeKind,
    TargetDirection,
    _row_payload,
    assert_forecast_only,
    coverage_report,
    decide_with_forecast,
    identity,
    require_forecast_reference,
)

DECISION_TIME = datetime(2026, 9, 24, 14, 30, tzinfo=UTC)
CREATED_AT = datetime(2026, 9, 24, 14, 31, tzinfo=UTC)
RESOLUTION_AT = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def target_spec(**overrides: object) -> CanonicalTargetSpec:
    payload: dict[str, object] = {
        "target_id": "spy-close-up-1pct-1d",
        "outcome_kind": OutcomeKind.BINARY,
        "reference_source": "xnys",
        "reference_field": "close",
        "reference_price_basis": "causal_adjusted_close",
        "direction": TargetDirection.AT_OR_ABOVE,
        "threshold": Decimal("0.01"),
        "horizon_sessions": 1,
        "resolution_policy": "first_close_at_or_after_horizon",
        "resolution_policy_version": "res-v1",
        "exchange_calendar": "XNYS",
        "session_semantics": "regular_session_close",
    }
    payload.update(overrides)
    return CanonicalTargetSpec(**payload)  # type: ignore[arg-type]


def lineage(**overrides: object) -> ForecastLineage:
    payload: dict[str, object] = {
        "model_name": "momentum-prob",
        "model_version": "1.0.0",
        "code_sha": "a" * 64,
        "strategy_identity": "momentum-v1",
        "feature_extractor_version": "feat-v3",
        "market_snapshot_id": "snap-001",
        "market_snapshot_hash": "b" * 64,
        "market_snapshot_as_of": DECISION_TIME - timedelta(minutes=5),
        "trial_family_id": "tf-momentum-1d",
        "deployment_id": "dep-001",
        "research_experiment_id": "exp-001",
    }
    payload.update(overrides)
    return ForecastLineage(**payload)  # type: ignore[arg-type]


def emitted(**overrides: object) -> ForecastRecord:
    payload: dict[str, object] = {
        "status": ForecastStatus.FORECAST_EMITTED,
        "scope_kind": ScopeKind.ASSET,
        "scope_id": "SPY",
        "opportunity_id": "opp-2026-09-24-SPY",
        "preregistered": True,
        "created_at": CREATED_AT,
        "decision_time": DECISION_TIME,
        "resolution_at": RESOLUTION_AT,
        "target": target_spec(),
        "distribution": ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.62")),
        "lineage": lineage(),
        "baseline": BaselineProbability(
            probability=Decimal("0.5"),
            source="historical_frequency",
            source_version="hf-v1",
            as_of=DECISION_TIME - timedelta(days=1),
        ),
        "regime": "TRENDING_UP",
        "source": "phase6-current",
    }
    payload.update(overrides)
    return ForecastRecord.create(**payload)  # type: ignore[arg-type]


def degraded(status: ForecastStatus, reason: DegradedReason, **overrides: object) -> ForecastRecord:
    payload: dict[str, object] = {
        "status": status,
        "scope_kind": ScopeKind.ASSET,
        "scope_id": "SPY",
        "opportunity_id": "opp-2026-09-24-SPY",
        "preregistered": True,
        "created_at": CREATED_AT,
        "decision_time": DECISION_TIME,
        "resolution_at": RESOLUTION_AT,
        "target": target_spec(),
        "distribution": ProbabilityDistribution(kind=OutcomeKind.BINARY),
        "lineage": lineage(),
        "degraded_reason": reason,
        "degraded_detail": "snapshot chybí",
    }
    payload.update(overrides)
    return ForecastRecord.create(**payload)  # type: ignore[arg-type]


def ledger() -> tuple[ForecastLedger, object]:
    engine = create_engine("sqlite://")
    import quantlab.phase7  # noqa: F401

    ForecastLedgerRecord.__table__.create(engine)
    factory = sessionmaker(engine)
    return ForecastLedger(factory), engine


# ---------------------------------------------------------------------------
# Canonical contract + authority boundary
# ---------------------------------------------------------------------------


def test_module_never_grants_execution_authority() -> None:
    assert FORECAST_AUTHORITY is False
    assert_forecast_only()
    import quantlab.forecast_ledger as module

    assert module.FORECAST_AUTHORITY is False
    module.FORECAST_AUTHORITY = True
    try:
        with pytest.raises(ForecastAuthorityError):
            assert_forecast_only()
    finally:
        module.FORECAST_AUTHORITY = False


def test_module_has_no_io_clock_or_broker_imports() -> None:
    import ast
    from pathlib import Path

    source = Path(__import__("quantlab.forecast_ledger", fromlist=["x"]).__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "urllib",
        "random",
        "time",
        "quantlab.trading",
        "quantlab.phase4",
    }
    assert imported.isdisjoint(forbidden)
    # No clock reads: timestamps are explicit arguments.
    assert "datetime.now(" not in source
    assert "utcnow(" not in source


def test_canonical_target_spec_is_machine_complete() -> None:
    spec = target_spec()
    spec.validate()
    payload = spec.to_dict()
    for key in (
        "schema_version",
        "reference_source",
        "reference_field",
        "direction",
        "threshold",
        "horizon_sessions",
        "resolution_policy",
        "resolution_policy_version",
        "exchange_calendar",
        "session_semantics",
        "timezone",
        "grace_sessions",
        "revision_policy",
    ):
        assert key in payload
    assert payload["timezone"] == "UTC"
    assert "XNYS" in spec.canonical_definition()


def test_target_spec_rejects_non_utc_timezone() -> None:
    with pytest.raises(InvalidForecastError, match="UTC"):
        target_spec(timezone="Europe/Prague").validate()


def test_target_spec_hash_is_stable_and_change_sensitive() -> None:
    assert target_spec().spec_hash == target_spec().spec_hash
    changed = target_spec(horizon_sessions=4)
    assert changed.spec_hash != target_spec().spec_hash


# ---------------------------------------------------------------------------
# Probability contract (BLOCKER A + statistical acceptance)
# ---------------------------------------------------------------------------


def test_binary_probability_must_be_finite_and_in_unit_interval() -> None:
    for bad in (Decimal("-0.1"), Decimal("1.1")):
        with pytest.raises(InvalidForecastError):
            ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=bad).validate()
    with pytest.raises(InvalidForecastError):
        ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("NaN")).validate()


def test_multiclass_distribution_requires_normalisation() -> None:
    ok = ProbabilityDistribution(
        kind=OutcomeKind.MULTICLASS,
        probabilities={"UP": Decimal("0.5"), "FLAT": Decimal("0.2"), "DOWN": Decimal("0.3")},
    )
    ok.validate()
    bad = ProbabilityDistribution(
        kind=OutcomeKind.MULTICLASS,
        probabilities={"UP": Decimal("0.5"), "DOWN": Decimal("0.3")},
    )
    with pytest.raises(InvalidForecastError, match="normalizovaná"):
        bad.validate()


def test_canonical_record_rejects_out_of_range_probability() -> None:
    # The canonical ForecastRecord path must enforce the probability contract,
    # not only the standalone ProbabilityDistribution.validate() helper.
    for bad in (Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")):
        with pytest.raises(InvalidForecastError):
            emitted(distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=bad))


def test_canonical_record_rejects_unnormalised_multiclass() -> None:
    with pytest.raises(InvalidForecastError, match="normalizovaná"):
        emitted(
            target=target_spec(outcome_kind=OutcomeKind.MULTICLASS),
            distribution=ProbabilityDistribution(
                kind=OutcomeKind.MULTICLASS,
                probabilities={"UP": Decimal("0.5"), "DOWN": Decimal("0.3")},
            ),
        )


def test_multiclass_emitted_forecast_persists_and_round_trips() -> None:
    # A valid multi-class FORECAST_EMITTED record carries its probability in the
    # distribution, not in the scalar ``raw_probability`` column. The DB presence
    # constraint must accept that shape instead of rejecting a legal forecast.
    store, _ = ledger()
    record = emitted(
        target=target_spec(outcome_kind=OutcomeKind.MULTICLASS),
        distribution=ProbabilityDistribution(
            kind=OutcomeKind.MULTICLASS,
            probabilities={"UP": Decimal("0.5"), "FLAT": Decimal("0.2"), "DOWN": Decimal("0.3")},
        ),
    )
    assert record.distribution.p_event is None
    assert store.commit(record).outcome is CommitOutcome.CREATED
    loaded = store.read(record.forecast_id)
    assert loaded is not None
    assert loaded.content_hash() == record.content_hash()
    assert loaded.distribution.probabilities == {
        "UP": Decimal("0.5"),
        "FLAT": Decimal("0.2"),
        "DOWN": Decimal("0.3"),
    }


def test_probability_presence_constraint_is_outcome_kind_aware() -> None:
    # Guards the fix: the presence constraint must branch on outcome_kind, or a
    # multi-class emitted forecast becomes unpersistable and coverage collapses.
    constraint = next(
        c
        for c in ForecastLedgerRecord.__table_args__
        if getattr(c, "name", None) == "ck_forecast_ledger_probability_presence"
    )
    sql = str(constraint.sqltext)
    assert "outcome_kind = 'BINARY'" in sql
    assert "outcome_kind = 'MULTICLASS'" in sql


def test_raw_and_calibrated_probability_are_separate_evidence() -> None:
    record = emitted(
        calibrated=CalibratedProbability(
            probability=Decimal("0.55"),
            calibrator_id="isotonic-v1",
            calibrator_version="2026-09-01",
            method="isotonic",
        )
    )
    assert record.distribution.p_event == Decimal("0.62")
    assert record.calibrated is not None
    assert record.calibrated.probability == Decimal("0.55")
    view = record.to_calibration_view()
    assert view["probability"] == "0.620000000000"
    assert view["calibrated_probability"] == "0.550000000000"
    # Calibration is a separate, content-addressed piece of evidence.
    assert record.to_dict()["calibrated"]["calibrator_id"] == "isotonic-v1"


def test_calibrator_identity_is_mandatory_when_calibrated_present() -> None:
    with pytest.raises(InvalidForecastError):
        CalibratedProbability(
            probability=Decimal("0.5"),
            calibrator_id="",
            calibrator_version="v1",
            method="isotonic",
        ).validate()


def test_confidence_is_not_a_probability_substitute() -> None:
    record = emitted(confidence=Decimal("0.9"))
    assert record.confidence == Decimal("0.9")
    assert record.distribution.p_event == Decimal("0.62")
    # confidence lives outside the distribution payload
    assert "confidence" not in record.distribution.to_dict()


# ---------------------------------------------------------------------------
# PIT / causality
# ---------------------------------------------------------------------------


def test_future_snapshot_fails_closed() -> None:
    with pytest.raises(InvalidForecastError, match="PIT"):
        lineage(market_snapshot_as_of=DECISION_TIME + timedelta(seconds=1)).validate(DECISION_TIME)


def test_future_baseline_fails_closed() -> None:
    with pytest.raises(InvalidForecastError, match="PIT"):
        BaselineProbability(
            probability=Decimal("0.5"),
            source="hist",
            source_version="v1",
            as_of=DECISION_TIME + timedelta(minutes=1),
        ).validate(DECISION_TIME)


def test_created_at_cannot_precede_decision_time() -> None:
    with pytest.raises(InvalidForecastError, match="created_at"):
        emitted(created_at=DECISION_TIME - timedelta(seconds=1))


def test_resolution_must_be_after_decision_time() -> None:
    with pytest.raises(InvalidForecastError, match="resolution_at"):
        emitted(resolution_at=DECISION_TIME)


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError):
        emitted(decision_time=datetime(2026, 9, 24, 14, 30))


# Same instant expressed with a non-UTC offset. ``14:30+00:00`` and
# ``16:30+02:00`` are one canonical UTC decision, so the content-addressed
# identity must not depend on the representation's offset.
PRAGUE = timezone(timedelta(hours=2))


def _offset(dt: datetime) -> datetime:
    return dt.astimezone(PRAGUE)


def test_equivalent_utc_instants_share_one_canonical_identity() -> None:
    base = emitted()
    shifted = emitted(
        created_at=_offset(CREATED_AT),
        decision_time=_offset(DECISION_TIME),
        resolution_at=_offset(RESOLUTION_AT),
    )
    assert base.decision_time == shifted.decision_time
    assert base.decision_identity() == shifted.decision_identity()
    assert base.content_hash() == shifted.content_hash()
    assert base.forecast_id == shifted.forecast_id


def test_offset_aware_lineage_and_baseline_are_canonical() -> None:
    base = emitted()
    shifted = emitted(
        lineage=lineage(market_snapshot_as_of=_offset(DECISION_TIME - timedelta(minutes=5))),
        baseline=BaselineProbability(
            probability=Decimal("0.5"),
            source="historical_frequency",
            source_version="hf-v1",
            as_of=_offset(DECISION_TIME - timedelta(days=1)),
        ),
    )
    assert base.decision_identity() == shifted.decision_identity()
    assert base.content_hash() == shifted.content_hash()


def test_equivalent_utc_instants_do_not_duplicate_a_forecast() -> None:
    store, _ = ledger()
    base = emitted()
    shifted = emitted(
        created_at=_offset(CREATED_AT),
        decision_time=_offset(DECISION_TIME),
        resolution_at=_offset(RESOLUTION_AT),
    )
    assert store.commit(base).outcome is CommitOutcome.CREATED
    assert store.commit(shifted).outcome is CommitOutcome.DUPLICATE_IDEMPOTENT
    assert store.count() == 1


def test_offset_aware_timestamp_round_trips_and_revalidates() -> None:
    # Regression: an offset-shifted aware record used to persist its wall-clock
    # digits, so the read-back content hash differed from the original and the
    # re-read record failed immutability validation.
    store, _ = ledger()
    record = emitted(
        created_at=_offset(CREATED_AT),
        decision_time=_offset(DECISION_TIME),
        resolution_at=_offset(RESOLUTION_AT),
    )
    store.commit(record)
    loaded = store.read(record.forecast_id)
    assert loaded is not None
    assert loaded.decision_time == DECISION_TIME
    assert loaded.content_hash() == record.content_hash()
    loaded.validate()


# ---------------------------------------------------------------------------
# Fail-closed degraded statuses (no fabricated probability)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,reason",
    [
        (ForecastStatus.ABSTAINED, DegradedReason.POLICY_ABSTAIN),
        (ForecastStatus.NO_FORECAST, DegradedReason.MODEL_UNAVAILABLE),
        (ForecastStatus.INVALID_DATA, DegradedReason.STALE_DATA),
        (ForecastStatus.NOT_EVALUATED, DegradedReason.NOT_EVALUATED),
    ],
)
def test_fail_closed_statuses_carry_no_probability(
    status: ForecastStatus, reason: DegradedReason
) -> None:
    record = degraded(status, reason)
    assert record.distribution.is_empty
    assert record.degraded_reason is reason
    record.validate()


def test_emitted_status_requires_a_real_probability() -> None:
    with pytest.raises(InvalidForecastError, match="fabrikovanou"):
        emitted(distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY))


def test_fail_closed_status_requires_a_reason() -> None:
    with pytest.raises(InvalidForecastError, match="degraded_reason"):
        ForecastRecord.create(
            status=ForecastStatus.NO_FORECAST,
            scope_kind=ScopeKind.ASSET,
            scope_id="SPY",
            opportunity_id="opp-1",
            preregistered=True,
            created_at=CREATED_AT,
            decision_time=DECISION_TIME,
            resolution_at=RESOLUTION_AT,
            target=target_spec(),
            distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY),
            lineage=lineage(),
        )


def test_fail_closed_status_must_not_carry_a_probability() -> None:
    with pytest.raises(InvalidForecastError, match="fabrikace"):
        degraded(
            ForecastStatus.NO_FORECAST,
            DegradedReason.MISSING_DATA,
            distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.5")),
        )


def test_stale_and_future_data_have_explicit_reasons() -> None:
    for reason in (DegradedReason.STALE_DATA, DegradedReason.FUTURE_DATA):
        record = degraded(ForecastStatus.INVALID_DATA, reason)
        assert record.to_dict()["degraded_reason"] == str(reason)


# ---------------------------------------------------------------------------
# Immutability + content addressing (BLOCKER C)
# ---------------------------------------------------------------------------


def test_forecast_id_is_content_addressed_and_cannot_be_fabricated() -> None:
    record = emitted()
    assert record.forecast_id == ForecastRecord.compute_forecast_id(
        record.decision_identity(), record.content_hash()
    )
    with pytest.raises(InvalidForecastError, match="odvozuje"):
        ForecastRecord.create(
            forecast_id="fabricated",
            status=ForecastStatus.FORECAST_EMITTED,
            scope_kind=ScopeKind.ASSET,
            scope_id="SPY",
            opportunity_id="opp-1",
            preregistered=True,
            created_at=CREATED_AT,
            decision_time=DECISION_TIME,
            resolution_at=RESOLUTION_AT,
            target=target_spec(),
            distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.5")),
            lineage=lineage(),
        )


def test_mutated_probability_breaks_the_content_hash() -> None:
    record = emitted()
    tampered = ForecastRecord(
        forecast_id=record.forecast_id,
        status=record.status,
        scope_kind=record.scope_kind,
        scope_id=record.scope_id,
        opportunity_id=record.opportunity_id,
        preregistered=record.preregistered,
        created_at=record.created_at,
        decision_time=record.decision_time,
        resolution_at=record.resolution_at,
        target=record.target,
        distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.99")),
        lineage=record.lineage,
    )
    with pytest.raises(InvalidForecastError, match="forecast_id neodpovídá"):
        tampered.validate()


def test_correction_is_a_new_version_not_an_update() -> None:
    original = emitted()
    corrected = emitted(
        distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.60")),
        prior_forecast_id=original.forecast_id,
        created_at=CREATED_AT + timedelta(minutes=1),
    )
    assert corrected.forecast_id != original.forecast_id
    assert corrected.prior_forecast_id == original.forecast_id
    assert original.distribution.p_event == Decimal("0.62")


def test_prior_forecast_id_cannot_reference_itself() -> None:
    original = emitted()
    with pytest.raises(InvalidForecastError, match="sebe"):
        ForecastRecord(
            forecast_id=original.forecast_id,
            status=ForecastStatus.FORECAST_EMITTED,
            scope_kind=ScopeKind.ASSET,
            scope_id="SPY",
            opportunity_id="opp-1",
            preregistered=True,
            created_at=CREATED_AT,
            decision_time=DECISION_TIME,
            resolution_at=RESOLUTION_AT,
            target=target_spec(),
            distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.6")),
            lineage=lineage(),
            prior_forecast_id=original.forecast_id,
        ).validate()


# ---------------------------------------------------------------------------
# Decision identity (BLOCKER C): no collision, snapshot change is new forecast
# ---------------------------------------------------------------------------


def test_two_model_versions_never_collide() -> None:
    a = emitted()
    b = emitted(lineage=lineage(model_version="2.0.0"))
    assert a.decision_identity() != b.decision_identity()
    assert a.forecast_id != b.forecast_id


def test_retry_with_changed_snapshot_is_not_a_duplicate() -> None:
    a = emitted()
    b = emitted(lineage=lineage(market_snapshot_hash="c" * 64))
    assert a.decision_identity() != b.decision_identity()


def test_same_inputs_are_deterministic() -> None:
    assert emitted().forecast_id == emitted().forecast_id


# ---------------------------------------------------------------------------
# Structural ordering gate (BLOCKER C)
# ---------------------------------------------------------------------------


def test_decision_requires_a_committed_forecast_reference() -> None:
    store, _ = ledger()
    reference = DecisionForecastReference(
        forecast_id="missing", decision_identity="whatever", decision_time=DECISION_TIME
    )
    with pytest.raises(ForecastGateError, match="neexistující"):
        require_forecast_reference(store, reference)


def test_decision_is_blocked_by_degraded_forecast() -> None:
    store, _ = ledger()
    record = degraded(ForecastStatus.NO_FORECAST, DegradedReason.MISSING_DATA)
    store.commit(record)
    with pytest.raises(ForecastGateError, match="FORECAST_EMITTED"):
        require_forecast_reference(
            store,
            DecisionForecastReference(
                forecast_id=record.forecast_id,
                decision_identity=record.decision_identity(),
                decision_time=DECISION_TIME,
            ),
        )


def test_decision_callable_runs_only_after_the_gate_passes() -> None:
    store, _ = ledger()
    record = emitted()
    store.commit(record)
    called: list[str] = []

    def decide(f: ForecastRecord) -> str:
        called.append(f.forecast_id)
        return "paper-intent"

    result = decide_with_forecast(
        store,
        DecisionForecastReference(
            forecast_id=record.forecast_id,
            decision_identity=record.decision_identity(),
            decision_time=DECISION_TIME,
        ),
        decide,
    )
    assert result == "paper-intent"
    assert called == [record.forecast_id]

    # Degraded evidence must never invoke the downstream decision.
    blocked = degraded(
        ForecastStatus.INVALID_DATA, DegradedReason.STALE_DATA, opportunity_id="opp-blocked"
    )
    store.commit(blocked)
    with pytest.raises(ForecastGateError):
        decide_with_forecast(
            store,
            DecisionForecastReference(
                forecast_id=blocked.forecast_id,
                decision_identity=blocked.decision_identity(),
                decision_time=DECISION_TIME,
            ),
            decide,
        )
    assert called == [record.forecast_id]


def test_causality_violation_is_rejected_structurally() -> None:
    store, _ = ledger()
    record = emitted()
    store.commit(record)
    with pytest.raises(ForecastGateError, match="causality"):
        require_forecast_reference(
            store,
            DecisionForecastReference(
                forecast_id=record.forecast_id,
                decision_identity=record.decision_identity(),
                decision_time=DECISION_TIME - timedelta(minutes=1),
            ),
        )


def test_mismatched_decision_identity_is_rejected() -> None:
    store, _ = ledger()
    record = emitted()
    store.commit(record)
    with pytest.raises(ForecastGateError, match="identita"):
        require_forecast_reference(
            store,
            DecisionForecastReference(
                forecast_id=record.forecast_id,
                decision_identity="other",
                decision_time=DECISION_TIME,
            ),
        )


# ---------------------------------------------------------------------------
# Append-only persistence
# ---------------------------------------------------------------------------


def test_commit_creates_then_is_idempotent() -> None:
    store, _ = ledger()
    record = emitted()
    first = store.commit(record)
    assert first.outcome is CommitOutcome.CREATED
    second = store.commit(record)
    assert second.outcome is CommitOutcome.DUPLICATE_IDEMPOTENT
    assert second.forecast_id == first.forecast_id
    assert store.count() == 1


def test_replay_and_restart_do_not_duplicate_a_forecast() -> None:
    engine = create_engine("sqlite://")
    import quantlab.phase7  # noqa: F401

    ForecastLedgerRecord.__table__.create(engine)
    factory = sessionmaker(engine)
    record = emitted()
    ForecastLedger(factory).commit(record)
    # New ledger instance (restart) over the same durable evidence.
    restarted = ForecastLedger(sessionmaker(engine))
    result = restarted.commit(record)
    assert result.outcome is CommitOutcome.DUPLICATE_IDEMPOTENT
    assert restarted.count() == 1


def test_conflicting_content_for_same_identity_fails_closed() -> None:
    store, _ = ledger()
    original = emitted()
    store.commit(original)
    conflicting = ForecastRecord.create(
        status=ForecastStatus.FORECAST_EMITTED,
        scope_kind=ScopeKind.ASSET,
        scope_id="SPY",
        opportunity_id="opp-2026-09-24-SPY",
        preregistered=True,
        created_at=CREATED_AT,
        decision_time=DECISION_TIME,
        resolution_at=RESOLUTION_AT,
        target=target_spec(),
        distribution=ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.99")),
        lineage=lineage(),
    )
    assert conflicting.decision_identity() == original.decision_identity()
    with pytest.raises(ForecastConflictError):
        store.commit(conflicting)
    assert store.count() == 1


def test_read_round_trips_the_record_and_content_hash() -> None:
    store, _ = ledger()
    record = emitted(
        calibrated=CalibratedProbability(
            probability=Decimal("0.55"),
            calibrator_id="iso",
            calibrator_version="v1",
            method="isotonic",
        ),
        uncertainty={"sample_size": "250"},
    )
    store.commit(record)
    loaded = store.read(record.forecast_id)
    assert loaded is not None
    assert loaded.content_hash() == record.content_hash()
    assert loaded.decision_identity() == record.decision_identity()
    assert loaded.distribution.p_event == Decimal("0.62")
    assert loaded.calibrated is not None and loaded.calibrated.probability == Decimal("0.55")
    assert loaded.uncertainty == {"sample_size": "250"}
    assert store.for_decision_identity(record.decision_identity()) is not None


# ---------------------------------------------------------------------------
# Persisted-evidence integrity verification on the read path
# ---------------------------------------------------------------------------


def _raw_insert(engine: object, row: dict[str, object]) -> None:
    """Insert a hand-built row, bypassing the append-only commit contract.

    The PostgreSQL trigger blocks UPDATE/DELETE for the runtime role, but a
    privileged writer, a hand-written INSERT or a partially applied restore can
    still land a row whose scalar columns disagree with the hashed
    ``record_json`` snapshot. ``_row_payload`` reproduces exactly what
    ``ForecastLedger.commit`` writes, so mutating it is the faithful simulation
    of that out-of-band write.
    """
    from sqlalchemy import text as sql_text

    payload = dict(row)
    for column in (
        "raw_probability",
        "calibrated_probability",
        "baseline_probability",
        "confidence",
    ):
        if payload.get(column) is not None:
            payload[column] = str(payload[column])
    columns = ", ".join(payload)
    placeholders = ", ".join(f":{name}" for name in payload)
    with engine.connect() as connection:  # type: ignore[attr-defined]
        with connection.begin():
            connection.execute(
                sql_text(f"INSERT INTO forecast_ledger ({columns}) VALUES ({placeholders})"),
                payload,
            )


def _corrupted_row(record: ForecastRecord, mutate: object) -> ForecastLedger:
    """Commit a genuine row, then land a tampered clone beside it.

    Returns the ledger so the caller can attempt to read the corrupted row.
    """
    from quantlab.forecast_ledger import _row_payload

    store, engine = ledger()
    store.commit(record)
    row = dict(_row_payload(record))
    mutate(row)  # type: ignore[operator]
    row["forecast_id"] = "5" * 64
    row["decision_identity"] = "4" * 64
    row["opportunity_id"] = "opp-tampered"
    _raw_insert(engine, row)
    return store


@pytest.mark.parametrize(
    "mutate",
    [
        # A tampered probability column: the scalar column no longer matches the
        # hashed distribution, so the record would otherwise report p=0.62 while
        # the database says 0.99.
        lambda row: row.update(raw_probability=Decimal("0.99")),
        # A tampered distribution_json with the scalar column left intact.
        lambda row: row.update(
            distribution_json='{"kind":"BINARY","p_event":"0.010000000000","probabilities":{}}'
        ),
        # A tampered record_json snapshot only (forecast_id/decision_identity
        # columns stay intact), i.e. silent mutation of persisted evidence.
        lambda row: row.update(
            record_json=str(row["record_json"]).replace('"0.620000000000"', '"0.990000000000"')
        ),
        # Fields that are NOT part of the decision identity but are still
        # persisted evidence and must not be silently mutable.
        lambda row: row.update(regime="EVIL"),
        lambda row: row.update(source="EVIL"),
        lambda row: row.update(uncertainty_json='{"sample_size":"99999"}'),
        lambda row: row.update(confidence=Decimal("0.01")),
        lambda row: row.update(preregistered=0),
        lambda row: row.update(degraded_detail="EVIL"),
        lambda row: row.update(calibrator_version="evil"),
        lambda row: row.update(
            target_spec_json=str(row["target_spec_json"]).replace(
                '"threshold":"0.01"', '"threshold":"0.99"'
            )
        ),
    ],
)
def test_tampered_persisted_evidence_fails_closed_on_read(mutate: object) -> None:
    record = emitted(
        calibrated=CalibratedProbability(
            probability=Decimal("0.55"),
            calibrator_id="iso",
            calibrator_version="v1",
            method="isotonic",
        ),
        uncertainty={"sample_size": "250"},
    )
    store = _corrupted_row(record, mutate)
    # Reading the row must not hand back a record that disagrees with its own
    # immutable evidence: fail closed instead of admitting a mutated forecast.
    with pytest.raises(ForecastIntegrityError):
        store.read("5" * 64)


def test_tampered_evidence_is_never_admitted_by_the_decision_gate() -> None:
    record = emitted()
    store, engine = ledger()
    store.commit(record)
    from quantlab.forecast_ledger import _row_payload

    row = dict(_row_payload(record))
    row["raw_probability"] = Decimal("0.99")
    row["forecast_id"] = "6" * 64
    row["decision_identity"] = "7" * 64
    row["opportunity_id"] = "opp-gate"
    _raw_insert(engine, row)
    store.commit(record)  # keep the genuine row too: only the clone is corrupt
    with pytest.raises(ForecastIntegrityError):
        require_forecast_reference(
            store,
            DecisionForecastReference(
                forecast_id="6" * 64, decision_identity="7" * 64, decision_time=DECISION_TIME
            ),
        )


def test_untampered_evidence_still_passes_integrity_verification() -> None:
    """Control: every legitimate shape must reproduce its own identity."""
    store, _ = ledger()
    record = emitted(
        calibrated=CalibratedProbability(
            probability=Decimal("0.55"),
            calibrator_id="iso",
            calibrator_version="v1",
            method="isotonic",
        ),
        uncertainty={"sample_size": "250"},
    )
    store.commit(record)
    loaded = store.read(record.forecast_id)
    assert loaded is not None
    assert loaded.content_hash() == record.content_hash()
    loaded.validate()


def _forecast_payload() -> dict[str, object]:
    """Raw constructor payload for a canonical emitted forecast."""
    return {
        "status": ForecastStatus.FORECAST_EMITTED,
        "scope_kind": ScopeKind.ASSET,
        "scope_id": "SPY",
        "opportunity_id": "opp-contract-violation",
        "preregistered": True,
        "created_at": CREATED_AT,
        "decision_time": DECISION_TIME,
        "resolution_at": RESOLUTION_AT,
        "target": target_spec(),
        "distribution": ProbabilityDistribution(kind=OutcomeKind.BINARY, p_event=Decimal("0.62")),
        "lineage": lineage(),
        "baseline": BaselineProbability(
            probability=Decimal("0.5"),
            source="historical_frequency",
            source_version="hf-v1",
            as_of=DECISION_TIME - timedelta(days=1),
        ),
        "regime": "TRENDING_UP",
        "source": "phase6-current",
    }


def _contract_violating_record(mutate: object) -> ForecastRecord:
    """Build an *identity-consistent* record that violates the canonical contract.

    The record bypasses ``ForecastRecord.create``/``validate`` and derives its
    own ``content_hash``/``forecast_id``, so the persisted row agrees with its
    own hashed evidence. Identity verification therefore cannot reject it; only
    a contract re-validation can. This is the faithful simulation of a
    privileged writer landing such a row.
    """
    payload = _forecast_payload()
    mutate(payload)  # type: ignore[operator]
    provisional = ForecastRecord(forecast_id="", **payload)  # type: ignore[arg-type]
    forecast_id = ForecastRecord.compute_forecast_id(
        provisional.decision_identity(), provisional.content_hash()
    )
    return ForecastRecord(forecast_id=forecast_id, **payload)  # type: ignore[arg-type]


def _forged_contract_row(
    mutate: object,
) -> tuple[ForecastLedger, object, ForecastRecord]:
    """Raw-insert an identity-consistent, contract-invalid row."""
    from quantlab.forecast_ledger import _row_payload

    store, engine = ledger()
    forged = _contract_violating_record(mutate)
    _raw_insert(engine, dict(_row_payload(forged)))
    return store, engine, forged


@pytest.mark.parametrize(
    "mutate",
    [
        # schema_version naming an unknown/unsupported contract version.
        lambda payload: payload.update(schema_version=FORECAST_SCHEMA_VERSION + 98),
        # FORECAST_EMITTED carrying a degraded marker (fail-open vs fail-closed).
        lambda payload: payload.update(degraded_detail="synthetic degraded marker"),
        # Look-ahead: the market snapshot was observed AFTER the decision.
        lambda payload: payload.update(
            lineage=lineage(market_snapshot_as_of=DECISION_TIME + timedelta(minutes=1))
        ),
        # Look-ahead: the baseline probability was observed AFTER the decision.
        lambda payload: payload.update(
            baseline=BaselineProbability(
                probability=Decimal("0.5"),
                source="historical_frequency",
                source_version="hf-v1",
                as_of=DECISION_TIME + timedelta(hours=3),
            )
        ),
    ],
)
def test_identity_consistent_but_contract_invalid_evidence_fails_closed(
    mutate: object,
) -> None:
    """A self-consistent row that breaks the #266 contract must still fail closed.

    These rows reproduce their own ``content_hash``/``forecast_id``/``record_json``
    (the DB check constraints do not cover them), so only re-running the canonical
    contract validation on read can stop them from driving a PAPER decision.
    """
    from sqlalchemy import text as sql_text

    store, engine, forged = _forged_contract_row(mutate)
    # Self-proving guard: the stored row IS identity-consistent, so the
    # rejection below cannot come from the identity check.
    with engine.connect() as connection:  # type: ignore[attr-defined]
        stored_hash = connection.execute(
            sql_text("SELECT content_hash FROM forecast_ledger WHERE forecast_id = :f"),
            {"f": forged.forecast_id},
        ).scalar()
    assert stored_hash == forged.content_hash()
    with pytest.raises(ForecastIntegrityError, match="kontraktu"):
        store.read(forged.forecast_id)
    with pytest.raises(ForecastIntegrityError, match="kontraktu"):
        require_forecast_reference(
            store,
            DecisionForecastReference(
                forecast_id=forged.forecast_id,
                decision_identity=forged.decision_identity(),
                decision_time=DECISION_TIME,
            ),
        )


# ---------------------------------------------------------------------------
# Undecodable persisted evidence must stay inside the ledger error contract
# ---------------------------------------------------------------------------

#: Every ``ForecastRecord`` field that is stored as canonical JSON or as a
#: typed scalar, paired with the minimal shape that *cannot* be decoded into
#: canonical evidence. ``NaN`` is deliberately absent: ``Decimal("NaN")``
#: parses, is rejected by ``validate()`` as non-finite, and already fails
#: closed as a contract violation.
UNDECODABLE_EVIDENCE = [
    # A probability literal that is not a finite number. ``Decimal.quantize``
    # raises ``decimal.InvalidOperation`` (an ``ArithmeticError``, not a
    # ``ValueError``) while the record is being content-addressed.
    (
        "distribution.p_event = Infinity",
        lambda row: row.update(
            distribution_json='{"kind":"BINARY","p_event":"Infinity","probabilities":{}}',
            record_json=str(row["record_json"]).replace('"0.620000000000"', '"Infinity"'),
        ),
    ),
    (
        "distribution.p_event = not-a-number",
        lambda row: row.update(
            distribution_json='{"kind":"BINARY","p_event":"not-a-number","probabilities":{}}',
            record_json=str(row["record_json"]).replace('"0.620000000000"', '"not-a-number"'),
        ),
    ),
    # A JSON document whose top level is not the expected object.
    (
        "distribution_json is not an object",
        lambda row: row.update(distribution_json="[]"),
    ),
    # An unparseable canonical target threshold.
    (
        "target.threshold = not-a-number",
        lambda row: row.update(
            target_spec_json=str(row["target_spec_json"]).replace(
                '"threshold":"0.01"', '"threshold":"x"'
            )
        ),
    ),
    # A datetime column whose stored value is not ISO-8601.
    (
        "market_snapshot_as_of is not a datetime",
        lambda row: row.update(market_snapshot_as_of="not-a-date"),
    ),
    (
        "created_at is not a datetime",
        lambda row: row.update(created_at="not-a-date"),
    ),
    (
        "baseline_as_of is not a datetime",
        lambda row: row.update(baseline_as_of="not-a-date"),
    ),
]


def test_decodable_evidence_control_is_not_falsely_rejected() -> None:
    """Control: a legitimate row still passes, so the checks below are specific.

    Also proves the raw-insert helper used by these tests lands a row that
    really is readable, i.e. the rejections cannot come from a broken fixture.
    """
    store, _ = ledger()
    record = emitted()
    store.commit(record)
    loaded = store.read(record.forecast_id)
    assert loaded is not None
    assert loaded.content_hash() == record.content_hash()


@pytest.mark.parametrize(
    "label,mutate", UNDECODABLE_EVIDENCE, ids=[case[0] for case in UNDECODABLE_EVIDENCE]
)
def test_undecodable_persisted_evidence_fails_closed_as_integrity_error(
    label: str, mutate: object
) -> None:
    """Undecodable evidence must fail closed inside the ledger error contract.

    A privileged writer (or a hand-written INSERT, or a partially applied
    restore) can land a row whose stored value cannot be decoded at all. Before
    this guard, ``Decimal.quantize``/``Decimal(...)`` raised
    ``decimal.InvalidOperation`` and a non-ISO datetime raised ``ValueError``
    *outside* ``ForecastLedgerError`` -- the ledger's documented fail-closed
    signal. A decision gate that only catches ``ForecastLedgerError`` would then
    propagate an unexpected exception instead of failing closed, and the
    integrity boundary would be unenforced for this evidence class.
    """
    store, engine = ledger()
    record = emitted()
    store.commit(record)
    row = dict(_row_payload(record))
    mutate(row)  # type: ignore[operator]
    # A distinct primary key/identity so the forged row lands beside the genuine
    # one instead of colliding with it.
    row["forecast_id"] = "d" * 64
    row["decision_identity"] = "e" * 64
    row["opportunity_id"] = "opp-undecodable"
    _raw_insert(engine, row)

    # The stored row really is present (so the rejection is a decode failure,
    # not a missing row).
    with engine.connect() as connection:  # type: ignore[attr-defined]
        present = connection.execute(
            text("SELECT 1 FROM forecast_ledger WHERE forecast_id = :f"), {"f": "d" * 64}
        ).first()
    assert present is not None

    with pytest.raises(ForecastIntegrityError):
        store.read("d" * 64)
    # The decision gate must fail closed for the same reason, never invoke the
    # downstream decision and never let a non-ledger exception escape.
    called: list[str] = []
    with pytest.raises(ForecastIntegrityError):
        require_forecast_reference(
            store,
            DecisionForecastReference(
                forecast_id="d" * 64,
                decision_identity="e" * 64,
                decision_time=DECISION_TIME,
            ),
        )
    with pytest.raises(ForecastIntegrityError):
        decide_with_forecast(
            store,
            DecisionForecastReference(
                forecast_id="d" * 64,
                decision_identity="e" * 64,
                decision_time=DECISION_TIME,
            ),
            lambda f: called.append(f.forecast_id),
        )
    assert called == []


def test_undecodable_evidence_for_decision_identity_lookup_fails_closed() -> None:
    """The identity lookup path must fail closed on the same evidence class."""
    store, engine = ledger()
    record = emitted()
    store.commit(record)
    row = dict(_row_payload(record))
    row["market_snapshot_as_of"] = "not-a-date"
    row["forecast_id"] = "f" * 64
    row["decision_identity"] = "0" * 64
    row["opportunity_id"] = "opp-undecodable-lookup"
    _raw_insert(engine, row)
    with pytest.raises(ForecastIntegrityError):
        store.for_decision_identity("0" * 64)


# ---------------------------------------------------------------------------
# Coverage accounting (STAT-BLOCKER A)
# ---------------------------------------------------------------------------


def test_coverage_keeps_abstentions_in_the_denominator() -> None:
    records = [
        emitted(opportunity_id="o1"),
        emitted(opportunity_id="o2"),
        degraded(ForecastStatus.ABSTAINED, DegradedReason.POLICY_ABSTAIN, opportunity_id="o3"),
        degraded(ForecastStatus.NO_FORECAST, DegradedReason.MISSING_DATA, opportunity_id="o4"),
        degraded(ForecastStatus.INVALID_DATA, DegradedReason.STALE_DATA, opportunity_id="o5"),
        degraded(ForecastStatus.NOT_EVALUATED, DegradedReason.NOT_EVALUATED, opportunity_id="o6"),
    ]
    report = coverage_report(records)
    assert isinstance(report, CoverageReport)
    assert report.eligible == 6
    assert report.emitted == 2
    assert report.abstained == 1
    assert report.no_forecast == 1
    assert report.invalid_data == 1
    assert report.not_evaluated == 1
    assert report.coverage == Decimal("0.333333")


def test_coverage_is_zero_for_an_empty_scope() -> None:
    report = coverage_report([])
    assert report.eligible == 0
    assert report.coverage == Decimal("0")


# ---------------------------------------------------------------------------
# Statistical lineage (STAT-BLOCKER B/C)
# ---------------------------------------------------------------------------


def test_target_or_horizon_change_creates_a_new_trial_family_identity() -> None:
    base = emitted()
    switched = emitted(target=target_spec(horizon_sessions=4))
    assert base.target.spec_hash != switched.target.spec_hash
    assert base.decision_identity() != switched.decision_identity()
    # The trial family is explicit, not implied by the record.
    assert base.lineage.trial_family_id == "tf-momentum-1d"
    assert base.to_calibration_view()["trial_family_id"] == "tf-momentum-1d"


def test_lineage_carries_full_multiple_testing_identity() -> None:
    payload = emitted().to_dict()["lineage"]
    assert payload["trial_family_id"] == "tf-momentum-1d"
    assert payload["research_experiment_id"] == "exp-001"
    assert payload["deployment_id"] == "dep-001"
    assert payload["feature_extractor_version"] == "feat-v3"
    assert payload["code_sha"] == "a" * 64


def test_schema_version_is_pinned() -> None:
    assert emitted().schema_version == FORECAST_SCHEMA_VERSION
    with pytest.raises(InvalidForecastError, match="schema_version"):
        emitted(schema_version=FORECAST_SCHEMA_VERSION + 1)


# ---------------------------------------------------------------------------
# Resource bounds (#190)
# ---------------------------------------------------------------------------


def test_distribution_class_count_is_bounded() -> None:
    from quantlab.forecast_ledger import MAX_DISTRIBUTION_CLASSES

    classes = MAX_DISTRIBUTION_CLASSES + 1
    share = Decimal(1) / Decimal(classes)
    with pytest.raises(InvalidForecastError, match="limit tříd"):
        ProbabilityDistribution(
            kind=OutcomeKind.MULTICLASS,
            probabilities={f"c{i}": share for i in range(classes)},
        ).validate()


def test_coverage_report_limit_fails_closed() -> None:
    import quantlab.forecast_ledger as module

    original = module.MAX_COVERAGE_OPPORTUNITIES
    module.MAX_COVERAGE_OPPORTUNITIES = 1
    try:
        with pytest.raises(ForecastLedgerError, match="coverage"):
            coverage_report([emitted(opportunity_id="o1"), emitted(opportunity_id="o2")])
    finally:
        module.MAX_COVERAGE_OPPORTUNITIES = original


# ---------------------------------------------------------------------------
# PostgreSQL: schema, immutability, runtime role, migration ownership
# ---------------------------------------------------------------------------

POSTGRES = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL integrační službu"
)

RUNTIME_ROLE = "quantlab_runtime_forecast"
RUNTIME_PASSWORD = "forecast-runtime-password"


def _dsn(database: str = "quantlab") -> str:
    from urllib.parse import urlsplit, urlunsplit

    configured = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(configured)
    # Preserve the query string: CI uses a TCP URL, local/alternate setups may
    # point at a Unix socket via ``?host=/path``.
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, ""))


@POSTGRES
def test_postgres_table_exists_with_check_constraints() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = 'forecast_ledger'")
        ).first()
        assert exists is not None
        constraints = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE t.relname = 'forecast_ledger' AND c.contype = 'c'"
                )
            )
        }
    engine.dispose()
    assert "ck_forecast_ledger_probability_presence" in constraints
    assert "ck_forecast_ledger_raw_probability_range" in constraints
    assert "ck_forecast_ledger_resolution_after_decision" in constraints


@POSTGRES
def test_postgres_trigger_rejects_update_and_delete() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            record = emitted(opportunity_id=f"pg-{identity(os.getpid())}")
            ForecastLedger(sessions).commit(record)
            for statement in (
                "UPDATE forecast_ledger SET raw_probability = 0.01 WHERE forecast_id = :id",
                "DELETE FROM forecast_ledger WHERE forecast_id = :id",
            ):
                with pytest.raises(DBAPIError, match="immutable"):
                    with sessions() as session, session.begin():
                        session.execute(text(statement), {"id": record.forecast_id})
        finally:
            transaction.rollback()
    engine.dispose()


@POSTGRES
def test_postgres_read_path_detects_out_of_band_tampering() -> None:
    """The trigger blocks runtime UPDATE, but a privileged writer bypasses it.

    PostgreSQL immutability is enforced by a BEFORE UPDATE/DELETE trigger plus
    runtime-role revocation; a superuser / migrator connection, a hand-written
    INSERT or a partially applied restore is NOT covered by either. The read
    path must therefore verify that the row still reproduces its own identity
    and fail closed instead of admitting mutated evidence.
    """
    from sqlalchemy import text as sql_text

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            store = ForecastLedger(sessions)
            record = emitted(opportunity_id=f"pg-tamper-{identity(os.getpid())}")
            store.commit(record)

            # The BEFORE UPDATE trigger correctly rejects an in-place mutation.
            with pytest.raises(DBAPIError, match="immutable"):
                with sessions() as session, session.begin():
                    session.execute(
                        sql_text(
                            "UPDATE forecast_ledger SET raw_probability = 0.99 "
                            "WHERE forecast_id = :id"
                        ),
                        {"id": record.forecast_id},
                    )

            # A privileged writer can still disable the trigger (or restore a
            # dump), so the read path must not trust the row's own columns.
            with sessions() as session, session.begin():
                session.execute(
                    sql_text(
                        "ALTER TABLE forecast_ledger DISABLE TRIGGER forecast_ledger_immutable"
                    )
                )
                session.execute(
                    sql_text(
                        "UPDATE forecast_ledger SET raw_probability = 0.99 WHERE forecast_id = :id"
                    ),
                    {"id": record.forecast_id},
                )
            with pytest.raises(ForecastIntegrityError):
                store.read(record.forecast_id)
            with pytest.raises(ForecastIntegrityError):
                require_forecast_reference(
                    store,
                    DecisionForecastReference(
                        forecast_id=record.forecast_id,
                        decision_identity=record.decision_identity(),
                        decision_time=DECISION_TIME,
                    ),
                )
        finally:
            transaction.rollback()
    engine.dispose()


@POSTGRES
def test_postgres_read_path_rejects_identity_consistent_contract_violation() -> None:
    """A privileged INSERT can land a self-consistent but contract-invalid row.

    The identity check cannot reject a row that reproduces its own hashed
    evidence, and the DB check constraints do not cover ``schema_version``,
    the fail-closed/degraded marker or PIT look-ahead. Only re-running the
    canonical contract on read keeps such a row from driving a PAPER decision.
    """
    from quantlab.forecast_ledger import _row_payload

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            store = ForecastLedger(sessions)
            forged = _contract_violating_record(
                lambda payload: payload.update(
                    lineage=lineage(market_snapshot_as_of=DECISION_TIME + timedelta(minutes=1))
                )
            )
            payload = _row_payload(forged)
            for column in (
                "raw_probability",
                "calibrated_probability",
                "baseline_probability",
                "confidence",
            ):
                if payload.get(column) is not None:
                    payload[column] = str(payload[column])
            columns = ", ".join(payload)
            placeholders = ", ".join(f":{name}" for name in payload)
            with sessions() as session, session.begin():
                session.execute(
                    text(f"INSERT INTO forecast_ledger ({columns}) VALUES ({placeholders})"),
                    payload,
                )
            with pytest.raises(ForecastIntegrityError, match="kontraktu"):
                store.read(forged.forecast_id)
            with pytest.raises(ForecastIntegrityError, match="kontraktu"):
                require_forecast_reference(
                    store,
                    DecisionForecastReference(
                        forecast_id=forged.forecast_id,
                        decision_identity=forged.decision_identity(),
                        decision_time=DECISION_TIME,
                    ),
                )
        finally:
            transaction.rollback()
    engine.dispose()


@POSTGRES
def test_postgres_read_path_rejects_undecodable_evidence() -> None:
    """A privileged INSERT can land a row whose stored value cannot be decoded.

    PostgreSQL stores the canonical JSON / typed columns verbatim, so a raw
    INSERT (or a partially applied restore) can hold a probability literal that
    ``Decimal`` cannot parse or a datetime column that is not ISO-8601. The
    read path must fail closed as ``ForecastIntegrityError`` -- the ledger's
    documented signal -- instead of letting ``decimal.InvalidOperation`` or
    ``ValueError`` escape to a decision gate that only handles
    ``ForecastLedgerError``.
    """
    from quantlab.forecast_ledger import _row_payload

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            store = ForecastLedger(sessions)
            genuine = emitted(opportunity_id=f"pg-decode-{identity(os.getpid())}")
            store.commit(genuine)
            payload = dict(_row_payload(genuine))
            payload["distribution_json"] = (
                '{"kind":"BINARY","p_event":"Infinity","probabilities":{}}'
            )
            payload["record_json"] = str(payload["record_json"]).replace(
                '"0.620000000000"', '"Infinity"'
            )
            payload["forecast_id"] = "d" * 64
            payload["decision_identity"] = "e" * 64
            payload["opportunity_id"] = f"pg-undecodable-{identity(os.getpid())}"
            for column in (
                "raw_probability",
                "calibrated_probability",
                "baseline_probability",
                "confidence",
            ):
                if payload.get(column) is not None:
                    payload[column] = str(payload[column])
            columns = ", ".join(payload)
            placeholders = ", ".join(f":{name}" for name in payload)
            with sessions() as session, session.begin():
                session.execute(
                    text(f"INSERT INTO forecast_ledger ({columns}) VALUES ({placeholders})"),
                    payload,
                )
            with pytest.raises(ForecastIntegrityError):
                store.read("d" * 64)
            with pytest.raises(ForecastIntegrityError):
                require_forecast_reference(
                    store,
                    DecisionForecastReference(
                        forecast_id="d" * 64,
                        decision_identity="e" * 64,
                        decision_time=DECISION_TIME,
                    ),
                )
        finally:
            transaction.rollback()
    engine.dispose()


@POSTGRES
def test_postgres_append_only_contract_survives_conflict() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            store = ForecastLedger(sessions)
            record = emitted(opportunity_id=f"pg-idem-{identity(os.getpid())}")
            assert store.commit(record).outcome is CommitOutcome.CREATED
            assert store.commit(record).outcome is CommitOutcome.DUPLICATE_IDEMPOTENT
            # Exactly one append-only row for this decision identity (the shared
            # CI database may already contain unrelated forecasts).
            with sessions() as session:
                rows = session.scalar(
                    text("SELECT count(*) FROM forecast_ledger WHERE decision_identity = :d"),
                    {"d": record.decision_identity()},
                )
            assert rows == 1
        finally:
            transaction.rollback()
    engine.dispose()


@POSTGRES
def test_postgres_check_constraint_blocks_fabricated_probability() -> None:
    """The DB itself refuses a fail-closed row carrying a probability (INSERT path)."""
    engine = create_engine(os.environ["DATABASE_URL"])
    record = degraded(ForecastStatus.NO_FORECAST, DegradedReason.MISSING_DATA)
    from quantlab.forecast_ledger import _row_payload

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            sessions = sessionmaker(connection, join_transaction_mode="create_savepoint")
            ForecastLedger(sessions).commit(record)
            payload = _row_payload(record)
            # Simulate a hand-written fabricated probability bypassing the contract.
            payload["raw_probability"] = Decimal("0.5")
            payload["forecast_id"] = "f" * 64
            columns = ", ".join(payload)
            placeholders = ", ".join(f":{name}" for name in payload)
            with pytest.raises((DBAPIError, IntegrityError), match="ck_forecast_ledger"):
                with sessions() as session, session.begin():
                    session.execute(
                        text(f"INSERT INTO forecast_ledger ({columns}) VALUES ({placeholders})"),
                        payload,
                    )
        finally:
            transaction.rollback()
    engine.dispose()


@POSTGRES
def test_postgres_runtime_role_cannot_mutate_forecast_evidence() -> None:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    with psycopg.connect(_dsn(), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (RUNTIME_ROLE,)
        ).fetchone()
        if exists is not None:
            connection.execute(f"DROP OWNED BY {RUNTIME_ROLE}")
            connection.execute(f"DROP ROLE {RUNTIME_ROLE}")
        connection.execute(f"CREATE ROLE {RUNTIME_ROLE} LOGIN PASSWORD '{RUNTIME_PASSWORD}'")
        connection.execute(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}")
        connection.execute(
            f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {RUNTIME_ROLE}"
        )
        connection.execute(f"REVOKE UPDATE, DELETE ON TABLE forecast_ledger FROM {RUNTIME_ROLE}")

    runtime_params = conninfo_to_dict(_dsn())
    runtime_params["user"] = RUNTIME_ROLE
    runtime_params["password"] = RUNTIME_PASSWORD
    runtime_dsn = make_conninfo(**runtime_params)
    with psycopg.connect(runtime_dsn, autocommit=True) as connection:
        for statement in (
            "UPDATE forecast_ledger SET raw_probability = 0.01 WHERE false",
            "DELETE FROM forecast_ledger WHERE false",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)


def test_runtime_role_script_revokes_forecast_ledger_mutation() -> None:
    from pathlib import Path

    script = (Path(__file__).parents[2] / "scripts" / "configure-runtime-role.sql").read_text()
    revoke_block = script.split("REVOKE UPDATE, DELETE ON TABLE", 1)[1].split(
        'FROM :"runtime_role";', 1
    )[0]
    assert "forecast_ledger" in revoke_block


def test_migration_revision_owns_only_the_forecast_ledger() -> None:
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    root = Path(__file__).parents[2]
    spec = spec_from_file_location(
        "forecast_ledger_migration",
        root / "alembic/versions/20260924_05_forecast_ledger.py",
    )
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260924_05"
    assert migration.down_revision == "20260924_04"
    assert migration.TABLE == "forecast_ledger"

    previous = (root / "alembic/versions/20260924_04_core_evidence_immutability.py").read_text()
    assert "forecast_ledger" not in previous


def test_metadata_matches_migration_columns() -> None:
    from pathlib import Path

    source = (
        Path(__file__).parents[2] / "alembic/versions/20260924_05_forecast_ledger.py"
    ).read_text()
    for column in ForecastLedgerRecord.__table__.columns.keys():
        assert f'"{column}"' in source, f"sloupec {column} chybí v migraci"


def test_alembic_env_registers_forecast_ledger_metadata() -> None:
    from pathlib import Path

    env = (Path(__file__).parents[2] / "alembic" / "env.py").read_text()
    assert "import quantlab.forecast_ledger" in env
