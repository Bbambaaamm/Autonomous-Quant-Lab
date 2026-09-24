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

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, aliased, mapped_column

from quantlab.config import Settings
from quantlab.domain import require_utc
from quantlab.market_catalog import CatalogError, NoRedirect
from quantlab.market_data_service import _database_utc, _lock
from quantlab.persistence import Base

ASSET_URLS = {
    "active": "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity",
    "inactive": "https://paper-api.alpaca.markets/v2/assets?status=inactive&asset_class=us_equity",
}
MAX_ASSET_BYTES = 32 * 1024 * 1024
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
    status: Mapped[str] = mapped_column(String(16))
    payload_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (Index("ix_asset_directory_entries_status", "snapshot_id", "status"),)


class AssetDirectorySnapshotMetric(Base):
    """Immutable read model pro O(1) dashboard summary asset directory."""

    __tablename__ = "asset_directory_snapshot_metrics"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    previous_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("asset_directory_snapshots.snapshot_id", ondelete="RESTRICT")
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[int] = mapped_column(Integer, nullable=False)
    inactive: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen: Mapped[int] = mapped_column(Integer, nullable=False)
    no_longer_present: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol_or_venue_changed: Mapped[int] = mapped_column(Integer, nullable=False)
    status_changed: Mapped[int] = mapped_column(Integer, nullable=False)
    became_inactive: Mapped[int] = mapped_column(Integer, nullable=False)
    became_active: Mapped[int] = mapped_column(Integer, nullable=False)


def parse_assets(body: bytes) -> list[dict[str, str]]:
    try:
        data = json.loads(body)
        if not isinstance(data, list) or not data:
            raise ValueError
        ids: set[str] = set()
        active_symbols: set[str] = set()
        output = []
        for row in data:
            identity = str(UUID(row["id"]))
            symbol = row["symbol"]
            name = row["name"]
            exchange = row["exchange"]
            status = row["status"]
            if (
                identity in ids
                or not isinstance(symbol, str)
                or not 1 <= len(symbol) <= 32
                or not isinstance(name, str)
                or len(name) > 255
                or (status == "active" and not name.strip())
                or not isinstance(exchange, str)
                or not 1 <= len(exchange) <= 32
                or row["class"] != "us_equity"
                or status not in {"active", "inactive"}
                or (status == "active" and symbol in active_symbols)
            ):
                raise ValueError
            ids.add(identity)
            if status == "active":
                active_symbols.add(symbol)
            output.append(
                {
                    "asset_id": identity,
                    "symbol": symbol,
                    "name": name,
                    "exchange": exchange,
                    "status": status,
                    "payload_json": json.dumps(row, sort_keys=True),
                }
            )
        return output
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise CatalogError("Neplatný nebo neúplný adresář identit Alpaca") from exc


