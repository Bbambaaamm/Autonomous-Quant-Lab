from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantlab.market_data import AssetType, Instrument, ProviderBar, XNYSCalendar, normalize_bar

CALENDAR = XNYSCalendar()


def test_normal_weekend_and_standard_holidays() -> None:
    assert CALENDAR.is_session(date(2026, 8, 12))
    assert not CALENDAR.is_session(date(2026, 8, 15))
    assert not CALENDAR.is_session(date(2026, 12, 25))
    assert not CALENDAR.is_session(date(2026, 11, 26))


def test_audited_schedule_contains_early_closes_and_dst() -> None:
    assert CALENDAR.session_close(date(2024, 11, 29)) == datetime(2024, 11, 29, 18, tzinfo=UTC)
    assert CALENDAR.session_close(date(2024, 7, 3)) == datetime(2024, 7, 3, 17, tzinfo=UTC)
    assert CALENDAR.session_open(date(2024, 1, 8)) == datetime(2024, 1, 8, 14, 30, tzinfo=UTC)
    assert CALENDAR.session_open(date(2024, 6, 10)) == datetime(2024, 6, 10, 13, 30, tzinfo=UTC)


def test_navigation_and_historical_exceptional_closure() -> None:
    assert CALENDAR.previous_session(date(2024, 7, 8)) == date(2024, 7, 5)
    assert CALENDAR.next_session(date(2024, 7, 3)) == date(2024, 7, 5)
    # XNYS zůstala po teroristických útocích uzavřena 11.–14. září 2001.
    assert not CALENDAR.is_session(date(2001, 9, 11))
    assert CALENDAR.next_session(date(2001, 9, 10)) == date(2001, 9, 17)


def test_calendar_bounds_are_explicit_and_fail_closed() -> None:
    assert CALENDAR.audited_start == date(1970, 1, 1)
    assert CALENDAR.audited_end == date(2100, 12, 31)
    with pytest.raises(ValueError, match="mimo auditované období"):
        CALENDAR.is_session(date(1969, 12, 31))
    with pytest.raises(ValueError, match="Následující session"):
        CALENDAR.next_session(CALENDAR.audited_end)
    with pytest.raises(ValueError, match="Předchozí session"):
        CALENDAR.previous_session(CALENDAR.audited_start)
    with pytest.raises(ValueError, match="Začátek rozsahu"):
        CALENDAR.sessions_between(date(2024, 1, 3), date(2024, 1, 2))


def test_latest_completed_session_is_session_aware() -> None:
    assert CALENDAR.latest_completed_session(datetime(2024, 7, 6, 12, tzinfo=UTC)) == date(
        2024, 7, 5
    )
    assert CALENDAR.latest_completed_session(datetime(2024, 9, 2, 20, tzinfo=UTC)) == date(
        2024, 8, 30
    )
    assert CALENDAR.latest_completed_session(datetime(2024, 11, 29, 17, 59, tzinfo=UTC)) == date(
        2024, 11, 27
    )
    assert CALENDAR.latest_completed_session(datetime(2024, 11, 29, 18, tzinfo=UTC)) == date(
        2024, 11, 29
    )


def test_daily_bar_uses_xnys_close_and_signal_executes_next_open() -> None:
    instrument = Instrument("a", "A", "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2000, 1, 1))
    bar = ProviderBar(
        date(2024, 11, 29),
        Decimal("10"),
        Decimal("11"),
        Decimal("9"),
        Decimal("10"),
        Decimal("100"),
        "fixture-1",
    )
    observation = normalize_bar(
        bar,
        instrument,
        "fixture",
        datetime(2024, 11, 29, 19, tzinfo=UTC),
        "ingestion",
        CALENDAR,
    )
    assert observation.timestamp == datetime(2024, 11, 29, 18, tzinfo=UTC)
    assert CALENDAR.session_open(CALENDAR.next_session(observation.session_date)) == datetime(
        2024, 12, 2, 14, 30, tzinfo=UTC
    )
