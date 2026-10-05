"""Immutable Forecast Ledger — canonical probability forecast evidence (issue #266).

Records the *pre-decision probabilistic forecast* that a downstream paper
intent / risk decision was derived from, so calibration (#269) and shadow
sizing (#270) can separate forecast quality from position sizing.

Pipeline position::

    PIT data snapshot
          ↓
    strategy / model
          ↓
    PROBABILITY FORECAST      ← this module owns the canonical contract
          ↓
    immutable Forecast Ledger
          ↓
    portfolio / risk / PAPER decision

Safety contract
---------------
* ``FORECAST_AUTHORITY`` is ``False``. Nothing here grants execution authority,
  submits an order, or touches RiskEngine limits. A forecast is evidence only.
* No I/O, no clock reads, no randomness in the contract layer. Timestamps are
  explicit arguments; ``Decimal`` arithmetic throughout.
* Fail-closed: incomplete or stale evidence produces an explicit degraded
  status, never a fabricated probability.
* Append-only: corrections are new versions with their own ``forecast_id``;
  historical probability and lineage are never rewritten.

Addresses the independent architecture review blockers:
  A) ``raw_probability`` and ``calibrated_probability`` are separate evidence
     with an immutable ``calibrator_id``/``calibrator_version``; calibration
     never overwrites the raw forecast. ``confidence`` is explicitly *not* a
     probability.
  B) ``CanonicalTargetSpec`` is machine-complete: schema/version, reference
     source/field/price basis, direction, threshold, horizon, resolution policy
     + version, exchange calendar, timezone, session semantics, grace and
     revision policy.
  C) Ordering is structural, not timestamp-only: decision evidence carries an
     explicit ``forecast_id`` and ``decision_identity``; two model versions for
     the same asset/time never collide.

Addresses the independent statistical / overfitting review blockers:
  A) Every preregistered opportunity produces exactly one ledger row, including
     ``NO_FORECAST`` / ``ABSTAINED`` / ``INVALID_DATA`` / ``NOT_EVALUATED``, so
     coverage is reconstructible and selective logging cannot inflate scores.
  B) ``trial_family_id`` + ``target_spec_hash`` make a target/horizon/threshold
     switch a new statistical trial family rather than a silent re-use.
  C) ``trial_family_id`` links hypothesis family, model variant, funnel, target
     version and dataset generation for multiple-testing accounting.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.domain import require_utc
from quantlab.persistence import Base

#: Canonical contract version. A material change bumps this and the schema.
FORECAST_SCHEMA_VERSION = 1

#: Hard boundary marker: this module never grants execution authority.
FORECAST_AUTHORITY = False

ZERO = Decimal(0)
ONE = Decimal(1)

#: Resource/robustness bounds (per runtime-architecture contract #190).
MAX_DISTRIBUTION_CLASSES = 64
MAX_LINEAGE_ENTRIES = 256
MAX_COVERAGE_OPPORTUNITIES = 1_000_000

#: Canonical timezone for horizon/resolution semantics (UTC only).
CANONICAL_TIMEZONE = "UTC"


class ForecastLedgerError(RuntimeError):
    """Base error for the immutable forecast ledger."""


class InvalidForecastError(ForecastLedgerError):
    """Raised when a forecast record fails canonical schema/PIT validation."""


class ForecastConflictError(ForecastLedgerError):
    """Raised when a committed decision identity is re-submitted with new content."""


class ForecastIntegrityError(ForecastLedgerError):
    """Raised when persisted evidence does not reproduce its own content identity."""


class ForecastGateError(ForecastLedgerError):
    """Raised when a probability-aware decision has no admissible forecast."""


class ForecastAuthorityError(ForecastLedgerError):
    """Raised when code tries to grant the ledger execution authority."""


class ForecastStatus(StrEnum):
    """Ledger row status. Non-emitted statuses keep the opportunity denominator."""

    FORECAST_EMITTED = "FORECAST_EMITTED"
    ABSTAINED = "ABSTAINED"
    NO_FORECAST = "NO_FORECAST"
    INVALID_DATA = "INVALID_DATA"
    NOT_EVALUATED = "NOT_EVALUATED"


class DegradedReason(StrEnum):
    """Explicit fail-closed reason; never a fabricated probability."""

    MISSING_DATA = "MISSING_DATA"
    STALE_DATA = "STALE_DATA"
    FUTURE_DATA = "FUTURE_DATA"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    CALENDAR_UNRESOLVED = "CALENDAR_UNRESOLVED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    POLICY_ABSTAIN = "POLICY_ABSTAIN"
    NOT_EVALUATED = "NOT_EVALUATED"


class OutcomeKind(StrEnum):
    """Target outcome cardinality."""

    BINARY = "BINARY"
    MULTICLASS = "MULTICLASS"


class ScopeKind(StrEnum):
    """Canonical forecast scope identity."""

    ASSET = "ASSET"
    UNIVERSE = "UNIVERSE"


class TargetDirection(StrEnum):
    """Threshold direction for a canonical target definition."""

    ABOVE = "ABOVE"
    AT_OR_ABOVE = "AT_OR_ABOVE"
    BELOW = "BELOW"
    AT_OR_BELOW = "AT_OR_BELOW"


class CommitOutcome(StrEnum):
    """Result of an append-only ledger commit."""

    CREATED = "CREATED"
    DUPLICATE_IDEMPOTENT = "DUPLICATE_IDEMPOTENT"


def canonical_json(value: Any) -> str:
    """Deterministic JSON encoding used for all content addressing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def identity(value: Any) -> str:
    """SHA-256 content identity over a canonical JSON encoding."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    """Normalize an ORM datetime to timezone-aware UTC.

    SQLite drops tzinfo on read, so round-tripped evidence must be normalized
    before it can be compared against the original content hash.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical_utc(value: datetime) -> datetime:
    """Canonical UTC rendering used for every content-addressed identity.

    Timezone safety requires more than rejecting naive datetimes: two aware
    representations of the *same instant* (``14:30+00:00`` and ``16:30+02:00``)
    are the same canonical decision and must produce the same
    ``decision_identity``, ``content_hash`` and persisted instant. Without this
    normalization the same opportunity could be logged twice (breaking
    append-only idempotency and inflating the coverage denominator) and a
    round-tripped record could appear mutated. Naive datetimes stay rejected.
    """
    return require_utc(value)


#: Canonical scale for every decimal that participates in a content-addressed
#: identity. Numerically equal values must serialize identically, otherwise
#: ``Decimal("0.01")`` and ``Decimal("0.010")`` produce different target/decision
#: identities for the same economic target and the same opportunity is logged
#: twice (inflating the coverage denominator).
CANONICAL_DECIMAL_SCALE = Decimal("0.000000000001")

