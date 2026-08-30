from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from quantlab import market_data
from quantlab.domain import Side
from quantlab.market_data import (
    AssetType,
    CorporateAction,
    CorporateActionKind,
    InMemoryObservationStore,
    Instrument,
    InvalidMarketData,
    InvalidProviderResponse,
    InvalidSymbol,
    ProviderBar,
    ProviderMetadata,
    ProviderRateLimited,
    ProviderUnavailable,
    StooqProvider,
    XNYSCalendar,
    build_snapshot,
    causal_adjusted_close,
    normalize_bar,
)
from quantlab.multi_asset import (
    CrossSectionalMomentumStrategy,
    MeanReversionStrategy,
    MultiAssetFill,
    MultiAssetPortfolio,
    ObservationKnowledgeMode,
    RebalanceFrequency,
    StrategyContext,
    TargetPortfolio,
    TrendStrategy,
    run_multi_asset,
)
from quantlab.universe import (
    PointInTimeUniverse,
    UniverseDefinition,
    UniverseKind,
    UniverseMembership,
)

NOW = datetime(2024, 1, 10, 22, tzinfo=UTC)
CAL = XNYSCalendar()
INST = Instrument("i-a", "AAA", "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2000, 1, 1))


class FixtureProvider:
    metadata = ProviderMetadata("fixture", "1", False, False)

    def __init__(self, bars):
        self.bars = bars

    def resolve(self, symbol):
        return {"symbol": symbol}

    def historical_daily(self, symbol, start, end):
        return [b for b in self.bars if start <= b.session_date <= end]

    def corporate_actions(self, symbol, start, end):
        return []


def bar(day, close="10", source=None):
    value = Decimal(close)
    return ProviderBar(day, value, value, value, value, Decimal("100"), source or str(day))


def obs(instrument, day, close, observed=None, ingestion="x"):
    if isinstance(instrument, str):
        instrument = Instrument(
            instrument, instrument, "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2000, 1, 1)
        )
    known_at = observed or CAL.session_close(day)
    return normalize_bar(bar(day, close), instrument, "fixture", known_at, ingestion, CAL)


def test_calendar_holiday_early_close_and_next_session():
    assert not CAL.is_session(date(2024, 7, 4))
    assert CAL.next_session(date(2024, 7, 3)) == date(2024, 7, 5)
    assert CAL.session_close(date(2024, 11, 29)).hour == 18  # 13:00 New York v UTC
    assert CAL.session_close(date(2024, 7, 3)).hour == 17
    assert CAL.session_close(date(2024, 11, 27)).hour == 21


def test_normalization_rejects_non_session_and_invalid_ohlc():
    with pytest.raises(InvalidMarketData):
        normalize_bar(bar(date(2024, 1, 6)), INST, "x", NOW, "x", CAL)
    with pytest.raises(InvalidMarketData):
        normalize_bar(
            ProviderBar(
                date(2024, 1, 8),
                Decimal("10"),
                Decimal("9"),
                Decimal("8"),
                Decimal("10"),
                Decimal("1"),
                "x",
            ),
            INST,
            "x",
            NOW,
            "x",
            CAL,
        )


def test_stooq_fixture_contract_valid_partial_empty_malformed_duplicate_rate_limit():
    payload = b"Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,1,2,10\n2024-01-03,2,3,2,3,11\n"
    provider = StooqProvider(lambda u, t: (200, {}, payload), max_attempts=1)
    assert [
        b.session_date
        for b in provider.historical_daily("AAPL", date(2024, 1, 3), date(2024, 1, 5))
    ] == [date(2024, 1, 3)]
    with pytest.raises(InvalidSymbol):
        StooqProvider(lambda u, t: (200, {}, b""), max_attempts=1).historical_daily(
            "A", date(2024, 1, 1), date(2024, 1, 2)
        )
    with pytest.raises(InvalidProviderResponse):
        StooqProvider(lambda u, t: (200, {}, b"bad\nrow"), max_attempts=1).historical_daily(
            "A", date(2024, 1, 1), date(2024, 1, 2)
        )
    duplicate = payload + b"2024-01-03,2,3,2,3,11\n"
    with pytest.raises(InvalidProviderResponse):
        StooqProvider(lambda u, t: (200, {}, duplicate), max_attempts=1).historical_daily(
            "A", date(2024, 1, 1), date(2024, 1, 4)
        )
    with pytest.raises(ProviderRateLimited):
        StooqProvider(
            lambda u, t: (429, {"Retry-After": "0"}, b""), max_attempts=1
        ).historical_daily("A", date(2024, 1, 1), date(2024, 1, 2))
    with pytest.raises(InvalidSymbol):
        provider.resolve("../secret")
    invalid_decimal = b"Date,Open,High,Low,Close,Volume\n2024-01-02,N/D,2,1,2,10\n"
    with pytest.raises(InvalidProviderResponse):
        StooqProvider(lambda u, t: (200, {}, invalid_decimal), max_attempts=1).historical_daily(
            "A", date(2024, 1, 1), date(2024, 1, 2)
        )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://stooq.com/q/d/l/",
        "https://evil.example/q/d/l/",
        "https://stooq.com:443/q/d/l/",
        "https://user:password@stooq.com/q/d/l/",
    ],
)
def test_stooq_http_transport_rejects_non_allowlisted_urls(url):
    with pytest.raises(ProviderUnavailable):
        StooqProvider._http(url, 1)


