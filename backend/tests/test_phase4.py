import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from quantlab.data import dataset_identity
from quantlab.domain import (
    Bar,
    OrderIntent,
    OrderStatus,
    OrderType,
    RiskDecisionStatus,
    RiskReason,
    Side,
    SystemTradingState,
    TradingCycleStatus,
)
from quantlab.phase4 import (
    AuditEventRecord,
    PaperFillRecord,
    PaperOrderRecord,
    Phase4Repository,
    PortfolioRiskSnapshot,
    ProductionRiskConfig,
    ProductionRiskEngine,
    ReconciliationService,
    RiskDecisionRecord,
    TradingCycleRecord,
    TradingCycleService,
    deterministic_cycle_key,
)

NOW = datetime(2026, 8, 11, 20, tzinfo=UTC)


def bar(*, volume: str = "1000", low: str = "99", high: str = "102") -> Bar:
    return Bar(
        "SPY",
        NOW + timedelta(days=1),
        Decimal("100"),
        Decimal(high),
        Decimal(low),
        Decimal("101"),
        Decimal(volume),
        Decimal("101"),
    )


def snapshot(**changes: object) -> PortfolioRiskSnapshot:
    values: dict[str, object] = {
        "cash": Decimal("100000"),
        "equity": Decimal("100000"),
        "high_water_mark": Decimal("100000"),
        "session_start_equity": Decimal("100000"),
        "positions": {},
        "prices": {"SPY": Decimal("100")},
        "pending": {},
        "daily_orders": 0,
        "daily_notional": Decimal(0),
        "trading_state": SystemTradingState.NORMAL,
    }
    values.update(changes)
    return PortfolioRiskSnapshot(**values)  # type: ignore[arg-type]


def intent(quantity: str = "10", side: Side = Side.BUY, symbol: str = "SPY") -> OrderIntent:
    return OrderIntent(symbol, side, Decimal(quantity), NOW, "test", "intent-1")


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ({"price": Decimal("NaN")}, RiskReason.INVALID_PRICE),
        ({"price": Decimal("0")}, RiskReason.INVALID_PRICE),
        ({"symbol": "QQQ"}, RiskReason.INSTRUMENT_NOT_ALLOWED),
        ({"quantity": "300"}, RiskReason.SINGLE_ORDER_LIMIT),
        ({"quantity": "260"}, RiskReason.POSITION_LIMIT),
    ],
)
def test_risk_rejects_invalid_and_limit_violations(
    changed: dict[str, object], reason: RiskReason
) -> None:
    order = intent(str(changed.get("quantity", "10")), symbol=str(changed.get("symbol", "SPY")))
    price = changed.get("price", Decimal("100"))
    assert isinstance(price, Decimal)
    decision = ProductionRiskEngine(ProductionRiskConfig()).evaluate(
        order, price, snapshot(), "cycle", "correlation", NOW, NOW
    )
    assert decision.status is RiskDecisionStatus.REJECTED
    assert reason in decision.reasons


def test_halt_allows_only_risk_reducing_exit() -> None:
    engine = ProductionRiskEngine(ProductionRiskConfig())
    halted = snapshot(positions={"SPY": Decimal("10")}, trading_state=SystemTradingState.HALTED)
    close = engine.evaluate(
        intent("10", Side.SELL), Decimal("100"), halted, "cycle", "correlation", NOW, NOW
    )
    increase = engine.evaluate(
        intent("1", Side.BUY), Decimal("100"), halted, "cycle", "correlation", NOW, NOW
    )
    assert close.status is RiskDecisionStatus.APPROVED
    assert RiskReason.TRADING_HALTED in increase.reasons


