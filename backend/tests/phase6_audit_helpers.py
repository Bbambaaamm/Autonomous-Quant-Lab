from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from quantlab.market_data import (
    AssetType,
    CorporateAction,
    Instrument,
    ProviderBar,
    ProviderMetadata,
    XNYSCalendar,
)
from quantlab.market_data_service import DatasetSnapshotService, PersistentMarketDataService
from quantlab.multi_asset import RebalanceFrequency
from quantlab.persistence import StrategyRecord, UniverseDefinitionRecord, UniverseMembershipRecord
from quantlab.phase6_runtime import Phase6ExperimentRequest

CALENDAR = XNYSCalendar()
CODE_SHA = "a" * 40


@dataclass
class MappingProvider:
    name: str
    bars: dict[str, list[ProviderBar]]
    actions: dict[str, list[CorporateAction]]

    @property
    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(self.name, "1", True, False)

    def resolve(self, symbol: str) -> dict[str, str]:
        return {"symbol": symbol}

    def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]:
        return [item for item in self.bars[symbol] if start <= item.session_date <= end]

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        return [
            item
            for item in self.actions.get(symbol, ())
            if start <= item.effective_at.date() <= end
        ]


def daily_bar(day: date, close: Decimal, source: str | None = None) -> ProviderBar:
    return ProviderBar(
        day, close, close, close, close, Decimal("10000"), source or f"{day}:{close}"
    )


def seed_phase6_snapshot(
    factory,
    *,
    suffix: str | None = None,
    closes: list[Decimal] | None = None,
    as_of: datetime | None = None,
    delayed_historical_ingestion: bool = False,
):
    suffix = suffix or uuid4().hex
    sessions = list(CALENDAR.sessions_between(date(2026, 1, 2), date(2026, 1, 30)))[:15]
    closes = closes or [Decimal(100 + index) for index in range(len(sessions))]
    # Phase 4 paper schema omezuje executable instrument identity na 40 znaků.
    instrument_identity = f"i-{hashlib.sha256(suffix.encode()).hexdigest()[:38]}"
    instrument = Instrument(
        instrument_identity,
        f"S{suffix[:7]}",
        "XNYS",
        "XNYS",
        "USD",
        AssetType.EQUITY,
        date(2020, 1, 1),
    )
    provider = MappingProvider(
        f"p6-{hashlib.sha256(suffix.encode()).hexdigest()[:32]}",
        {
            instrument.symbol: [
                daily_bar(day, value) for day, value in zip(sessions, closes, strict=True)
            ]
        },
        {},
    )
    observed_at = as_of or CALENDAR.session_close(sessions[-1])
    market_data = PersistentMarketDataService(factory)
    for day in sessions:
        # Standardní fixture modeluje průběžný ingest; delayed varianta modeluje
        # production bootstrap, kdy celý historický rozsah dorazí až po poslední session.
        result = market_data.ingest(
            provider,
            instrument,
            day,
            day,
            observed_at
            if delayed_historical_ingestion
            else min(observed_at, CALENDAR.session_close(day)),
        )
        assert result.status == "SUCCEEDED"
    market_data.verify_corporate_action_readiness(
        provider, instrument, sessions[0], sessions[-1], observed_at
    )
    universe_id = f"universe-{suffix}"
    with factory() as session, session.begin():
        session.add(
            UniverseDefinitionRecord(
                universe_id=universe_id,
                name=universe_id,
                kind="POINT_IN_TIME_MEMBERSHIP",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        session.add(
            UniverseMembershipRecord(
                universe_id=universe_id,
                instrument_id=instrument.instrument_id,
                valid_from=datetime(2020, 1, 1, tzinfo=UTC),
                valid_to=None,
                known_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        strategy = session.scalar(
            select(StrategyRecord).where(
                StrategyRecord.strategy_name == "multi_asset_trend",
                StrategyRecord.strategy_version == "1.0.0",
            )
        )
        if strategy is None:
            session.add(
                StrategyRecord(
                    strategy_identity=f"trend-{suffix}",
                    strategy_name="multi_asset_trend",
                    strategy_version="1.0.0",
                    created_at=datetime.now(UTC),
                    metadata_json="{}",
                )
            )
    snapshot = DatasetSnapshotService(factory).build(
        as_of=observed_at,
        provider=provider.metadata.name,
        universe_id=universe_id,
        start=sessions[0],
        end=sessions[-1],
        minimum_coverage=Decimal("1"),
    )
    request = Phase6ExperimentRequest(
        snapshot.snapshot_id,
        "multi_asset_trend",
        "1.0.0",
        (
            {"fast": 2, "slow": 3, "rebalance_frequency": RebalanceFrequency.DAILY},
            {"fast": 3, "slow": 5, "rebalance_frequency": RebalanceFrequency.DAILY},
        ),
        code_sha=CODE_SHA,
    )
    return instrument, provider, sessions, snapshot, request
