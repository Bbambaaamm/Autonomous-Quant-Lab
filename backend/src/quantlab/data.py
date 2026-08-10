import csv
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from quantlab.domain import Bar


@dataclass(frozen=True)
class DataQualityEvent:
    code: str
    message: str
    severity: str = "fatal"
    timestamp: datetime | None = None


DataQualityIssue = DataQualityEvent


class DataValidationError(ValueError):
    def __init__(self, issues: list[DataQualityIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


def inspect_bars(
    bars: list[Bar], calendar: "TradingCalendar | None" = None
) -> list[DataQualityEvent]:
    issues: list[DataQualityIssue] = []
    keys = [(bar.symbol, bar.timestamp) for bar in bars]
    duplicates = [key for key, count in Counter(keys).items() if count > 1]
    if duplicates:
        issues.append(DataQualityIssue("duplicate_timestamp", f"Duplicitní bary: {duplicates}"))
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.timestamp <= previous.timestamp:
            issues.append(DataQualityIssue("non_monotonic", "Časy nejsou striktně rostoucí"))
    for bar in bars:
        values = (bar.open, bar.high, bar.low, bar.close, bar.adjusted_close, bar.volume)
        if any(not value.is_finite() for value in values):
            issues.append(DataQualityEvent("non_finite", f"NaN/Infinity v {bar.timestamp}"))
            continue
        if min(bar.open, bar.high, bar.low, bar.close, bar.adjusted_close) <= Decimal("0"):
            issues.append(DataQualityIssue("invalid_price", f"Neplatná cena v {bar.timestamp}"))
        if bar.volume < 0:
            issues.append(DataQualityIssue("invalid_volume", f"Záporný objem v {bar.timestamp}"))
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            issues.append(
                DataQualityIssue("ohlc_invariant", f"Porušen OHLC invariant v {bar.timestamp}")
            )
    for previous, current in zip(bars, bars[1:], strict=False):
        if previous.close > 0 and abs(current.close / previous.close - 1) > Decimal("0.30"):
            issues.append(
                DataQualityEvent(
                    "large_price_jump",
                    f"Skok ceny v {current.timestamp}",
                    "warning",
                    current.timestamp,
                )
            )
    if calendar and bars:
        present = {bar.timestamp.date() for bar in bars}
        day = bars[0].timestamp.date()
        while day <= bars[-1].timestamp.date():
            if calendar.is_session(day) and day not in present:
                issues.append(DataQualityEvent("missing_bar", f"Chybějící bar {day}", "warning"))
            day += timedelta(days=1)
    if not bars:
        issues.append(DataQualityIssue("empty", "Dataset je prázdný"))
    return issues


def validate_bars(
    bars: list[Bar], calendar: "TradingCalendar | None" = None
) -> list[DataQualityEvent]:
    issues = inspect_bars(bars, calendar)
    fatal = [issue for issue in issues if issue.severity == "fatal"]
    if fatal:
        raise DataValidationError(fatal)
    return issues


class TradingCalendar(Protocol):
    def is_session(self, day: date) -> bool: ...


@dataclass(frozen=True)
class USExchangeCalendar:
    """Deterministický kalendář: víkendy a explicitně dodané burzovní svátky."""

    holidays: frozenset[date] = frozenset()

    def is_session(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays


class MarketDataProvider(Protocol):
    def load(
        self, symbol: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[Bar]: ...


def _filter(bars: list[Bar], start: datetime | None, end: datetime | None) -> list[Bar]:
    return [
        bar
        for bar in bars
        if (start is None or bar.timestamp >= start) and (end is None or bar.timestamp <= end)
    ]


@dataclass(frozen=True)
class CSVMarketDataProvider:
    path: Path

    def load(
        self, symbol: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[Bar]:
        with self.path.open(newline="", encoding="utf-8") as handle:
            bars = [
                Bar(
                    symbol=row["symbol"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                    adjusted_close=Decimal(row["adjusted_close"]),
                    source=row.get("source", "csv"),
                    timeframe=row.get("timeframe", "1d"),
                )
                for row in csv.DictReader(handle)
                if row["symbol"] == symbol
            ]
        validate_bars(bars)
        return _filter(bars, start, end)


@dataclass(frozen=True)
class ParquetMarketDataProvider:
    path: Path

    def load(
        self, symbol: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[Bar]:
        import pyarrow.parquet as pq

        rows = pq.read_table(self.path).to_pylist()
        bars = [
            Bar(
                symbol=str(row["symbol"]),
                timestamp=row["timestamp"],
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                adjusted_close=Decimal(str(row["adjusted_close"])),
                source=str(row.get("source", "parquet")),
                timeframe=str(row.get("timeframe", "1d")),
            )
            for row in rows
            if row["symbol"] == symbol
        ]
        validate_bars(bars)
        return _filter(bars, start, end)


def dataset_identity(bars: list[Bar]) -> str:
    canonical = "\n".join(
        "|".join(
            (
                b.symbol,
                b.timestamp.astimezone(UTC).isoformat(),
                str(b.open),
                str(b.high),
                str(b.low),
                str(b.close),
                str(b.volume),
                str(b.adjusted_close),
                b.source,
                b.timeframe,
            )
        )
        for b in bars
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