def test_daily_loss_drawdown_stale_and_persistent_halt() -> None:
    repository = Phase4Repository()
    repository.seed_account()
    engine = ProductionRiskEngine(ProductionRiskConfig())
    bad = snapshot(
        equity=Decimal("89000"),
        high_water_mark=Decimal("100000"),
        session_start_equity=Decimal("100000"),
    )
    decision = engine.evaluate(
        intent(), Decimal("100"), bad, "cycle", "correlation", NOW, NOW - timedelta(days=5)
    )
    assert {RiskReason.DAILY_LOSS, RiskReason.DRAWDOWN, RiskReason.STALE_DATA} <= set(
        decision.reasons
    )
    repository.halt("paper-main", "test", "correlation")
    restarted = Phase4Repository.__new__(Phase4Repository)
    restarted.engine = repository.engine
    assert restarted.account("paper-main").trading_state is SystemTradingState.HALTED


def test_end_to_end_cycle_is_idempotent_and_reconciles() -> None:
    repository = Phase4Repository()
    repository.seed_account()
    service = TradingCycleService(
        repository, ProductionRiskConfig(max_single_order_pct=Decimal("0.20"))
    )
    bars = [
        Bar(
            "SPY",
            NOW,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            Decimal("10000"),
            Decimal("100"),
        ),
        bar(volume="10000"),
    ]
    cycle = service.run(
        "paper-main", "fixture:1", bars, {"SPY": Decimal("0.10")}, date(2026, 8, 12), NOW
    )
    cycle_again = service.run(
        "paper-main", "fixture:1", bars, {"SPY": Decimal("0.10")}, date(2026, 8, 12), NOW
    )
    assert cycle_again == cycle
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count(TradingCycleRecord.id))) == 1
        assert session.scalar(select(func.count(PaperOrderRecord.id))) == 1
        assert session.scalar(select(func.count(PaperFillRecord.id))) == 1
        assert session.scalar(select(func.count(RiskDecisionRecord.id))) == 1
        assert session.scalar(select(func.count(AuditEventRecord.id))) >= 6
    account = repository.account("paper-main")
    position = repository.positions("paper-main")[0]
    assert position.quantity == Decimal("100")
    assert account.cash >= 0
    assert account.equity == account.cash + position.quantity * Decimal("101")


def test_partial_fill_cancel_and_invalid_transition() -> None:
    repository = Phase4Repository()
    repository.seed_account()
    service = TradingCycleService(
        repository, ProductionRiskConfig(max_single_order_pct=Decimal("0.20"))
    )
    bars = [
        Bar(
            "SPY",
            NOW,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            Decimal("10000"),
            Decimal("100"),
        ),
        bar(volume="500"),
    ]
    service.run("paper-main", "partial:1", bars, {"SPY": Decimal("0.10")}, date(2026, 8, 12), NOW)
    with Session(repository.engine) as session:
        order_id = session.scalar(select(PaperOrderRecord.id))
    assert order_id is not None
    order = service.broker.get_order(order_id)
    assert order is not None and order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("50")
    TradingCycleService(repository, ProductionRiskConfig(max_single_order_pct=Decimal("0.20"))).run(
        "paper-main",
        "partial-retry:1",
        bars,
        {"SPY": Decimal("0.10")},
        date(2026, 8, 12),
        NOW,
    )
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count(PaperOrderRecord.id))) == 1
    cancelled = service.broker.cancel_order(order_id)
    assert cancelled.status == OrderStatus.CANCELLED
    assert service.broker.cancel_order(order_id).status == OrderStatus.CANCELLED


