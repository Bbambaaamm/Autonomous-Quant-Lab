from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.domain import require_utc
from quantlab.market_data import AssetType, DatasetInvalid, Instrument
from quantlab.multi_asset import STRATEGY_REGISTRY
from quantlab.persistence import (
    InstrumentRecord,
    InstrumentSymbolRecord,
    StrategyRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.universe import UniverseDefinition, UniverseKind, UniverseMembership


class ControlPlaneRegistryService:
    """Malá persistentní hranice pro registry, jejichž domain model už existuje."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def register_instrument(self, instrument: Instrument) -> InstrumentRecord:
        if not instrument.instrument_id.strip() or not instrument.symbol.strip():
            raise ValueError("Instrument identity a symbol jsou povinné")
        if instrument.exchange != "XNYS" or instrument.calendar != "XNYS":
            raise ValueError("Phase 6 control plane podporuje pouze XNYS")
        if instrument.currency != "USD" or instrument.asset_type is not AssetType.EQUITY:
            raise ValueError("Phase 6 control plane podporuje pouze USD equities")
        if instrument.active_to is not None and instrument.active_to <= instrument.active_from:
            raise ValueError("Active interval musí být neprázdný")
        with self.sessions() as session, session.begin():
            existing = session.get(InstrumentRecord, instrument.instrument_id)
            identity = (
                instrument.symbol,
                instrument.exchange,
                instrument.calendar,
                instrument.currency,
                instrument.asset_type.value,
                instrument.active_from,
                instrument.active_to,
            )
            if existing is not None:
                persisted = (
                    existing.symbol,
                    existing.exchange,
                    existing.calendar,
                    existing.currency,
                    existing.asset_type,
                    existing.active_from.date(),
                    existing.active_to.date() if existing.active_to else None,
                )
                if persisted != identity:
                    raise DatasetInvalid("Instrument identity koliduje s immutable metadata")
                session.expunge(existing)
                return existing
            symbol_conflict = session.scalar(
                select(InstrumentRecord).where(
                    InstrumentRecord.symbol == instrument.symbol,
                    InstrumentRecord.exchange == instrument.exchange,
                )
            )
            if symbol_conflict is not None:
                raise DatasetInvalid("Symbol a venue již označují jiný canonical instrument")
            row = InstrumentRecord(
                instrument_id=instrument.instrument_id,
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                calendar=instrument.calendar,
                currency=instrument.currency,
                asset_type=instrument.asset_type.value,
                active_from=datetime.combine(instrument.active_from, datetime.min.time(), UTC),
                active_to=(
                    datetime.combine(instrument.active_to, datetime.min.time(), UTC)
                    if instrument.active_to
                    else None
                ),
                created_at=require_utc(instrument.created_at),
            )
            session.add(row)
            session.flush()
            session.add(
                InstrumentSymbolRecord(
                    instrument_id=instrument.instrument_id,
                    symbol=instrument.symbol,
                    valid_from=row.active_from,
                    valid_to=row.active_to,
                )
            )
            session.expunge(row)
            return row

    def create_universe(self, definition: UniverseDefinition) -> UniverseDefinitionRecord:
        if definition.kind is not UniverseKind.POINT_IN_TIME_MEMBERSHIP:
            raise ValueError("Production Phase 6 bootstrap vyžaduje PIT universe")
        with self.sessions() as session, session.begin():
            existing = session.get(UniverseDefinitionRecord, definition.universe_id)
            if existing is not None:
                if existing.name != definition.name or existing.kind != definition.kind.value:
                    raise DatasetInvalid("Universe identity koliduje s immutable metadata")
                session.expunge(existing)
                return existing
            same_name = session.scalar(
                select(UniverseDefinitionRecord).where(
                    UniverseDefinitionRecord.name == definition.name
                )
            )
            if same_name is not None:
                raise DatasetInvalid("Universe name již používá jiná identity")
            row = UniverseDefinitionRecord(
                universe_id=definition.universe_id,
                name=definition.name,
                kind=definition.kind.value,
                created_at=require_utc(definition.created_at),
            )
            session.add(row)
            session.flush()
            session.expunge(row)
            return row

    def add_membership(self, membership: UniverseMembership) -> UniverseMembershipRecord:
        with self.sessions() as session, session.begin():
            universe = session.get(UniverseDefinitionRecord, membership.universe_id)
            if universe is None or universe.kind != UniverseKind.POINT_IN_TIME_MEMBERSHIP.value:
                raise DatasetInvalid("Membership vyžaduje existující PIT universe")
            if session.get(InstrumentRecord, membership.instrument_id) is None:
                raise DatasetInvalid("Membership instrument neexistuje")
            existing = session.scalar(
                select(UniverseMembershipRecord).where(
                    UniverseMembershipRecord.universe_id == membership.universe_id,
                    UniverseMembershipRecord.instrument_id == membership.instrument_id,
                    UniverseMembershipRecord.valid_from == membership.valid_from,
                )
            )
            if existing is not None:
                if (
                    existing.valid_to != membership.valid_to
                    or existing.known_at != membership.known_at
                ):
                    raise DatasetInvalid("Membership identity koliduje s immutable intervalem")
                session.expunge(existing)
                return existing
            row = UniverseMembershipRecord(
                universe_id=membership.universe_id,
                instrument_id=membership.instrument_id,
                valid_from=membership.valid_from,
                valid_to=membership.valid_to,
                known_at=membership.known_at,
            )
            session.add(row)
            session.flush()
            session.expunge(row)
            return row

    def ensure_strategy(self, name: str, version: str, now: datetime) -> StrategyRecord:
        strategy_type = STRATEGY_REGISTRY.get(name)
        if strategy_type is None or strategy_type().version != version:
            raise DatasetInvalid("Strategie a verze nejsou v allowlistu")
        implementation = {
            "registry": "quantlab.multi_asset.STRATEGY_REGISTRY",
            "class": strategy_type.__qualname__,
            "version": version,
        }
        identity = hashlib.sha256(
            json.dumps(implementation, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.sessions() as session, session.begin():
            existing = session.scalar(
                select(StrategyRecord).where(
                    StrategyRecord.strategy_name == name,
                    StrategyRecord.strategy_version == version,
                )
            )
            if existing is not None:
                if existing.strategy_identity != identity:
                    raise DatasetInvalid("Registrovaná strategy implementation identity koliduje")
                session.expunge(existing)
                return existing
            row = StrategyRecord(
                strategy_identity=identity,
                strategy_name=name,
                strategy_version=version,
                created_at=require_utc(now),
                metadata_json=json.dumps(implementation, sort_keys=True),
            )
            session.add(row)
            session.flush()
            session.expunge(row)
            return row