def test_stooq_http_transport_rejects_redirect_outside_allowlist():
    handler = market_data._StooqRedirectHandler()
    with pytest.raises(ProviderUnavailable):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/internal")


def test_ingestion_idempotency_overlap_revision_and_snapshot_immutability():
    store = InMemoryObservationStore()
    first = store.ingest(
        FixtureProvider([bar(date(2024, 1, 8), "10")]),
        INST,
        date(2024, 1, 1),
        date(2024, 1, 8),
        NOW,
        CAL,
    )
    assert first.status == "SUCCEEDED" and len(first.observations) == 1
    assert not store.ingest(
        FixtureProvider([bar(date(2024, 1, 8), "10")]),
        INST,
        date(2024, 1, 1),
        date(2024, 1, 8),
        NOW + timedelta(hours=1),
        CAL,
    ).observations
    s1 = build_snapshot(
        store,
        as_of=NOW,
        provider="fixture",
        calendar_identity=CAL.identity,
        universe_id="u",
        instrument_ids=["i-a"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 8),
    )
    corrected = store.ingest(
        FixtureProvider([bar(date(2024, 1, 8), "11", "correction")]),
        INST,
        date(2024, 1, 1),
        date(2024, 1, 8),
        NOW + timedelta(days=1),
        CAL,
    )
    assert corrected.observations[0].revision == 2
    s1_again = build_snapshot(
        store,
        as_of=NOW,
        provider="fixture",
        calendar_identity=CAL.identity,
        universe_id="u",
        instrument_ids=["i-a"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 8),
    )
    s2 = build_snapshot(
        store,
        as_of=NOW + timedelta(days=1),
        provider="fixture",
        calendar_identity=CAL.identity,
        universe_id="u",
        instrument_ids=["i-a"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 8),
    )
    assert s1.content_hash == s1_again.content_hash != s2.content_hash


def test_future_corporate_action_does_not_change_past_adjustment():
    observations = [obs("i-a", date(2024, 1, 8), "100")]
    future = CorporateAction(
        "split",
        "i-a",
        CorporateActionKind.SPLIT,
        datetime(2024, 2, 1, tzinfo=UTC),
        datetime(2024, 1, 20, tzinfo=UTC),
        Decimal("2"),
    )
    assert causal_adjusted_close(observations, [], NOW) == causal_adjusted_close(
        observations, [future], NOW
    )


def test_pit_universe_entry_leave_and_future_membership():
    definition = UniverseDefinition("u", "pit", UniverseKind.POINT_IN_TIME_MEMBERSHIP)
    memberships = [
        UniverseMembership(
            "u", "a", datetime(2024, 1, 1, tzinfo=UTC), None, datetime(2024, 1, 1, tzinfo=UTC)
        ),
        UniverseMembership(
            "u",
            "b",
            datetime(2024, 1, 5, tzinfo=UTC),
            datetime(2024, 1, 9, tzinfo=UTC),
            datetime(2024, 1, 5, tzinfo=UTC),
        ),
        UniverseMembership(
            "u", "c", datetime(2024, 1, 9, tzinfo=UTC), None, datetime(2024, 1, 9, tzinfo=UTC)
        ),
    ]
    universe = PointInTimeUniverse(definition, memberships)
    assert universe.eligible(datetime(2024, 1, 4, tzinfo=UTC)) == ("a",)
    assert universe.eligible(datetime(2024, 1, 6, tzinfo=UTC)) == ("a", "b")
    assert universe.eligible(datetime(2024, 1, 10, tzinfo=UTC)) == ("a", "c")
    assert (
        UniverseDefinition("s", "static", UniverseKind.STATIC).survivorship_bias_status
        == "BIAS_PRONE_STATIC"
    )


