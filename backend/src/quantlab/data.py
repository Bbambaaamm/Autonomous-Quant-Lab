from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from quantlab.domain import Bar


@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    message: str


class DataValidationError(ValueError):
    def __init__(self, issues: list[DataQualityIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


def validate_bars(bars: list[Bar]) -> None:
    issues: list[DataQualityIssue] = []
    keys = [(bar.symbol, bar.timestamp) for bar in bars]
    duplicates = [key for key, count in Counter(keys).items() if count > 1]
    if duplicates:
        issues.append(DataQualityIssue("duplicate_timestamp", f"Duplicitní bary: {duplicates}"))
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.timestamp <= previous.timestamp:
            issues.append(DataQualityIssue("non_monotonic", "Časy nejsou striktně rostoucí"))
    for bar in bars:
        if min(bar.open, bar.high, bar.low, bar.close, bar.adjusted_close) <= Decimal("0"):
            issues.append(DataQualityIssue("invalid_price", f"Neplatná cena v {bar.timestamp}"))
        if bar.volume < 0:
            issues.append(DataQualityIssue("invalid_volume", f"Záporný objem v {bar.timestamp}"))
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            issues.append(DataQualityIssue("ohlc_invariant", f"Porušen OHLC invariant v {bar.timestamp}"))
    if not bars:
        issues.append(DataQualityIssue("empty", "Dataset je prázdný"))
    if issues:
        raise DataValidationError(issues)
