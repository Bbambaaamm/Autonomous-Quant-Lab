from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Čas musí obsahovat časovou zónu")
    return value.astimezone(timezone.utc)


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