def test_targets_validation_ties_and_missing_lookback():
    with pytest.raises(ValueError):
        TargetPortfolio((("a", Decimal("nan")),), "bad")
    strategy = CrossSectionalMomentumStrategy(2, 1)
    d1, d2, d3 = date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)
    histories = {
        x: tuple(obs(x, d, c) for d, c in [(d1, "10"), (d2, "11"), (d3, "12")]) for x in ("a", "b")
    }
    signals = {key: tuple(bar.close for bar in bars) for key, bars in histories.items()}
    target = strategy.generate_targets(
        StrategyContext(CAL.session_close(d3), histories, ("a", "b"), signals)
    )
    assert target.weights[0][0] == "a"
    assert (
        CrossSectionalMomentumStrategy(5, 2)
        .generate_targets(StrategyContext(CAL.session_close(d3), histories, ("a", "b"), signals))
        .weights
        == ()
    )
    TrendStrategy(2, 3)
    MeanReversionStrategy(2, Decimal("0.9"))


def test_context_and_prefix_invariance_reject_future_data():
    past = obs("a", date(2024, 1, 8), "10")
    future = obs("a", date(2024, 1, 9), "999")
    context = StrategyContext(past.timestamp, {"a": (past,)}, ("a",), {"a": (past.close,)})
    assert CrossSectionalMomentumStrategy(2, 1).generate_targets(context).weights == ()
    with pytest.raises(ValueError):
        StrategyContext(
            past.timestamp,
            {"a": (past, future)},
            ("a",),
            {"a": (past.close, future.close)},
        )


def test_multi_asset_accounting_split_dividend_and_cash_constraint():
    portfolio = MultiAssetPortfolio(Decimal("1000"))
    portfolio.apply_fill(
        MultiAssetFill("a", Side.BUY, Decimal("5"), Decimal("100"), Decimal("1"), NOW)
    )
    portfolio.apply_action(
        CorporateAction("s", "a", CorporateActionKind.SPLIT, NOW, NOW, Decimal("2"))
    )
    portfolio.apply_action(
        CorporateAction("d", "a", CorporateActionKind.CASH_DIVIDEND, NOW, NOW, Decimal("1"))
    )
    assert (
        portfolio.positions["a"] == 10
        and portfolio.cost_basis["a"] == Decimal("50.1")
        and portfolio.cash == 509
    )
    with pytest.raises(ValueError):
        portfolio.apply_fill(
            MultiAssetFill("b", Side.BUY, Decimal("100"), Decimal("10"), Decimal("0"), NOW)
        )
    with pytest.raises(RuntimeError):
        portfolio.apply_action(CorporateAction("x", "a", CorporateActionKind.DELISTING, NOW, NOW))


def test_multi_asset_next_session_shared_cash_and_master_future_mutation():
    instruments = [
        Instrument(x, x, "XNYS", "XNYS", "USD", AssetType.EQUITY, date(2000, 1, 1))
        for x in ("a", "b")
    ]
    days = [date(2024, 1, d) for d in (3, 4, 5, 8)]
    rows = [
        obs(inst.instrument_id, day, str(10 + i + j))
        for i, day in enumerate(days)
        for j, inst in enumerate(instruments)
    ]
    universe = PointInTimeUniverse(
        UniverseDefinition("u", "pit", UniverseKind.POINT_IN_TIME_MEMBERSHIP),
        [
            UniverseMembership(
                "u",
                x.instrument_id,
                datetime(2020, 1, 1, tzinfo=UTC),
                None,
                datetime(2020, 1, 1, tzinfo=UTC),
            )
            for x in instruments
        ],
    )
    result = run_multi_asset(
        rows, universe, CrossSectionalMomentumStrategy(2, 1), initial_cash=Decimal("1000")
    )
    assert all(fill.timestamp > result.decisions[0][0] for fill in result.fills)
    prefix = tuple(
        (t, target.weights)
        for t, target in result.decisions
        if t <= CAL.session_close(date(2024, 1, 5))
    )
    mutated = rows + [obs("a", date(2024, 1, 9), "999"), obs("b", date(2024, 1, 9), "1")]
    rerun = run_multi_asset(
        mutated, universe, CrossSectionalMomentumStrategy(2, 1), initial_cash=Decimal("1000")
    )
    assert prefix == tuple(
        (t, target.weights)
        for t, target in rerun.decisions
        if t <= CAL.session_close(date(2024, 1, 5))
    )


def single_asset_universe() -> PointInTimeUniverse:
    return PointInTimeUniverse(
        UniverseDefinition("single", "pit", UniverseKind.POINT_IN_TIME_MEMBERSHIP),
        [
            UniverseMembership(
                "single",
                "a",
                datetime(2020, 1, 1, tzinfo=UTC),
                None,
                datetime(2020, 1, 1, tzinfo=UTC),
            )
        ],
    )


