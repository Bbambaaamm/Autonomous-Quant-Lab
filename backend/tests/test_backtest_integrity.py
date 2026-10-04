"""Tests for the Backtest Integrity Gate (Issue #267).

Cover every acceptance criterion of the issue, including the adversarial
fixtures for the known failure mode (intraday 15:55 exit on a grid that cannot
represent it, which previously produced an absurdly high P&L instead of a
fail-closed INVALID_BACKTEST).
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quantlab.backtest_integrity import (
    GATE_VERSION,
    PAPER_ONLY,
    AggregationMode,
    AssetClass,
    BacktestIntegrityInputs,
    BarGrid,
    CarryRule,
    CausalityEvidence,
    EndOfTestPolicy,
    EventKind,
    ExecutionAssumptions,
    ExecutionCompleteness,
    FillSemantics,
    FindingSeverity,
    GateVerdict,
    GridAlignmentPolicy,
    HoldoutEvidence,
    InstrumentEquityCurve,
    IntegrityGateError,
    IntrabarAmbiguity,
    IntrabarEvent,
    IntrabarPolicy,
    MembershipEvidence,
    MissingDataPolicy,
    ObservationPoint,
    PartialFill,
    PortfolioAggregation,
    PositionLifecycle,
    PriceField,
    PromotionDecision,
    RequestedEvent,
    RequiredScope,
    SessionWindow,
    SplitContract,
    TerminalEvent,
    TerminalLifecycle,
    TerminalPolicy,
    Timeframe,
    TrialFamily,
    UncertaintyEvidence,
    UncertaintyMethod,
    assert_promotion_eligible,
    check_causality,
    check_execution,
    check_execution_completeness,
    check_holdout_burn,
    check_intrabar_ambiguity,
    check_missing_data,
    check_portfolio_aggregation,
    check_position_lifecycle,
    check_purge_embargo,
    check_survivorship,
    check_time_grid,
    check_trial_accounting,
    check_uncertainty,
    detect_naive_sum_aggregation,
    evaluate_promotion_gate,
    run_gated_promotion,
    run_integrity_gate,
)

UTC_ZONE = "UTC"
NY = "America/New_York"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _daily_session(day: date, *, early: bool = False) -> SessionWindow:
    # Kanonická US session: lokálně 09:30-16:00 America/New_York.
    local_open = time(9, 30)
    local_close = time(16, 0)
    if early:
        local_close = time(13, 0)
    zone = ZoneInfo(NY)
    opened = datetime.combine(day, local_open, tzinfo=zone).astimezone(UTC)
    closed = datetime.combine(day, local_close, tzinfo=zone).astimezone(UTC)
    regular = None
    if early:
        regular = datetime.combine(day, time(16, 0), tzinfo=zone).astimezone(UTC)
    return SessionWindow(
        session_date=day,
        local_open=local_open,
        local_close=local_close,
        open_at=opened,
        close_at=closed,
        early_close=early,
        regular_close_at=regular,
    )


def _intraday_session(day: date) -> SessionWindow:
    return _daily_session(day)


def _daily_grid(
    days: tuple[date, ...],
    *,
    early: tuple[date, ...] = (),
    alignment: GridAlignmentPolicy = GridAlignmentPolicy.NONE,
    sessions: tuple[SessionWindow, ...] | None = None,
    bars: tuple[datetime, ...] | None = None,
) -> BarGrid:
    if sessions is None:
        sessions = tuple(_daily_session(day, early=day in early) for day in days)
    if bars is None:
        bars = tuple(session.close_at for session in sessions)
    return BarGrid(
        timeframe=Timeframe.D1,
        timezone_name=NY,
        calendar_identity="XNYS:test",
        sessions=sessions,
        bar_timestamps=bars,
        alignment_policy=alignment,
    )


def _intraday_grid(
    day: date,
    step_minutes: int,
    *,
    timeframe: Timeframe,
    bars: tuple[datetime, ...] | None = None,
    alignment: GridAlignmentPolicy = GridAlignmentPolicy.NONE,
) -> BarGrid:
    session = _intraday_session(day)
    if bars is None:
        generated: list[datetime] = []
        current = session.open_at
        while current <= session.close_at:
            generated.append(current)
            current += timedelta(minutes=step_minutes)
        bars = tuple(generated)
    return BarGrid(
        timeframe=timeframe,
        timezone_name=NY,
        calendar_identity="XNYS:test",
        sessions=(session,),
        bar_timestamps=bars,
        alignment_policy=alignment,
    )


def _execution(**overrides: object) -> ExecutionAssumptions:
    base: dict[str, object] = {
        "fees_bps": Decimal("1"),
        "spread_bps": Decimal("0.5"),
        "slippage_bps": Decimal("5"),
        "impact_model": "linear_participation_v1",
        "fill_semantics": FillSemantics.NEXT_BAR_OPEN,
        "signal_price_field": PriceField.ADJUSTED_CLOSE,
        "executable_price_field": PriceField.OPEN,
        "liquidity_participation": Decimal("0.05"),
        "zero_cost_diagnostic": False,
    }
    base.update(overrides)
    return ExecutionAssumptions(**base)  # type: ignore[arg-type]


def _causality(as_of: datetime, observations: tuple[ObservationPoint, ...]) -> CausalityEvidence:
    return CausalityEvidence(as_of=as_of, observations=observations, memberships=())


def _valid_inputs(**overrides: object) -> BacktestIntegrityInputs:
    day = date(2026, 8, 12)
    session = _daily_session(day)
    observation = ObservationPoint(
        instrument_id="AAA",
        session_date=day,
        timestamp=session.close_at,
        observed_at=session.close_at,
    )
    base: dict[str, object] = {
        "run_id": "run-1",
        "strategy_name": "ma",
        "strategy_version": "1.0.0",
        "promotion_grade": True,
        "grid": _daily_grid((day,)),
        "requested_events": (),
        "positions": (),
        "scope": RequiredScope(("AAA",), (day,)),
        "observations": (observation,),
        "execution": _execution(),
        "aggregation": PortfolioAggregation(
            mode=AggregationMode.SHARED_LEDGER,
            instruments=("AAA",),
            shared_capital=True,
            simultaneous_signal_policy=None,
        ),
        "causality": _causality(session.close_at, (observation,)),
    }
    base.update(overrides)
    return BacktestIntegrityInputs(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Acceptance criterion 1: exit=15:55 on a timeframe that cannot represent it
# ---------------------------------------------------------------------------


class TestTimeGridExitAlignment:
    def test_1555_exit_on_daily_grid_is_invalid_fail_closed(self) -> None:
        day = date(2026, 8, 12)
        exit_at = datetime.combine(day, time(15, 55), tzinfo=ZoneInfo(NY)).astimezone(UTC)
        grid = _daily_grid((day,))
        events = (RequestedEvent("exit", exit_at, EventKind.EXIT),)
        findings = check_time_grid(grid, events)
        codes = {finding.code for finding in findings}
        assert "time_grid.unrepresentable_event" in codes
        assert any(
            finding.verdict is GateVerdict.INVALID_TIME_GRID and finding.severity.value == "ERROR"
            for finding in findings
        )

    def test_1555_exit_on_hourly_grid_is_unrepresentable(self) -> None:
        day = date(2026, 8, 12)
        session = _daily_session(day)
        # Hodinový grid: 09:30, 10:30, ... 15:30. 15:55 chybí.
        bars = tuple(
            session.open_at + timedelta(minutes=60 * i)
            for i in range(7)
            if session.open_at + timedelta(minutes=60 * i) <= session.close_at
        )
        grid = _intraday_grid(day, 60, timeframe=Timeframe.H1, bars=bars)
        exit_at = datetime.combine(day, time(15, 55), tzinfo=ZoneInfo(NY)).astimezone(UTC)
        findings = check_time_grid(grid, (RequestedEvent("exit", exit_at, EventKind.EXIT),))
        assert any(finding.code == "time_grid.unrepresentable_event" for finding in findings)

    def test_1555_exit_representable_on_5m_grid(self) -> None:
        day = date(2026, 8, 12)
        session = _daily_session(day)
        # 5m grid pokrývá 15:55 (offset od open 09:30 je 385 minut = 77 * 5).
        bars = tuple(
            session.open_at + timedelta(minutes=5 * i)
            for i in range(0, 79)
            if session.open_at + timedelta(minutes=5 * i) <= session.close_at
        )
        grid = _intraday_grid(day, 5, timeframe=Timeframe.M5, bars=bars)
        exit_at = session.open_at + timedelta(minutes=385)
        findings = check_time_grid(grid, (RequestedEvent("exit", exit_at, EventKind.EXIT),))
        assert not [finding for finding in findings if finding.severity.value == "ERROR"]

    def test_declared_alignment_policy_is_audited_not_silent(self) -> None:
        day = date(2026, 8, 12)
        session = _daily_session(day)
        bars = tuple(
            session.open_at + timedelta(minutes=60 * i)
            for i in range(7)
            if session.open_at + timedelta(minutes=60 * i) <= session.close_at
        )
        grid = _intraday_grid(
            day,
            60,
            timeframe=Timeframe.H1,
            bars=bars,
            alignment=GridAlignmentPolicy.PREVIOUS_BAR,
        )
        exit_at = datetime.combine(day, time(15, 55), tzinfo=ZoneInfo(NY)).astimezone(UTC)
        findings = check_time_grid(grid, (RequestedEvent("exit", exit_at, EventKind.EXIT),))
        aligned = [f for f in findings if f.code == "time_grid.declared_grid_alignment"]
        assert len(aligned) == 1
        assert aligned[0].severity.value == "INFO"
        assert "PREVIOUS_BAR" in aligned[0].detail

    def test_naive_event_timestamp_fails_closed(self) -> None:
        day = date(2026, 8, 12)
        grid = _daily_grid((day,))
        naive = datetime(2026, 8, 12, 19, 55)
        findings = check_time_grid(grid, (RequestedEvent("exit", naive, EventKind.EXIT),))
        assert any(finding.code == "time_grid.naive_event_timestamp" for finding in findings)


# ---------------------------------------------------------------------------
# Acceptance criterion 2: early-close / DST / session regressions
# ---------------------------------------------------------------------------


class TestCalendarRegressions:
    def test_early_close_session_passes_with_canonical_evidence(self) -> None:
        # 2024-11-29 (Black Friday) XNYS early close 13:00 local.
        day = date(2024, 11, 29)
        grid = _daily_grid((day,), early=(day,))
        findings = check_time_grid(grid, ())
        assert not [f for f in findings if f.severity.value == "ERROR"]

    def test_early_close_without_regular_close_evidence_fails(self) -> None:
        day = date(2024, 11, 29)
        session = SessionWindow(
            session_date=day,
            local_open=time(9, 30),
            local_close=time(13, 0),
            open_at=_daily_session(day).open_at,
            close_at=datetime.combine(day, time(13, 0), tzinfo=ZoneInfo(NY)).astimezone(UTC),
            early_close=True,
            regular_close_at=None,
        )
        grid = _daily_grid((day,), sessions=(session,))
        findings = check_time_grid(grid, ())
        assert any(finding.code == "time_grid.early_close_evidence_missing" for finding in findings)

    def test_dst_transition_open_close_are_dst_aware(self) -> None:
        # 2024-06-10 (EDT, 13:30 UTC open) a 2024-01-08 (EST, 14:30 UTC open).
        summer = _daily_session(date(2024, 6, 10))
        winter = _daily_session(date(2024, 1, 8))
        assert summer.open_at.hour == 13 and summer.open_at.minute == 30
        assert winter.open_at.hour == 14 and winter.open_at.minute == 30
        grid = _daily_grid((date(2024, 1, 8), date(2024, 6, 10)))
        findings = check_time_grid(grid, ())
        assert not [f for f in findings if f.severity.value == "ERROR"]

    def test_incorrect_dst_utc_open_fails(self) -> None:
        day = date(2024, 6, 10)
        correct = _daily_session(day)
        broken = SessionWindow(
            session_date=day,
            local_open=time(9, 30),
            local_close=time(16, 0),
            open_at=correct.open_at + timedelta(hours=1),  # špatná DST konverze
            close_at=correct.close_at,
        )
        grid = _daily_grid((day,), sessions=(broken,), bars=(correct.close_at,))
        findings = check_time_grid(grid, ())
        assert any(finding.code == "time_grid.open_dst_mismatch" for finding in findings)

    def test_daily_bar_not_on_close_fails(self) -> None:
        day = date(2026, 8, 12)
        session = _daily_session(day)
        grid = _daily_grid((day,), bars=(session.open_at,))
        findings = check_time_grid(grid, ())
        assert any(finding.code == "time_grid.daily_bar_not_at_close" for finding in findings)

    def test_unknown_timezone_fails_closed(self) -> None:
        day = date(2026, 8, 12)
        grid = BarGrid(
            timeframe=Timeframe.D1,
            timezone_name="Not/AZone",
            calendar_identity="XNYS:test",
            sessions=(_daily_session(day),),
            bar_timestamps=(_daily_session(day).close_at,),
        )
        findings = check_time_grid(grid, ())
        assert any(finding.code == "time_grid.unknown_timezone" for finding in findings)


# ---------------------------------------------------------------------------
# Acceptance criterion 3: orphan position without carry/end policy
# ---------------------------------------------------------------------------


class TestPositionLifecycle:
    def test_orphan_position_without_policy_fails(self) -> None:
        position = PositionLifecycle(
            position_id="p1",
            instrument_id="AAA",
            entry_at=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
            quantity=Decimal("10"),
            exit_at=None,
            exit_quantity=Decimal("0"),
        )
        findings = check_position_lifecycle((position,))
        codes = {finding.code for finding in findings}
        assert "lifecycle.orphan_position" in codes
        assert "lifecycle.missing_end_of_test_policy" in codes

    def test_orphan_with_explicit_carry_and_end_policy_passes(self) -> None:
        position = PositionLifecycle(
            position_id="p1",
            instrument_id="AAA",
            entry_at=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
            quantity=Decimal("10"),
            exit_at=None,
            exit_quantity=Decimal("0"),
            carry_rule=CarryRule.EXPLICIT_CARRY,
            end_of_test_policy=EndOfTestPolicy.MARK_TO_MARKET,
        )
        assert check_position_lifecycle((position,)) == []

    def test_exit_before_entry_fails(self) -> None:
        position = PositionLifecycle(
            position_id="p1",
            instrument_id="AAA",
            entry_at=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
            quantity=Decimal("10"),
            exit_at=datetime(2026, 8, 12, 13, 0, tzinfo=UTC),
            exit_quantity=Decimal("10"),
        )
        assert any(
            finding.code == "lifecycle.exit_before_entry"
            for finding in check_position_lifecycle((position,))
        )

    def test_partial_fill_out_of_window_fails(self) -> None:
        position = PositionLifecycle(
            position_id="p1",
            instrument_id="AAA",
            entry_at=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
            quantity=Decimal("10"),
            exit_at=datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
            exit_quantity=Decimal("10"),
            partial_fills=(
                PartialFill(datetime(2026, 8, 12, 13, 0, tzinfo=UTC), Decimal("5"), Decimal("1")),
            ),
        )
        assert any(
            finding.code == "lifecycle.partial_fill_out_of_window"
            for finding in check_position_lifecycle((position,))
        )

    def test_closed_position_with_consistent_partials_passes(self) -> None:
        position = PositionLifecycle(
            position_id="p1",
            instrument_id="AAA",
            entry_at=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
            quantity=Decimal("10"),
            exit_at=datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
            exit_quantity=Decimal("10"),
            partial_fills=(
                PartialFill(datetime(2026, 8, 12, 15, 0, tzinfo=UTC), Decimal("5"), Decimal("1")),
            ),
        )
        assert check_position_lifecycle((position,)) == []


# ---------------------------------------------------------------------------
# Acceptance criterion 4: missing required bar / stale data
# ---------------------------------------------------------------------------


class TestMissingData:
    def test_missing_required_bar_fails(self) -> None:
        scope = RequiredScope(("AAA",), (date(2026, 8, 12), date(2026, 8, 13)))
        observation = ObservationPoint(
            "AAA",
            date(2026, 8, 12),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
        )
        findings = check_missing_data(scope, (observation,))
        assert any(finding.code == "missing_data.required_bar_missing" for finding in findings)
        assert all(
            finding.severity.value == "ERROR"
            for finding in findings
            if finding.code == "missing_data.required_bar_missing"
        )

    def test_missing_required_bar_degrades_when_policy_says_so(self) -> None:
        scope = RequiredScope(
            ("AAA",),
            (date(2026, 8, 12), date(2026, 8, 13)),
            missing_policy=MissingDataPolicy.DEGRADE,
        )
        observation = ObservationPoint(
            "AAA",
            date(2026, 8, 12),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
        )
        findings = check_missing_data(scope, (observation,))
        missing = [f for f in findings if f.code == "missing_data.required_bar_missing"]
        assert missing and all(f.severity.value == "DEGRADED" for f in missing)

    def test_duplicate_observation_fails(self) -> None:
        scope = RequiredScope(("AAA",), (date(2026, 8, 12),))
        stamp = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        rows = (
            ObservationPoint("AAA", date(2026, 8, 12), stamp, stamp),
            ObservationPoint("AAA", date(2026, 8, 12), stamp, stamp),
        )
        findings = check_missing_data(scope, rows)
        assert any(finding.code == "missing_data.duplicate_observation" for finding in findings)

    def test_stale_observation_fails(self) -> None:
        scope = RequiredScope(("AAA",), (date(2026, 8, 12),), max_staleness=timedelta(hours=1))
        stamp = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        observation = ObservationPoint("AAA", date(2026, 8, 12), stamp, stamp + timedelta(days=3))
        findings = check_missing_data(scope, (observation,))
        assert any(finding.code == "missing_data.stale_observation" for finding in findings)

    def test_empty_scope_fails(self) -> None:
        findings = check_missing_data(RequiredScope((), ()), ())
        assert any(finding.code == "missing_data.empty_required_scope" for finding in findings)


# ---------------------------------------------------------------------------
# Acceptance criterion 5: promotion-grade cannot have implicit/undeclared costs
# ---------------------------------------------------------------------------


class TestExecutionAssumptions:
    def test_promotion_grade_with_undeclared_costs_fails(self) -> None:
        assumptions = _execution(fees_bps=None, spread_bps=None, slippage_bps=None)
        findings = check_execution(assumptions, promotion_grade=True)
        codes = {finding.code for finding in findings}
        assert "execution.undeclared_fees_bps" in codes
        assert "execution.undeclared_spread_bps" in codes
        assert "execution.undeclared_slippage_bps" in codes
        assert all(finding.severity.value == "ERROR" for finding in findings)

    def test_diagnostic_run_can_omit_costs_but_is_degraded(self) -> None:
        assumptions = _execution(fees_bps=None, spread_bps=None, slippage_bps=None)
        findings = check_execution(assumptions, promotion_grade=False)
        undeclared = [f for f in findings if f.code == "execution.undeclared_fees_bps"]
        assert undeclared and undeclared[0].severity.value == "DEGRADED"

    def test_implicit_zero_cost_fails(self) -> None:
        assumptions = _execution(
            fees_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            zero_cost_diagnostic=False,
        )
        findings = check_execution(assumptions, promotion_grade=False)
        assert any(finding.code == "execution.implicit_zero_cost" for finding in findings)

    def test_zero_cost_diagnostic_cannot_be_promotion_grade(self) -> None:
        assumptions = _execution(
            fees_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            zero_cost_diagnostic=True,
        )
        findings = check_execution(assumptions, promotion_grade=True)
        assert any(
            finding.code == "execution.zero_cost_not_promotion_grade" for finding in findings
        )

    def test_signal_price_equal_to_executable_price_fails(self) -> None:
        assumptions = _execution(
            signal_price_field=PriceField.CLOSE, executable_price_field=PriceField.CLOSE
        )
        findings = check_execution(assumptions, promotion_grade=True)
        assert any(
            finding.code == "execution.signal_price_used_as_executable" for finding in findings
        )

    def test_report_always_lists_cost_assumptions(self) -> None:
        report = run_integrity_gate(_valid_inputs())
        listed = dict(report.execution_assumptions)
        assert set(listed) == {
            "fees_bps",
            "spread_bps",
            "slippage_bps",
            "impact_model",
            "fill_semantics",
            "signal_price_field",
            "executable_price_field",
            "liquidity_participation",
            "zero_cost_diagnostic",
        }
        assert listed["fees_bps"] == "1"
        assert listed["impact_model"] == "linear_participation_v1"


# ---------------------------------------------------------------------------
# Acceptance criterion 6: multi-asset shared capital + naive-sum detection
# ---------------------------------------------------------------------------


class TestPortfolioAggregation:
    def test_naive_sum_of_independent_tickers_is_invalid(self) -> None:
        stamps = (
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
        )
        curve_a = InstrumentEquityCurve("AAA", tuple((t, Decimal("100")) for t in stamps))
        curve_b = InstrumentEquityCurve("BBB", tuple((t, Decimal("50")) for t in stamps))
        claimed = tuple((t, Decimal("150")) for t in stamps)  # přesný součet
        assert detect_naive_sum_aggregation(claimed, (curve_a, curve_b)) is True
        aggregation = PortfolioAggregation(
            mode=AggregationMode.SHARED_LEDGER,
            instruments=("AAA", "BBB"),
            shared_capital=True,
            simultaneous_signal_policy="pro-rata risk budget",
            claimed_portfolio_curve=claimed,
            per_instrument_curves=(curve_a, curve_b),
        )
        findings = check_portfolio_aggregation(aggregation)
        assert any(finding.code == "portfolio.naive_sum_detected" for finding in findings)

    def test_independent_sum_mode_is_invalid(self) -> None:
        aggregation = PortfolioAggregation(
            mode=AggregationMode.INDEPENDENT_SUM,
            instruments=("AAA", "BBB"),
            shared_capital=False,
            simultaneous_signal_policy=None,
        )
        findings = check_portfolio_aggregation(aggregation)
        codes = {finding.code for finding in findings}
        assert "portfolio.independent_sum_of_tickers" in codes
        assert "portfolio.capital_not_shared" in codes
        assert "portfolio.simultaneous_policy_missing" in codes

    def test_shared_ledger_multi_asset_passes(self) -> None:
        aggregation = PortfolioAggregation(
            mode=AggregationMode.SHARED_LEDGER,
            instruments=("AAA", "BBB"),
            shared_capital=True,
            simultaneous_signal_policy="pro-rata risk budget",
        )
        assert check_portfolio_aggregation(aggregation) == []

    def test_single_asset_does_not_require_shared_policy(self) -> None:
        aggregation = PortfolioAggregation(
            mode=AggregationMode.SHARED_LEDGER,
            instruments=("AAA",),
            shared_capital=False,
            simultaneous_signal_policy=None,
        )
        assert check_portfolio_aggregation(aggregation) == []

    def test_non_additive_curve_is_not_flagged_as_naive(self) -> None:
        stamps = (datetime(2026, 8, 12, 20, 0, tzinfo=UTC),)
        curve_a = InstrumentEquityCurve("AAA", ((stamps[0], Decimal("100")),))
        curve_b = InstrumentEquityCurve("BBB", ((stamps[0], Decimal("50")),))
        claimed = ((stamps[0], Decimal("140")),)  # sdílený cash -> není prostý součet
        assert detect_naive_sum_aggregation(claimed, (curve_a, curve_b)) is False


# ---------------------------------------------------------------------------
# Acceptance criterion 7: causality / look-ahead
# ---------------------------------------------------------------------------


class TestCausality:
    def test_future_observation_fails(self) -> None:
        as_of = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        future = ObservationPoint(
            "AAA",
            date(2026, 8, 13),
            datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
        )
        findings = check_causality(_causality(as_of, (future,)))
        assert any(finding.code == "causality.future_observation" for finding in findings)

    def test_future_knowledge_fails(self) -> None:
        as_of = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        leaked = ObservationPoint(
            "AAA",
            date(2026, 8, 12),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
        )
        findings = check_causality(_causality(as_of, (leaked,)))
        assert any(finding.code == "causality.future_knowledge" for finding in findings)

    def test_future_membership_fails(self) -> None:
        as_of = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        evidence = CausalityEvidence(
            as_of=as_of,
            observations=(),
            memberships=(MembershipEvidence("AAA", date(2026, 9, 1)),),
        )
        findings = check_causality(evidence)
        assert any(finding.code == "causality.future_membership" for finding in findings)

    def test_post_hoc_revision_fails(self) -> None:
        as_of = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        revised = ObservationPoint(
            "AAA",
            date(2026, 8, 12),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            revision=3,
        )
        evidence = CausalityEvidence(
            as_of=as_of,
            observations=(revised,),
            memberships=(),
            pinned_revisions={"AAA": 1},
        )
        findings = check_causality(evidence)
        assert any(finding.code == "causality.post_hoc_revision" for finding in findings)

    def test_clean_causality_passes(self) -> None:
        as_of = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        observation = ObservationPoint(
            "AAA",
            date(2026, 8, 12),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
        )
        evidence = CausalityEvidence(
            as_of=as_of,
            observations=(observation,),
            memberships=(MembershipEvidence("AAA", date(2026, 1, 1)),),
        )
        assert check_causality(evidence) == []


# ---------------------------------------------------------------------------
# Acceptance criteria: verdict aggregation, immutability, promotion blocking
# ---------------------------------------------------------------------------


class TestGateOrchestration:
    def test_valid_backtest_passes(self) -> None:
        report = run_integrity_gate(_valid_inputs())
        assert report.verdict is GateVerdict.VALID
        assert report.promotion_grade_eligible is True
        assert report.paper_only is True

    def test_verdict_uses_canonical_order(self) -> None:
        day = date(2026, 8, 12)
        session = _daily_session(day)
        # Současně: nereprezentovatelný event (TIME_GRID) i chybějící bar (MISSING_DATA).
        exit_at = datetime.combine(day, time(15, 55), tzinfo=ZoneInfo(NY)).astimezone(UTC)
        report = run_integrity_gate(
            _valid_inputs(
                requested_events=(RequestedEvent("exit", exit_at, EventKind.EXIT),),
                scope=RequiredScope(("AAA",), (date(2026, 8, 12), date(2026, 8, 13))),
                observations=(ObservationPoint("AAA", day, session.close_at, session.close_at),),
            )
        )
        assert report.verdict is GateVerdict.INVALID_TIME_GRID

    def test_report_is_immutable_and_hashable_evidence(self) -> None:
        report = run_integrity_gate(_valid_inputs())
        record = report.to_ledger_record()
        assert record["verdict"] == "VALID"
        assert record["evidence_hash"] == report.evidence_hash
        assert len(report.evidence_hash) == 64
        with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
            report.verdict = GateVerdict.VALID  # type: ignore[misc]

    def test_deterministic_evidence_hash(self) -> None:
        first = run_integrity_gate(_valid_inputs())
        second = run_integrity_gate(_valid_inputs())
        assert first.evidence_hash == second.evidence_hash

    def test_different_inputs_yield_different_hash(self) -> None:
        first = run_integrity_gate(_valid_inputs())
        second = run_integrity_gate(_valid_inputs(run_id="run-2"))
        assert first.evidence_hash != second.evidence_hash

    def test_gate_blocks_promotion_on_invalid(self) -> None:
        day = date(2026, 8, 12)
        session = _daily_session(day)
        orphan = PositionLifecycle("p1", "AAA", session.close_at, Decimal("10"), None, Decimal("0"))
        report = run_integrity_gate(_valid_inputs(positions=(orphan,)))
        assert report.verdict is GateVerdict.INVALID_POSITION_LIFECYCLE
        assert evaluate_promotion_gate(report) is PromotionDecision.BLOCKED
        with pytest.raises(IntegrityGateError):
            assert_promotion_eligible(report)

    def test_zero_cost_report_is_diagnostic_only(self) -> None:
        report = run_integrity_gate(
            _valid_inputs(
                execution=_execution(
                    fees_bps=Decimal("0"),
                    spread_bps=Decimal("0"),
                    slippage_bps=Decimal("0"),
                    zero_cost_diagnostic=True,
                )
            )
        )
        assert report.verdict is GateVerdict.VALID
        assert report.promotion_grade_eligible is False
        assert evaluate_promotion_gate(report) is PromotionDecision.DIAGNOSTIC_ONLY

    def test_degraded_report_not_promotion_grade_without_allowance(self) -> None:
        day = date(2026, 8, 12)
        report = run_integrity_gate(
            _valid_inputs(
                scope=RequiredScope(
                    ("AAA",),
                    (date(2026, 8, 12), date(2026, 8, 13)),
                    missing_policy=MissingDataPolicy.DEGRADE,
                ),
                observations=(
                    ObservationPoint(
                        "AAA", day, _daily_session(day).close_at, _daily_session(day).close_at
                    ),
                ),
            )
        )
        assert report.degraded is True
        assert evaluate_promotion_gate(report) is PromotionDecision.BLOCKED
        with pytest.raises(IntegrityGateError):
            assert_promotion_eligible(report)

    def test_degraded_allowed_is_recorded(self) -> None:
        report = run_integrity_gate(_valid_inputs(degraded_promotion_allowed=True))
        assert report.degraded_promotion_allowed is True

    def test_gate_runs_before_promotion_scorecard(self) -> None:
        """Gate je blocking: scorecard se nesmí spustit při neplatném reportu."""

        calls: list[str] = []

        def scorecard(report: object) -> str:
            calls.append("ran")
            return "scorecard"

        day = date(2026, 8, 12)
        session = _daily_session(day)
        orphan = PositionLifecycle("p1", "AAA", session.close_at, Decimal("10"), None, Decimal("0"))
        with pytest.raises(IntegrityGateError):
            run_gated_promotion(
                _valid_inputs(positions=(orphan,)),
                scorecard,  # type: ignore[arg-type]
            )
        assert calls == []

        result = run_gated_promotion(_valid_inputs(), scorecard)  # type: ignore[arg-type]
        assert result == "scorecard"
        assert calls == ["ran"]

    def test_gate_blocks_scorecard_for_diagnostic_zero_cost(self) -> None:
        def scorecard(report: object) -> str:  # pragma: no cover - nesmí se zavolat
            raise AssertionError("scorecard se nesmí spustit")

        with pytest.raises(IntegrityGateError):
            run_gated_promotion(
                _valid_inputs(
                    execution=_execution(
                        fees_bps=Decimal("0"),
                        spread_bps=Decimal("0"),
                        slippage_bps=Decimal("0"),
                        zero_cost_diagnostic=True,
                    )
                ),
                scorecard,  # type: ignore[arg-type]
            )

    def test_findings_are_bounded(self) -> None:
        scope = RequiredScope(
            ("AAA",),
            tuple(date(2026, 8, 12) + timedelta(days=i) for i in range(500)),
        )
        report = run_integrity_gate(_valid_inputs(scope=scope, observations=()))
        assert len(report.findings) <= 201
        assert any(finding.code == "gate.findings_truncated" for finding in report.findings)


class TestCanonicalCalendarIntegration:
    """Session evidence musí pocházet z canonical kalendáře repa (XNYS)."""

    @staticmethod
    def _grid_from_calendar(days: tuple[date, ...]) -> BarGrid:
        from zoneinfo import ZoneInfo

        from quantlab.market_data import XNYSCalendar

        calendar = XNYSCalendar()
        zone = ZoneInfo("America/New_York")
        sessions = tuple(
            SessionWindow(
                session_date=day,
                local_open=calendar.session_open(day).astimezone(zone).time(),
                local_close=calendar.session_close(day).astimezone(zone).time(),
                open_at=calendar.session_open(day),
                close_at=calendar.session_close(day),
                early_close=(calendar.session_close(day).astimezone(zone).time() != time(16, 0)),
                regular_close_at=(
                    datetime.combine(day, time(16, 0), tzinfo=zone).astimezone(UTC)
                    if calendar.session_close(day).astimezone(zone).time() != time(16, 0)
                    else None
                ),
            )
            for day in days
        )
        return BarGrid(
            timeframe=Timeframe.D1,
            timezone_name="America/New_York",
            calendar_identity=calendar.identity,
            sessions=sessions,
            bar_timestamps=tuple(session.close_at for session in sessions),
        )

    def test_canonical_xnys_early_close_and_dst_sessions_pass(self) -> None:
        days = (date(2024, 1, 8), date(2024, 6, 10), date(2024, 11, 29))
        grid = self._grid_from_calendar(days)
        findings = check_time_grid(grid, ())
        assert not [f for f in findings if f.severity.value == "ERROR"]

    def test_canonical_xnys_daily_grid_cannot_represent_1555_exit(self) -> None:
        day = date(2026, 8, 12)
        grid = self._grid_from_calendar((day,))
        zone = ZoneInfo("America/New_York")
        exit_at = datetime.combine(day, time(15, 55), tzinfo=zone).astimezone(UTC)
        findings = check_time_grid(grid, (RequestedEvent("exit", exit_at, EventKind.EXIT),))
        assert any(
            finding.code == "time_grid.unrepresentable_event" and finding.severity.value == "ERROR"
            for finding in findings
        )


class TestPurityAndPaperSafety:
    def test_module_is_pure_no_io_no_clock(self) -> None:
        source = (
            Path(__file__).parents[1] / "src" / "quantlab" / "backtest_integrity.py"
        ).read_text()
        tree = ast.parse(source)
        forbidden_calls = {"open", "input", "print"}
        forbidden_attrs = {"now", "today", "utcnow"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                    raise AssertionError(f"Zakázané volání: {node.func.id}")
                if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_attrs:
                    raise AssertionError(f"Zakázaný clock read: {node.func.attr}")
        assert "socket" not in source
        assert "subprocess" not in source
        assert "requests" not in source

    def test_module_declares_paper_only(self) -> None:
        assert PAPER_ONLY is True
        source = (
            Path(__file__).parents[1] / "src" / "quantlab" / "backtest_integrity.py"
        ).read_text()
        assert "PAPER_ONLY = True" in source
        assert GATE_VERSION == "2.0.0"

    def test_module_does_not_import_execution_boundary(self) -> None:
        source = (
            Path(__file__).parents[1] / "src" / "quantlab" / "backtest_integrity.py"
        ).read_text()
        for banned in (
            "from quantlab.trading",
            "from quantlab.phase4",
            "from quantlab.security",
            "import quantlab.trading",
        ):
            assert banned not in source


class TestAdversarialKnownFailureModes:
    """Adversarial/regression fixtures pro známé failure modes."""

    def test_reviewed_workflow_failure_mode_is_invalid_not_high_pnl(self) -> None:
        """Exit 15:55 na denním gridu: dříve absurdně vysoké P&L, nyní INVALID."""

        day = date(2026, 8, 12)
        session = _daily_session(day)
        exit_at = datetime.combine(day, time(15, 55), tzinfo=ZoneInfo(NY)).astimezone(UTC)
        # Pozice, která měla být uzavřena v 15:55, ale grid ji nechal otevřenou.
        orphan = PositionLifecycle(
            "p1", "AAA", session.open_at, Decimal("1000"), None, Decimal("0")
        )
        report = run_integrity_gate(
            _valid_inputs(
                requested_events=(RequestedEvent("exit", exit_at, EventKind.EXIT),),
                positions=(orphan,),
            )
        )
        assert report.verdict is GateVerdict.INVALID_TIME_GRID
        assert report.promotion_grade_eligible is False
        assert evaluate_promotion_gate(report) is PromotionDecision.BLOCKED

    def test_naive_portfolio_sum_regression(self) -> None:
        stamps = tuple(datetime(2026, 8, 12 + i, 20, 0, tzinfo=UTC) for i in range(3))
        curves = tuple(
            InstrumentEquityCurve(
                symbol,
                tuple((t, Decimal("100") + Decimal(i)) for i, t in enumerate(stamps)),
            )
            for symbol in ("AAA", "BBB", "CCC")
        )
        claimed = tuple(
            (stamps[i], sum((curve.points[i][1] for curve in curves), Decimal("0")))
            for i in range(len(stamps))
        )
        report = run_integrity_gate(
            _valid_inputs(
                aggregation=PortfolioAggregation(
                    mode=AggregationMode.SHARED_LEDGER,
                    instruments=("AAA", "BBB", "CCC"),
                    shared_capital=True,
                    simultaneous_signal_policy="pro-rata",
                    claimed_portfolio_curve=claimed,
                    per_instrument_curves=curves,
                )
            )
        )
        assert report.verdict is GateVerdict.INVALID_PORTFOLIO_AGGREGATION

    def test_lookahead_fixture_regression(self) -> None:
        as_of = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        leaked = ObservationPoint(
            "AAA",
            date(2026, 8, 13),
            datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
            datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
        )
        report = run_integrity_gate(_valid_inputs(causality=_causality(as_of, (leaked,))))
        assert report.verdict is GateVerdict.INVALID_CAUSALITY


# ---------------------------------------------------------------------------
# 7. Intrabar ambiguity tests (Architecture BLOCKER A)
# ---------------------------------------------------------------------------


class TestIntrabarAmbiguity:
    """Tests for intrabar ambiguity detection (Architecture BLOCKER A)."""

    def test_same_bar_stop_target_without_policy_fails(self) -> None:
        ambiguity = IntrabarAmbiguity(
            events=(
                IntrabarEvent("stop", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
                IntrabarEvent("target", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
            ),
            policy=IntrabarPolicy.NONE,
            same_bar_stop_target=True,
        )
        findings = check_intrabar_ambiguity(ambiguity)
        assert any(f.code == "intrabar.same_bar_stop_target" for f in findings)
        assert all(f.verdict is GateVerdict.INVALID_INTRABAR_AMBIGUITY for f in findings)

    def test_signal_close_filled_at_close_without_policy_fails(self) -> None:
        ambiguity = IntrabarAmbiguity(
            events=(),
            policy=IntrabarPolicy.NONE,
            signal_from_close_filled_at_close=True,
        )
        findings = check_intrabar_ambiguity(ambiguity)
        assert any(f.code == "intrabar.signal_close_filled_at_close" for f in findings)

    def test_multiple_events_same_bar_without_policy_fails(self) -> None:
        bar_ts = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)
        ambiguity = IntrabarAmbiguity(
            events=(
                IntrabarEvent("event1", bar_ts, EventKind.ENTRY),
                IntrabarEvent("event2", bar_ts, EventKind.EXIT),
            ),
            policy=IntrabarPolicy.NONE,
        )
        findings = check_intrabar_ambiguity(ambiguity)
        assert any(f.code == "intrabar.multiple_events_same_bar" for f in findings)

    def test_conservative_policy_passes(self) -> None:
        ambiguity = IntrabarAmbiguity(
            events=(
                IntrabarEvent("stop", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
                IntrabarEvent("target", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
            ),
            policy=IntrabarPolicy.CONSERVATIVE,
            same_bar_stop_target=True,
        )
        findings = check_intrabar_ambiguity(ambiguity)
        assert all(f.severity is not FindingSeverity.ERROR for f in findings)

    def test_finer_data_policy_passes(self) -> None:
        ambiguity = IntrabarAmbiguity(
            events=(
                IntrabarEvent("stop", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
                IntrabarEvent("target", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
            ),
            policy=IntrabarPolicy.FINER_DATA,
            same_bar_stop_target=True,
        )
        findings = check_intrabar_ambiguity(ambiguity)
        assert all(f.severity is not FindingSeverity.ERROR for f in findings)

    def test_no_ambiguity_no_findings(self) -> None:
        ambiguity = IntrabarAmbiguity(
            events=(
                IntrabarEvent("entry", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.ENTRY),
            ),
            policy=IntrabarPolicy.NONE,
        )
        findings = check_intrabar_ambiguity(ambiguity)
        assert findings == []


# ---------------------------------------------------------------------------
# 8. Survivorship tests (Architecture BLOCKER C)
# ---------------------------------------------------------------------------


class TestSurvivorship:
    """Tests for survivorship / terminal lifecycle (Architecture BLOCKER C)."""

    def test_delisting_without_policy_fails(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.DELISTING,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.NONE,
        )
        findings = check_survivorship((terminal,))
        assert any(f.code == "survivorship.missing_terminal_policy" for f in findings)
        assert all(f.verdict is GateVerdict.INVALID_SURVIVORSHIP for f in findings)

    def test_bankruptcy_without_policy_fails(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="BBB",
            terminal_event=TerminalEvent.BANKRUPTCY,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.NONE,
        )
        findings = check_survivorship((terminal,))
        assert any(f.code == "survivorship.missing_terminal_policy" for f in findings)

    def test_symbol_change_without_policy_fails(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="CCC",
            terminal_event=TerminalEvent.SYMBOL_CHANGE,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.NONE,
        )
        findings = check_survivorship((terminal,))
        assert any(f.code == "survivorship.missing_terminal_policy" for f in findings)

    def test_mark_to_last_without_value_fails(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.DELISTING,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.MARK_TO_LAST,
            terminal_value=None,
        )
        findings = check_survivorship((terminal,))
        assert any(f.code == "survivorship.mark_to_last_without_value" for f in findings)

    def test_mark_to_last_with_value_passes(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.DELISTING,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.MARK_TO_LAST,
            terminal_value=Decimal("10.50"),
        )
        findings = check_survivorship((terminal,))
        assert findings == []

    def test_force_close_policy_passes(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.DELISTING,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.FORCE_CLOSE,
        )
        findings = check_survivorship((terminal,))
        assert findings == []

    def test_no_terminal_event_passes(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.NONE,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.NONE,
        )
        findings = check_survivorship((terminal,))
        assert findings == []

    def test_duplicate_terminal_fails(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.DELISTING,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.FORCE_CLOSE,
        )
        findings = check_survivorship((terminal, terminal))
        assert any(f.code == "survivorship.duplicate_terminal" for f in findings)


# ---------------------------------------------------------------------------
# 9. Execution completeness tests (Architecture BLOCKER B)
# ---------------------------------------------------------------------------


class TestExecutionCompleteness:
    """Tests for asset-class execution completeness (Architecture BLOCKER B)."""

    def test_equity_short_missing_short_availability_fails(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY_SHORT,
            short_availability=False,
            borrow_fees_bps=Decimal("50"),
            corporate_actions_handled=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
            financing_carry_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert any(f.code == "execution_completeness.short_availability_missing" for f in findings)

    def test_equity_short_missing_borrow_fees_fails(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY_SHORT,
            short_availability=True,
            borrow_fees_bps=None,
            corporate_actions_handled=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
            financing_carry_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert any(f.code == "execution_completeness.borrow_fees_missing" for f in findings)

    def test_equity_missing_corporate_actions_fails(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY,
            corporate_actions_handled=False,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert any(f.code == "execution_completeness.corporate_actions_missing" for f in findings)

    def test_fx_missing_conversion_fails(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.FX,
            fx_conversion_declared=False,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert any(f.code == "execution_completeness.fx_conversion_missing" for f in findings)

    def test_missing_spread_slippage_impact_fails(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY,
            corporate_actions_handled=True,
            spread_slippage_impact_declared=False,
            liquidity_participation_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert any(
            f.code == "execution_completeness.spread_slippage_impact_missing" for f in findings
        )

    def test_missing_liquidity_participation_fails(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY,
            corporate_actions_handled=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=False,
        )
        findings = check_execution_completeness(completeness)
        assert any(
            f.code == "execution_completeness.liquidity_participation_missing" for f in findings
        )

    def test_futures_missing_financing_carry_fails(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.FUTURES,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
            financing_carry_declared=False,
        )
        findings = check_execution_completeness(completeness)
        assert any(f.code == "execution_completeness.financing_carry_missing" for f in findings)

    def test_fully_declared_equity_passes(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY,
            corporate_actions_handled=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert findings == []

    def test_fully_declared_equity_short_passes(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY_SHORT,
            short_availability=True,
            borrow_fees_bps=Decimal("50"),
            corporate_actions_handled=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
            financing_carry_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert findings == []

    def test_fully_declared_fx_passes(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.FX,
            fx_conversion_declared=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
        )
        findings = check_execution_completeness(completeness)
        assert findings == []


# ---------------------------------------------------------------------------
# 10. Trial accounting tests (STAT-BLOCKER A)
# ---------------------------------------------------------------------------


class TestTrialAccounting:
    """Tests for trial accounting / data snooping (STAT-BLOCKER A)."""

    def test_no_trials_fails(self) -> None:
        family = TrialFamily(
            family_id="fam1",
            total_trials=0,
            failed_trials=0,
            rejected_trials=0,
            successful_trials=0,
        )
        findings = check_trial_accounting(family)
        assert any(f.code == "trial_accounting.no_trials" for f in findings)
        assert all(f.verdict is GateVerdict.INVALID_TRIAL_ACCOUNTING for f in findings)

    def test_failed_variants_not_preserved_fails(self) -> None:
        family = TrialFamily(
            family_id="fam1",
            total_trials=10,
            failed_trials=5,
            rejected_trials=3,
            successful_trials=2,
            failed_variants_preserved=False,
        )
        findings = check_trial_accounting(family)
        assert any(f.code == "trial_accounting.failed_variants_not_preserved" for f in findings)

    def test_trial_count_mismatch_fails(self) -> None:
        family = TrialFamily(
            family_id="fam1",
            total_trials=10,
            failed_trials=5,
            rejected_trials=3,
            successful_trials=1,
        )
        findings = check_trial_accounting(family)
        assert any(f.code == "trial_accounting.trial_count_mismatch" for f in findings)

    def test_consistent_family_passes(self) -> None:
        family = TrialFamily(
            family_id="fam1",
            total_trials=10,
            failed_trials=5,
            rejected_trials=3,
            successful_trials=2,
            failed_variants_preserved=True,
        )
        findings = check_trial_accounting(family)
        assert findings == []


# ---------------------------------------------------------------------------
# 11. Holdout burn tests (STAT-BLOCKER B)
# ---------------------------------------------------------------------------


class TestHoldoutBurn:
    """Tests for holdout burn semantics (STAT-BLOCKER B)."""

    def test_adaptive_iteration_without_burn_fails(self) -> None:
        evidence = HoldoutEvidence(
            holdout_id="holdout1",
            holdout_accessed_at=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            holdout_burned_for_family=False,
            adaptive_iteration_after_access=True,
        )
        findings = check_holdout_burn(evidence)
        assert any(f.code == "holdout_burn.adaptive_iteration_without_burn" for f in findings)
        assert all(f.verdict is GateVerdict.INVALID_HOLDOUT_BURN for f in findings)

    def test_adaptive_iteration_with_burn_passes(self) -> None:
        evidence = HoldoutEvidence(
            holdout_id="holdout1",
            holdout_accessed_at=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            holdout_burned_for_family=True,
            adaptive_iteration_after_access=True,
        )
        findings = check_holdout_burn(evidence)
        assert findings == []

    def test_no_access_passes(self) -> None:
        evidence = HoldoutEvidence(
            holdout_id="holdout1",
            holdout_accessed_at=None,
            holdout_burned_for_family=False,
            adaptive_iteration_after_access=False,
        )
        findings = check_holdout_burn(evidence)
        assert findings == []

    def test_access_without_adaptation_passes(self) -> None:
        evidence = HoldoutEvidence(
            holdout_id="holdout1",
            holdout_accessed_at=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            holdout_burned_for_family=False,
            adaptive_iteration_after_access=False,
        )
        findings = check_holdout_burn(evidence)
        assert findings == []


# ---------------------------------------------------------------------------
# 12. Purge / embargo tests (STAT-BLOCKER C)
# ---------------------------------------------------------------------------


class TestPurgeEmbargo:
    """Tests for purge / embargo (STAT-BLOCKER C)."""

    def test_label_before_decision_fails(self) -> None:
        contract = SplitContract(
            decision_time=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            label_resolution_time=datetime(2026, 8, 12, 19, 0, tzinfo=UTC),
            purge_interval=timedelta(days=1),
            embargo_interval=timedelta(hours=1),
        )
        findings = check_purge_embargo(contract)
        assert any(f.code == "purge_embargo.label_before_decision" for f in findings)
        assert all(f.verdict is GateVerdict.INVALID_PURGE_EMBARGO for f in findings)

    def test_negative_purge_fails(self) -> None:
        contract = SplitContract(
            decision_time=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            label_resolution_time=datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
            purge_interval=timedelta(days=-1),
            embargo_interval=timedelta(hours=1),
        )
        findings = check_purge_embargo(contract)
        assert any(f.code == "purge_embargo.negative_purge" for f in findings)

    def test_negative_embargo_fails(self) -> None:
        contract = SplitContract(
            decision_time=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            label_resolution_time=datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
            purge_interval=timedelta(days=1),
            embargo_interval=timedelta(hours=-1),
        )
        findings = check_purge_embargo(contract)
        assert any(f.code == "purge_embargo.negative_embargo" for f in findings)

    def test_valid_contract_passes(self) -> None:
        contract = SplitContract(
            decision_time=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            label_resolution_time=datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
            purge_interval=timedelta(days=1),
            embargo_interval=timedelta(hours=1),
        )
        findings = check_purge_embargo(contract)
        assert findings == []


# ---------------------------------------------------------------------------
# 13. Uncertainty tests (STAT-BLOCKER D)
# ---------------------------------------------------------------------------


class TestUncertainty:
    """Tests for uncertainty quantification (STAT-BLOCKER D)."""

    def test_no_method_fails(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.NONE,
            confidence_level=Decimal("0.95"),
        )
        findings = check_uncertainty(evidence)
        assert any(f.code == "uncertainty.no_method" for f in findings)
        assert all(f.verdict is GateVerdict.INVALID_UNCERTAINTY for f in findings)

    def test_block_bootstrap_missing_block_size_fails(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.BLOCK_BOOTSTRAP,
            confidence_level=Decimal("0.95"),
            block_size=None,
            n_resamples=1000,
        )
        findings = check_uncertainty(evidence)
        assert any(f.code == "uncertainty.missing_block_size" for f in findings)

    def test_block_bootstrap_missing_n_resamples_fails(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.BLOCK_BOOTSTRAP,
            confidence_level=Decimal("0.95"),
            block_size=10,
            n_resamples=None,
        )
        findings = check_uncertainty(evidence)
        assert any(f.code == "uncertainty.missing_n_resamples" for f in findings)

    def test_cluster_bootstrap_missing_params_fails(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.CLUSTER_BOOTSTRAP,
            confidence_level=Decimal("0.95"),
            block_size=None,
            n_resamples=None,
        )
        findings = check_uncertainty(evidence)
        assert any(f.code == "uncertainty.missing_block_size" for f in findings)
        assert any(f.code == "uncertainty.missing_n_resamples" for f in findings)

    def test_invalid_confidence_level_fails(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.BLOCK_BOOTSTRAP,
            confidence_level=Decimal("1.5"),
            block_size=10,
            n_resamples=1000,
        )
        findings = check_uncertainty(evidence)
        assert any(f.code == "uncertainty.invalid_confidence_level" for f in findings)

    def test_valid_block_bootstrap_passes(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.BLOCK_BOOTSTRAP,
            confidence_level=Decimal("0.95"),
            block_size=10,
            n_resamples=1000,
        )
        findings = check_uncertainty(evidence)
        assert findings == []

    def test_valid_cluster_bootstrap_passes(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.CLUSTER_BOOTSTRAP,
            confidence_level=Decimal("0.90"),
            block_size=20,
            n_resamples=500,
        )
        findings = check_uncertainty(evidence)
        assert findings == []

    def test_valid_stationary_bootstrap_passes(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.STATIONARY_BOOTSTRAP,
            confidence_level=Decimal("0.99"),
            block_size=15,
            n_resamples=2000,
        )
        findings = check_uncertainty(evidence)
        assert findings == []


# ---------------------------------------------------------------------------
# Integration tests: new blockers via run_integrity_gate
# ---------------------------------------------------------------------------


class TestNewBlockersIntegration:
    """Integration tests for new blockers via the full gate."""

    def test_intrabar_same_bar_stop_target_via_gate(self) -> None:
        ambiguity = IntrabarAmbiguity(
            events=(
                IntrabarEvent("stop", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
                IntrabarEvent("target", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
            ),
            policy=IntrabarPolicy.NONE,
            same_bar_stop_target=True,
        )
        report = run_integrity_gate(_valid_inputs(intrabar=ambiguity))
        assert report.verdict is GateVerdict.INVALID_INTRABAR_AMBIGUITY

    def test_survivorship_delisting_via_gate(self) -> None:
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.DELISTING,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.NONE,
        )
        report = run_integrity_gate(_valid_inputs(terminals=(terminal,)))
        assert report.verdict is GateVerdict.INVALID_SURVIVORSHIP

    def test_execution_completeness_short_via_gate(self) -> None:
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY_SHORT,
            short_availability=False,
            borrow_fees_bps=None,
            corporate_actions_handled=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
            financing_carry_declared=True,
        )
        report = run_integrity_gate(_valid_inputs(execution_completeness=completeness))
        assert report.verdict is GateVerdict.INVALID_EXECUTION_ASSUMPTIONS

    def test_trial_accounting_via_gate(self) -> None:
        family = TrialFamily(
            family_id="fam1",
            total_trials=0,
            failed_trials=0,
            rejected_trials=0,
            successful_trials=0,
        )
        report = run_integrity_gate(_valid_inputs(trial_family=family))
        assert report.verdict is GateVerdict.INVALID_TRIAL_ACCOUNTING

    def test_holdout_burn_via_gate(self) -> None:
        evidence = HoldoutEvidence(
            holdout_id="holdout1",
            holdout_accessed_at=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            holdout_burned_for_family=False,
            adaptive_iteration_after_access=True,
        )
        report = run_integrity_gate(_valid_inputs(holdout=evidence))
        assert report.verdict is GateVerdict.INVALID_HOLDOUT_BURN

    def test_purge_embargo_via_gate(self) -> None:
        contract = SplitContract(
            decision_time=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            label_resolution_time=datetime(2026, 8, 12, 19, 0, tzinfo=UTC),
            purge_interval=timedelta(days=1),
            embargo_interval=timedelta(hours=1),
        )
        report = run_integrity_gate(_valid_inputs(split_contract=contract))
        assert report.verdict is GateVerdict.INVALID_PURGE_EMBARGO

    def test_uncertainty_via_gate(self) -> None:
        evidence = UncertaintyEvidence(
            method=UncertaintyMethod.NONE,
            confidence_level=Decimal("0.95"),
        )
        report = run_integrity_gate(_valid_inputs(uncertainty=evidence))
        assert report.verdict is GateVerdict.INVALID_UNCERTAINTY

    def test_all_new_blockers_valid_via_gate(self) -> None:
        """All new evidence declared correctly -> VALID."""
        ambiguity = IntrabarAmbiguity(
            events=(
                IntrabarEvent("stop", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
                IntrabarEvent("target", datetime(2026, 8, 12, 20, 0, tzinfo=UTC), EventKind.EXIT),
            ),
            policy=IntrabarPolicy.CONSERVATIVE,
            same_bar_stop_target=True,
        )
        terminal = TerminalLifecycle(
            instrument_id="AAA",
            terminal_event=TerminalEvent.DELISTING,
            terminal_date=date(2026, 8, 12),
            policy=TerminalPolicy.MARK_TO_LAST,
            terminal_value=Decimal("10.50"),
        )
        completeness = ExecutionCompleteness(
            asset_class=AssetClass.EQUITY,
            corporate_actions_handled=True,
            spread_slippage_impact_declared=True,
            liquidity_participation_declared=True,
        )
        family = TrialFamily(
            family_id="fam1",
            total_trials=10,
            failed_trials=5,
            rejected_trials=3,
            successful_trials=2,
            failed_variants_preserved=True,
        )
        holdout = HoldoutEvidence(
            holdout_id="holdout1",
            holdout_accessed_at=None,
            holdout_burned_for_family=False,
            adaptive_iteration_after_access=False,
        )
        split = SplitContract(
            decision_time=datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
            label_resolution_time=datetime(2026, 8, 13, 20, 0, tzinfo=UTC),
            purge_interval=timedelta(days=1),
            embargo_interval=timedelta(hours=1),
        )
        uncertainty = UncertaintyEvidence(
            method=UncertaintyMethod.BLOCK_BOOTSTRAP,
            confidence_level=Decimal("0.95"),
            block_size=10,
            n_resamples=1000,
        )
        report = run_integrity_gate(
            _valid_inputs(
                intrabar=ambiguity,
                terminals=(terminal,),
                execution_completeness=completeness,
                trial_family=family,
                holdout=holdout,
                split_contract=split,
                uncertainty=uncertainty,
            )
        )
        assert report.verdict is GateVerdict.VALID