def fetch_assets(settings: Settings) -> bytes:
    """Fetch both active and inactive US equity identities without hiding lifecycle state."""
    if not settings.alpaca_key_id or not settings.alpaca_secret_key:
        raise CatalogError("Na serveru chybí přístupové údaje Alpaca")
    combined: list[dict[str, Any]] = []
    for status, url in ASSET_URLS.items():
        if url not in ASSET_URLS.values():
            raise CatalogError("Adresář identit používá nepovolený endpoint")
        # The exact HTTPS URL is selected exclusively from the module-level allowlist.
        request = urllib.request.Request(  # noqa: S310
            url,
            method="GET",
            headers={
                "APCA-API-KEY-ID": settings.alpaca_key_id,
                "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
                body: bytes = response.read(MAX_ASSET_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise CatalogError(
                f"Adresář identit {status} odmítl požadavek (HTTP {exc.code})"
            ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CatalogError(f"Adresář identit {status} není dostupný") from exc
        if len(body) > MAX_ASSET_BYTES:
            raise CatalogError("Adresář překročil limit; neúplná odpověď nebyla uložena")
        try:
            rows = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogError("Adresář identit neobsahuje platné JSON") from exc
        if not isinstance(rows, list) or not rows:
            raise CatalogError("Adresář identit je prázdný nebo má neplatný tvar")
        if any(not isinstance(row, dict) or row.get("status") != status for row in rows):
            raise CatalogError("Provider vrátil jiný lifecycle status než požadovaný")
        combined.extend(rows)
    return json.dumps(combined, sort_keys=True, separators=(",", ":")).encode()


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
            session.flush()
            previous = session.scalar(
                select(AssetDirectorySnapshot)
                .where(
                    AssetDirectorySnapshot.received_at < received,
                    AssetDirectorySnapshot.snapshot_id != identity,
                )
                .order_by(AssetDirectorySnapshot.received_at.desc())
                .limit(1)
            )
            session.add(
                self._summary_metric(
                    session,
                    identity,
                    previous.snapshot_id if previous is not None else None,
                    rows,
                )
            )
        return identity

    @staticmethod
    def _summary_metric(
        session: Session,
        snapshot_id: str,
        previous_snapshot_id: str | None,
        parsed_rows: list[dict[str, str]],
    ) -> AssetDirectorySnapshotMetric:
        total = len(parsed_rows)
        active = sum(row["status"] == "active" for row in parsed_rows)
        inactive = total - active
        if previous_snapshot_id is None:
            return AssetDirectorySnapshotMetric(
                snapshot_id=snapshot_id,
                previous_snapshot_id=None,
                total=total,
                active=active,
                inactive=inactive,
                first_seen=total,
                no_longer_present=0,
                symbol_or_venue_changed=0,
                status_changed=0,
                became_inactive=0,
                became_active=0,
            )

        current = aliased(AssetDirectoryEntry)
        previous = aliased(AssetDirectoryEntry)
        first_seen = session.scalar(
            select(func.count())
            .select_from(current)
            .outerjoin(
                previous,
                (previous.snapshot_id == previous_snapshot_id)
                & (previous.asset_id == current.asset_id),
            )
            .where(current.snapshot_id == snapshot_id, previous.asset_id.is_(None))
        )
        no_longer = session.scalar(
            select(func.count())
            .select_from(previous)
            .outerjoin(
                current,
                (current.snapshot_id == snapshot_id) & (current.asset_id == previous.asset_id),
            )
            .where(previous.snapshot_id == previous_snapshot_id, current.asset_id.is_(None))
        )
        changed = session.execute(
            select(
                func.sum(
                    func.cast(
                        (current.symbol != previous.symbol)
                        | (current.exchange != previous.exchange),
                        Integer,
                    )
                ),
                func.sum(func.cast(current.status != previous.status, Integer)),
                func.sum(
                    func.cast(
                        (previous.status == "active") & (current.status == "inactive"),
                        Integer,
                    )
                ),
                func.sum(
                    func.cast(
                        (previous.status == "inactive") & (current.status == "active"),
                        Integer,
                    )
                ),
            )
            .select_from(current)
            .join(
                previous,
                (previous.snapshot_id == previous_snapshot_id)
                & (previous.asset_id == current.asset_id),
            )
            .where(current.snapshot_id == snapshot_id)
        ).one()
        return AssetDirectorySnapshotMetric(
            snapshot_id=snapshot_id,
            previous_snapshot_id=previous_snapshot_id,
            total=total,
            active=active,
            inactive=inactive,
            first_seen=int(first_seen or 0),
            no_longer_present=int(no_longer or 0),
            symbol_or_venue_changed=int(changed[0] or 0),
            status_changed=int(changed[1] or 0),
            became_inactive=int(changed[2] or 0),
            became_active=int(changed[3] or 0),
        )

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
            metric = session.get(AssetDirectorySnapshotMetric, row.snapshot_id)
            if metric is None:
                raise CatalogError("ASSET_DIRECTORY_SUMMARY_MISSING")
            previous_received_at = None
            if metric.previous_snapshot_id is not None:
                previous_received_at = session.scalar(
                    select(AssetDirectorySnapshot.received_at).where(
                        AssetDirectorySnapshot.snapshot_id == metric.previous_snapshot_id
                    )
                )
                if previous_received_at is None:
                    raise CatalogError("ASSET_DIRECTORY_PREVIOUS_SNAPSHOT_MISSING")
                previous_received_at = _database_utc(previous_received_at)
            changes = (
                {
                    "first_seen": metric.first_seen,
                    "no_longer_present": metric.no_longer_present,
                    "symbol_or_venue_changed": metric.symbol_or_venue_changed,
                    "status_changed": metric.status_changed,
                    "became_inactive": metric.became_inactive,
                    "became_active": metric.became_active,
                    "previous_received_at": previous_received_at,
                }
                if metric.previous_snapshot_id is not None
                else None
            )
            return {
                "snapshot_id": row.snapshot_id,
                "received_at": _database_utc(row.received_at),
                "total": metric.total,
                "active": metric.active,
                "inactive": metric.inactive,
                "changes": changes,
            }