def test_observation_identity_contains_provider_and_coverage_counts_sessions():
    first = normalize_bar(bar(date(2024, 1, 8)), INST, "first", NOW, "x", CAL)
    second = normalize_bar(bar(date(2024, 1, 8)), INST, "second", NOW, "x", CAL)
    assert first.observation_id != second.observation_id
    store = InMemoryObservationStore()
    store.ingest(
        FixtureProvider([bar(date(2024, 1, 8))]),
        INST,
        date(2024, 1, 8),
        date(2024, 1, 12),
        NOW,
        CAL,
    )
    snapshot = build_snapshot(
        store,
        as_of=NOW,
        provider="fixture",
        calendar_identity=CAL.identity,
        universe_id="u",
        instrument_ids=["i-a"],
        start=date(2024, 1, 8),
        end=date(2024, 1, 12),
    )
    assert snapshot.coverage == Decimal("0.2")
    assert snapshot.status == "INVALID"


def test_revision_known_later_does_not_change_earlier_decision():
    days = [date(2024, 1, day) for day in (3, 4, 5, 8)]
    rows = [obs("a", day, str(10 + index)) for index, day in enumerate(days)]
    correction = obs(
        "a",
        days[0],
        "999",
        observed=CAL.session_close(days[-1]),
        ingestion="correction",
    )
    strategy = TrendStrategy(1, 2, rebalance_frequency=RebalanceFrequency.DAILY)
    baseline = run_multi_asset(rows, single_asset_universe(), strategy)
    revised = run_multi_asset([*rows, correction], single_asset_universe(), strategy)
    cutoff = CAL.session_close(days[2])
    assert [(when, target.weights) for when, target in baseline.decisions if when <= cutoff] == [
        (when, target.weights) for when, target in revised.decisions if when <= cutoff
    ]


def test_snapshot_pinned_research_uses_bars_ingested_after_historical_sessions():
    days = [date(2024, 1, day) for day in (3, 4, 5, 8)]
    ingested_at = datetime(2024, 2, 1, tzinfo=UTC)
    rows = [obs("a", day, str(10 + index), observed=ingested_at) for index, day in enumerate(days)]

    result = run_multi_asset(
        rows,
        single_asset_universe(),
        TrendStrategy(1, 2, rebalance_frequency=RebalanceFrequency.DAILY),
        initial_cash=Decimal("1000"),
        commission_bps=Decimal("0"),
        observation_knowledge_mode=ObservationKnowledgeMode.SNAPSHOT_PINNED,
    )

    assert result.decisions
    assert result.fills
    assert all(when < ingested_at for when, _ in result.decisions)


def test_static_universe_is_snapshot_current_and_explicitly_bias_prone():
    snapshot_as_of = datetime(2024, 2, 1, tzinfo=UTC)
    definition = UniverseDefinition("static", "current constituents", UniverseKind.STATIC)
    universe = PointInTimeUniverse(
        definition,
        [UniverseMembership("static", "a", snapshot_as_of, None, snapshot_as_of)],
        static_knowledge_as_of=snapshot_as_of,
    )

    assert universe.eligible(CAL.session_close(date(2024, 1, 3))) == ("a",)
    assert definition.survivorship_bias_status == "BIAS_PRONE_STATIC"
    assert definition.survivorship_bias_status != "POINT_IN_TIME_SAFE"


def test_runner_uses_adjusted_signals_and_applies_actions():
    days = [date(2024, 1, day) for day in (3, 4, 5, 8)]
    closes = ("90", "100", "55", "60")
    rows = [obs("a", day, close) for day, close in zip(days, closes, strict=True)]
    split = CorporateAction(
        "split",
        "a",
        CorporateActionKind.SPLIT,
        CAL.session_close(days[2]),
        CAL.session_close(days[2]),
        Decimal("2"),
    )
    dividend = CorporateAction(
        "dividend",
        "a",
        CorporateActionKind.CASH_DIVIDEND,
        CAL.session_close(days[3]),
        CAL.session_close(days[3]),
        Decimal("1"),
    )
    result = run_multi_asset(
        rows,
        single_asset_universe(),
        TrendStrategy(1, 2, rebalance_frequency=RebalanceFrequency.DAILY),
        initial_cash=Decimal("1000"),
        commission_bps=Decimal("0"),
        corporate_actions=(split, dividend),
    )
    assert dict(result.decisions)[CAL.session_close(days[2])].weights == (("a", Decimal("1")),)
    assert result.dividend_income > 0


def test_evaluation_window_preserves_lookback_without_pre_window_trades():
    days = [date(2024, 1, day) for day in (3, 4, 5, 8, 9)]
    rows = [obs("a", day, str(100 + index)) for index, day in enumerate(days)]
    evaluation_start = CAL.session_close(days[3])

    result = run_multi_asset(
        rows,
        single_asset_universe(),
        TrendStrategy(2, 3, rebalance_frequency=RebalanceFrequency.DAILY),
        initial_cash=Decimal("1000"),
        commission_bps=Decimal("0"),
        evaluation_start=evaluation_start,
    )

    assert result.equity[0][0] == evaluation_start
    assert result.decisions[0][0] == evaluation_start
    assert all(fill.timestamp > evaluation_start for fill in result.fills)
