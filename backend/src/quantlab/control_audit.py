from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from quantlab.phase4 import AuditEventRecord


@dataclass(frozen=True)
class ControlAudit:
    event_type: str
    entity_type: str
    actor: dict[str, str]
    reason: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("Control audit vyžaduje důvod")
        if not self.correlation_id.strip():
            raise ValueError("Control audit vyžaduje correlation_id")
        if not self.actor.get("actor_id"):
            raise ValueError("Control audit vyžaduje actor_id")


def add_control_audit(
    session: Session,
    audit: ControlAudit,
    entity_id: str,
    *,
    timestamp: datetime | None = None,
) -> AuditEventRecord:
    identity = hashlib.sha256(
        json.dumps(
            [
                audit.event_type,
                audit.entity_type,
                entity_id,
                audit.actor["actor_id"],
                audit.reason,
                audit.correlation_id,
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = session.get(AuditEventRecord, identity)
    if existing is not None:
        return existing
    row = AuditEventRecord(
        id=identity,
        timestamp=timestamp or datetime.now(UTC),
        event_type=audit.event_type,
        entity_type=audit.entity_type,
        entity_id=entity_id,
        trading_cycle_id=None,
        correlation_id=audit.correlation_id,
        payload_json=json.dumps(
            {"actor": audit.actor, "reason": audit.reason},
            sort_keys=True,
        ),
    )
    session.add(row)
    return row
