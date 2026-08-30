from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from quantlab.domain import require_utc


class UniverseKind(StrEnum):
    STATIC = "STATIC"
    POINT_IN_TIME_MEMBERSHIP = "POINT_IN_TIME_MEMBERSHIP"


@dataclass(frozen=True)
class UniverseDefinition:
    universe_id: str
    name: str
    kind: UniverseKind
    created_at: datetime = datetime(1970, 1, 1, tzinfo=UTC)

    @property
    def survivorship_bias_status(self) -> str:
        return "BIAS_PRONE_STATIC" if self.kind is UniverseKind.STATIC else "POINT_IN_TIME_SAFE"


@dataclass(frozen=True)
class UniverseMembership:
    universe_id: str
    instrument_id: str
    valid_from: datetime
    valid_to: datetime | None
    known_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_from", require_utc(self.valid_from))
        object.__setattr__(self, "known_at", require_utc(self.known_at))
        if self.valid_to is not None:
            object.__setattr__(self, "valid_to", require_utc(self.valid_to))
            if self.valid_to <= self.valid_from:
                raise ValueError("Membership interval musí být neprázdný")


class PointInTimeUniverse:
    def __init__(
        self,
        definition: UniverseDefinition,
        memberships: list[UniverseMembership],
        *,
        static_knowledge_as_of: datetime | None = None,
    ) -> None:
        if any(m.universe_id != definition.universe_id for m in memberships):
            raise ValueError("Membership patří do jiného universe")
        if definition.kind is UniverseKind.STATIC and static_knowledge_as_of is None:
            raise ValueError("STATIC universe vyžaduje explicitní snapshot knowledge cutoff")
        self.definition = definition
        self._memberships = tuple(memberships)
        self._static_knowledge_as_of = (
            require_utc(static_knowledge_as_of) if static_knowledge_as_of is not None else None
        )

    def eligible(
        self, decision_time: datetime, *, knowledge_as_of: datetime | None = None
    ) -> tuple[str, ...]:
        decision = require_utc(decision_time)
        knowledge = require_utc(knowledge_as_of or decision)
        if self.definition.kind is UniverseKind.STATIC:
            # STATIC je explicitně current/snapshot seznam aplikovaný na celé historické
            # období. known_at se nebackdatuje: seznam smí obsahovat jen membership známé
            # k explicitnímu snapshot cutoffu.
            if self._static_knowledge_as_of is None:
                raise RuntimeError("STATIC universe nemá snapshot knowledge cutoff")
            cutoff = self._static_knowledge_as_of
            return tuple(
                sorted({m.instrument_id for m in self._memberships if m.known_at <= cutoff})
            )
        return tuple(
            sorted(
                {
                    m.instrument_id
                    for m in self._memberships
                    if m.known_at <= knowledge
                    and m.valid_from <= decision
                    and (m.valid_to is None or decision < m.valid_to)
                }
            )
        )

    def rebalance_eligible(self, day: date, decision_time: datetime) -> tuple[str, ...]:
        if day != require_utc(decision_time).date():
            raise ValueError("Rebalance day neodpovídá decision time")
        return self.eligible(decision_time)