def test_limit_order_not_reached_then_filled_and_risk_cannot_be_bypassed() -> None:
    repository = Phase4Repository()
    repository.seed_account()
    service = TradingCycleService(repository)
    cycle_id = "cycle"
    with Session(repository.engine) as session:
        session.add(
            TradingCycleRecord(
                id=cycle_id,
                cycle_key=cycle_id,
                account_id="paper-main",
                strategy_id="fixture",
                session_date=date(2026, 8, 12),
                started_at=NOW,
                status="RUNNING",
                correlation_id="correlation",
                data_fingerprint="x",
            )
        )
        session.commit()
    order_intent = OrderIntent(
        "SPY",
        Side.BUY,
        Decimal("10"),
        NOW,
        "limit",
        "limit-intent",
        OrderType.LIMIT,
        Decimal("98"),
        cycle_id,
        "correlation",
    )
    with pytest.raises(PermissionError):
        service.broker.submit_order(
            "paper-main",
            "fixture",
            order_intent,
            ProductionRiskEngine(ProductionRiskConfig())
            .evaluate(order_intent, Decimal("100"), snapshot(), cycle_id, "correlation", NOW, NOW)
            .__class__(
                "wrong",
                NOW,
                "wrong-intent",
                RiskDecisionStatus.APPROVED,
                Decimal("10"),
                Decimal("10"),
                (RiskReason.ALLOWED,),
                {},
                {},
                "correlation",
                cycle_id,
            ),
        )
    decision = service.risk.evaluate(
        order_intent, Decimal("100"), snapshot(), cycle_id, "correlation", NOW, NOW
    )
    with Session(repository.engine) as session:
        session.add(
            RiskDecisionRecord(
                id=decision.decision_id,
                timestamp=NOW,
                account_id="paper-main",
                order_intent_id=order_intent.id,
                trading_cycle_id=cycle_id,
                status=decision.status,
                original_quantity=decision.original_quantity,
                approved_quantity=decision.approved_quantity,
                reasons_json="[]",
                limits_json="{}",
                portfolio_json="{}",
                correlation_id="correlation",
            )
        )
        session.commit()
    order = service.broker.submit_order("paper-main", "fixture", order_intent, decision)
    assert service.broker.process(order.id, bar(low="99")).filled_quantity == 0
    assert service.broker.process(order.id, bar(low="97")).status == OrderStatus.FILLED
    with Session(repository.engine) as session:
        fill_price = session.scalar(
            select(PaperFillRecord.price).where(PaperFillRecord.order_id == order.id)
        )
    assert fill_price == Decimal("98")

    sell_cycle = "sell-cycle"
    sell_intent = OrderIntent(
        "SPY",
        Side.SELL,
        Decimal("10"),
        NOW,
        "limit-close",
        "sell-limit-intent",
        OrderType.LIMIT,
        Decimal("100"),
        sell_cycle,
        "correlation",
    )
    with Session(repository.engine) as session:
        session.add(
            TradingCycleRecord(
                id=sell_cycle,
                cycle_key=sell_cycle,
                account_id="paper-main",
                strategy_id="fixture",
                session_date=date(2026, 8, 12),
                started_at=NOW,
                status="RUNNING",
                correlation_id="correlation",
                data_fingerprint="x",
            )
        )
        session.commit()
    sell_snapshot = snapshot(
        cash=repository.account("paper-main").cash,
        positions={"SPY": Decimal("10")},
    )
    sell_decision = service.risk.evaluate(
        sell_intent, Decimal("100"), sell_snapshot, sell_cycle, "correlation", NOW, NOW
    )
    with Session(repository.engine) as session:
        session.add(
            RiskDecisionRecord(
                id=sell_decision.decision_id,
                timestamp=NOW,
                account_id="paper-main",
                order_intent_id=sell_intent.id,
                trading_cycle_id=sell_cycle,
                status=sell_decision.status,
                original_quantity=sell_decision.original_quantity,
                approved_quantity=sell_decision.approved_quantity,
                reasons_json="[]",
                limits_json="{}",
                portfolio_json="{}",
                correlation_id="correlation",
            )
        )
        session.commit()
    sell_order = service.execution.submit("paper-main", "fixture", sell_intent, sell_decision)
    assert service.broker.process(sell_order.id, bar(high="102")).status == OrderStatus.FILLED
    with Session(repository.engine) as session:
        sell_price = session.scalar(
            select(PaperFillRecord.price).where(PaperFillRecord.order_id == sell_order.id)
        )
    assert sell_price == Decimal("100")


