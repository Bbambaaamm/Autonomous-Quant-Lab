from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from quantlab.control_plane import ControlPlaneRegistryService
from quantlab.market_data import AssetType, Instrument
from quantlab.persistence import (
    Base,
    InstrumentRecord,
    UniverseDefinitionRecord,
)
from quantlab.phase4 import AuditEventRecord
from quantlab.universe import UniverseDefinition, UniverseKind


def factory() -> sessionmaker:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _instrument(instrument_id: str = "217-foo") -> Instrument:
    return Instrument(
        instrument_id,
        "FOO",
        "XNYS",
        "XNYS",
        "USD",
        AssetType.EQUITY,
        datetime(2024, 1, 1, tzinfo=UTC),
        None,
        datetime(2024, 1, 2, tzinfo=UTC),
    )


_ACTOR = {"actor_id": "test-operator", "role": "ADMIN"}


@pytest.mark.parametrize("operation", ["register_instrument", "create_universe"])
def test_control_mutation_audit_is_atomic_on_audit_write_failure(operation: str) -> None:
    """P1 #217: an audit-write failure MUST roll back the control mutation inside the
    same transaction - the mutation must not be committed without its audit evidence."""
    sessions = factory()
    service = ControlPlaneRegistryService(sessions)

    fired: list[bool] = []

    def _fail_audit(mapper, connection, target):  # noqa: ARG001
        fired.append(True)
        raise RuntimeError("forced audit write failure for issue 217 fault-injection")

    event.listen(AuditEventRecord, "before_insert", _fail_audit)
    try:
        if operation == "register_instrument":
            with pytest.raises(RuntimeError, match="forced audit write failure"):
                service.register_instrument(
                    _instrument("217-atomic-foo"),
                    actor=_ACTOR,
                    reason="atomicty rollback regression",
                    correlation_id="corr-217-atomic",
                )
            with sessions() as session:
                assert session.get(InstrumentRecord, "217-atomic-foo") is None
                assert session.scalar(select(AuditEventRecord)) is None
        else:
            universe_def = UniverseDefinition(
                "217-atomic-u",
                "ATOMIC-U",
                UniverseKind.POINT_IN_TIME_MEMBERSHIP,
                datetime(2024, 1, 1, tzinfo=UTC),
            )
            with pytest.raises(RuntimeError, match="forced audit write failure"):
                service.create_universe(
                    universe_def,
                    actor=_ACTOR,
                    reason="universe atomicity rollback regression",
                    correlation_id="corr-217-atomic-u",
                )
            with sessions() as session:
                assert session.get(UniverseDefinitionRecord, "217-atomic-u") is None
                assert session.scalar(select(AuditEventRecord)) is None
        assert fired  # the fault-injection hook actually fired
    finally:
        event.remove(AuditEventRecord, "before_insert", _fail_audit)


def test_control_mutation_persists_audit_evidence_happy_path() -> None:
    """P1 #217: a successful control mutation writes immutable audit evidence carrying
    reason, actor, and correlation_id - all in the same transaction."""
    sessions = factory()
    service = ControlPlaneRegistryService(sessions)

    instrument = _instrument("217-happy-foo")
    row = service.register_instrument(
        instrument,
        actor=_ACTOR,
        reason="happy path audit evidence",
        correlation_id="corr-217-happy",
    )

    assert row.instrument_id == "217-happy-foo"
    with sessions() as session:
        persisted = session.get(InstrumentRecord, "217-happy-foo")
        assert persisted is not None
        audit = session.scalar(
            select(AuditEventRecord).where(AuditEventRecord.entity_id == "217-happy-foo")
        )
        assert audit is not None
        assert audit.event_type == "CONTROL_INSTRUMENT_REGISTERED"
        assert audit.entity_type == "instrument"
        assert audit.entity_id == "217-happy-foo"
        assert audit.correlation_id == "corr-217-happy"
        payload = json.loads(audit.payload_json)
        assert payload["reason"] == "happy path audit evidence"
        assert payload["actor"] == _ACTOR
        # identity is the deterministic SHA-256 of the canonical tuple
        expected = hashlib.sha256(
            json.dumps(
                [
                    "CONTROL_INSTRUMENT_REGISTERED",
                    "instrument",
                    "217-happy-foo",
                    _ACTOR["actor_id"],
                    "happy path audit evidence",
                    "corr-217-happy",
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        assert audit.id == expected


@pytest.mark.parametrize(
    ("reason", "actor_id", "correlation_id"),
    [
        ("minimum valid reason", "op-1", "corr-1"),
        ("a" * 1000, "op-2", "a" * 64),
    ],
)
def test_audit_evidence_carries_mandatory_reason_actor_correlation(
    reason: str, actor_id: str, correlation_id: str
) -> None:
    """P1 #217: every audit record MUST carry mandatory reason, actor, correlation_id."""
    sessions = factory()
    service = ControlPlaneRegistryService(sessions)
    instrument = _instrument("217-evidence-foo")
    actor = {"actor_id": actor_id, "role": "ADMIN"}
    row = service.register_instrument(
        instrument,
        actor=actor,
        reason=reason,
        correlation_id=correlation_id,
    )
    assert row.instrument_id == "217-evidence-foo"
    with sessions() as session:
        audit = session.scalar(
            select(AuditEventRecord).where(AuditEventRecord.entity_id == "217-evidence-foo")
        )
        assert audit is not None
        assert audit.correlation_id == correlation_id
        payload = json.loads(audit.payload_json)
        assert payload["reason"] == reason
        assert payload["actor"]["actor_id"] == actor_id


def test_register_instrument_without_reason_rejected_by_api_model() -> None:
    """P1 #217: ReasonedMutation enforces min_length(reason)=3 at the API contract layer."""
    from quantlab.api import ReasonedMutation  # pyright: ignore[reportMissingModule]

    with pytest.raises(ValueError):  # pydantic ValidationError (subclass of ValueError)
        ReasonedMutation(reason="ab")  # 2 chars -> below min_length=3
