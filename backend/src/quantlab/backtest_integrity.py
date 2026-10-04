"""Backtest Integrity Gate (Issue #267).

Deterministický fail-closed gate, který odmítne metodicky neplatný nebo
strukturálně podezřelý backtest dříve, než se jeho P&L/Sharpe použije pro
validation nebo promotion (parent roadmap #76).

Modul je čistá funkce nad explicitně deklarovanými kontrakty: žádné I/O, žádné
čtení hodin, žádný síťový ani broker přístup, pouze ``Decimal`` a tz-aware
``datetime``. PAPER-only zůstává tvrdou runtime hranicí; gate nic nepovyšuje,
neautorizuje a nezakládá nový backtest framework — pouze validuje existující
research/backtest tok.

Failure mode, který gate řeší: strategie měla intradenní exit v 15:55, ale
použitý timeframe daný bar neposkytoval, pozice zůstala tiše otevřená a
backtest vykázal absurdně vysoký výsledek. Takový výsledek musí skončit jako
``INVALID_TIME_GRID`` (nebo jiný ``INVALID_*``), nikdy jako vysoké P&L.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import islice
from zoneinfo import ZoneInfo

GATE_VERSION = "2.0.0"
PAPER_ONLY = True
MAX_FINDINGS = 200
_MAX_INTRADAY_CANDIDATES = 20000


class GateVerdict(StrEnum):
    """Výstup gate. První selhávající kategorie v kanonickém pořadí vyhrává."""

    VALID = "VALID"
    INVALID_TIME_GRID = "INVALID_TIME_GRID"
    INVALID_POSITION_LIFECYCLE = "INVALID_POSITION_LIFECYCLE"
    INVALID_MISSING_DATA = "INVALID_MISSING_DATA"
    INVALID_CAUSALITY = "INVALID_CAUSALITY"
    INVALID_EXECUTION_ASSUMPTIONS = "INVALID_EXECUTION_ASSUMPTIONS"
    INVALID_PORTFOLIO_AGGREGATION = "INVALID_PORTFOLIO_AGGREGATION"
    INVALID_INTRABAR_AMBIGUITY = "INVALID_INTRABAR_AMBIGUITY"
    INVALID_SURVIVORSHIP = "INVALID_SURVIVORSHIP"
    INVALID_TRIAL_ACCOUNTING = "INVALID_TRIAL_ACCOUNTING"
    INVALID_HOLDOUT_BURN = "INVALID_HOLDOUT_BURN"
    INVALID_PURGE_EMBARGO = "INVALID_PURGE_EMBARGO"
    INVALID_UNCERTAINTY = "INVALID_UNCERTAINTY"


CANONICAL_VERDICT_ORDER: tuple[GateVerdict, ...] = (
    GateVerdict.INVALID_TIME_GRID,
    GateVerdict.INVALID_POSITION_LIFECYCLE,
    GateVerdict.INVALID_MISSING_DATA,
    GateVerdict.INVALID_CAUSALITY,
    GateVerdict.INVALID_EXECUTION_ASSUMPTIONS,
    GateVerdict.INVALID_PORTFOLIO_AGGREGATION,
    GateVerdict.INVALID_INTRABAR_AMBIGUITY,
    GateVerdict.INVALID_SURVIVORSHIP,
    GateVerdict.INVALID_TRIAL_ACCOUNTING,
    GateVerdict.INVALID_HOLDOUT_BURN,
    GateVerdict.INVALID_PURGE_EMBARGO,
    GateVerdict.INVALID_UNCERTAINTY,
)


class FindingSeverity(StrEnum):
    ERROR = "ERROR"
    DEGRADED = "DEGRADED"
    INFO = "INFO"


class PromotionDecision(StrEnum):
    PROMOTION_GRADE = "PROMOTION_GRADE"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    BLOCKED = "BLOCKED"


class IntegrityGateError(RuntimeError):
    """Fail-closed odmítnutí: report není promotion-grade."""


@dataclass(frozen=True)
class IntegrityFinding:
    code: str
    verdict: GateVerdict
    severity: FindingSeverity
    subject: str
    detail: str


# ---------------------------------------------------------------------------
# 1. Time / grid integrity
# ---------------------------------------------------------------------------


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    D1 = "1d"


_TIMEFRAME_MINUTES: Mapping[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
}


class GridAlignmentPolicy(StrEnum):
    """Deklarovaná policy pro zaokrouhlení požadovaného eventu na bar.

    ``NONE`` znamená, že požadovaný timestamp musí být přesně reprezentovatelný;
    jakékoli tiché posunutí je pak ``INVALID_TIME_GRID``.
    """

    NONE = "NONE"
    NEXT_BAR = "NEXT_BAR"
    PREVIOUS_BAR = "PREVIOUS_BAR"


class EventKind(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    PARTIAL_FILL = "PARTIAL_FILL"


@dataclass(frozen=True)
class RequestedEvent:
    """Strategy event/exit timestamp, který musí být reprezentovatelný v gridu."""

    label: str
    timestamp: datetime
    kind: EventKind


@dataclass(frozen=True)
class SessionWindow:
    """Canonical calendar evidence jedné session včetně DST a early close."""

    session_date: date
    local_open: time
    local_close: time
    open_at: datetime
    close_at: datetime
    early_close: bool = False
    regular_close_at: datetime | None = None


@dataclass(frozen=True)
class BarGrid:
    """Použitý bar/event grid a jeho canonical calendar evidence."""

    timeframe: Timeframe
    timezone_name: str
    calendar_identity: str
    sessions: tuple[SessionWindow, ...]
    bar_timestamps: tuple[datetime, ...]
    alignment_policy: GridAlignmentPolicy = GridAlignmentPolicy.NONE


def _utc(value: datetime) -> datetime | None:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _wall_minutes(value: datetime, zone: ZoneInfo) -> tuple[int, int]:
    local = value.astimezone(zone)
    return local.hour, local.minute


def _sessions_by_date(grid: BarGrid) -> dict[date, SessionWindow]:
    return {session.session_date: session for session in grid.sessions}


def _session_for(
    timestamp: datetime, sessions: Mapping[date, SessionWindow], zone: ZoneInfo
) -> SessionWindow | None:
    return sessions.get(timestamp.astimezone(zone).date())


def check_time_grid(
    grid: BarGrid, requested_events: Sequence[RequestedEvent]
) -> list[IntegrityFinding]:
    verdict = GateVerdict.INVALID_TIME_GRID
    findings: list[IntegrityFinding] = []
    if not grid.calendar_identity.strip():
        findings.append(
            IntegrityFinding(
                "time_grid.calendar_identity_missing",
                verdict,
                FindingSeverity.ERROR,
                "grid",
                "Chybí canonical calendar evidence identity",
            )
        )
    try:
        zone = ZoneInfo(grid.timezone_name)
    except KeyError:
        findings.append(
            IntegrityFinding(
                "time_grid.unknown_timezone",
                verdict,
                FindingSeverity.ERROR,
                "grid",
                f"Neznámá časová zóna {grid.timezone_name!r}",
            )
        )
        return findings

    if not grid.bar_timestamps:
        findings.append(
            IntegrityFinding(
                "time_grid.empty_grid",
                verdict,
                FindingSeverity.ERROR,
                "grid",
                "Grid neobsahuje žádný bar",
            )
        )
    if not grid.sessions:
        findings.append(
            IntegrityFinding(
                "time_grid.no_sessions",
                verdict,
                FindingSeverity.ERROR,
                "grid",
                "Grid neobsahuje žádnou session",
            )
        )

    seen_dates: set[date] = set()
    for session in grid.sessions:
        subject = f"session:{session.session_date.isoformat()}"
        if session.session_date in seen_dates:
            findings.append(
                IntegrityFinding(
                    "time_grid.duplicate_session",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Duplicitní session v canonical calendar evidence",
                )
            )
        seen_dates.add(session.session_date)
        opened = _utc(session.open_at)
        closed = _utc(session.close_at)
        if opened is None or closed is None:
            findings.append(
                IntegrityFinding(
                    "time_grid.naive_session_time",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Session open/close musí být tz-aware",
                )
            )
            continue
        if opened >= closed:
            findings.append(
                IntegrityFinding(
                    "time_grid.session_window_inverted",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Session open musí být před close",
                )
            )
        if (opened.astimezone(zone).hour, opened.astimezone(zone).minute) != (
            session.local_open.hour,
            session.local_open.minute,
        ):
            findings.append(
                IntegrityFinding(
                    "time_grid.open_dst_mismatch",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "UTC open neodpovídá deklarovanému lokálnímu open (DST/timezone)",
                )
            )
        if (closed.astimezone(zone).hour, closed.astimezone(zone).minute) != (
            session.local_close.hour,
            session.local_close.minute,
        ):
            findings.append(
                IntegrityFinding(
                    "time_grid.close_dst_mismatch",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "UTC close neodpovídá deklarovanému lokálnímu close (DST/timezone)",
                )
            )
        if session.early_close:
            regular = session.regular_close_at
            if regular is None:
                findings.append(
                    IntegrityFinding(
                        "time_grid.early_close_evidence_missing",
                        verdict,
                        FindingSeverity.ERROR,
                        subject,
                        "Early close vyžaduje canonical regular_close_at evidence",
                    )
                )
            elif _utc(regular) is None or closed is None or closed >= regular:
                findings.append(
                    IntegrityFinding(
                        "time_grid.early_close_not_early",
                        verdict,
                        FindingSeverity.ERROR,
                        subject,
                        "Early close musí být dříve než regular close",
                    )
                )

    sessions = _sessions_by_date(grid)
    step = _TIMEFRAME_MINUTES.get(grid.timeframe)

    seen_bars: set[datetime] = set()
    duplicates: list[datetime] = []
    for bar in grid.bar_timestamps:
        value = _utc(bar)
        if value is None:
            findings.append(
                IntegrityFinding(
                    "time_grid.naive_bar_timestamp",
                    verdict,
                    FindingSeverity.ERROR,
                    "grid",
                    "Bar timestamp musí být tz-aware",
                )
            )
            continue
        if value in seen_bars:
            duplicates.append(value)
        seen_bars.add(value)
    if duplicates:
        findings.append(
            IntegrityFinding(
                "missing_data.duplicate_timestamp",
                GateVerdict.INVALID_MISSING_DATA,
                FindingSeverity.ERROR,
                "grid",
                f"Duplicitní bar timestamps: {len(duplicates)}",
            )
        )
    for previous, current in zip(grid.bar_timestamps, grid.bar_timestamps[1:], strict=False):
        if current <= previous:
            findings.append(
                IntegrityFinding(
                    "missing_data.non_monotonic",
                    GateVerdict.INVALID_MISSING_DATA,
                    FindingSeverity.ERROR,
                    "grid",
                    "Bar timestamps nejsou striktně rostoucí",
                )
            )
            break

    for bar in grid.bar_timestamps:
        value = _utc(bar)
        if value is None:
            continue
        bar_session = _session_for(value, sessions, zone)
        if bar_session is None:
            findings.append(
                IntegrityFinding(
                    "time_grid.bar_outside_session",
                    verdict,
                    FindingSeverity.ERROR,
                    f"bar:{value.isoformat()}",
                    "Bar neleží v žádné deklarované session",
                )
            )
            continue
        opened = _utc(bar_session.open_at)
        closed = _utc(bar_session.close_at)
        if opened is None or closed is None:
            continue
        if not opened <= value <= closed:
            findings.append(
                IntegrityFinding(
                    "time_grid.bar_outside_window",
                    verdict,
                    FindingSeverity.ERROR,
                    f"bar:{value.isoformat()}",
                    "Bar leží mimo session open/close (early close/DST)",
                )
            )
            continue
        if grid.timeframe is Timeframe.D1:
            if value != closed:
                findings.append(
                    IntegrityFinding(
                        "time_grid.daily_bar_not_at_close",
                        verdict,
                        FindingSeverity.ERROR,
                        f"bar:{value.isoformat()}",
                        "Denní bar musí ležet přesně na session close",
                    )
                )
            continue
        if step is not None:
            elapsed = int((value - opened).total_seconds())
            if elapsed < 0 or elapsed % (step * 60) != 0:
                findings.append(
                    IntegrityFinding(
                        "time_grid.bar_not_aligned_to_timeframe",
                        verdict,
                        FindingSeverity.ERROR,
                        f"bar:{value.isoformat()}",
                        f"Bar není zarovnaný na timeframe {grid.timeframe.value}",
                    )
                )

    bar_set = set(grid.bar_timestamps)
    for event in requested_events:
        value = _utc(event.timestamp)
        subject = f"event:{event.label}"
        if value is None:
            findings.append(
                IntegrityFinding(
                    "time_grid.naive_event_timestamp",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Požadovaný event timestamp musí být tz-aware",
                )
            )
            continue
        if value in bar_set:
            continue
        if grid.alignment_policy is GridAlignmentPolicy.NONE:
            findings.append(
                IntegrityFinding(
                    "time_grid.unrepresentable_event",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    f"{event.kind.value} {value.isoformat()} není v gridu reprezentovatelný "
                    "a není deklarována alignment policy",
                )
            )
            continue
        event_session = _session_for(value, sessions, zone)
        if event_session is None:
            findings.append(
                IntegrityFinding(
                    "time_grid.event_outside_session",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Požadovaný event neleží v žádné session",
                )
            )
            continue
        opened = _utc(event_session.open_at)
        closed = _utc(event_session.close_at)
        if opened is None or closed is None:
            continue
        candidates = list(
            islice(
                (bar for bar in grid.bar_timestamps if opened <= bar <= closed),
                _MAX_INTRADAY_CANDIDATES,
            )
        )
        target: datetime | None
        if grid.alignment_policy is GridAlignmentPolicy.NEXT_BAR:
            target = min((bar for bar in candidates if bar > value), default=None)
        else:
            target = max((bar for bar in candidates if bar < value), default=None)
        if target is None:
            findings.append(
                IntegrityFinding(
                    "time_grid.alignment_target_missing",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Deklarovaná alignment policy nenajde v session žádný cílový bar",
                )
            )
            continue
        findings.append(
            IntegrityFinding(
                "time_grid.declared_grid_alignment",
                verdict,
                FindingSeverity.INFO,
                subject,
                f"{event.kind.value} {value.isoformat()} -> {target.isoformat()} "
                f"podle policy {grid.alignment_policy.value}",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 2. Position lifecycle
# ---------------------------------------------------------------------------


class CarryRule(StrEnum):
    NONE = "NONE"
    EXPLICIT_CARRY = "EXPLICIT_CARRY"


class EndOfTestPolicy(StrEnum):
    NONE = "NONE"
    MARK_TO_MARKET = "MARK_TO_MARKET"
    FORCE_CLOSE = "FORCE_CLOSE"
    CARRY_FORWARD = "CARRY_FORWARD"


@dataclass(frozen=True)
class PartialFill:
    timestamp: datetime
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True)
class PositionLifecycle:
    position_id: str
    instrument_id: str
    entry_at: datetime
    quantity: Decimal
    exit_at: datetime | None
    exit_quantity: Decimal
    partial_fills: tuple[PartialFill, ...] = ()
    carry_rule: CarryRule = CarryRule.NONE
    end_of_test_policy: EndOfTestPolicy = EndOfTestPolicy.NONE


def check_position_lifecycle(
    positions: Sequence[PositionLifecycle],
) -> list[IntegrityFinding]:
    verdict = GateVerdict.INVALID_POSITION_LIFECYCLE
    findings: list[IntegrityFinding] = []
    seen: set[str] = set()
    for position in positions:
        subject = f"position:{position.position_id}"
        if position.position_id in seen:
            findings.append(
                IntegrityFinding(
                    "lifecycle.duplicate_position_id",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Duplicitní position_id v ledgeru",
                )
            )
        seen.add(position.position_id)
        entry = _utc(position.entry_at)
        if entry is None:
            findings.append(
                IntegrityFinding(
                    "lifecycle.naive_entry",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Entry timestamp musí být tz-aware",
                )
            )
            continue
        if not position.quantity.is_finite() or position.quantity <= 0:
            findings.append(
                IntegrityFinding(
                    "lifecycle.invalid_quantity",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Entry quantity musí být kladná a konečná",
                )
            )
        if position.exit_at is None:
            if position.carry_rule is not CarryRule.EXPLICIT_CARRY:
                findings.append(
                    IntegrityFinding(
                        "lifecycle.orphan_position",
                        verdict,
                        FindingSeverity.ERROR,
                        subject,
                        "Otevřená pozice na konci testu bez explicitní carry rule",
                    )
                )
            if position.end_of_test_policy is EndOfTestPolicy.NONE:
                findings.append(
                    IntegrityFinding(
                        "lifecycle.missing_end_of_test_policy",
                        verdict,
                        FindingSeverity.ERROR,
                        subject,
                        "Otevřená pozice na konci testu bez explicitní end-of-test policy",
                    )
                )
            continue
        exit_at = _utc(position.exit_at)
        if exit_at is None:
            findings.append(
                IntegrityFinding(
                    "lifecycle.naive_exit",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Exit timestamp musí být tz-aware",
                )
            )
            continue
        if exit_at <= entry:
            findings.append(
                IntegrityFinding(
                    "lifecycle.exit_before_entry",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Exit musí nastat po entry",
                )
            )
        if not position.exit_quantity.is_finite() or position.exit_quantity <= 0:
            findings.append(
                IntegrityFinding(
                    "lifecycle.invalid_exit_quantity",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Uzavřená pozice musí mít kladné exit quantity",
                )
            )
        if position.exit_quantity > position.quantity:
            findings.append(
                IntegrityFinding(
                    "lifecycle.exit_exceeds_entry",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Exit quantity překračuje entry quantity",
                )
            )
        previous: datetime | None = None
        partial_total = Decimal("0")
        for fill in position.partial_fills:
            value = _utc(fill.timestamp)
            if value is None or value < entry or value > exit_at:
                findings.append(
                    IntegrityFinding(
                        "lifecycle.partial_fill_out_of_window",
                        verdict,
                        FindingSeverity.ERROR,
                        subject,
                        "Partial fill leží mimo [entry, exit] okno",
                    )
                )
                continue
            if previous is not None and value <= previous:
                findings.append(
                    IntegrityFinding(
                        "lifecycle.partial_fill_not_increasing",
                        verdict,
                        FindingSeverity.ERROR,
                        subject,
                        "Partial fills nejsou striktně rostoucí",
                    )
                )
            previous = value
            partial_total += fill.quantity
        if partial_total > position.exit_quantity:
            findings.append(
                IntegrityFinding(
                    "lifecycle.partial_fills_exceed_exit",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Součet partial fills překračuje exit quantity",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 3. Missing / stale data
# ---------------------------------------------------------------------------


class MissingDataPolicy(StrEnum):
    FAIL = "FAIL"
    DEGRADE = "DEGRADE"


@dataclass(frozen=True)
class ObservationPoint:
    instrument_id: str
    session_date: date
    timestamp: datetime
    observed_at: datetime
    revision: int = 1


@dataclass(frozen=True)
class RequiredScope:
    instrument_ids: tuple[str, ...]
    session_dates: tuple[date, ...]
    max_staleness: timedelta = timedelta(days=1)
    missing_policy: MissingDataPolicy = MissingDataPolicy.FAIL


def check_missing_data(
    scope: RequiredScope, observations: Sequence[ObservationPoint]
) -> list[IntegrityFinding]:
    verdict = GateVerdict.INVALID_MISSING_DATA
    severity = (
        FindingSeverity.DEGRADED
        if scope.missing_policy is MissingDataPolicy.DEGRADE
        else FindingSeverity.ERROR
    )
    findings: list[IntegrityFinding] = []
    if not scope.instrument_ids or not scope.session_dates:
        findings.append(
            IntegrityFinding(
                "missing_data.empty_required_scope",
                verdict,
                FindingSeverity.ERROR,
                "scope",
                "Required observation scope je prázdný",
            )
        )
        return findings

    by_instrument: dict[str, list[ObservationPoint]] = {}
    seen_keys: set[tuple[str, datetime]] = set()
    for observation in observations:
        stamp = _utc(observation.timestamp)
        if stamp is None:
            findings.append(
                IntegrityFinding(
                    "missing_data.naive_observation",
                    verdict,
                    FindingSeverity.ERROR,
                    f"observation:{observation.instrument_id}",
                    "Observation timestamp musí být tz-aware",
                )
            )
            continue
        key = (observation.instrument_id, stamp)
        if key in seen_keys:
            findings.append(
                IntegrityFinding(
                    "missing_data.duplicate_observation",
                    verdict,
                    FindingSeverity.ERROR,
                    f"observation:{observation.instrument_id}",
                    f"Duplicitní observation timestamp {stamp.isoformat()}",
                )
            )
            continue
        seen_keys.add(key)
        by_instrument.setdefault(observation.instrument_id, []).append(observation)

    for instrument, rows in by_instrument.items():
        ordered = sorted(rows, key=lambda item: item.timestamp)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.timestamp <= previous.timestamp:
                findings.append(
                    IntegrityFinding(
                        "missing_data.non_monotonic_observation",
                        verdict,
                        FindingSeverity.ERROR,
                        f"observation:{instrument}",
                        "Observation chronologie není striktně rostoucí",
                    )
                )
                break

    present = {
        (observation.instrument_id, observation.session_date) for observation in observations
    }
    for instrument in scope.instrument_ids:
        for session_date in scope.session_dates:
            if (instrument, session_date) not in present:
                findings.append(
                    IntegrityFinding(
                        "missing_data.required_bar_missing",
                        verdict,
                        severity,
                        f"observation:{instrument}:{session_date.isoformat()}",
                        "Chybí povinný bar/event v required observation scope",
                    )
                )

    for observation in observations:
        stamp = _utc(observation.timestamp)
        known = _utc(observation.observed_at)
        if stamp is None or known is None:
            continue
        if known - stamp > scope.max_staleness:
            findings.append(
                IntegrityFinding(
                    "missing_data.stale_observation",
                    verdict,
                    severity,
                    f"observation:{observation.instrument_id}:{stamp.isoformat()}",
                    "Observation je stale vůči deklarované staleness policy",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 4. Execution realism contract
# ---------------------------------------------------------------------------


class FillSemantics(StrEnum):
    NEXT_BAR_OPEN = "NEXT_BAR_OPEN"
    SAME_BAR_CLOSE = "SAME_BAR_CLOSE"
    VWAP = "VWAP"
    UNDECLARED = "UNDECLARED"


class PriceField(StrEnum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    ADJUSTED_CLOSE = "ADJUSTED_CLOSE"
    UNDECLARED = "UNDECLARED"


@dataclass(frozen=True)
class ExecutionAssumptions:
    fees_bps: Decimal | None
    spread_bps: Decimal | None
    slippage_bps: Decimal | None
    impact_model: str | None
    fill_semantics: FillSemantics
    signal_price_field: PriceField
    executable_price_field: PriceField
    liquidity_participation: Decimal | None
    zero_cost_diagnostic: bool = False

    def describe(self) -> tuple[tuple[str, str], ...]:
        """Explicitní rozpis cost/execution assumptions do reportu (nikdy implicitní)."""

        def show(value: Decimal | None) -> str:
            return "UNDECLARED" if value is None else str(value)

        return (
            ("fees_bps", show(self.fees_bps)),
            ("spread_bps", show(self.spread_bps)),
            ("slippage_bps", show(self.slippage_bps)),
            ("impact_model", self.impact_model or "UNDECLARED"),
            ("fill_semantics", self.fill_semantics.value),
            ("signal_price_field", self.signal_price_field.value),
            ("executable_price_field", self.executable_price_field.value),
            (
                "liquidity_participation",
                "UNDECLARED"
                if self.liquidity_participation is None
                else str(self.liquidity_participation),
            ),
            ("zero_cost_diagnostic", str(self.zero_cost_diagnostic)),
        )

    @property
    def zero_cost(self) -> bool:
        return self.fees_bps == 0 and self.spread_bps == 0 and self.slippage_bps == 0


def check_execution(
    assumptions: ExecutionAssumptions, *, promotion_grade: bool
) -> list[IntegrityFinding]:
    verdict = GateVerdict.INVALID_EXECUTION_ASSUMPTIONS
    findings: list[IntegrityFinding] = []
    cost_severity = FindingSeverity.ERROR if promotion_grade else FindingSeverity.DEGRADED

    for name, value in (
        ("fees_bps", assumptions.fees_bps),
        ("spread_bps", assumptions.spread_bps),
        ("slippage_bps", assumptions.slippage_bps),
    ):
        if value is None:
            findings.append(
                IntegrityFinding(
                    f"execution.undeclared_{name}",
                    verdict,
                    cost_severity,
                    "execution",
                    f"{name} není deklarováno",
                )
            )
        elif not value.is_finite() or value < 0:
            findings.append(
                IntegrityFinding(
                    f"execution.invalid_{name}",
                    verdict,
                    FindingSeverity.ERROR,
                    "execution",
                    f"{name} musí být konečné a nezáporné",
                )
            )
    if not assumptions.impact_model or not assumptions.impact_model.strip():
        findings.append(
            IntegrityFinding(
                "execution.undeclared_impact_model",
                verdict,
                cost_severity,
                "execution",
                "Slippage/impact model není deklarován",
            )
        )
    if assumptions.fill_semantics is FillSemantics.UNDECLARED:
        findings.append(
            IntegrityFinding(
                "execution.undeclared_fill_semantics",
                verdict,
                FindingSeverity.ERROR,
                "execution",
                "Fill semantics není deklarováno",
            )
        )
    if assumptions.signal_price_field is PriceField.UNDECLARED:
        findings.append(
            IntegrityFinding(
                "execution.undeclared_signal_price",
                verdict,
                FindingSeverity.ERROR,
                "execution",
                "Signal price field není deklarován",
            )
        )
    if assumptions.executable_price_field is PriceField.UNDECLARED:
        findings.append(
            IntegrityFinding(
                "execution.undeclared_executable_price",
                verdict,
                FindingSeverity.ERROR,
                "execution",
                "Executable price field není deklarován",
            )
        )
    if (
        assumptions.signal_price_field is not PriceField.UNDECLARED
        and assumptions.signal_price_field is assumptions.executable_price_field
    ):
        findings.append(
            IntegrityFinding(
                "execution.signal_price_used_as_executable",
                verdict,
                FindingSeverity.ERROR,
                "execution",
                "Signal price nesmí být zároveň executable price",
            )
        )
    participation = assumptions.liquidity_participation
    if participation is None:
        findings.append(
            IntegrityFinding(
                "execution.undeclared_liquidity",
                verdict,
                cost_severity,
                "execution",
                "Liquidity/participation assumption není deklarováno",
            )
        )
    elif not participation.is_finite() or not Decimal("0") < participation <= Decimal("1"):
        findings.append(
            IntegrityFinding(
                "execution.invalid_liquidity",
                verdict,
                FindingSeverity.ERROR,
                "execution",
                "Liquidity participation musí být v intervalu (0, 1]",
            )
        )
    if assumptions.zero_cost:
        if not assumptions.zero_cost_diagnostic:
            findings.append(
                IntegrityFinding(
                    "execution.implicit_zero_cost",
                    verdict,
                    FindingSeverity.ERROR,
                    "execution",
                    "Nulové costs musí být explicitně označený diagnostic counterfactual",
                )
            )
        elif promotion_grade:
            findings.append(
                IntegrityFinding(
                    "execution.zero_cost_not_promotion_grade",
                    verdict,
                    FindingSeverity.DEGRADED,
                    "execution",
                    "0 cost counterfactual nesmí být promotion-grade výsledek",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 5. Shared-capital portfolio realism
# ---------------------------------------------------------------------------


class AggregationMode(StrEnum):
    SHARED_LEDGER = "SHARED_LEDGER"
    INDEPENDENT_SUM = "INDEPENDENT_SUM"


@dataclass(frozen=True)
class InstrumentEquityCurve:
    instrument_id: str
    points: tuple[tuple[datetime, Decimal], ...]


@dataclass(frozen=True)
class PortfolioAggregation:
    mode: AggregationMode
    instruments: tuple[str, ...]
    shared_capital: bool
    simultaneous_signal_policy: str | None
    claimed_portfolio_curve: tuple[tuple[datetime, Decimal], ...] = ()
    per_instrument_curves: tuple[InstrumentEquityCurve, ...] = ()


def detect_naive_sum_aggregation(
    claimed_portfolio_curve: Sequence[tuple[datetime, Decimal]],
    per_instrument_curves: Sequence[InstrumentEquityCurve],
) -> bool:
    """Odhalí, že 'portfolio' křivka je jen součet nezávislých single-ticker křivek."""

    if len(per_instrument_curves) < 2 or not claimed_portfolio_curve:
        return False
    lengths = {len(curve.points) for curve in per_instrument_curves}
    if lengths != {len(claimed_portfolio_curve)}:
        return False
    for index, (timestamp, value) in enumerate(claimed_portfolio_curve):
        total = Decimal("0")
        for curve in per_instrument_curves:
            point_timestamp, point_value = curve.points[index]
            if point_timestamp != timestamp:
                return False
            total += point_value
        if total != value:
            return False
    return True


def check_portfolio_aggregation(
    aggregation: PortfolioAggregation,
) -> list[IntegrityFinding]:
    verdict = GateVerdict.INVALID_PORTFOLIO_AGGREGATION
    findings: list[IntegrityFinding] = []
    instruments = aggregation.instruments
    if len(set(instruments)) != len(instruments):
        findings.append(
            IntegrityFinding(
                "portfolio.duplicate_instrument",
                verdict,
                FindingSeverity.ERROR,
                "portfolio",
                "Portfolio obsahuje duplicitní instrument",
            )
        )
    multi_asset = len(instruments) > 1
    if multi_asset:
        if aggregation.mode is AggregationMode.INDEPENDENT_SUM:
            findings.append(
                IntegrityFinding(
                    "portfolio.independent_sum_of_tickers",
                    verdict,
                    FindingSeverity.ERROR,
                    "portfolio",
                    "Součet nezávislých single-ticker equity curves není společné portfolio",
                )
            )
        if not aggregation.shared_capital:
            findings.append(
                IntegrityFinding(
                    "portfolio.capital_not_shared",
                    verdict,
                    FindingSeverity.ERROR,
                    "portfolio",
                    "Multi-asset portfolio musí sdílet cash/exposure/capital constraints",
                )
            )
        if not aggregation.simultaneous_signal_policy or not (
            aggregation.simultaneous_signal_policy.strip()
        ):
            findings.append(
                IntegrityFinding(
                    "portfolio.simultaneous_policy_missing",
                    verdict,
                    FindingSeverity.ERROR,
                    "portfolio",
                    "Simultaneous signals musí soutěžit o stejný risk/capital budget",
                )
            )
    if detect_naive_sum_aggregation(
        aggregation.claimed_portfolio_curve, aggregation.per_instrument_curves
    ):
        findings.append(
            IntegrityFinding(
                "portfolio.naive_sum_detected",
                verdict,
                FindingSeverity.ERROR,
                "portfolio",
                "Portfolio equity curve je přesně součet nezávislých ticker P&L",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 6. Causality / look-ahead
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MembershipEvidence:
    instrument_id: str
    valid_from: date
    valid_to: date | None = None


@dataclass(frozen=True)
class CausalityEvidence:
    as_of: datetime
    observations: tuple[ObservationPoint, ...]
    memberships: tuple[MembershipEvidence, ...]
    pinned_revisions: Mapping[str, int] = field(default_factory=dict)


def check_causality(evidence: CausalityEvidence) -> list[IntegrityFinding]:
    verdict = GateVerdict.INVALID_CAUSALITY
    findings: list[IntegrityFinding] = []
    as_of = _utc(evidence.as_of)
    if as_of is None:
        findings.append(
            IntegrityFinding(
                "causality.naive_as_of",
                verdict,
                FindingSeverity.ERROR,
                "causality",
                "as_of musí být tz-aware",
            )
        )
        return findings
    as_of_date = as_of.date()
    for observation in evidence.observations:
        stamp = _utc(observation.timestamp)
        known = _utc(observation.observed_at)
        subject = f"observation:{observation.instrument_id}"
        if stamp is not None and stamp > as_of:
            findings.append(
                IntegrityFinding(
                    "causality.future_observation",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    f"Observation {stamp.isoformat()} leží po as_of",
                )
            )
        if known is not None and known > as_of:
            findings.append(
                IntegrityFinding(
                    "causality.future_knowledge",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    f"observed_at {known.isoformat()} leží po as_of",
                )
            )
        pinned = evidence.pinned_revisions.get(observation.instrument_id)
        if pinned is not None and observation.revision > pinned:
            findings.append(
                IntegrityFinding(
                    "causality.post_hoc_revision",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    f"Revision {observation.revision} > pinned {pinned}",
                )
            )
    seen_memberships: set[str] = set()
    for membership in evidence.memberships:
        subject = f"membership:{membership.instrument_id}"
        if membership.instrument_id in seen_memberships:
            findings.append(
                IntegrityFinding(
                    "causality.duplicate_membership",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Duplicitní PIT membership evidence",
                )
            )
        seen_memberships.add(membership.instrument_id)
        if membership.valid_from > as_of_date:
            findings.append(
                IntegrityFinding(
                    "causality.future_membership",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Membership platí až po as_of",
                )
            )
        if membership.valid_to is not None and membership.valid_to < as_of_date:
            findings.append(
                IntegrityFinding(
                    "causality.expired_membership",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Použitá membership už v as_of vypršela",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 7. Intrabar ambiguity (Architecture BLOCKER A)
# ---------------------------------------------------------------------------


class IntrabarPolicy(StrEnum):
    """Policy pro řešení intrabar ambiguity (stop+target ve stejném baru)."""

    NONE = "NONE"
    CONSERVATIVE = "CONSERVATIVE"
    FINER_DATA = "FINER_DATA"


@dataclass(frozen=True)
class IntrabarEvent:
    """Event v rámci jednoho baru, kde pořadí není pozorovatelné."""

    label: str
    bar_timestamp: datetime
    kind: EventKind


@dataclass(frozen=True)
class IntrabarAmbiguity:
    """Deklarace intrabar ambiguity a policy pro její řešení."""

    events: tuple[IntrabarEvent, ...]
    policy: IntrabarPolicy
    signal_from_close_filled_at_close: bool = False
    same_bar_stop_target: bool = False


def check_intrabar_ambiguity(
    ambiguity: IntrabarAmbiguity,
) -> list[IntegrityFinding]:
    """Fail-closed kontrola intrabar ambiguity (Architecture BLOCKER A).

    Pokud jsou ve stejném baru zasaženy například stop i target, nebo signal
    odvozený z close je plněn na tomtéž close, musí být buď použita jemnější
    data, aplikována konzervativní policy, nebo run označen jako
    ``INVALID_INTRABAR_AMBIGUITY``.
    """

    verdict = GateVerdict.INVALID_INTRABAR_AMBIGUITY
    findings: list[IntegrityFinding] = []
    if ambiguity.policy is IntrabarPolicy.NONE:
        if ambiguity.same_bar_stop_target:
            findings.append(
                IntegrityFinding(
                    "intrabar.same_bar_stop_target",
                    verdict,
                    FindingSeverity.ERROR,
                    "intrabar",
                    "Stop+target ve stejném baru bez deklarované policy",
                )
            )
        if ambiguity.signal_from_close_filled_at_close:
            findings.append(
                IntegrityFinding(
                    "intrabar.signal_close_filled_at_close",
                    verdict,
                    FindingSeverity.ERROR,
                    "intrabar",
                    "Signal odvozený z close plněn na tomtéž close bez evidence",
                )
            )
        if len(ambiguity.events) >= 2:
            bar_groups: dict[datetime, list[IntrabarEvent]] = {}
            for event in ambiguity.events:
                bar_groups.setdefault(event.bar_timestamp, []).append(event)
            for bar_ts, events in bar_groups.items():
                if len(events) >= 2:
                    findings.append(
                        IntegrityFinding(
                            "intrabar.multiple_events_same_bar",
                            verdict,
                            FindingSeverity.ERROR,
                            f"intrabar:{bar_ts.isoformat()}",
                            f"{len(events)} events ve stejném baru bez deklarované policy",
                        )
                    )
    elif ambiguity.policy is IntrabarPolicy.CONSERVATIVE:
        if ambiguity.same_bar_stop_target:
            findings.append(
                IntegrityFinding(
                    "intrabar.conservative_policy_declared",
                    verdict,
                    FindingSeverity.INFO,
                    "intrabar",
                    "Stop+target ve stejném baru: konzervativní policy deklarována",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 8. Survivorship / terminal lifecycle (Architecture BLOCKER C)
# ---------------------------------------------------------------------------


class TerminalEvent(StrEnum):
    """Typ terminálního eventu instrumentu."""

    DELISTING = "DELISTING"
    BANKRUPTCY = "BANKRUPTCY"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    NONE = "NONE"


class TerminalPolicy(StrEnum):
    """Policy pro terminální valuation/exit."""

    NONE = "NONE"
    MARK_TO_LAST = "MARK_TO_LAST"
    FORCE_CLOSE = "FORCE_CLOSE"
    CARRY_FORWARD = "CARRY_FORWARD"


@dataclass(frozen=True)
class TerminalLifecycle:
    """Evidence terminálního lifecycle instrumentu během testu."""

    instrument_id: str
    terminal_event: TerminalEvent
    terminal_date: date
    policy: TerminalPolicy
    terminal_value: Decimal | None = None


def check_survivorship(
    terminals: Sequence[TerminalLifecycle],
) -> list[IntegrityFinding]:
    """Fail-closed kontrola survivorship (Architecture BLOCKER C).

    Delisting/bankruptcy/symbol change musí mít explicitní terminal
    valuation/exit policy a nesmí tiše zmizet z portfolia.
    """

    verdict = GateVerdict.INVALID_SURVIVORSHIP
    findings: list[IntegrityFinding] = []
    seen: set[str] = set()
    for terminal in terminals:
        subject = f"terminal:{terminal.instrument_id}"
        if terminal.instrument_id in seen:
            findings.append(
                IntegrityFinding(
                    "survivorship.duplicate_terminal",
                    verdict,
                    FindingSeverity.ERROR,
                    subject,
                    "Duplicitní terminal lifecycle evidence",
                )
            )
        seen.add(terminal.instrument_id)
        if terminal.terminal_event is not TerminalEvent.NONE:
            if terminal.policy is TerminalPolicy.NONE:
                findings.append(
                    IntegrityFinding(
                        "survivorship.missing_terminal_policy",
                        verdict,
                        FindingSeverity.ERROR,
                        subject,
                        f"{terminal.terminal_event.value} bez terminal valuation/exit policy",
                    )
                )
            if terminal.policy is TerminalPolicy.MARK_TO_LAST:
                if terminal.terminal_value is None:
                    findings.append(
                        IntegrityFinding(
                            "survivorship.mark_to_last_without_value",
                            verdict,
                            FindingSeverity.ERROR,
                            subject,
                            "MARK_TO_LAST policy vyžaduje terminal_value",
                        )
                    )
    return findings


# ---------------------------------------------------------------------------
# 9. Asset-class execution completeness (Architecture BLOCKER B)
# ---------------------------------------------------------------------------


class AssetClass(StrEnum):
    """Asset class pro execution completeness."""

    EQUITY = "EQUITY"
    EQUITY_SHORT = "EQUITY_SHORT"
    FX = "FX"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    CRYPTO = "CRYPTO"


@dataclass(frozen=True)
class ExecutionCompleteness:
    """Deklarace execution completeness podle asset class."""

    asset_class: AssetClass
    short_availability: bool = False
    borrow_fees_bps: Decimal | None = None
    corporate_actions_handled: bool = False
    fx_conversion_declared: bool = False
    spread_slippage_impact_declared: bool = False
    liquidity_participation_declared: bool = False
    financing_carry_declared: bool = False


def check_execution_completeness(
    completeness: ExecutionCompleteness,
) -> list[IntegrityFinding]:
    """Fail-closed kontrola execution completeness podle asset class (BLOCKER B).

    Promotion-grade gate musí ověřit relevantní položky podle strategie/asset
    class, ne jen obecný seznam. Chybějící required component = fail/degraded.
    """

    verdict = GateVerdict.INVALID_EXECUTION_ASSUMPTIONS
    findings: list[IntegrityFinding] = []
    ac = completeness.asset_class
    if ac is AssetClass.EQUITY_SHORT:
        if not completeness.short_availability:
            findings.append(
                IntegrityFinding(
                    "execution_completeness.short_availability_missing",
                    verdict,
                    FindingSeverity.ERROR,
                    "execution_completeness",
                    "Short strategy musí deklarovat short availability",
                )
            )
        if completeness.borrow_fees_bps is None:
            findings.append(
                IntegrityFinding(
                    "execution_completeness.borrow_fees_missing",
                    verdict,
                    FindingSeverity.ERROR,
                    "execution_completeness",
                    "Short strategy musí deklarovat borrow fees",
                )
            )
    if ac in (AssetClass.EQUITY, AssetClass.EQUITY_SHORT):
        if not completeness.corporate_actions_handled:
            findings.append(
                IntegrityFinding(
                    "execution_completeness.corporate_actions_missing",
                    verdict,
                    FindingSeverity.ERROR,
                    "execution_completeness",
                    "Equity strategy musí deklarovat corporate actions handling",
                )
            )
    if ac is AssetClass.FX:
        if not completeness.fx_conversion_declared:
            findings.append(
                IntegrityFinding(
                    "execution_completeness.fx_conversion_missing",
                    verdict,
                    FindingSeverity.ERROR,
                    "execution_completeness",
                    "FX strategy musí deklarovat FX conversion",
                )
            )
    if not completeness.spread_slippage_impact_declared:
        findings.append(
            IntegrityFinding(
                "execution_completeness.spread_slippage_impact_missing",
                verdict,
                FindingSeverity.ERROR,
                "execution_completeness",
                "Spread/slippage/impact musí být deklarovány",
            )
        )
    if not completeness.liquidity_participation_declared:
        findings.append(
            IntegrityFinding(
                "execution_completeness.liquidity_participation_missing",
                verdict,
                FindingSeverity.ERROR,
                "execution_completeness",
                "Liquidity/participation musí být deklarováno",
            )
        )
    if ac in (AssetClass.EQUITY_SHORT, AssetClass.FUTURES):
        if not completeness.financing_carry_declared:
            findings.append(
                IntegrityFinding(
                    "execution_completeness.financing_carry_missing",
                    verdict,
                    FindingSeverity.ERROR,
                    "execution_completeness",
                    "Short/Futures strategy musí deklarovat financing/carry",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 10. Trial accounting / data snooping (STAT-BLOCKER A)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrialFamily:
    """Evidence celé family testovaných strategií/parametrů."""

    family_id: str
    total_trials: int
    failed_trials: int
    rejected_trials: int
    successful_trials: int
    failed_variants_preserved: bool = True


def check_trial_accounting(
    family: TrialFamily,
) -> list[IntegrityFinding]:
    """Fail-closed kontrola trial accounting (STAT-BLOCKER A).

    Gate musí znát celou family testovaných strategií/parametrů, ne pouze
    vítězný run. Vymazání neúspěšných variant nesmí resetovat budget.
    """

    verdict = GateVerdict.INVALID_TRIAL_ACCOUNTING
    findings: list[IntegrityFinding] = []
    if family.total_trials <= 0:
        findings.append(
            IntegrityFinding(
                "trial_accounting.no_trials",
                verdict,
                FindingSeverity.ERROR,
                "trial_accounting",
                "Family musí mít alespoň 1 trial",
            )
        )
    if family.failed_variants_preserved is False:
        findings.append(
            IntegrityFinding(
                "trial_accounting.failed_variants_not_preserved",
                verdict,
                FindingSeverity.ERROR,
                "trial_accounting",
                "Failed/rejected variants musí být zachovány v experiment registry",
            )
        )
    total = family.failed_trials + family.rejected_trials + family.successful_trials
    if total != family.total_trials:
        findings.append(
            IntegrityFinding(
                "trial_accounting.trial_count_mismatch",
                verdict,
                FindingSeverity.ERROR,
                "trial_accounting",
                f"Součet trialů ({total}) neodpovídá total_trials ({family.total_trials})",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 11. Holdout burn semantics (STAT-BLOCKER B)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldoutEvidence:
    """Evidence OOS/holdout access a burn semantics."""

    holdout_id: str
    holdout_accessed_at: datetime | None
    holdout_burned_for_family: bool
    adaptive_iteration_after_access: bool = False


def check_holdout_burn(
    evidence: HoldoutEvidence,
) -> list[IntegrityFinding]:
    """Fail-closed kontrola holdout burn (STAT-BLOCKER B).

    Jakmile je OOS výsledek použit pro adaptaci (změna strategie, parametrů,
    funnelu, benchmarku, cost modelu, targetu), holdout je burned pro family.
    """

    verdict = GateVerdict.INVALID_HOLDOUT_BURN
    findings: list[IntegrityFinding] = []
    if evidence.holdout_accessed_at is not None:
        if evidence.adaptive_iteration_after_access:
            if not evidence.holdout_burned_for_family:
                findings.append(
                    IntegrityFinding(
                        "holdout_burn.adaptive_iteration_without_burn",
                        verdict,
                        FindingSeverity.ERROR,
                        "holdout_burn",
                        "Adaptace po OOS access musí mít holdout burned pro family",
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# 12. Purge / embargo (STAT-BLOCKER C)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitContract:
    """Split contract s purge/embargo pro overlapping labels."""

    decision_time: datetime
    label_resolution_time: datetime
    purge_interval: timedelta
    embargo_interval: timedelta


def check_purge_embargo(
    contract: SplitContract,
) -> list[IntegrityFinding]:
    """Fail-closed kontrola purge/embargo (STAT-BLOCKER C).

    U strategií/modelů, jejichž target/label sahá za decision timestamp,
    nesmí train/validation/OOS hranice obsahovat observations s
    překrývajícím se outcome window.
    """

    verdict = GateVerdict.INVALID_PURGE_EMBARGO
    findings: list[IntegrityFinding] = []
    if contract.label_resolution_time <= contract.decision_time:
        findings.append(
            IntegrityFinding(
                "purge_embargo.label_before_decision",
                verdict,
                FindingSeverity.ERROR,
                "purge_embargo",
                "Label resolution musí být po decision time",
            )
        )
    if contract.purge_interval < timedelta(0):
        findings.append(
            IntegrityFinding(
                "purge_embargo.negative_purge",
                verdict,
                FindingSeverity.ERROR,
                "purge_embargo",
                "Purge interval nesmí být záporný",
            )
        )
    if contract.embargo_interval < timedelta(0):
        findings.append(
            IntegrityFinding(
                "purge_embargo.negative_embargo",
                verdict,
                FindingSeverity.ERROR,
                "purge_embargo",
                "Embargo interval nesmí být záporný",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 13. Uncertainty quantification (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


class UncertaintyMethod(StrEnum):
    """Preregistered uncertainty method."""

    NONE = "NONE"
    BLOCK_BOOTSTRAP = "BLOCK_BOOTSTRAP"
    CLUSTER_BOOTSTRAP = "CLUSTER_BOOTSTRAP"
    STATIONARY_BOOTSTRAP = "STATIONARY_BOOTSTRAP"


@dataclass(frozen=True)
class UncertaintyEvidence:
    """Evidence uncertainty quantification pro dependent returns."""

    method: UncertaintyMethod
    confidence_level: Decimal
    block_size: int | None = None
    n_resamples: int | None = None


def check_uncertainty(
    evidence: UncertaintyEvidence,
) -> list[IntegrityFinding]:
    """Fail-closed kontrola uncertainty quantification (STAT-BLOCKER D).

    Promotion-grade report musí používat preregistered uncertainty method
    vhodnou pro daný proces (block/cluster-aware bootstrap).
    """

    verdict = GateVerdict.INVALID_UNCERTAINTY
    findings: list[IntegrityFinding] = []
    if evidence.method is UncertaintyMethod.NONE:
        findings.append(
            IntegrityFinding(
                "uncertainty.no_method",
                verdict,
                FindingSeverity.ERROR,
                "uncertainty",
                "Promotion-grade report musí mít preregistered uncertainty method",
            )
        )
    if evidence.method in (
        UncertaintyMethod.BLOCK_BOOTSTRAP,
        UncertaintyMethod.CLUSTER_BOOTSTRAP,
        UncertaintyMethod.STATIONARY_BOOTSTRAP,
    ):
        if evidence.block_size is None or evidence.block_size <= 0:
            findings.append(
                IntegrityFinding(
                    "uncertainty.missing_block_size",
                    verdict,
                    FindingSeverity.ERROR,
                    "uncertainty",
                    f"{evidence.method.value} vyžaduje block_size > 0",
                )
            )
        if evidence.n_resamples is None or evidence.n_resamples <= 0:
            findings.append(
                IntegrityFinding(
                    "uncertainty.missing_n_resamples",
                    verdict,
                    FindingSeverity.ERROR,
                    "uncertainty",
                    f"{evidence.method.value} vyžaduje n_resamples > 0",
                )
            )
    if not evidence.confidence_level.is_finite() or not Decimal(
        "0"
    ) < evidence.confidence_level < Decimal("1"):
        findings.append(
            IntegrityFinding(
                "uncertainty.invalid_confidence_level",
                verdict,
                FindingSeverity.ERROR,
                "uncertainty",
                "Confidence level musí být v intervalu (0, 1)",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Gate orchestration + immutable evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestIntegrityInputs:
    run_id: str
    strategy_name: str
    strategy_version: str
    promotion_grade: bool
    grid: BarGrid
    requested_events: tuple[RequestedEvent, ...]
    positions: tuple[PositionLifecycle, ...]
    scope: RequiredScope
    observations: tuple[ObservationPoint, ...]
    execution: ExecutionAssumptions
    aggregation: PortfolioAggregation
    causality: CausalityEvidence
    degraded_promotion_allowed: bool = False
    intrabar: IntrabarAmbiguity | None = None
    terminals: tuple[TerminalLifecycle, ...] = ()
    execution_completeness: ExecutionCompleteness | None = None
    trial_family: TrialFamily | None = None
    holdout: HoldoutEvidence | None = None
    split_contract: SplitContract | None = None
    uncertainty: UncertaintyEvidence | None = None


@dataclass(frozen=True)
class BacktestIntegrityReport:
    gate_version: str
    run_id: str
    strategy_name: str
    strategy_version: str
    verdict: GateVerdict
    findings: tuple[IntegrityFinding, ...]
    evidence_hash: str
    degraded: bool
    promotion_grade_eligible: bool
    degraded_promotion_allowed: bool
    execution_assumptions: tuple[tuple[str, str], ...]
    paper_only: bool = True

    @property
    def execution_assumptions_zero_cost(self) -> bool:
        values = dict(self.execution_assumptions)
        return (
            values.get("fees_bps") == "0"
            and values.get("spread_bps") == "0"
            and values.get("slippage_bps") == "0"
        )

    def to_ledger_record(self) -> dict[str, object]:
        """Immutable, JSON-serializovatelný lineage záznam výsledku gate."""

        return {
            "gate_version": self.gate_version,
            "run_id": self.run_id,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "verdict": self.verdict.value,
            "degraded": self.degraded,
            "promotion_grade_eligible": self.promotion_grade_eligible,
            "paper_only": self.paper_only,
            "evidence_hash": self.evidence_hash,
            "findings": [
                {
                    "code": finding.code,
                    "verdict": finding.verdict.value,
                    "severity": finding.severity.value,
                    "subject": finding.subject,
                    "detail": finding.detail,
                }
                for finding in self.findings
            ],
            "execution_assumptions": dict(self.execution_assumptions),
        }


def _canonical(value: object) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _bound(findings: Sequence[IntegrityFinding]) -> tuple[IntegrityFinding, ...]:
    if len(findings) <= MAX_FINDINGS:
        return tuple(findings)
    bounded = list(findings[:MAX_FINDINGS])
    bounded.append(
        IntegrityFinding(
            "gate.findings_truncated",
            GateVerdict.VALID,
            FindingSeverity.DEGRADED,
            "gate",
            f"Počet findings překročil bound {MAX_FINDINGS}",
        )
    )
    return tuple(bounded)


def _verdict(findings: Sequence[IntegrityFinding]) -> GateVerdict:
    failed = {
        finding.verdict
        for finding in findings
        if finding.severity is FindingSeverity.ERROR and finding.verdict is not GateVerdict.VALID
    }
    for candidate in CANONICAL_VERDICT_ORDER:
        if candidate in failed:
            return candidate
    return GateVerdict.VALID


def run_integrity_gate(inputs: BacktestIntegrityInputs) -> BacktestIntegrityReport:
    """Spustí všechny integritní kontroly a vrátí deterministický verdikt + evidence."""

    collected: list[IntegrityFinding] = []
    collected.extend(check_time_grid(inputs.grid, inputs.requested_events))
    collected.extend(check_position_lifecycle(inputs.positions))
    collected.extend(check_missing_data(inputs.scope, inputs.observations))
    collected.extend(check_causality(inputs.causality))
    collected.extend(check_execution(inputs.execution, promotion_grade=inputs.promotion_grade))
    collected.extend(check_portfolio_aggregation(inputs.aggregation))
    if inputs.intrabar is not None:
        collected.extend(check_intrabar_ambiguity(inputs.intrabar))
    if inputs.terminals:
        collected.extend(check_survivorship(inputs.terminals))
    if inputs.execution_completeness is not None:
        collected.extend(check_execution_completeness(inputs.execution_completeness))
    if inputs.trial_family is not None:
        collected.extend(check_trial_accounting(inputs.trial_family))
    if inputs.holdout is not None:
        collected.extend(check_holdout_burn(inputs.holdout))
    if inputs.split_contract is not None:
        collected.extend(check_purge_embargo(inputs.split_contract))
    if inputs.uncertainty is not None:
        collected.extend(check_uncertainty(inputs.uncertainty))
    findings = _bound(collected)
    verdict = _verdict(findings)
    degraded = any(finding.severity is FindingSeverity.DEGRADED for finding in findings)
    promotion_grade_eligible = (
        verdict is GateVerdict.VALID
        and not inputs.execution.zero_cost
        and (not degraded or inputs.degraded_promotion_allowed)
    )
    payload = {
        "gate_version": GATE_VERSION,
        "run_id": inputs.run_id,
        "strategy_name": inputs.strategy_name,
        "strategy_version": inputs.strategy_version,
        "verdict": verdict.value,
        "degraded": degraded,
        "promotion_grade_eligible": promotion_grade_eligible,
        "findings": [
            {
                "code": finding.code,
                "verdict": finding.verdict.value,
                "severity": finding.severity.value,
                "subject": finding.subject,
                "detail": finding.detail,
            }
            for finding in findings
        ],
        "execution_assumptions": dict(inputs.execution.describe()),
    }
    evidence_hash = hashlib.sha256(_canonical(payload).encode()).hexdigest()
    return BacktestIntegrityReport(
        gate_version=GATE_VERSION,
        run_id=inputs.run_id,
        strategy_name=inputs.strategy_name,
        strategy_version=inputs.strategy_version,
        verdict=verdict,
        findings=findings,
        evidence_hash=evidence_hash,
        degraded=degraded,
        promotion_grade_eligible=promotion_grade_eligible,
        degraded_promotion_allowed=inputs.degraded_promotion_allowed,
        execution_assumptions=inputs.execution.describe(),
    )


def evaluate_promotion_gate(
    report: BacktestIntegrityReport, *, zero_cost_diagnostic: bool = False
) -> PromotionDecision:
    """Gate běží před eligibility/promotion scorecard (#76) a je blocking."""

    if report.verdict is not GateVerdict.VALID:
        return PromotionDecision.BLOCKED
    if zero_cost_diagnostic or report.execution_assumptions_zero_cost:
        return PromotionDecision.DIAGNOSTIC_ONLY
    if report.degraded and not report.degraded_promotion_allowed:
        return PromotionDecision.BLOCKED
    if not report.promotion_grade_eligible:
        return PromotionDecision.BLOCKED
    return PromotionDecision.PROMOTION_GRADE


def assert_promotion_eligible(report: BacktestIntegrityReport) -> None:
    """Fail-closed: promotion-grade run nesmí projít s neplatným gate reportem."""

    if report.verdict is not GateVerdict.VALID:
        raise IntegrityGateError(
            f"Backtest je {report.verdict.value}; P&L/Sharpe nesmí být použito pro promotion"
        )
    if report.degraded and not report.degraded_promotion_allowed:
        raise IntegrityGateError("Degradovaný backtest není promotion-grade")
    if not report.promotion_grade_eligible:
        raise IntegrityGateError("Report není promotion-grade (implicitní/nezadané costs)")


def run_gated_promotion[T](
    inputs: BacktestIntegrityInputs,
    evaluate: Callable[[BacktestIntegrityReport], T],
) -> T:
    """Spustí gate PŘED eligibility/promotion scorecard (#76) a je blocking.

    ``evaluate`` je promotion/eligibility scorecard; je vyvolán pouze tehdy, když
    gate vrátí ``PROMOTION_GRADE``. Jakýkoli neplatný nebo diagnostický výsledek
    failne ještě před použitím P&L/Sharpe, takže se nedostane do promotion toku.
    """

    report = run_integrity_gate(inputs)
    decision = evaluate_promotion_gate(report)
    if decision is not PromotionDecision.PROMOTION_GRADE:
        raise IntegrityGateError(
            f"Gate verdict={report.verdict.value} decision={decision.value}; "
            "promotion scorecard se nesmí spustit"
        )
    return evaluate(report)