def test_reconciliation_mismatch_halts_and_blocks_resume() -> None:
    repository = Phase4Repository()
    repository.seed_account()
    result = ReconciliationService(repository).reconcile(
        "paper-main", expected_cash=Decimal("1"), expected_positions={}
    )
    assert result.status.value == "FAILED"
    assert repository.account("paper-main").trading_state is SystemTradingState.HALTED
    with pytest.raises(PermissionError):
        repository.resume("paper-main", "correlation")


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="vyžaduje PostgreSQL CI")
def test_postgres_phase4_constraints_and_persistence() -> None:
    repository = Phase4Repository(os.environ["DATABASE_URL"], bootstrap_test_schema=False)
    account_id = "phase4-postgres"
    repository.seed_account(account_id)
    assert repository.account(account_id).cash == Decimal("100000")
    names = set(inspect(repository.engine).get_table_names())
    assert {
        "paper_accounts",
        "paper_orders",
        "paper_fills",
        "risk_decisions",
        "trading_cycles",
    } <= names


def test_concurrent_cycle_start_creates_one_cycle_and_order(tmp_path: object) -> None:
    database = str(tmp_path) + "/phase4.db"
    repository = Phase4Repository(f"sqlite:///{database}")
    repository.seed_account()
    bars = [
        Bar(
            "SPY",
            NOW,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            Decimal("10000"),
            Decimal("100"),
        ),
        bar(volume="10000"),
    ]

    def run() -> str:
        local = Phase4Repository(f"sqlite:///{database}", bootstrap_test_schema=False)
        return TradingCycleService(
            local, ProductionRiskConfig(max_single_order_pct=Decimal("0.20"))
        ).run("paper-main", "concurrent:1", bars, {"SPY": Decimal("0.10")}, date(2026, 8, 12), NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        cycle_ids = list(executor.map(lambda _: run(), range(2)))
    assert cycle_ids[0] == cycle_ids[1]
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count(TradingCycleRecord.id))) == 1
        assert session.scalar(select(func.count(PaperOrderRecord.id))) == 1


def test_expired_cycle_lease_is_recovered_after_crash() -> None:
    repository = Phase4Repository()
    repository.seed_account()
    service = TradingCycleService(repository)
    bars = [
        Bar(
            "SPY",
            NOW,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            Decimal("10000"),
            Decimal("100"),
        ),
        bar(volume="10000"),
    ]
    cycle_key = deterministic_cycle_key("paper-main", "recovery:1", date(2026, 8, 12))
    with Session(repository.engine) as session:
        session.add(
            TradingCycleRecord(
                id=cycle_key,
                cycle_key=cycle_key,
                account_id="paper-main",
                strategy_id="recovery:1",
                session_date=date(2026, 8, 12),
                started_at=NOW,
                status=TradingCycleStatus.RUNNING,
                correlation_id=cycle_key,
                data_fingerprint=dataset_identity(bars),
                lease_owner="crashed-worker",
                lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        session.commit()
    assert (
        service.run(
            "paper-main", "recovery:1", bars, {"SPY": Decimal("0.10")}, date(2026, 8, 12), NOW
        )
        == cycle_key
    )
    with Session(repository.engine) as session:
        recovered = session.get(TradingCycleRecord, cycle_key)
        assert recovered is not None and recovered.status == TradingCycleStatus.COMPLETED
        assert session.scalar(select(func.count(PaperOrderRecord.id))) == 1


def test_daily_limits_use_execution_session_instead_of_decision_date() -> None:
    repository = Phase4Repository()
    repository.seed_account()
    config = ProductionRiskConfig(max_single_order_pct=Decimal("0.20"), max_orders_per_day=1)
    service = TradingCycleService(repository, config)
    bars = [
        Bar(
            "SPY",
            NOW,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            Decimal("10000"),
            Decimal("100"),
        ),
        bar(volume="10000"),
    ]
    service.run(
        "paper-main", "daily-first:1", bars, {"SPY": Decimal("0.10")}, date(2026, 8, 12), NOW
    )
    service.run(
        "paper-main", "daily-second:1", bars, {"SPY": Decimal("0.20")}, date(2026, 8, 12), NOW
    )
    with Session(repository.engine) as session:
        assert session.scalar(select(func.count(PaperOrderRecord.id))) == 1
        rejected = session.scalars(
            select(RiskDecisionRecord).where(
                RiskDecisionRecord.status == RiskDecisionStatus.REJECTED
            )
        ).one()
    assert RiskReason.DAILY_ORDER_LIMIT.value in rejected.reasons_json
