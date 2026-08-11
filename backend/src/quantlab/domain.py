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


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class RiskDecisionStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"


class RiskReason(StrEnum):
    ALLOWED = "ALLOWED"
    INVALID_PRICE = "INVALID_PRICE"
    INSTRUMENT_NOT_ALLOWED = "INSTRUMENT_NOT_ALLOWED"
    SINGLE_ORDER_LIMIT = "SINGLE_ORDER_LIMIT"
    POSITION_LIMIT = "POSITION_LIMIT"
    GROSS_EXPOSURE = "GROSS_EXPOSURE"
    NET_EXPOSURE = "NET_EXPOSURE"
    MAX_POSITIONS = "MAX_POSITIONS"
    DAILY_ORDER_LIMIT = "DAILY_ORDER_LIMIT"
    DAILY_NOTIONAL_LIMIT = "DAILY_NOTIONAL_LIMIT"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    LONG_ONLY = "LONG_ONLY"
    STALE_DATA = "STALE_DATA"
    TRADING_HALTED = "TRADING_HALTED"
    DAILY_LOSS = "DAILY_LOSS"
    DRAWDOWN = "DRAWDOWN"


class SystemTradingState(StrEnum):
    NORMAL = "NORMAL"
    HALTED = "HALTED"


class TradingCycleStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    HALTED = "HALTED"


class ReconciliationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AuditEventType(StrEnum):
    TRADING_CYCLE_STARTED = "TRADING_CYCLE_STARTED"
    DATA_VALIDATED = "DATA_VALIDATED"
    DATA_REJECTED = "DATA_REJECTED"
    TARGET_GENERATED = "TARGET_GENERATED"
    ORDER_INTENT_CREATED = "ORDER_INTENT_CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_MODIFIED = "RISK_MODIFIED"
    RISK_REJECTED = "RISK_REJECTED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_FAILED = "ORDER_FAILED"
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_SUCCEEDED = "RECONCILIATION_SUCCEEDED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"
    KILL_SWITCH_MANUAL_HALT = "KILL_SWITCH_MANUAL_HALT"
    KILL_SWITCH_RESUMED = "KILL_SWITCH_RESUMED"
    TRADING_CYCLE_COMPLETED = "TRADING_CYCLE_COMPLETED"
    TRADING_CYCLE_FAILED = "TRADING_CYCLE_FAILED"


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
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    trading_cycle_id: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_time", require_utc(self.decision_time))
        if self.quantity <= 0:
            raise ValueError("Množství příkazu musí být kladné")


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    timestamp: datetime
    order_intent_id: str
    status: RiskDecisionStatus
    original_quantity: Decimal
    approved_quantity: Decimal
    reasons: tuple[RiskReason, ...]
    evaluated_limits: dict[str, str]
    portfolio_snapshot: dict[str, str]
    correlation_id: str
    trading_cycle_id: str


@dataclass(frozen=True)
class Position:
    account_id: str
    instrument_id: str
    quantity: Decimal
    average_cost: Decimal
    realized_pnl: Decimal
    updated_at: datetime


@dataclass(frozen=True)
class PaperAccount:
    id: str
    base_currency: str
    starting_cash: Decimal
    cash: Decimal
    equity: Decimal
    high_water_mark: Decimal
    realized_pnl: Decimal
    trading_state: SystemTradingState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TradingCycle:
    id: str
    cycle_key: str
    account_id: str
    strategy_id: str
    session_date: str
    status: TradingCycleStatus
    correlation_id: str
    data_fingerprint: str
    started_at: datetime
    completed_at: datetime | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class AuditEvent:
    id: str
    timestamp: datetime
    event_type: AuditEventType
    entity_type: str
    entity_id: str
    trading_cycle_id: str | None
    correlation_id: str
    payload: dict[str, object]


@dataclass(frozen=True)
class ReconciliationResult:
    id: str
    account_id: str
    status: ReconciliationStatus
    timestamp: datetime
    differences: dict[str, object]


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