#: Storage scale of every probability column (``Numeric(30, 12)``).
PROBABILITY_SCALE = CANONICAL_DECIMAL_SCALE

#: Exception family that means "this persisted evidence cannot be faithfully
#: decoded into canonical evidence". ``decimal.InvalidOperation`` (raised by
#: ``Decimal(...)`` on non-numeric text and by ``Decimal.quantize`` on
#: ``Infinity``/``sNaN``) is an ``ArithmeticError``, **not** a ``ValueError``, so
#: the ``decimal`` family must be listed explicitly. ``json.JSONDecodeError`` is
#: a ``ValueError`` and is kept for documentation. A missing/None row is not an
#: error here, and driver/connection failures (``DBAPIError``) are deliberately
#: *not* in this tuple so genuine operational errors are never reported as
#: tampering.
EVIDENCE_DECODE_ERRORS = (
    KeyError,
    ValueError,
    TypeError,
    ArithmeticError,
    AttributeError,
    json.JSONDecodeError,
)


def _probability_text(value: Decimal) -> str:
    """Render a probability at the ledger's authoritative storage scale.

    Content addressing must agree with what the database actually stores, so a
    read-back record reproduces the original ``content_hash`` instead of
    appearing mutated merely because ``Numeric(30, 12)`` re-scaled the value.
    """
    return str(value.quantize(PROBABILITY_SCALE))


def _storage_probability(value: Decimal) -> Decimal:
    """Round a probability to the authoritative storage scale *before* writing.

    The persisted column must hold exactly the value that was content-addressed:
    writing an unquantized ``Decimal`` lets ``Numeric(30, 12)`` round it (with a
    different rounding mode than ``Decimal.quantize``), so a >12-decimal input
    could store a value that no longer reproduces the hashed evidence.
    """
    return value.quantize(PROBABILITY_SCALE)


def _decimal_text(value: Decimal) -> str:
    """Canonical text for a content-addressed decimal.

    ``Decimal("0.01")``, ``Decimal("0.010")`` and ``Decimal("0.0100")`` are the
    same number and must serialize to one string, otherwise the target spec hash
    -- and therefore the whole decision identity -- depends on the input's
    trailing zeros and a target/horizon "switch" is indistinguishable from a
    formatting difference.
    """
    return str(value.quantize(CANONICAL_DECIMAL_SCALE))


