from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Čas musí obsahovat časovou zónu")
    return value.astimezone(UTC)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    adjusted_close: Decimal
    source: str = "fixture"
    timeframe: str = "1d"

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", require_utc(self.timestamp))


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    weight: Decimal
    reason: str


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Side
    quantity: Decimal
    decision_time: datetime
    reason: str
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    commission: Decimal
    timestamp: datetime
    reference_price: Decimal | None = None

    @property
    def slippage_cost(self) -> Decimal:
        if self.reference_price is None:
            return Decimal("0")
        direction = Decimal("1") if self.side is Side.BUY else Decimal("-1")
        return (self.price - self.reference_price) * direction * self.quantity


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    effective_at: datetime
    action_type: CorporateActionType
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_at", require_utc(self.effective_at))
        if self.value <= 0:
            raise ValueError("Hodnota corporate action musí být kladná")
