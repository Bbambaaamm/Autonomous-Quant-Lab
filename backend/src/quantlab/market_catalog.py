"""Immutable reference listings. A listing symbol is NOT a security identity."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, or_, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.domain import require_utc
from quantlab.persistence import Base

SOURCES = {
    "nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "otherlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
MAX_BYTES = 16 * 1024 * 1024
EXCHANGES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}


class CatalogError(ValueError):
    pass


class CatalogSnapshot(Base):
    __tablename__ = "market_catalog_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    sources_json: Mapped[str] = mapped_column(Text)
    listing_count: Mapped[int] = mapped_column(Integer)
    test_count: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(1000))


class CatalogListing(Base):
    __tablename__ = "market_catalog_listings"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("market_catalog_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    listing_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str] = mapped_column(String(64), index=True)
    security_type: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)


def parse_directory(source: str, body: bytes) -> tuple[list[dict[str, str]], str, int]:
    if source not in SOURCES or len(body) > MAX_BYTES:
        raise CatalogError("Neplatný zdroj nebo příliš velký katalog")
    try:
        lines = body.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise CatalogError("Katalog nemá platné kódování") from exc
    if len(lines) < 3 or not lines[-1].startswith("File Creation Time: "):
        raise CatalogError("Neúplný katalog: chybí závěrečný čas zdroje")
    stamp = lines[-1].split("|", 1)[0].removeprefix("File Creation Time: ").strip()
    try:
        datetime.strptime(stamp, "%m%d%Y%H:%M")
    except ValueError as exc:
        raise CatalogError("Neplatný čas zdroje") from exc
    reader = csv.DictReader(io.StringIO("\n".join(lines[:-1])), delimiter="|")
    symbol_key = "Symbol" if source == "nasdaqlisted" else "ACT Symbol"
    required = {symbol_key, "Security Name", "ETF", "Test Issue"}
    if source == "otherlisted":
        required.add("Exchange")
    if not required.issubset(reader.fieldnames or ()):
        raise CatalogError("Změněné schéma katalogu")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    tests = 0
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise CatalogError("Neúplný řádek katalogu")
        symbol = row[symbol_key].strip()
        name = row["Security Name"].strip()
        if not symbol or len(symbol) > 32 or not name or len(name) > 255:
            raise CatalogError("Neplatný symbol nebo název")
        if symbol in seen or row["Test Issue"] not in {"Y", "N"} or row["ETF"] not in {"Y", "N"}:
            raise CatalogError("Duplicitní symbol nebo neplatný příznak")
        seen.add(symbol)
        if row["Test Issue"] == "Y":
            tests += 1
            continue
        code = row.get("Exchange", "")
        if source == "otherlisted" and (not code or len(code) > 8):
            raise CatalogError("Neplatná burza")
        rows.append(
            {
                "listing_key": f"{source}:{symbol}",
                "symbol": symbol,
                "name": name,
                "exchange": "Nasdaq"
                if source == "nasdaqlisted"
                else EXCHANGES.get(code, f"Neznámá ({code})"),
                # Non-ETF includes preferred shares, warrants etc.; do not claim common stock.
                "security_type": "ETF" if row["ETF"] == "Y" else "OTHER_LISTED_SECURITY",
                "source": source,
                "payload_json": json.dumps(row, sort_keys=True),
            }
        )
    if not rows:
        raise CatalogError("Prázdný katalog")
    return rows, stamp, tests


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise CatalogError("Přesměrování zdroje katalogu není povoleno")


def download_directory(source: str) -> bytes:
    if source not in SOURCES:
        raise CatalogError("Neznámý zdroj katalogu")
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            SOURCES[source], timeout=15
        ) as response:
            body = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CatalogError(
            "Zdroj katalogu není dostupný; předchozí verze zůstává zachována"
        ) from exc
    if len(body) > MAX_BYTES:
        raise CatalogError("Zdroj překročil limit velikosti; katalog nebyl zkrácen ani uložen")
    return cast(bytes, body)


class MarketCatalogService:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def sync(
        self,
        *,
        actor: str,
        reason: str,
        fetch: Callable[[str], bytes] = download_directory,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> str:
        if not actor or len(actor) > 128 or not 3 <= len(reason.strip()) <= 1000:
            raise CatalogError("Katalog vyžaduje identitu a důvod aktualizace")
        rows: list[dict[str, str]] = []
        evidence: dict[str, dict[str, Any]] = {}
        test_count = 0
        # Both complete responses must validate before a transaction publishes anything.
        for source, url in SOURCES.items():
            body = fetch(source)
            parsed, stamp, tests = parse_directory(source, body)
            rows.extend(parsed)
            test_count += tests
            evidence[source] = {
                "url": url,
                "sha256": hashlib.sha256(body).hexdigest(),
                "source_time_local": stamp,
                "listings": len(parsed),
                "tests": tests,
            }
        received = require_utc(clock())  # receipt, never backdated to source file time
        for item in evidence.values():
            source_day = datetime.strptime(item["source_time_local"], "%m%d%Y%H:%M").date()
            if source_day > received.date() + timedelta(days=1):
                raise CatalogError("Zdroj uvádí budoucí datum")
        evidence_json = json.dumps(evidence, sort_keys=True)
        digest = hashlib.sha256(evidence_json.encode()).hexdigest()
        snapshot_id = hashlib.sha256(f"{digest}|{received.isoformat()}".encode()).hexdigest()
        with self.sessions() as session, session.begin():
            if session.get(CatalogSnapshot, snapshot_id) is not None:
                return snapshot_id
            session.add(
                CatalogSnapshot(
                    snapshot_id=snapshot_id,
                    received_at=received,
                    content_hash=digest,
                    sources_json=evidence_json,
                    listing_count=len(rows),
                    test_count=test_count,
                    actor=actor,
                    reason=reason.strip(),
                )
            )
            session.flush()
            session.add_all(CatalogListing(snapshot_id=snapshot_id, **row) for row in rows)
        return snapshot_id

    def read(
        self, now: datetime, *, query: str = "", offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        now = require_utc(now)
        if not 1 <= limit <= 200 or offset < 0 or len(query) > 100:
            raise CatalogError("Neplatné stránkování")
        with self.sessions() as session:
            snapshot = session.scalar(
                select(CatalogSnapshot)
                .where(CatalogSnapshot.received_at <= now)
                .order_by(CatalogSnapshot.received_at.desc(), CatalogSnapshot.snapshot_id.desc())
                .limit(1)
            )
            result: dict[str, Any] = {
                "scope": "Referenční katalog amerických burzovních cenných papírů",
                "global_coverage": "NOT_IMPLEMENTED",
                "price_coverage": None,
                "status": "NOT_SYNCED",
                "snapshot": None,
                "items": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
                "exchanges": [],
                "limitations": [
                    "Katalog neprokazuje dostupnost cen, úplnost historie ani obchodovatelnost.",
                    "Symbol není trvalá identita. Změny symbolů a delisting "
                    "vyžadují další referenční zdroj.",
                    "Historické složení trhu před prvním sběrem není doloženo.",
                    "Evropa, Asie a další regiony ani další třídy aktiv zatím nejsou pokryté.",
                ],
            }
            if snapshot is None:
                return result
            received = (
                snapshot.received_at.replace(tzinfo=UTC)
                if snapshot.received_at.tzinfo is None
                else snapshot.received_at
            )
            sources = json.loads(snapshot.sources_json)
            oldest_source = min(
                datetime.strptime(item["source_time_local"], "%m%d%Y%H:%M").date()
                for item in sources.values()
            )
            stale = now - received > timedelta(days=3) or now.date() - oldest_source > timedelta(
                days=3
            )
            result["status"] = "STALE" if stale else "RECEIVED"
            result["snapshot"] = {
                "id": snapshot.snapshot_id,
                "received_at": received,
                "listing_count": snapshot.listing_count,
                "test_count": snapshot.test_count,
                "sources": json.loads(snapshot.sources_json),
            }
            conditions = [CatalogListing.snapshot_id == snapshot.snapshot_id]
            if query.strip():
                conditions.append(
                    or_(
                        CatalogListing.symbol.icontains(query.strip(), autoescape=True),
                        CatalogListing.name.icontains(query.strip(), autoescape=True),
                    )
                )
            result["total"] = session.scalar(
                select(func.count()).select_from(CatalogListing).where(*conditions)
            )
            result["items"] = [
                {
                    "symbol": row.symbol,
                    "name": row.name,
                    "exchange": row.exchange,
                    "security_type": row.security_type,
                }
                for row in session.scalars(
                    select(CatalogListing)
                    .where(*conditions)
                    .order_by(CatalogListing.symbol, CatalogListing.listing_key)
                    .offset(offset)
                    .limit(limit)
                )
            ]
            result["exchanges"] = [
                {"exchange": exchange, "count": count}
                for exchange, count in session.execute(
                    select(CatalogListing.exchange, func.count())
                    .where(CatalogListing.snapshot_id == snapshot.snapshot_id)
                    .group_by(CatalogListing.exchange)
                    .order_by(CatalogListing.exchange)
                )
            ]
            return result
