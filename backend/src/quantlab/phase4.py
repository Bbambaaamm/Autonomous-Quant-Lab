from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.data import dataset_identity, validate_bars
from quantlab.domain import (
    AuditEventType,
    Bar,
    OrderIntent,
    OrderStatus,
    OrderType,
    PaperAccount,
    Position,
    ReconciliationResult,
    ReconciliationStatus,
    RiskDecision,
    RiskDecisionStatus,
    RiskReason,
    Side,
    SystemTradingState,
    TradingCycleStatus,
)
from quantlab.persistence import Base, _sqlite_fk
from quantlab.trading import CostModel, FixedBpsSlippage


class PaperAccountRecord(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    starting_cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    high_water_mark: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    trading_state: Mapped[str] = mapped_column(String(20), nullable=False)
    session_date: Mapped[date | None] = mapped_column(Date)
    session_start_equity: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    reconciliation_safe: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionRecord(Base):
    __tablename__ = "paper_positions"
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), primary_key=True
    )
    instrument_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    lots_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TradingCycleRecord(Base):
    __tablename__ = "trading_cycles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cycle_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(64), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RiskDecisionRecord(Base):
    __tablename__ = "risk_decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_intent_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trading_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("trading_cycles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    original_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    approved_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    limits_json: Mapped[str] = mapped_column(Text, nullable=False)
    portfolio_json: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class PaperOrderRecord(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_paper_orders_quantity_positive"),
        CheckConstraint("filled_quantity >= 0", name="ck_paper_orders_filled_nonnegative"),
        CheckConstraint("remaining_quantity >= 0", name="ck_paper_orders_remaining_nonnegative"),
        CheckConstraint("filled_quantity <= quantity", name="ck_paper_orders_not_overfilled"),
        CheckConstraint(
            "remaining_quantity = quantity - filled_quantity",
            name="ck_paper_orders_quantity_balance",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    trading_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("trading_cycles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_intent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_decision_id: Mapped[str] = mapped_column(
        ForeignKey("risk_decisions.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    instrument_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    submitted_notional: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class PaperFillRecord(Base):
    __tablename__ = "paper_fills"
    __table_args__ = (
        UniqueConstraint("order_id", "sequence"),
        CheckConstraint("sequence > 0", name="ck_paper_fills_sequence_positive"),
        CheckConstraint("quantity > 0", name="ck_paper_fills_quantity_positive"),
        CheckConstraint("price > 0", name="ck_paper_fills_price_positive"),
        CheckConstraint("reference_price > 0", name="ck_paper_fills_reference_price_positive"),
        CheckConstraint("commission >= 0", name="ck_paper_fills_commission_nonnegative"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("paper_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trading_cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("trading_cycles.id", ondelete="RESTRICT"), index=True
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class RiskEventRecord(Base):
    __tablename__ = "risk_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class ReconciliationRecord(Base):
    __tablename__ = "reconciliation_results"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    differences_json: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


Index("ix_orders_account_created", PaperOrderRecord.account_id, PaperOrderRecord.created_at)


@dataclass(frozen=True)
class ProductionRiskConfig:
    max_position_pct: Decimal = Decimal("0.25")
    max_single_order_pct: Decimal = Decimal("0.10")
    max_single_order_notional: Decimal = Decimal("25000")
    max_gross_exposure: Decimal = Decimal("1.0")
    max_net_exposure: Decimal = Decimal("1.0")
    max_number_positions: int = 10
    max_daily_loss: Decimal = Decimal("0.03")
    max_portfolio_drawdown: Decimal = Decimal("0.10")
    max_orders_per_day: int = 20
    max_notional_per_day: Decimal = Decimal("100000")
    instrument_allowlist: frozenset[str] = frozenset({"SPY"})
    long_only: bool = True
    max_leverage: Decimal = Decimal("1.0")
    stale_data_threshold: timedelta = timedelta(days=4)
    clip_quantity: bool = False


class Phase4Repository:
    def __init__(self, url: str = "sqlite:///:memory:", *, bootstrap_test_schema: bool = True):
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _sqlite_fk)
        if bootstrap_test_schema:
            Base.metadata.create_all(self.engine)

    def seed_account(
        self, account_id: str = "paper-main", cash: Decimal = Decimal("100000")
    ) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            if session.get(PaperAccountRecord, account_id) is None:
                session.add(
                    PaperAccountRecord(
                        id=account_id,
                        base_currency="USD",
                        starting_cash=cash,
                        cash=cash,
                        equity=cash,
                        high_water_mark=cash,
                        realized_pnl=Decimal(0),
                        trading_state=SystemTradingState.NORMAL,
                        reconciliation_safe=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.commit()

    def account(self, account_id: str) -> PaperAccount:
        with Session(self.engine) as session:
            row = session.get(PaperAccountRecord, account_id)
            if row is None:
                raise KeyError(account_id)
            return PaperAccount(
                row.id,
                row.base_currency,
                row.starting_cash,
                row.cash,
                row.equity,
                row.high_water_mark,
                row.realized_pnl,
                SystemTradingState(row.trading_state),
                row.created_at.replace(tzinfo=UTC)
                if row.created_at.tzinfo is None
                else row.created_at,
                row.updated_at.replace(tzinfo=UTC)
                if row.updated_at.tzinfo is None
                else row.updated_at,
            )

    def positions(self, account_id: str) -> list[Position]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(PositionRecord).where(PositionRecord.account_id == account_id)
            ).all()
            return [
                Position(
                    r.account_id,
                    r.instrument_id,
                    r.quantity,
                    r.average_cost,
                    r.realized_pnl,
                    r.updated_at.replace(tzinfo=UTC)
                    if r.updated_at.tzinfo is None
                    else r.updated_at,
                )
                for r in rows
            ]

    def page(self, model: type[Base], limit: int, offset: int) -> list[Base]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Neplatná pagination")
        with Session(self.engine) as session:
            return list(session.scalars(select(model).limit(limit).offset(offset)).all())

    def audit(
        self,
        session: Session,
        event_type: AuditEventType,
        entity_type: str,
        entity_id: str,
        cycle_id: str | None,
        correlation_id: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        session.add(
            AuditEventRecord(
                id=str(uuid4()),
                timestamp=datetime.now(UTC),
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                trading_cycle_id=cycle_id,
                correlation_id=correlation_id,
                payload_json=json.dumps(payload or {}, default=str, sort_keys=True),
            )
        )

    def halt(
        self,
        account_id: str,
        reason: str,
        correlation_id: str,
        event_type: AuditEventType = AuditEventType.KILL_SWITCH_TRIGGERED,
    ) -> None:
        with Session(self.engine) as session:
            account = session.get(PaperAccountRecord, account_id)
            if account is None:
                raise KeyError(account_id)
            account.trading_state = SystemTradingState.HALTED
            account.updated_at = datetime.now(UTC)
            session.add(
                RiskEventRecord(
                    id=str(uuid4()),
                    account_id=account_id,
                    timestamp=datetime.now(UTC),
                    event_type=event_type,
                    reason=reason,
                    correlation_id=correlation_id,
                )
            )
            self.audit(
                session, event_type, "account", account_id, None, correlation_id, {"reason": reason}
            )
            session.commit()

    def resume(self, account_id: str, correlation_id: str) -> None:
        with Session(self.engine) as session:
            account = session.get(PaperAccountRecord, account_id)
            if account is None or not account.reconciliation_safe:
                raise PermissionError("Resume vyžaduje úspěšnou reconciliation")
            account.trading_state = SystemTradingState.NORMAL
            account.updated_at = datetime.now(UTC)
            self.audit(
                session,
                AuditEventType.KILL_SWITCH_RESUMED,
                "account",
                account_id,
                None,
                correlation_id,
            )
            session.commit()


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    cash: Decimal
    equity: Decimal
    high_water_mark: Decimal
    session_start_equity: Decimal
    positions: dict[str, Decimal]
    prices: dict[str, Decimal]
    pending: dict[str, Decimal]
    daily_orders: int
    daily_notional: Decimal
    trading_state: SystemTradingState


class ProductionRiskEngine:
    def __init__(self, config: ProductionRiskConfig):
        self.config = config

    def evaluate(
        self,
        intent: OrderIntent,
        price: Decimal,
        snapshot: PortfolioRiskSnapshot,
        cycle_id: str,
        correlation_id: str,
        now: datetime,
        latest_market_timestamp: datetime,
    ) -> RiskDecision:
        reasons: list[RiskReason] = []
        invalid_portfolio = any(
            not value.is_finite()
            for value in (
                snapshot.cash,
                snapshot.equity,
                snapshot.high_water_mark,
                snapshot.session_start_equity,
                snapshot.daily_notional,
                *snapshot.positions.values(),
                *snapshot.prices.values(),
                *snapshot.pending.values(),
            )
        )
        if invalid_portfolio or not price.is_finite() or price <= 0:
            return RiskDecision(
                hashlib.sha256(f"{cycle_id}|{intent.id}".encode()).hexdigest(),
                now,
                intent.id,
                RiskDecisionStatus.REJECTED,
                intent.quantity,
                Decimal(0),
                (RiskReason.INVALID_PRICE,),
                {key: str(value) for key, value in vars(self.config).items()},
                {
                    "cash": str(snapshot.cash),
                    "equity": str(snapshot.equity),
                    "reference_price": str(price),
                },
                correlation_id,
                cycle_id,
            )
        current = snapshot.positions.get(intent.symbol, Decimal(0))
        pending = snapshot.pending.get(intent.symbol, Decimal(0))
        signed = intent.quantity if intent.side is Side.BUY else -intent.quantity
        resulting = current + pending + signed
        reducing = abs(resulting) < abs(current + pending) and resulting >= 0
        if intent.symbol not in self.config.instrument_allowlist:
            reasons.append(RiskReason.INSTRUMENT_NOT_ALLOWED)
        if now - latest_market_timestamp > self.config.stale_data_threshold:
            reasons.append(RiskReason.STALE_DATA)
        if snapshot.trading_state is SystemTradingState.HALTED and not reducing:
            reasons.append(RiskReason.TRADING_HALTED)
        if self.config.long_only and resulting < 0:
            reasons.append(RiskReason.LONG_ONLY)
        nonpositive_equity = snapshot.equity.is_finite() and snapshot.equity <= 0
        if nonpositive_equity:
            reasons.append(RiskReason.SINGLE_ORDER_LIMIT)
        if nonpositive_equity:
            return RiskDecision(
                hashlib.sha256(f"{cycle_id}|{intent.id}".encode()).hexdigest(),
                now,
                intent.id,
                RiskDecisionStatus.REJECTED,
                intent.quantity,
                Decimal(0),
                tuple(reasons),
                {key: str(value) for key, value in vars(self.config).items()},
                {
                    "cash": str(snapshot.cash),
                    "equity": str(snapshot.equity),
                    "reference_price": str(price),
                },
                correlation_id,
                cycle_id,
            )
        notional = intent.quantity * price
        if notional > self.config.max_single_order_notional or (
            snapshot.equity <= 0 or notional / snapshot.equity > self.config.max_single_order_pct
        ):
            reasons.append(RiskReason.SINGLE_ORDER_LIMIT)
        future_values = {
            s: q * snapshot.prices.get(s, price) for s, q in snapshot.positions.items()
        }
        future_values[intent.symbol] = resulting * price
        gross = sum((abs(v) for v in future_values.values()), Decimal(0)) / snapshot.equity
        net = sum(future_values.values(), Decimal(0)) / snapshot.equity
        if abs(future_values[intent.symbol]) / snapshot.equity > self.config.max_position_pct:
            reasons.append(RiskReason.POSITION_LIMIT)
        if gross > min(self.config.max_gross_exposure, self.config.max_leverage):
            reasons.append(RiskReason.GROSS_EXPOSURE)
        if abs(net) > self.config.max_net_exposure:
            reasons.append(RiskReason.NET_EXPOSURE)
        open_count = sum(q != 0 for q in snapshot.positions.values())
        if current == 0 and resulting != 0 and open_count >= self.config.max_number_positions:
            reasons.append(RiskReason.MAX_POSITIONS)
        if snapshot.daily_orders >= self.config.max_orders_per_day:
            reasons.append(RiskReason.DAILY_ORDER_LIMIT)
        if snapshot.daily_notional + notional > self.config.max_notional_per_day:
            reasons.append(RiskReason.DAILY_NOTIONAL_LIMIT)
        if intent.side is Side.BUY and notional > snapshot.cash:
            reasons.append(RiskReason.INSUFFICIENT_CASH)
        if (
            snapshot.session_start_equity > 0
            and (snapshot.session_start_equity - snapshot.equity) / snapshot.session_start_equity
            >= self.config.max_daily_loss
        ):
            reasons.append(RiskReason.DAILY_LOSS)
        if (
            snapshot.high_water_mark > 0
            and (snapshot.high_water_mark - snapshot.equity) / snapshot.high_water_mark
            >= self.config.max_portfolio_drawdown
        ):
            reasons.append(RiskReason.DRAWDOWN)
        status = RiskDecisionStatus.REJECTED if reasons else RiskDecisionStatus.APPROVED
        return RiskDecision(
            hashlib.sha256(f"{cycle_id}|{intent.id}".encode()).hexdigest(),
            now,
            intent.id,
            status,
            intent.quantity,
            intent.quantity if not reasons else Decimal(0),
            tuple(reasons or [RiskReason.ALLOWED]),
            {k: str(v) for k, v in vars(self.config).items()},
            {
                "cash": str(snapshot.cash),
                "equity": str(snapshot.equity),
                "reference_price": str(price),
                "resulting_position": str(resulting),
                "gross": str(gross),
                "net": str(net),
            },
            correlation_id,
            cycle_id,
        )


def deterministic_cycle_key(account_id: str, strategy_id: str, session_date: date) -> str:
    return hashlib.sha256(
        f"{account_id}|{strategy_id}|{session_date.isoformat()}".encode()
    ).hexdigest()


def cycle_input_fingerprint(
    bars: list[Bar], decision_time: datetime, target_weights: dict[str, Decimal]
) -> str:
    payload = {
        "dataset": dataset_identity(bars),
        "decision_time": decision_time.astimezone(UTC).isoformat(),
        "target_weights": {
            symbol: str(weight) for symbol, weight in sorted(target_weights.items())
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def order_intent_fingerprint(intent: OrderIntent) -> str:
    payload = {
        "correlation_id": intent.correlation_id,
        "decision_time": intent.decision_time.astimezone(UTC).isoformat(),
        "id": intent.id,
        "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
        "order_type": intent.order_type.value,
        "quantity": str(intent.quantity),
        "reason": intent.reason,
        "side": intent.side.value,
        "symbol": intent.symbol,
        "trading_cycle_id": intent.trading_cycle_id,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def deterministic_client_order_id(
    account_id: str, cycle_id: str, strategy_id: str, intent: OrderIntent
) -> str:
    value = f"{account_id}|{cycle_id}|{strategy_id}|{intent.symbol}|{intent.side}|{intent.id}"
    return hashlib.sha256(value.encode()).hexdigest()


class PersistentPaperBroker:
    def __init__(
        self,
        repository: Phase4Repository,
        costs: CostModel | None = None,
        slippage: FixedBpsSlippage | None = None,
        volume_fraction: Decimal = Decimal("0.10"),
    ):
        self.repository = repository
        self.costs = costs or CostModel()
        self.slippage = slippage or FixedBpsSlippage()
        self.volume_fraction = volume_fraction

    def get_account(self, account_id: str) -> PaperAccount:
        return self.repository.account(account_id)

    def get_positions(self, account_id: str) -> list[Position]:
        return self.repository.positions(account_id)

    def get_open_orders(self, account_id: str) -> list[PaperOrderRecord]:
        with Session(self.repository.engine) as session:
            return list(
                session.scalars(
                    select(PaperOrderRecord).where(
                        PaperOrderRecord.account_id == account_id,
                        PaperOrderRecord.status.in_(
                            [OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED]
                        ),
                    )
                ).all()
            )

    def get_order(self, order_id: str) -> PaperOrderRecord | None:
        with Session(self.repository.engine) as session:
            return session.get(PaperOrderRecord, order_id)

    def get_fills(self, account_id: str) -> list[PaperFillRecord]:
        with Session(self.repository.engine) as session:
            return list(
                session.scalars(
                    select(PaperFillRecord)
                    .join(PaperOrderRecord)
                    .where(PaperOrderRecord.account_id == account_id)
                ).all()
            )

    def submit_order(
        self, account_id: str, strategy_id: str, intent: OrderIntent, decision: RiskDecision
    ) -> PaperOrderRecord:
        if (
            decision.order_intent_id != intent.id
            or decision.status not in (RiskDecisionStatus.APPROVED, RiskDecisionStatus.MODIFIED)
            or decision.approved_quantity <= 0
        ):
            raise PermissionError("Broker vyžaduje platné risk schválení stejného intentu")
        client_id = deterministic_client_order_id(
            account_id, decision.trading_cycle_id, strategy_id, intent
        )
        with Session(self.repository.engine) as session:
            account = session.scalar(
                select(PaperAccountRecord)
                .where(PaperAccountRecord.id == account_id)
                .with_for_update()
            )
            persisted = session.get(RiskDecisionRecord, decision.decision_id)
            cycle = session.get(TradingCycleRecord, decision.trading_cycle_id)
            persisted_snapshot = json.loads(persisted.portfolio_json) if persisted else {}
            if (
                account is None
                or persisted is None
                or cycle is None
                or persisted.account_id != account_id
                or cycle.account_id != account_id
                or persisted.trading_cycle_id != decision.trading_cycle_id
                or persisted.order_intent_id != intent.id
                or persisted.status != decision.status
                or persisted.approved_quantity != decision.approved_quantity
                or persisted.correlation_id != decision.correlation_id
                or persisted_snapshot.get("order_intent_fingerprint")
                != order_intent_fingerprint(intent)
            ):
                raise PermissionError("Broker vyžaduje autoritativní persisted risk rozhodnutí")
            if account.trading_state == SystemTradingState.HALTED:
                position = session.get(PositionRecord, (account_id, intent.symbol))
                held = position.quantity if position is not None else Decimal(0)
                if intent.side is not Side.SELL or decision.approved_quantity > held:
                    raise PermissionError("HALTED účet dovoluje pouze risk-reducing prodej")
            existing = session.scalar(
                select(PaperOrderRecord).where(PaperOrderRecord.client_order_id == client_id)
            )
            if existing:
                return existing
            row = PaperOrderRecord(
                id=str(uuid4()),
                client_order_id=client_id,
                account_id=account_id,
                trading_cycle_id=decision.trading_cycle_id,
                order_intent_id=intent.id,
                risk_decision_id=decision.decision_id,
                instrument_id=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                quantity=decision.approved_quantity,
                submitted_notional=(
                    decision.approved_quantity * Decimal(persisted_snapshot["reference_price"])
                ),
                filled_quantity=Decimal(0),
                remaining_quantity=decision.approved_quantity,
                limit_price=intent.limit_price,
                status=OrderStatus.SUBMITTED,
                created_at=datetime.now(UTC),
                submitted_at=intent.decision_time,
                correlation_id=decision.correlation_id,
            )
            session.add(row)
            try:
                session.flush()
                self.repository.audit(
                    session,
                    AuditEventType.ORDER_SUBMITTED,
                    "order",
                    row.id,
                    row.trading_cycle_id,
                    row.correlation_id,
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                recovered = session.scalar(
                    select(PaperOrderRecord).where(PaperOrderRecord.client_order_id == client_id)
                )
                if recovered is None:
                    raise
                return recovered
            session.refresh(row)
            return row

    def process(self, order_id: str, bar: Bar) -> PaperOrderRecord:
        validate_bars([bar])
        with Session(self.repository.engine) as session:
            order = session.scalar(
                select(PaperOrderRecord).where(PaperOrderRecord.id == order_id).with_for_update()
            )
            if order is None:
                raise KeyError(order_id)
            if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
                return order
            submitted_at = order.submitted_at
            assert submitted_at is not None
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=UTC)
            if bar.timestamp <= submitted_at:
                raise ValueError("Bar nesmí předcházet aktivaci příkazu")
            side = Side(order.side)
            if order.order_type == OrderType.LIMIT:
                if order.limit_price is None:
                    raise ValueError("Limit order nemá limit cenu")
                reached = (
                    bar.low <= order.limit_price
                    if side is Side.BUY
                    else bar.high >= order.limit_price
                )
                if not reached:
                    return order
                reference = (
                    min(bar.open, order.limit_price)
                    if side is Side.BUY
                    else max(bar.open, order.limit_price)
                )
            else:
                reference = bar.open
            capacity = (bar.volume * self.volume_fraction).to_integral_value(rounding=ROUND_DOWN)
            quantity = min(order.remaining_quantity, capacity)
            if quantity <= 0:
                return order
            price = self.slippage.apply(reference, side)
            if order.order_type == OrderType.LIMIT:
                assert order.limit_price is not None
                price = (
                    min(price, order.limit_price)
                    if side is Side.BUY
                    else max(price, order.limit_price)
                )
            commission = self.costs.commission(price * quantity)
            account = session.scalar(
                select(PaperAccountRecord)
                .where(PaperAccountRecord.id == order.account_id)
                .with_for_update()
            )
            if account is None:
                raise KeyError(order.account_id)
            cash_delta = (
                -(price * quantity + commission)
                if side is Side.BUY
                else price * quantity - commission
            )
            if account.cash + cash_delta < 0:
                raise ValueError("Fill by vytvořil zápornou hotovost")
            position = session.get(PositionRecord, (order.account_id, order.instrument_id))
            if position is None:
                position = PositionRecord(
                    account_id=order.account_id,
                    instrument_id=order.instrument_id,
                    quantity=Decimal(0),
                    average_cost=Decimal(0),
                    realized_pnl=Decimal(0),
                    lots_json="[]",
                    updated_at=bar.timestamp,
                )
                session.add(position)
            lots = json.loads(position.lots_json)
            realized = Decimal(0)
            if side is Side.BUY:
                lots.append(
                    {"quantity": str(quantity), "unit_basis": str(price + commission / quantity)}
                )
                position.quantity += quantity
            else:
                if quantity > position.quantity:
                    raise ValueError("Nelze prodat více, než účet drží")
                remaining = quantity
                while remaining:
                    lot = lots[0]
                    lot_quantity = Decimal(lot["quantity"])
                    allocated = min(remaining, lot_quantity)
                    realized += allocated * (price - Decimal(lot["unit_basis"]))
                    lot_quantity -= allocated
                    remaining -= allocated
                    if lot_quantity == 0:
                        lots.pop(0)
                    else:
                        lot["quantity"] = str(lot_quantity)
                realized -= commission
                position.quantity -= quantity
                position.realized_pnl += realized
                account.realized_pnl += realized
            position.lots_json = json.dumps(lots, sort_keys=True)
            position.average_cost = (
                sum(
                    (
                        Decimal(lot_item["quantity"]) * Decimal(lot_item["unit_basis"])
                        for lot_item in lots
                    ),
                    Decimal(0),
                )
                / position.quantity
                if position.quantity
                else Decimal(0)
            )
            position.updated_at = bar.timestamp
            account.cash += cash_delta
            account.updated_at = bar.timestamp
            sequence = (
                int(
                    session.scalar(
                        select(func.count(PaperFillRecord.id)).where(
                            PaperFillRecord.order_id == order.id
                        )
                    )
                    or 0
                )
                + 1
            )
            fill = PaperFillRecord(
                id=hashlib.sha256(f"{order.id}|{sequence}".encode()).hexdigest(),
                order_id=order.id,
                sequence=sequence,
                quantity=quantity,
                price=price,
                reference_price=reference,
                commission=commission,
                timestamp=bar.timestamp,
            )
            session.add(fill)
            order.filled_quantity += quantity
            order.remaining_quantity = order.quantity - order.filled_quantity
            order.status = (
                OrderStatus.FILLED
                if order.remaining_quantity == 0
                else OrderStatus.PARTIALLY_FILLED
            )
            if order.status == OrderStatus.FILLED:
                order.completed_at = bar.timestamp
            prices = {
                p.instrument_id: (bar.close if p.instrument_id == bar.symbol else p.average_cost)
                for p in session.scalars(
                    select(PositionRecord).where(PositionRecord.account_id == order.account_id)
                ).all()
            }
            account.equity = account.cash + sum(
                (
                    p.quantity * prices[p.instrument_id]
                    for p in session.scalars(
                        select(PositionRecord).where(PositionRecord.account_id == order.account_id)
                    ).all()
                ),
                Decimal(0),
            )
            account.high_water_mark = max(account.high_water_mark, account.equity)
            event_type = (
                AuditEventType.ORDER_FILLED
                if order.status == OrderStatus.FILLED
                else AuditEventType.ORDER_PARTIALLY_FILLED
            )
            self.repository.audit(
                session,
                event_type,
                "order",
                order.id,
                order.trading_cycle_id,
                order.correlation_id,
                {"fill_id": fill.id, "quantity": str(quantity), "commission": str(commission)},
            )
            session.commit()
            session.refresh(order)
            return order

    def cancel_order(self, order_id: str) -> PaperOrderRecord:
        with Session(self.repository.engine) as session:
            order = session.get(PaperOrderRecord, order_id)
            if order is None:
                raise KeyError(order_id)
            if order.status == OrderStatus.CANCELLED:
                return order
            if order.status not in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
                raise ValueError("Příkaz v tomto stavu nelze zrušit")
            order.status = OrderStatus.CANCELLED
            order.cancelled_at = datetime.now(UTC)
            self.repository.audit(
                session,
                AuditEventType.ORDER_CANCELLED,
                "order",
                order.id,
                order.trading_cycle_id,
                order.correlation_id,
            )
            session.commit()
            session.refresh(order)
            return order


class PersistentExecutionEngine:
    """Jediná aplikační hranice mezi schváleným risk rozhodnutím a brokerem."""

    def __init__(self, broker: PersistentPaperBroker) -> None:
        self.broker = broker

    def submit(
        self,
        account_id: str,
        strategy_id: str,
        intent: OrderIntent,
        decision: RiskDecision,
    ) -> PaperOrderRecord:
        if decision.order_intent_id != intent.id:
            raise PermissionError("ExecutionEngine odmítl risk rozhodnutí jiného intentu")
        if decision.status not in (
            RiskDecisionStatus.APPROVED,
            RiskDecisionStatus.MODIFIED,
        ):
            raise PermissionError("ExecutionEngine odmítl neschválený příkaz")
        return self.broker.submit_order(account_id, strategy_id, intent, decision)


class ReconciliationService:
    def __init__(self, repository: Phase4Repository):
        self.repository = repository

    def reconcile(
        self,
        account_id: str,
        expected_cash: Decimal | None = None,
        expected_positions: dict[str, Decimal] | None = None,
        tolerance: Decimal = Decimal("0.000001"),
        correlation_id: str | None = None,
    ) -> ReconciliationResult:
        correlation_id = correlation_id or str(uuid4())
        differences: dict[str, object] = {}
        with Session(self.repository.engine) as session:
            account = session.get(PaperAccountRecord, account_id)
            if account is None:
                raise KeyError(account_id)
            self.repository.audit(
                session,
                AuditEventType.RECONCILIATION_STARTED,
                "account",
                account_id,
                None,
                correlation_id,
            )
            fills = session.execute(
                select(PaperFillRecord, PaperOrderRecord)
                .join(PaperOrderRecord, PaperOrderRecord.id == PaperFillRecord.order_id)
                .where(PaperOrderRecord.account_id == account_id)
            ).all()
            if expected_cash is None:
                expected_cash = account.starting_cash
                for fill, order in fills:
                    if order.side == Side.BUY:
                        expected_cash -= fill.price * fill.quantity + fill.commission
                    else:
                        expected_cash += fill.price * fill.quantity - fill.commission
            if expected_positions is None:
                expected_positions = {}
                for fill, order in fills:
                    signed = fill.quantity if order.side == Side.BUY else -fill.quantity
                    expected_positions[order.instrument_id] = (
                        expected_positions.get(order.instrument_id, Decimal(0)) + signed
                    )
            if expected_cash is not None and abs(account.cash - expected_cash) > tolerance:
                differences["cash"] = {"expected": str(expected_cash), "actual": str(account.cash)}
            actual_positions = {
                p.instrument_id: p.quantity
                for p in session.scalars(
                    select(PositionRecord).where(PositionRecord.account_id == account_id)
                ).all()
            }
            if expected_positions is not None:
                for symbol in set(actual_positions) | set(expected_positions):
                    difference = actual_positions.get(symbol, Decimal(0)) - expected_positions.get(
                        symbol, Decimal(0)
                    )
                    if abs(difference) > tolerance:
                        differences.setdefault("positions", {})[symbol] = str(difference)  # type: ignore[index]
            for order in session.scalars(
                select(PaperOrderRecord).where(PaperOrderRecord.account_id == account_id)
            ).all():
                filled = session.scalar(
                    select(func.sum(PaperFillRecord.quantity)).where(
                        PaperFillRecord.order_id == order.id
                    )
                ) or Decimal(0)
                if (
                    filled != order.filled_quantity
                    or order.remaining_quantity != order.quantity - filled
                    or filled > order.quantity
                    or (order.status == OrderStatus.FILLED and order.remaining_quantity != 0)
                    or (
                        order.status == OrderStatus.PARTIALLY_FILLED
                        and not 0 < order.filled_quantity < order.quantity
                    )
                ):
                    differences.setdefault("orders", {})[order.id] = "quantity invariant"  # type: ignore[index]
            status = ReconciliationStatus.FAILED if differences else ReconciliationStatus.SUCCEEDED
            account.reconciliation_safe = not differences
            if differences:
                account.trading_state = SystemTradingState.HALTED
            result_id = str(uuid4())
            session.add(
                ReconciliationRecord(
                    id=result_id,
                    account_id=account_id,
                    timestamp=datetime.now(UTC),
                    status=status,
                    differences_json=json.dumps(differences, sort_keys=True),
                    correlation_id=correlation_id,
                )
            )
            event_type = (
                AuditEventType.RECONCILIATION_FAILED
                if differences
                else AuditEventType.RECONCILIATION_SUCCEEDED
            )
            self.repository.audit(
                session, event_type, "account", account_id, None, correlation_id, differences
            )
            session.commit()
        return ReconciliationResult(result_id, account_id, status, datetime.now(UTC), differences)


class TradingCycleService:
    def __init__(
        self,
        repository: Phase4Repository,
        risk_config: ProductionRiskConfig | None = None,
        lease_duration: timedelta = timedelta(minutes=5),
    ):
        self.repository = repository
        self.risk = ProductionRiskEngine(risk_config or ProductionRiskConfig())
        self.broker = PersistentPaperBroker(repository)
        self.execution = PersistentExecutionEngine(self.broker)
        self.reconciliation = ReconciliationService(repository)
        self.lease_duration = lease_duration

    def _assert_cycle_lease(self, cycle_id: str, lease_owner: str) -> None:
        with Session(self.repository.engine) as session:
            cycle = session.get(TradingCycleRecord, cycle_id)
            if cycle is None or cycle.lease_owner != lease_owner:
                raise RuntimeError("Trading cycle ztratil databázový lease")

    def run(
        self,
        account_id: str,
        strategy_id: str,
        bars: list[Bar],
        target_weights: dict[str, Decimal],
        session_date: date,
        decision_time: datetime,
    ) -> str:
        if not bars:
            raise ValueError("Chybí market data")
        bars_by_symbol: dict[str, Bar] = {}
        for symbol in sorted({item.symbol for item in bars}):
            symbol_bars = sorted(
                (item for item in bars if item.symbol == symbol), key=lambda item: item.timestamp
            )
            validate_bars(symbol_bars)
            executable = symbol_bars[-1]
            if executable.timestamp <= decision_time:
                raise ValueError("Executable bar musí následovat po decision time")
            bars_by_symbol[symbol] = executable
        cycle_key = deterministic_cycle_key(account_id, strategy_id, session_date)
        input_fingerprint = cycle_input_fingerprint(bars, decision_time, target_weights)
        cycle_id = cycle_key
        correlation_id = cycle_key
        lease_owner = str(uuid4())
        lease_now = datetime.now(UTC)
        lease_expires_at = lease_now + self.lease_duration
        with Session(self.repository.engine) as session:
            existing = session.scalar(
                select(TradingCycleRecord).where(TradingCycleRecord.cycle_key == cycle_key)
            )
            if existing and existing.data_fingerprint != input_fingerprint:
                raise ValueError("Retry trading cycle má odlišná vstupní data nebo decision time")
            if existing and existing.status == TradingCycleStatus.COMPLETED:
                return existing.id
            if existing and existing.status == TradingCycleStatus.RUNNING:
                expires_at = existing.lease_expires_at
                if expires_at is not None and expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at is not None and expires_at > lease_now:
                    return existing.id
                claimed = cast(
                    CursorResult[Any],
                    session.execute(
                        update(TradingCycleRecord)
                        .where(
                            TradingCycleRecord.id == existing.id,
                            TradingCycleRecord.status == TradingCycleStatus.RUNNING,
                            or_(
                                TradingCycleRecord.lease_expires_at.is_(None),
                                TradingCycleRecord.lease_expires_at <= lease_now,
                            ),
                        )
                        .values(lease_owner=lease_owner, lease_expires_at=lease_expires_at)
                        .execution_options(synchronize_session=False)
                    ),
                )
                session.commit()
                if claimed.rowcount != 1:
                    return existing.id
            if existing is None:
                session.add(
                    TradingCycleRecord(
                        id=cycle_id,
                        cycle_key=cycle_key,
                        account_id=account_id,
                        strategy_id=strategy_id,
                        session_date=session_date,
                        started_at=datetime.now(UTC),
                        status=TradingCycleStatus.RUNNING,
                        correlation_id=correlation_id,
                        data_fingerprint=input_fingerprint,
                        lease_owner=lease_owner,
                        lease_expires_at=lease_expires_at,
                    )
                )
                try:
                    session.flush()
                    self.repository.audit(
                        session,
                        AuditEventType.TRADING_CYCLE_STARTED,
                        "cycle",
                        cycle_id,
                        cycle_id,
                        correlation_id,
                    )
                    session.commit()
                except IntegrityError as error:
                    session.rollback()
                    recovered = session.scalar(
                        select(TradingCycleRecord).where(TradingCycleRecord.cycle_key == cycle_key)
                    )
                    if recovered is None:
                        raise
                    if recovered.data_fingerprint != input_fingerprint:
                        raise ValueError(
                            "Concurrent trading cycle má odlišná vstupní data, "
                            "targety nebo decision time"
                        ) from error
                    return recovered.id
            else:
                existing.status = TradingCycleStatus.RUNNING
                existing.lease_owner = lease_owner
                existing.lease_expires_at = lease_expires_at
                session.commit()
        with Session(self.repository.engine) as session:
            event_types = set(
                session.scalars(
                    select(AuditEventRecord.event_type).where(
                        AuditEventRecord.trading_cycle_id == cycle_id
                    )
                ).all()
            )
            if AuditEventType.DATA_VALIDATED not in event_types:
                self.repository.audit(
                    session,
                    AuditEventType.DATA_VALIDATED,
                    "cycle",
                    cycle_id,
                    cycle_id,
                    correlation_id,
                    {"data_fingerprint": input_fingerprint},
                )
            if AuditEventType.TARGET_GENERATED not in event_types:
                self.repository.audit(
                    session,
                    AuditEventType.TARGET_GENERATED,
                    "cycle",
                    cycle_id,
                    cycle_id,
                    correlation_id,
                    {"symbols": sorted(target_weights)},
                )
            session.commit()
        with Session(self.repository.engine) as session:
            account_row = session.get(PaperAccountRecord, account_id)
            if account_row is None:
                raise KeyError(account_id)
            if account_row.session_date != session_date:
                account_row.session_date = session_date
                account_row.session_start_equity = account_row.equity
                session.commit()
        account = self.repository.account(account_id)
        positions = {p.instrument_id: p.quantity for p in self.repository.positions(account_id)}
        pending: dict[str, Decimal] = {}
        for open_order in self.broker.get_open_orders(account_id):
            signed_remaining = (
                open_order.remaining_quantity
                if open_order.side == Side.BUY
                else -open_order.remaining_quantity
            )
            pending[open_order.instrument_id] = (
                pending.get(open_order.instrument_id, Decimal(0)) + signed_remaining
            )
        # Risk snapshot na open nesmí obsahovat close stejné executable session.
        prices = {symbol: item.open for symbol, item in bars_by_symbol.items()}
        with Session(self.repository.engine) as session:
            daily_orders = int(
                session.scalar(
                    select(func.count(PaperOrderRecord.id))
                    .join(
                        TradingCycleRecord,
                        TradingCycleRecord.id == PaperOrderRecord.trading_cycle_id,
                    )
                    .where(
                        PaperOrderRecord.account_id == account_id,
                        TradingCycleRecord.session_date == session_date,
                    )
                )
                or 0
            )
            daily_notional = Decimal(
                session.scalar(
                    select(func.coalesce(func.sum(PaperOrderRecord.submitted_notional), 0))
                    .join(
                        TradingCycleRecord,
                        TradingCycleRecord.id == PaperOrderRecord.trading_cycle_id,
                    )
                    .where(
                        PaperOrderRecord.account_id == account_id,
                        TradingCycleRecord.session_date == session_date,
                    )
                )
                or 0
            )
        managed_symbols = set(target_weights) | {
            symbol for symbol, quantity in positions.items() if quantity != 0
        }
        for symbol in sorted(managed_symbols):
            weight = target_weights.get(symbol, Decimal("0"))
            bar = bars_by_symbol.get(symbol)
            if bar is None:
                raise ValueError(
                    "Cycle vyžaduje executable bar každého drženého a target instrumentu"
                )
            desired = (account.equity * weight / bar.open).to_integral_value(rounding=ROUND_DOWN)
            delta = desired - positions.get(symbol, Decimal(0)) - pending.get(symbol, Decimal(0))
            if delta == 0:
                continue
            intent_id = hashlib.sha256(f"{cycle_id}|{symbol}|{desired}".encode()).hexdigest()
            intent = OrderIntent(
                symbol,
                Side.BUY if delta > 0 else Side.SELL,
                abs(delta),
                decision_time,
                "target-vs-actual",
                intent_id,
                OrderType.MARKET,
                None,
                cycle_id,
                correlation_id,
            )
            with Session(self.repository.engine) as session:
                account_row = session.get(PaperAccountRecord, account_id)
                assert account_row is not None
                session_start_equity = account_row.session_start_equity or account.equity
            snapshot = PortfolioRiskSnapshot(
                account.cash,
                account.equity,
                account.high_water_mark,
                session_start_equity,
                positions,
                prices,
                pending,
                daily_orders,
                daily_notional,
                account.trading_state,
            )
            decision = self.risk.evaluate(
                intent, bar.open, snapshot, cycle_id, correlation_id, decision_time, bar.timestamp
            )
            with Session(self.repository.engine) as session:
                if session.get(RiskDecisionRecord, decision.decision_id) is None:
                    self.repository.audit(
                        session,
                        AuditEventType.ORDER_INTENT_CREATED,
                        "order_intent",
                        intent.id,
                        cycle_id,
                        correlation_id,
                    )
                    session.add(
                        RiskDecisionRecord(
                            id=decision.decision_id,
                            timestamp=decision.timestamp,
                            account_id=account_id,
                            order_intent_id=intent.id,
                            trading_cycle_id=cycle_id,
                            status=decision.status,
                            original_quantity=decision.original_quantity,
                            approved_quantity=decision.approved_quantity,
                            reasons_json=json.dumps([r.value for r in decision.reasons]),
                            limits_json=json.dumps(decision.evaluated_limits, sort_keys=True),
                            portfolio_json=json.dumps(
                                {
                                    **decision.portfolio_snapshot,
                                    "order_intent_fingerprint": order_intent_fingerprint(intent),
                                },
                                sort_keys=True,
                            ),
                            correlation_id=correlation_id,
                        )
                    )
                    session.flush()
                    audit_type = (
                        AuditEventType.RISK_APPROVED
                        if decision.status is RiskDecisionStatus.APPROVED
                        else AuditEventType.RISK_REJECTED
                    )
                    self.repository.audit(
                        session,
                        audit_type,
                        "risk_decision",
                        decision.decision_id,
                        cycle_id,
                        correlation_id,
                    )
                    session.commit()
            if decision.status is RiskDecisionStatus.REJECTED:
                if (
                    RiskReason.DAILY_LOSS in decision.reasons
                    or RiskReason.DRAWDOWN in decision.reasons
                ):
                    self.repository.halt(
                        account_id, ",".join(r.value for r in decision.reasons), correlation_id
                    )
                continue
            self._assert_cycle_lease(cycle_id, lease_owner)
            order = self.execution.submit(account_id, strategy_id, intent, decision)
            self.broker.process(order.id, bar)
        result = self.reconciliation.reconcile(account_id, correlation_id=correlation_id)
        with Session(self.repository.engine) as session:
            cycle = session.get(TradingCycleRecord, cycle_id)
            assert cycle is not None
            if cycle.lease_owner != lease_owner:
                raise RuntimeError("Trading cycle ztratil databázový lease před dokončením")
            cycle.completed_at = datetime.now(UTC)
            cycle.status = (
                TradingCycleStatus.COMPLETED
                if result.status is ReconciliationStatus.SUCCEEDED
                else TradingCycleStatus.HALTED
            )
            cycle.lease_owner = None
            cycle.lease_expires_at = None
            self.repository.audit(
                session,
                AuditEventType.TRADING_CYCLE_COMPLETED,
                "cycle",
                cycle_id,
                cycle_id,
                correlation_id,
            )
            session.commit()
        return cycle_id