def _require_text(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidForecastError(f"{field_name} musí být neprázdný text")
    return value


def _require_probability(value: Decimal | None, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise InvalidForecastError(f"{field_name} musí být Decimal")
    if not value.is_finite():
        raise InvalidForecastError(f"{field_name} musí být konečná hodnota")
    if not (ZERO <= value <= ONE):
        raise InvalidForecastError(f"{field_name} musí být v intervalu <0, 1>")
    return value


def assert_forecast_only() -> None:
    """Fail closed on any attempt to treat the ledger as an execution authority."""
    if FORECAST_AUTHORITY:
        raise ForecastAuthorityError("Forecast ledger nesmí udělovat exekuční autoritu")


# ---------------------------------------------------------------------------
# Canonical target specification (BLOCKER B)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalTargetSpec:
    """Machine-complete, versioned target/outcome definition.

    A downstream resolver (#269) must never have to infer the meaning of the
    target: every degree of freedom is an explicit, hashed field.
    """

    target_id: str
    outcome_kind: OutcomeKind
    reference_source: str
    reference_field: str
    reference_price_basis: str
    direction: TargetDirection
    threshold: Decimal
    horizon_sessions: int
    resolution_policy: str
    resolution_policy_version: str
    exchange_calendar: str
    session_semantics: str
    schema_version: str = "target-v1"
    timezone: str = CANONICAL_TIMEZONE
    grace_sessions: int = 0
    revision_policy: str = "PINNED_REVISION"

    def validate(self) -> None:
        for name, value in (
            ("target.target_id", self.target_id),
            ("target.reference_source", self.reference_source),
            ("target.reference_field", self.reference_field),
            ("target.reference_price_basis", self.reference_price_basis),
            ("target.resolution_policy", self.resolution_policy),
            ("target.resolution_policy_version", self.resolution_policy_version),
            ("target.exchange_calendar", self.exchange_calendar),
            ("target.session_semantics", self.session_semantics),
            ("target.schema_version", self.schema_version),
            ("target.revision_policy", self.revision_policy),
        ):
            _require_text(value, name)
        if not isinstance(self.threshold, Decimal) or not self.threshold.is_finite():
            raise InvalidForecastError("target.threshold musí být konečná Decimal hodnota")
        if self.horizon_sessions <= 0:
            raise InvalidForecastError("target.horizon_sessions musí být kladné")
        if self.grace_sessions < 0:
            raise InvalidForecastError("target.grace_sessions musí být >= 0")
        if self.timezone != CANONICAL_TIMEZONE:
            raise InvalidForecastError("target.timezone musí být UTC (timezone-safe)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_id": self.target_id,
            "outcome_kind": str(self.outcome_kind),
            "reference_source": self.reference_source,
            "reference_field": self.reference_field,
            "reference_price_basis": self.reference_price_basis,
            "direction": str(self.direction),
            "threshold": _decimal_text(self.threshold),
            "horizon_sessions": self.horizon_sessions,
            "resolution_policy": self.resolution_policy,
            "resolution_policy_version": self.resolution_policy_version,
            "exchange_calendar": self.exchange_calendar,
            "session_semantics": self.session_semantics,
            "timezone": self.timezone,
            "grace_sessions": self.grace_sessions,
            "revision_policy": self.revision_policy,
        }

    @property
    def spec_hash(self) -> str:
        return identity(self.to_dict())

    def canonical_definition(self) -> str:
        """Human/machine readable canonical target definition string."""
        return (
            f"{self.target_id}:{self.reference_source}.{self.reference_field}"
            f"[{self.reference_price_basis}] {self.direction} {_decimal_text(self.threshold)} "
            f"over {self.horizon_sessions} sessions "
            f"({self.exchange_calendar}/{self.session_semantics}/{self.timezone})"
            f" policy={self.resolution_policy}@{self.resolution_policy_version}"
            f" grace={self.grace_sessions} revision={self.revision_policy}"
        )


# ---------------------------------------------------------------------------
# Probability evidence (BLOCKER A)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbabilityDistribution:
    """Binary ``p_event`` or a normalised multi-class distribution."""

    kind: OutcomeKind
    p_event: Decimal | None = None
    probabilities: Mapping[str, Decimal] = field(default_factory=dict)

    def validate(self) -> None:
        if self.kind is OutcomeKind.BINARY:
            if self.probabilities:
                raise InvalidForecastError("binární distribuce nesmí nést třídy")
            _require_probability(self.p_event, "distribution.p_event")
            return
        if self.p_event is not None:
            raise InvalidForecastError("multi-class distribuce nesmí nést p_event")
        if len(self.probabilities) < 2:
            raise InvalidForecastError("multi-class distribuce vyžaduje >= 2 třídy")
        if len(self.probabilities) > MAX_DISTRIBUTION_CLASSES:
            raise InvalidForecastError("multi-class distribuce překročila limit tříd")
        total = ZERO
        for label, probability in self.probabilities.items():
            _require_text(label, "distribution.label")
            total += _require_probability(probability, f"distribution.{label}")
        if total != ONE:
            raise InvalidForecastError(
                f"multi-class distribuce musí být normalizovaná na 1 (součet={total})"
            )

    @property
    def is_empty(self) -> bool:
        return self.p_event is None and not self.probabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "p_event": None if self.p_event is None else _probability_text(self.p_event),
            "probabilities": {
                label: _probability_text(value)
                for label, value in sorted(self.probabilities.items())
            },
        }


@dataclass(frozen=True)
class BaselineProbability:
    """Point-in-time reference probability with its own source/version lineage."""

    probability: Decimal
    source: str
    source_version: str
    as_of: datetime

    def validate(self, decision_time: datetime) -> None:
        _require_probability(self.probability, "baseline.probability")
        _require_text(self.source, "baseline.source")
        _require_text(self.source_version, "baseline.source_version")
        as_of = require_utc(self.as_of)
        if as_of > decision_time:
            raise InvalidForecastError("baseline.as_of nesmí být po decision_time (PIT)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": _probability_text(self.probability),
            "source": self.source,
            "source_version": self.source_version,
            "as_of": _canonical_utc(self.as_of).isoformat(),
        }


@dataclass(frozen=True)
class CalibratedProbability:
    """Calibrator output kept as separate evidence; never overwrites raw."""

    probability: Decimal
    calibrator_id: str
    calibrator_version: str
    method: str

    def validate(self) -> None:
        _require_probability(self.probability, "calibrated.probability")
        _require_text(self.calibrator_id, "calibrated.calibrator_id")
        _require_text(self.calibrator_version, "calibrated.calibrator_version")
        _require_text(self.method, "calibrated.method")

    def to_dict(self) -> dict[str, Any]:
        return {
            "probability": _probability_text(self.probability),
            "calibrator_id": self.calibrator_id,
            "calibrator_version": self.calibrator_version,
            "method": self.method,
        }


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastLineage:
    """Immutable model / strategy / data / feature / research lineage."""

    model_name: str
    model_version: str
    code_sha: str
    strategy_identity: str
    feature_extractor_version: str
    market_snapshot_id: str
    market_snapshot_hash: str
    market_snapshot_as_of: datetime
    data_lineage: Mapping[str, str] = field(default_factory=dict)
    deployment_id: str | None = None
    research_experiment_id: str | None = None
    calibration_version: str | None = None
    trial_family_id: str | None = None
    source_research_identity: str | None = None

    def validate(self, decision_time: datetime) -> None:
        for name, value in (
            ("lineage.model_name", self.model_name),
            ("lineage.model_version", self.model_version),
            ("lineage.code_sha", self.code_sha),
            ("lineage.strategy_identity", self.strategy_identity),
            ("lineage.feature_extractor_version", self.feature_extractor_version),
            ("lineage.market_snapshot_id", self.market_snapshot_id),
            ("lineage.market_snapshot_hash", self.market_snapshot_hash),
        ):
            _require_text(value, name)
        if len(self.data_lineage) > MAX_LINEAGE_ENTRIES:
            raise InvalidForecastError("lineage.data_lineage překročil limit záznamů")
        for key, value in self.data_lineage.items():
            _require_text(key, "lineage.data_lineage.key")
            _require_text(value, f"lineage.data_lineage.{key}")
        snapshot_as_of = require_utc(self.market_snapshot_as_of)
        if snapshot_as_of > decision_time:
            raise InvalidForecastError(
                "lineage.market_snapshot_as_of nesmí být po decision_time (PIT)"
            )

    @property
    def artifact_hash(self) -> str:
        """Model/strategy artifact identity used in the decision identity."""
        return identity(
            {
                "model_name": self.model_name,
                "model_version": self.model_version,
                "code_sha": self.code_sha,
                "strategy_identity": self.strategy_identity,
                "feature_extractor_version": self.feature_extractor_version,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "code_sha": self.code_sha,
            "strategy_identity": self.strategy_identity,
            "feature_extractor_version": self.feature_extractor_version,
            "market_snapshot_id": self.market_snapshot_id,
            "market_snapshot_hash": self.market_snapshot_hash,
            "market_snapshot_as_of": _canonical_utc(self.market_snapshot_as_of).isoformat(),
            "data_lineage": dict(sorted(self.data_lineage.items())),
            "deployment_id": self.deployment_id,
            "research_experiment_id": self.research_experiment_id,
            "calibration_version": self.calibration_version,
            "trial_family_id": self.trial_family_id,
            "source_research_identity": self.source_research_identity,
        }


# ---------------------------------------------------------------------------
# Canonical forecast record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastRecord:
    """Canonical typed immutable forecast evidence.

    ``forecast_id`` is content-addressed: it is derived from the canonical
    decision identity plus the record content, so a fabricated or mutated
    record fails validation.
    """

    forecast_id: str
    status: ForecastStatus
    scope_kind: ScopeKind
    scope_id: str
    opportunity_id: str
    preregistered: bool
    created_at: datetime
    decision_time: datetime
    resolution_at: datetime
    target: CanonicalTargetSpec
    distribution: ProbabilityDistribution
    lineage: ForecastLineage
    schema_version: int = FORECAST_SCHEMA_VERSION
    baseline: BaselineProbability | None = None
    calibrated: CalibratedProbability | None = None
    confidence: Decimal | None = None
    uncertainty: Mapping[str, str] = field(default_factory=dict)
    degraded_reason: DegradedReason | None = None
    degraded_detail: str | None = None
    prior_forecast_id: str | None = None
    regime: str | None = None
    source: str | None = None

    # -- identity -----------------------------------------------------------

    def decision_identity(self) -> str:
        """Retry/idempotency identity (BLOCKER C).

        Includes target spec + asset/universe + decision time + model/strategy
        artifact + source snapshot lineage, so two model versions for the same
        asset/time never collide and a changed snapshot is a new forecast.
        """
        return identity(
            {
                "schema_version": self.schema_version,
                "scope_kind": str(self.scope_kind),
                "scope_id": self.scope_id,
                "opportunity_id": self.opportunity_id,
                "decision_time": _canonical_utc(self.decision_time).isoformat(),
                "target_spec_hash": self.target.spec_hash,
                "model_artifact_hash": self.lineage.artifact_hash,
                "market_snapshot_hash": self.lineage.market_snapshot_hash,
            }
        )

    def content_hash(self) -> str:
        return identity(self._payload())

    @staticmethod
    def compute_forecast_id(decision_identity: str, content_hash: str) -> str:
        return identity({"decision_identity": decision_identity, "content": content_hash})

    # -- validation ---------------------------------------------------------

    def validate(self) -> None:
        assert_forecast_only()
        if self.schema_version != FORECAST_SCHEMA_VERSION:
            raise InvalidForecastError(f"schema_version musí být {FORECAST_SCHEMA_VERSION}")
        _require_text(self.scope_id, "scope_id")
        _require_text(self.opportunity_id, "opportunity_id")
        self.target.validate()
        self.lineage.validate(require_utc(self.decision_time))
        created_at = require_utc(self.created_at)
        decision_time = require_utc(self.decision_time)
        resolution_at = require_utc(self.resolution_at)
        if created_at < decision_time:
            raise InvalidForecastError("created_at nesmí předcházet decision_time")
        if resolution_at <= decision_time:
            raise InvalidForecastError("resolution_at musí být po decision_time")
        if self.target.outcome_kind is not self.distribution.kind:
            raise InvalidForecastError("target.outcome_kind musí odpovídat distribuci")
        if self.baseline is not None:
            self.baseline.validate(decision_time)
        if self.calibrated is not None:
            self.calibrated.validate()
        if self.confidence is not None:
            _require_probability(self.confidence, "confidence")
        if len(self.uncertainty) > MAX_LINEAGE_ENTRIES:
            raise InvalidForecastError("uncertainty překročila limit záznamů")
        if self.prior_forecast_id is not None:
            _require_text(self.prior_forecast_id, "prior_forecast_id")
            if self.prior_forecast_id == self.forecast_id:
                raise InvalidForecastError("prior_forecast_id nesmí odkazovat na sebe")

        if self.status is ForecastStatus.FORECAST_EMITTED:
            if self.distribution.is_empty:
                raise InvalidForecastError(
                    "FORECAST_EMITTED vyžaduje pravděpodobnost, ne fabrikovanou hodnotu"
                )
            # A real forecast must exist strictly before its own outcome is
            # resolved. Otherwise a privileged writer (or a back-filled research
            # job) can land a row that already knows the answer -- the exact
            # look-ahead this ledger exists to make impossible.
            if created_at >= resolution_at:
                raise InvalidForecastError(
                    "FORECAST_EMITTED musí vzniknout před resolution_at (look-ahead)"
                )
            # STAT-BLOCKER B/C: a preregistered opportunity without a trial
            # family cannot be attributed to a multiple-testing family, so a
            # target/horizon switch would silently escape the #76 accounting.
            if self.preregistered and not (self.lineage.trial_family_id or "").strip():
                raise InvalidForecastError(
                    "preregistered forecast vyžaduje lineage.trial_family_id"
                )
            # The canonical record path must enforce the probability contract
            # itself: finite, in [0, 1] and (multi-class) exactly normalised.
            # A raw forecast may never carry an out-of-range or unnormalised
            # distribution even when it bypasses the DB check constraints.
            self.distribution.validate()
            if self.degraded_reason is not None or self.degraded_detail is not None:
                raise InvalidForecastError("FORECAST_EMITTED nesmí nést degraded stav")
        else:
            if not self.distribution.is_empty:
                raise InvalidForecastError(
                    "fail-closed status nesmí nést pravděpodobnost (žádná fabrikace)"
                )
            if self.degraded_reason is None:
                raise InvalidForecastError("fail-closed status vyžaduje degraded_reason")
            if self.calibrated is not None:
                raise InvalidForecastError("fail-closed status nesmí nést kalibrovanou hodnotu")

        expected = self.compute_forecast_id(self.decision_identity(), self.content_hash())
        if expected != self.forecast_id:
            raise InvalidForecastError(
                "forecast_id neodpovídá obsahu (immutability/lineage porušena)"
            )

    # -- serialization ------------------------------------------------------

    def _payload(self) -> dict[str, Any]:
        """Content-identity payload: excludes the derived id and its own hash."""
        return {
            "schema_version": self.schema_version,
            "status": str(self.status),
            "scope_kind": str(self.scope_kind),
            "scope_id": self.scope_id,
            "opportunity_id": self.opportunity_id,
            "preregistered": self.preregistered,
            "created_at": _canonical_utc(self.created_at).isoformat(),
            "decision_time": _canonical_utc(self.decision_time).isoformat(),
            "resolution_at": _canonical_utc(self.resolution_at).isoformat(),
            "target": self.target.to_dict(),
            "distribution": self.distribution.to_dict(),
            "lineage": self.lineage.to_dict(),
            "baseline": None if self.baseline is None else self.baseline.to_dict(),
            "calibrated": None if self.calibrated is None else self.calibrated.to_dict(),
            "confidence": None if self.confidence is None else _probability_text(self.confidence),
            "uncertainty": dict(sorted(self.uncertainty.items())),
            "degraded_reason": None if self.degraded_reason is None else str(self.degraded_reason),
            "degraded_detail": self.degraded_detail,
            "prior_forecast_id": self.prior_forecast_id,
            "regime": self.regime,
            "source": self.source,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        payload["forecast_id"] = self.forecast_id
        payload["target_spec_hash"] = self.target.spec_hash
        payload["decision_identity"] = self.decision_identity()
        payload["content_hash"] = self.content_hash()
        return payload

    def to_calibration_view(self) -> dict[str, Any]:
        """Field mapping consumed by the downstream calibration engine (#269)."""
        raw = self.distribution.p_event
        return {
            "forecast_id": self.forecast_id,
            "probability": None if raw is None else _probability_text(raw),
            "calibrated_probability": (
                None if self.calibrated is None else _probability_text(self.calibrated.probability)
            ),
            "horizon_sessions": self.target.horizon_sessions,
            "decision_at": _canonical_utc(self.decision_time).isoformat(),
            "model_version": self.lineage.model_version,
            # ``scope_id`` may name an asset *or* a universe. The calibration
            # engine must not silently treat a universe as a single asset, so
            # both the identity and its kind travel together.
            "scope_kind": str(self.scope_kind),
            "scope_id": self.scope_id,
            "asset": self.scope_id if self.scope_kind is ScopeKind.ASSET else None,
            "regime": self.regime,
            "source": self.source,
            "target_definition": self.target.canonical_definition(),
            "target_spec_hash": self.target.spec_hash,
            "trial_family_id": self.lineage.trial_family_id,
            "status": str(self.status),
        }

    # -- construction -------------------------------------------------------

    @classmethod
    def create(cls, **kwargs: Any) -> ForecastRecord:
        """Build, validate and content-address a forecast record.

        ``forecast_id`` is always derived; passing one is rejected so callers
        cannot fabricate an immutable identity.
        """
        if "forecast_id" in kwargs:
            raise InvalidForecastError("forecast_id se odvozuje, nesmí být zadán")
        provisional = cls(forecast_id="", **kwargs)
        decision_identity = provisional.decision_identity()
        content_hash = provisional.content_hash()
        forecast_id = cls.compute_forecast_id(decision_identity, content_hash)
        record = cls(forecast_id=forecast_id, **kwargs)
        record.validate()
        return record


# ---------------------------------------------------------------------------
# Coverage accounting (STAT-BLOCKER A)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """Reconstructible opportunity denominator — no selective logging."""

    eligible: int
    emitted: int
    abstained: int
    no_forecast: int
    invalid_data: int
    not_evaluated: int
    excluded_non_preregistered: int = 0

    @property
    def coverage(self) -> Decimal:
        if self.eligible == 0:
            return ZERO
        return (Decimal(self.emitted) / Decimal(self.eligible)).quantize(Decimal("0.000001"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "emitted": self.emitted,
            "abstained": self.abstained,
            "no_forecast": self.no_forecast,
            "invalid_data": self.invalid_data,
            "not_evaluated": self.not_evaluated,
            "excluded_non_preregistered": self.excluded_non_preregistered,
            "coverage": str(self.coverage),
        }


def coverage_report(records: Iterable[ForecastRecord]) -> CoverageReport:
    """Count every preregistered opportunity exactly once.

    Only *preregistered* opportunities form the statistical denominator
    (STAT-BLOCKER A/B). Exploratory or ad-hoc forecasts are legitimate evidence
    but were never part of a preregistered opportunity scope, so counting them
    would let an agent inflate the denominator -- and its apparent coverage --
    with rows that carry no preregistration obligation. They are reported
    separately and never silently dropped.
    """
    counts = {status: 0 for status in ForecastStatus}
    eligible = 0
    excluded = 0
    for record in records:
        if not record.preregistered:
            excluded += 1
            continue
        eligible += 1
        counts[record.status] += 1
        if eligible > MAX_COVERAGE_OPPORTUNITIES:
            raise ForecastLedgerError("překročen limit opportunities pro coverage report")
    return CoverageReport(
        eligible=eligible,
        emitted=counts[ForecastStatus.FORECAST_EMITTED],
        abstained=counts[ForecastStatus.ABSTAINED],
        no_forecast=counts[ForecastStatus.NO_FORECAST],
        invalid_data=counts[ForecastStatus.INVALID_DATA],
        not_evaluated=counts[ForecastStatus.NOT_EVALUATED],
        excluded_non_preregistered=excluded,
    )


# ---------------------------------------------------------------------------
# Persistent immutable evidence
# ---------------------------------------------------------------------------


class ForecastLedgerRecord(Base):
    """Append-only forecast evidence; PostgreSQL trigger rejects UPDATE/DELETE."""

    __tablename__ = "forecast_ledger"
    __table_args__ = (
        UniqueConstraint("decision_identity", name="uq_forecast_ledger_decision_identity"),
        CheckConstraint(
            "(status = 'FORECAST_EMITTED' AND degraded_reason IS NULL "
            "AND ((outcome_kind = 'BINARY' AND raw_probability IS NOT NULL) "
            "OR (outcome_kind = 'MULTICLASS' AND raw_probability IS NULL))) OR "
            "(status <> 'FORECAST_EMITTED' AND raw_probability IS NULL "
            "AND calibrated_probability IS NULL AND degraded_reason IS NOT NULL)",
            name="ck_forecast_ledger_probability_presence",
        ),
        CheckConstraint(
            "raw_probability IS NULL OR (raw_probability >= 0 AND raw_probability <= 1)",
            name="ck_forecast_ledger_raw_probability_range",
        ),
        CheckConstraint(
            "calibrated_probability IS NULL "
            "OR (calibrated_probability >= 0 AND calibrated_probability <= 1)",
            name="ck_forecast_ledger_calibrated_probability_range",
        ),
        CheckConstraint(
            "calibrated_probability IS NULL "
            "OR (calibrator_id IS NOT NULL AND calibrator_version IS NOT NULL)",
            name="ck_forecast_ledger_calibrator_identity",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_forecast_ledger_confidence_range",
        ),
        CheckConstraint(
            "baseline_probability IS NULL OR (baseline_source IS NOT NULL "
            "AND baseline_source_version IS NOT NULL AND baseline_as_of IS NOT NULL)",
            name="ck_forecast_ledger_baseline_identity",
        ),
        CheckConstraint(
            "resolution_at > decision_time", name="ck_forecast_ledger_resolution_after_decision"
        ),
        CheckConstraint(
            "created_at >= decision_time", name="ck_forecast_ledger_created_after_decision"
        ),
        CheckConstraint(
            "prior_forecast_id IS NULL OR prior_forecast_id <> forecast_id",
            name="ck_forecast_ledger_prior_not_self",
        ),
        CheckConstraint(
            "status <> 'FORECAST_EMITTED' OR created_at < resolution_at",
            name="ck_forecast_ledger_emitted_before_resolution",
        ),
        CheckConstraint(
            "preregistered = 0 OR trial_family_id IS NOT NULL",
            name="ck_forecast_ledger_preregistered_trial_family",
        ),
        Index("ix_forecast_ledger_scope", "scope_kind", "scope_id", "decision_time"),
        Index("ix_forecast_ledger_opportunity", "opportunity_id"),
        Index("ix_forecast_ledger_decision_time", "decision_time"),
        Index("ix_forecast_ledger_created_at", "created_at"),
        Index("ix_forecast_ledger_target_spec", "target_spec_hash"),
        Index("ix_forecast_ledger_trial_family", "trial_family_id"),
    )

    forecast_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    degraded_reason: Mapped[str | None] = mapped_column(String(40))
    degraded_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    opportunity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    preregistered: Mapped[int] = mapped_column(Integer, nullable=False)
    target_spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    target_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_definition: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_probability: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    calibrated_probability: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    calibrator_id: Mapped[str | None] = mapped_column(String(128))
    calibrator_version: Mapped[str | None] = mapped_column(String(64))
    distribution_json: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_probability: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    baseline_source: Mapped[str | None] = mapped_column(String(128))
    baseline_source_version: Mapped[str | None] = mapped_column(String(64))
    baseline_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    uncertainty_json: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    code_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    feature_extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    market_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    market_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    market_snapshot_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deployment_id: Mapped[str | None] = mapped_column(String(64))
    research_experiment_id: Mapped[str | None] = mapped_column(String(64))
    calibration_version: Mapped[str | None] = mapped_column(String(64))
    trial_family_id: Mapped[str | None] = mapped_column(String(64))
    source_research_identity: Mapped[str | None] = mapped_column(String(128))
    prior_forecast_id: Mapped[str | None] = mapped_column(String(64))
    regime: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)


@dataclass(frozen=True)
class CommitResult:
    """Outcome of an append-only commit."""

    outcome: CommitOutcome
    forecast_id: str
    decision_identity: str


def _row_payload(record: ForecastRecord) -> dict[str, Any]:
    calibrated = record.calibrated
    baseline = record.baseline
    raw = record.distribution.p_event
    return {
        "forecast_id": record.forecast_id,
        "decision_identity": record.decision_identity(),
        "schema_version": record.schema_version,
        "status": str(record.status),
        "degraded_reason": None if record.degraded_reason is None else str(record.degraded_reason),
        "degraded_detail": record.degraded_detail,
        "created_at": _canonical_utc(record.created_at),
        "decision_time": _canonical_utc(record.decision_time),
        "resolution_at": _canonical_utc(record.resolution_at),
        "scope_kind": str(record.scope_kind),
        "scope_id": record.scope_id,
        "opportunity_id": record.opportunity_id,
        "preregistered": int(record.preregistered),
        "target_spec_json": canonical_json(record.target.to_dict()),
        "target_spec_hash": record.target.spec_hash,
        "target_definition": record.target.canonical_definition(),
        "outcome_kind": str(record.target.outcome_kind),
        "raw_probability": None if raw is None else _storage_probability(raw),
        "calibrated_probability": (
            None if calibrated is None else _storage_probability(calibrated.probability)
        ),
        "calibrator_id": None if calibrated is None else calibrated.calibrator_id,
        "calibrator_version": None if calibrated is None else calibrated.calibrator_version,
        "distribution_json": canonical_json(record.distribution.to_dict()),
        "baseline_probability": (
            None if baseline is None else _storage_probability(baseline.probability)
        ),
        "baseline_source": None if baseline is None else baseline.source,
        "baseline_source_version": None if baseline is None else baseline.source_version,
        "baseline_as_of": None if baseline is None else _canonical_utc(baseline.as_of),
        "confidence": None
        if record.confidence is None
        else _storage_probability(record.confidence),
        "uncertainty_json": canonical_json(dict(record.uncertainty)),
        "model_name": record.lineage.model_name,
        "model_version": record.lineage.model_version,
        "code_sha": record.lineage.code_sha,
        "strategy_identity": record.lineage.strategy_identity,
        "feature_extractor_version": record.lineage.feature_extractor_version,
        "market_snapshot_id": record.lineage.market_snapshot_id,
        "market_snapshot_hash": record.lineage.market_snapshot_hash,
        "market_snapshot_as_of": _canonical_utc(record.lineage.market_snapshot_as_of),
        "deployment_id": record.lineage.deployment_id,
        "research_experiment_id": record.lineage.research_experiment_id,
        "calibration_version": record.lineage.calibration_version,
        "trial_family_id": record.lineage.trial_family_id,
        "source_research_identity": record.lineage.source_research_identity,
        "prior_forecast_id": record.prior_forecast_id,
        "regime": record.regime,
        "source": record.source,
        "content_hash": record.content_hash(),
        "record_json": canonical_json(record.to_dict()),
    }


def _lock(session: Session, lock_identity: str) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        key = int.from_bytes(
            hashlib.sha256(lock_identity.encode()).digest()[:8], "big", signed=True
        )
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


class ForecastLedger:
    """Append-only persistence for immutable forecast evidence.

    The ledger never updates or deletes: a repeated commit of an identical
    record is an idempotent no-op, and a re-commit of a *changed* record for an
    already-committed decision identity fails closed instead of rewriting
    history.
    """

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def commit(self, record: ForecastRecord) -> CommitResult:
        record.validate()
        decision_identity = record.decision_identity()
        with self.sessions() as session, session.begin():
            _lock(session, f"forecast-ledger:{decision_identity}")
            existing = session.get(ForecastLedgerRecord, record.forecast_id)
            if existing is not None:
                if existing.content_hash != record.content_hash():
                    raise ForecastConflictError(
                        "existující forecast_id má odlišný obsah (immutability porušena)"
                    )
                # Defense in depth: an idempotent retry must not silently bless a
                # row whose stored columns no longer reproduce its own identity.
                _record_from_row(existing)
                return CommitResult(
                    CommitOutcome.DUPLICATE_IDEMPOTENT, record.forecast_id, decision_identity
                )
            prior = session.scalar(
                select(ForecastLedgerRecord).where(
                    ForecastLedgerRecord.decision_identity == decision_identity
                )
            )
            if prior is not None:
                raise ForecastConflictError(
                    "rozhodovací identita už má commitnutý forecast s jiným obsahem"
                )
            # A correction must reference evidence that actually exists. Without
            # this, any non-empty ``prior_forecast_id`` is accepted, so the
            # "corrections are a new version of an existing forecast" contract
            # is unenforceable and a correction chain can dangle.
            if record.prior_forecast_id is not None:
                referenced = session.get(ForecastLedgerRecord, record.prior_forecast_id)
                if referenced is None:
                    raise ForecastConflictError(
                        "prior_forecast_id neodkazuje na existující forecast evidence"
                    )
            session.add(ForecastLedgerRecord(**_row_payload(record)))
            try:
                session.flush()
            except IntegrityError as error:  # pragma: no cover - defensive concurrency guard
                raise ForecastConflictError(
                    "souběžný commit stejné rozhodovací identity"
                ) from error
        return CommitResult(CommitOutcome.CREATED, record.forecast_id, decision_identity)

    def read(self, forecast_id: str) -> ForecastRecord | None:
        try:
            with self.sessions() as session:
                row = session.get(ForecastLedgerRecord, forecast_id)
        except EVIDENCE_DECODE_ERRORS as error:
            # The ORM result processor (e.g. DateTime) can fail while decoding a
            # malformed stored value *before* ``_record_from_row`` runs.
            raise ForecastIntegrityError(
                "persistovanou evidenci nelze přečíst (immutability porušena)"
            ) from error
        if row is None:
            return None
        return _record_from_row(row)

    def for_decision_identity(self, decision_identity: str) -> ForecastRecord | None:
        try:
            with self.sessions() as session:
                row = session.scalar(
                    select(ForecastLedgerRecord).where(
                        ForecastLedgerRecord.decision_identity == decision_identity
                    )
                )
        except EVIDENCE_DECODE_ERRORS as error:
            raise ForecastIntegrityError(
                "persistovanou evidenci nelze přečíst (immutability porušena)"
            ) from error
        if row is None:
            return None
        return _record_from_row(row)

    def count(self) -> int:
        with self.sessions() as session:
            return int(session.scalar(select(func.count()).select_from(ForecastLedgerRecord)) or 0)


def _record_from_row(row: ForecastLedgerRecord) -> ForecastRecord:
    """Rebuild the canonical record from persisted evidence (append-only read).

    The read path is also the immutability verifier: the reconstructed record
    must reproduce the stored ``content_hash``, ``forecast_id`` and
    ``record_json`` snapshot, so a row that disagrees with its own evidence
    fails closed instead of driving a downstream decision.

    A row that cannot even be decoded into canonical evidence -- an unparseable
    probability/threshold (``decimal.InvalidOperation``), a non-ISO datetime, a
    non-string label, a JSON document of the wrong shape -- fails closed the
    same way. Otherwise the raw decoder exception would escape the ledger's
    error contract and the decision gate could fail *open* for a caller that
    only handles ``ForecastLedgerError``.
    """
    try:
        record = ForecastRecord(
            forecast_id=row.forecast_id,
            status=ForecastStatus(row.status),
            scope_kind=ScopeKind(row.scope_kind),
            scope_id=row.scope_id,
            opportunity_id=row.opportunity_id,
            preregistered=bool(row.preregistered),
            created_at=_utc(row.created_at),
            decision_time=_utc(row.decision_time),
            resolution_at=_utc(row.resolution_at),
            target=_target_from_json(row.target_spec_json),
            distribution=_distribution_from_json(row.distribution_json),
            lineage=_lineage_from_json(row),
            schema_version=row.schema_version,
            baseline=_baseline_from_row(row),
            calibrated=_calibrated_from_row(row),
            confidence=row.confidence,
            uncertainty=json.loads(row.uncertainty_json or "{}"),
            degraded_reason=None
            if row.degraded_reason is None
            else DegradedReason(row.degraded_reason),
            degraded_detail=row.degraded_detail,
            prior_forecast_id=row.prior_forecast_id,
            regime=row.regime,
            source=row.source,
        )
        # Both verification helpers re-derive the content-addressed identity, so
        # they decode the same evidence a second time (``quantize`` on a stored
        # ``Infinity`` fails here). The guard therefore wraps the whole read-back
        # path, not just the record construction.
        _verify_persisted_identity(row, record)
        _verify_persisted_contract(record)
    except EVIDENCE_DECODE_ERRORS as error:
        raise ForecastIntegrityError(
            "persistovanou evidenci nelze rekonstruovat (immutability porušena)"
        ) from error
    return record


def _verify_persisted_contract(record: ForecastRecord) -> None:
    """Fail closed when persisted evidence violates the canonical contract.

    Identity verification alone is not sufficient. A privileged writer (or a
    hand-written INSERT, or a partially applied restore) can land a row that is
    perfectly *self-consistent* -- its ``content_hash``, ``forecast_id`` and
    ``record_json`` all agree -- while still breaking the #266 contract:

    * ``schema_version`` naming an unknown/unsupported contract version;
    * ``FORECAST_EMITTED`` carrying a degraded marker (fail-open vs fail-closed);
    * a look-ahead ``market_snapshot_as_of``/``baseline.as_of`` after
      ``decision_time`` (PIT/causality).

    ``_verify_persisted_identity`` recomputes the identity but never re-runs
    ``ForecastRecord.validate()``, so such a row would be admitted and drive a
    PAPER decision. Re-validate the reconstructed record here and surface any
    contract violation as ``ForecastIntegrityError`` so the decision gate fails
    closed on invalid evidence exactly as it does on mutated evidence.
    """
    try:
        record.validate()
    except InvalidForecastError as error:
        raise ForecastIntegrityError(
            f"persistovaná evidence neodpovídá kanonickému kontraktu: {error}"
        ) from error


def _verify_persisted_identity(row: ForecastLedgerRecord, record: ForecastRecord) -> None:
    """Fail closed when persisted evidence no longer reproduces its own identity.

    The PostgreSQL trigger and the runtime-role revocation stop UPDATE/DELETE,
    but a privileged writer, a hand-written INSERT or a partially applied
    restore can still land a row whose scalar columns disagree with the hashed
    ``record_json`` snapshot. ``require_forecast_reference`` only compares the
    ``decision_identity`` (target spec + scope + decision time + model artifact
    + snapshot lineage), so any tampered field *outside* that subset -- the
    probability columns, ``record_json``, regime, source, uncertainty,
    confidence, calibration, baseline, degraded detail -- would otherwise be
    admitted and drive a PAPER decision. Recompute both content-addressed
    identities and the canonical snapshot from the read-back record and compare
    them with the stored columns.
    """
    expected_content_hash = record.content_hash()
    if expected_content_hash != row.content_hash:
        raise ForecastIntegrityError(
            "persistovaná evidence neodpovídá svému content_hash (immutability porušena)"
        )
    expected_forecast_id = ForecastRecord.compute_forecast_id(
        record.decision_identity(), expected_content_hash
    )
    if expected_forecast_id != row.forecast_id:
        raise ForecastIntegrityError(
            "persistovaná evidence neodpovídá svému forecast_id (immutability porušena)"
        )
    if canonical_json(record.to_dict()) != row.record_json:
        raise ForecastIntegrityError(
            "persistovaná evidence neodpovídá svému record_json snapshotu (immutability porušena)"
        )
    # Denormalized scalar columns are read directly by downstream SQL consumers
    # (calibration/scoring, dashboards, ad-hoc audit queries). They must agree
    # with the hashed evidence they were derived from, otherwise the ledger
    # reports one probability in ``record_json`` and another in the column.
    expected_columns = _row_payload(record)
    for column, expected in expected_columns.items():
        if not _stored_matches(getattr(row, column, None), expected):
            raise ForecastIntegrityError(
                f"persistovaný sloupec {column} neodpovídá svému content hashi "
                "(immutability porušena)"
            )


def _stored_matches(stored: Any, expected: Any) -> bool:
    """Compare a persisted column against the canonical value it must hold.

    Values are normalized to their authoritative storage form: probabilities to
    the ``Numeric(30, 12)`` scale and timestamps to the canonical UTC instant,
    so a database round-trip is not mistaken for a mutation.
    """
    if stored is None or expected is None:
        return stored is None and expected is None
    if isinstance(expected, Decimal):
        if not isinstance(stored, Decimal):
            return False
        return _probability_text(stored) == _probability_text(expected)
    if isinstance(expected, datetime):
        if not isinstance(stored, datetime):
            return False
        # SQLite drops tzinfo on read; normalize both sides to the canonical
        # UTC instant so a round-trip is not mistaken for a mutation.
        return _utc(stored) == _canonical_utc(expected)
    if isinstance(expected, bool):
        return bool(stored) is expected
    return bool(stored == expected)


def _target_from_json(payload: str) -> CanonicalTargetSpec:
    data = json.loads(payload)
    return CanonicalTargetSpec(
        target_id=data["target_id"],
        outcome_kind=OutcomeKind(data["outcome_kind"]),
        reference_source=data["reference_source"],
        reference_field=data["reference_field"],
        reference_price_basis=data["reference_price_basis"],
        direction=TargetDirection(data["direction"]),
        threshold=Decimal(data["threshold"]),
        horizon_sessions=data["horizon_sessions"],
        resolution_policy=data["resolution_policy"],
        resolution_policy_version=data["resolution_policy_version"],
        exchange_calendar=data["exchange_calendar"],
        session_semantics=data["session_semantics"],
        schema_version=data["schema_version"],
        timezone=data["timezone"],
        grace_sessions=data["grace_sessions"],
        revision_policy=data["revision_policy"],
    )


def _distribution_from_json(payload: str) -> ProbabilityDistribution:
    data = json.loads(payload)
    return ProbabilityDistribution(
        kind=OutcomeKind(data["kind"]),
        p_event=None if data["p_event"] is None else Decimal(data["p_event"]),
        probabilities={label: Decimal(value) for label, value in data["probabilities"].items()},
    )


def _lineage_from_json(row: ForecastLedgerRecord) -> ForecastLineage:
    data = json.loads(row.record_json)["lineage"]
    return ForecastLineage(
        model_name=row.model_name,
        model_version=row.model_version,
        code_sha=row.code_sha,
        strategy_identity=row.strategy_identity,
        feature_extractor_version=row.feature_extractor_version,
        market_snapshot_id=row.market_snapshot_id,
        market_snapshot_hash=row.market_snapshot_hash,
        market_snapshot_as_of=_utc(row.market_snapshot_as_of),
        data_lineage=data["data_lineage"],
        deployment_id=row.deployment_id,
        research_experiment_id=row.research_experiment_id,
        calibration_version=row.calibration_version,
        trial_family_id=row.trial_family_id,
        source_research_identity=row.source_research_identity,
    )


def _baseline_from_row(row: ForecastLedgerRecord) -> BaselineProbability | None:
    if row.baseline_probability is None:
        return None
    return BaselineProbability(
        probability=row.baseline_probability,
        source=row.baseline_source or "",
        source_version=row.baseline_source_version or "",
        as_of=_utc(row.baseline_as_of),  # type: ignore[arg-type]
    )


def _calibrated_from_row(row: ForecastLedgerRecord) -> CalibratedProbability | None:
    if row.calibrated_probability is None:
        return None
    data = json.loads(row.record_json)["calibrated"]
    return CalibratedProbability(
        probability=row.calibrated_probability,
        calibrator_id=row.calibrator_id or "",
        calibrator_version=row.calibrator_version or "",
        method=data["method"],
    )


# ---------------------------------------------------------------------------
# Structural ordering gate (BLOCKER C)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionForecastReference:
    """Explicit immutable forecast reference carried by decision evidence."""

    forecast_id: str
    decision_identity: str
    decision_time: datetime

    def validate(self) -> None:
        _require_text(self.forecast_id, "reference.forecast_id")
        _require_text(self.decision_identity, "reference.decision_identity")
        require_utc(self.decision_time)

    def to_dict(self) -> dict[str, str]:
        """Canonical embeddable payload for downstream decision evidence.

        A paper/risk decision record can carry this verbatim (e.g. inside its
        existing evidence JSON) so the link to the exact forecast artifact is
        explicit rather than inferred from timestamps.
        """
        return {
            "forecast_id": self.forecast_id,
            "decision_identity": self.decision_identity,
            "decision_time": _canonical_utc(self.decision_time).isoformat(),
        }


def require_forecast_reference(
    ledger: ForecastLedger, reference: DecisionForecastReference
) -> ForecastRecord:
    """Fail closed unless a committed, PIT-safe forecast backs the decision.

    Ordering is structural: the decision must carry the forecast's own
    ``forecast_id``/``decision_identity``, not merely an earlier timestamp.
    """
    reference.validate()
    record = ledger.read(reference.forecast_id)
    if record is None:
        raise ForecastGateError("rozhodnutí odkazuje na neexistující forecast")
    if record.decision_identity() != reference.decision_identity:
        raise ForecastGateError("rozhodovací identita neodpovídá forecastu")
    if require_utc(record.decision_time) > require_utc(reference.decision_time):
        raise ForecastGateError("forecast vznikl po rozhodnutí (causality porušena)")
    # ``decision_time`` is the market timestamp the forecast refers to. The
    # evidence itself must also have existed when the decision was taken: a
    # forecast *created* after the decision could only have been back-filled
    # from knowledge of the outcome.
    if require_utc(record.created_at) > require_utc(reference.decision_time):
        raise ForecastGateError(
            "forecast byl vytvořen po rozhodnutí (evidence neexistovala v čase rozhodnutí)"
        )
    if record.status is not ForecastStatus.FORECAST_EMITTED:
        raise ForecastGateError(
            f"probability-aware rozhodnutí vyžaduje FORECAST_EMITTED (stav={record.status})"
        )
    return record


def decide_with_forecast(
    ledger: ForecastLedger,
    reference: DecisionForecastReference,
    decide: Callable[[ForecastRecord], Any],
) -> Any:
    """Run the downstream decision only after the forecast gate passes.

    Mirrors the promotion-gate pattern: the decision callable is never invoked
    when the forecast evidence is missing, uncommitted or degraded.
    """
    record = require_forecast_reference(ledger, reference)
    return decide(record)
