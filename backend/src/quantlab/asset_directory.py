"""Read-only provider identities; never infer a permanent identity from a ticker."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.config import Settings
from quantlab.domain import require_utc
from quantlab.market_catalog import CatalogError, NoRedirect
from quantlab.market_data_service import _lock
from quantlab.persistence import Base

ASSET_URL = "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"
VENUES = {
    "NYSE": "XNYS",
    "NASDAQ": "XNAS",
    "AMEX": "XASE",
    "ARCA": "ARCX",
    "BATS": "BATS",
    "NYSEARCA": "ARCX",
}


class AssetDirectorySnapshot(Base):
    __tablename__ = "asset_directory_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(1000))


class AssetDirectoryEntry(Base):
    __tablename__ = "asset_directory_entries"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    exchange: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[str] = mapped_column(Text)


def parse_assets(body: bytes) -> list[dict[str, str]]:
    try:
        data = json.loads(body)
        if not isinstance(data, list) or not data:
            raise ValueError
        ids: set[str] = set()
        symbols: set[str] = set()
        output = []
        for row in data:
            identity = str(UUID(row["id"]))
            symbol = row["symbol"]
            name = row["name"]
            exchange = row["exchange"]
            if (
                identity in ids
                or symbol in symbols
                or not isinstance(symbol, str)
                or not 1 <= len(symbol) <= 32
                or not isinstance(name, str)
                or not 1 <= len(name) <= 255
                or not isinstance(exchange, str)
                or not 1 <= len(exchange) <= 32
                or row["class"] != "us_equity"
                or row["status"] != "active"
            ):
                raise ValueError
            ids.add(identity)
            symbols.add(symbol)
            output.append(
                {
                    "asset_id": identity,
                    "symbol": symbol,
                    "name": name,
                    "exchange": exchange,
                    "payload_json": json.dumps(row, sort_keys=True),
                }
            )
        return output
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CatalogError("Neplatný nebo neúplný adresář identit Alpaca") from exc


def fetch_assets(settings: Settings) -> bytes:
    if not settings.alpaca_key_id or not settings.alpaca_secret_key:
        raise CatalogError("Na serveru chybí přístupové údaje Alpaca")
    request = urllib.request.Request(
        ASSET_URL,
        method="GET",
        headers={
            "APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            body: bytes = response.read(32 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise CatalogError(f"Adresář identit odmítl požadavek (HTTP {exc.code})") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CatalogError("Adresář identit není dostupný") from exc
    if len(body) > 32 * 1024 * 1024:
        raise CatalogError("Adresář překročil limit; neúplná odpověď nebyla uložena")
    return body


class AssetDirectoryService:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def sync(self, body: bytes, *, actor: str, reason: str, received_at: datetime) -> str:
        rows = parse_assets(body)
        received = require_utc(received_at)
        if not actor or len(actor) > 128 or not 3 <= len(reason.strip()) <= 1000:
            raise CatalogError("Chybí platná identita nebo důvod")
        digest = hashlib.sha256(body).hexdigest()
        identity = hashlib.sha256(f"alpaca|{digest}|{received.isoformat()}".encode()).hexdigest()
        with self.sessions() as session, session.begin():
            _lock(session, f"asset-directory:{identity}")
            if session.get(AssetDirectorySnapshot, identity):
                return identity
            session.add(
                AssetDirectorySnapshot(
                    snapshot_id=identity,
                    received_at=received,
                    content_hash=digest,
                    actor=actor,
                    reason=reason,
                )
            )
            session.flush()
            session.add_all(AssetDirectoryEntry(snapshot_id=identity, **row) for row in rows)
        return identity

    def latest(self, now: datetime) -> dict[str, Any]:
        with self.sessions() as session:
            row = session.scalar(
                select(AssetDirectorySnapshot)
                .where(AssetDirectorySnapshot.received_at <= require_utc(now))
                .order_by(AssetDirectorySnapshot.received_at.desc())
                .limit(1)
            )
            if row is None:
                return {"snapshot_id": None, "received_at": None}
            return {"snapshot_id": row.snapshot_id, "received_at": row.received_at}
