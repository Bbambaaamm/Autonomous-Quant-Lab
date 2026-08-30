from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from importlib.metadata import version
from typing import Any, Protocol, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

import exchange_calendars

from quantlab.domain import require_utc


class ProviderError(RuntimeError):
    """Bezpečná základní chyba provideru bez response payloadu nebo credentials."""


class ProviderUnavailable(ProviderError):
    pass


class ProviderRateLimited(ProviderUnavailable):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Provider omezil rychlost požadavků")
        self.retry_after = retry_after


class InvalidProviderResponse(ProviderError):
    pass


class InvalidSymbol(ProviderError):
    pass


class InvalidMarketData(ValueError):
    pass


class DatasetInvalid(ValueError):
    pass


class AssetType(StrEnum):
    EQUITY = "EQUITY"


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    symbol: str
    exchange: str
    calendar: str
    currency: str
    asset_type: AssetType
    active_from: date
    active_to: date | None = None
    created_at: datetime = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class SymbolAlias:
    instrument_id: str
    symbol: str
    valid_from: date
    valid_to: date | None = None


@dataclass(frozen=True)
class ProviderMetadata:
    name: str
    version: str
    supports_actions: bool
    requires_credentials: bool


@dataclass(frozen=True)
class ProviderBar:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_id: str


class MarketDataProvider(Protocol):
    @property
    def metadata(self) -> ProviderMetadata: ...

    def resolve(self, symbol: str) -> dict[str, str]: ...

    def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]: ...

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]: ...


class XNYSCalendar:
    audited_start = date(1970, 1, 1)
    audited_end = date(2100, 12, 31)
    timezone = ZoneInfo("America/New_York")
    executable_open_window = timedelta(seconds=1)

    def __init__(self) -> None:
        library_version = version("exchange-calendars")
        self.identity = f"XNYS:exchange-calendars:{library_version}"
        self._calendar = exchange_calendars.get_calendar(
            "XNYS", start=self.audited_start.isoformat(), end=self.audited_end.isoformat()
        )

    def _validate_day(self, day: date) -> None:
        if not self.audited_start <= day <= self.audited_end:
            raise ValueError("Datum je mimo auditované období kalendáře")

    def is_session(self, day: date) -> bool:
        self._validate_day(day)
        return bool(self._calendar.is_session(day.isoformat()))

    def session_open(self, day: date) -> datetime:
        if not self.is_session(day):
            raise ValueError("Datum není burzovní session")
        opened = self._calendar.session_open(day.isoformat()).to_pydatetime().astimezone(UTC)
        return cast(datetime, opened)

    def session_close(self, day: date) -> datetime:
        if not self.is_session(day):
            raise ValueError("Datum není burzovní session")
        closed = self._calendar.session_close(day.isoformat()).to_pydatetime().astimezone(UTC)
        return cast(datetime, closed)

    def executable_open_cutoff(self, day: date) -> datetime:
        """Vrátí exkluzivní konec krátkého okna pro kauzální raw-open execution."""
        return self.session_open(day) + self.executable_open_window

    def is_executable_open_time(self, day: date, timestamp: datetime) -> bool:
        """Ověří, že skutečný knowledge/run čas leží v krátkém XNYS open okně."""
        value = require_utc(timestamp)
        return self.session_open(day) <= value < self.executable_open_cutoff(day)

    def session_for_timestamp(self, timestamp: datetime) -> date | None:
        value = require_utc(timestamp)
        local = value.astimezone(self.timezone)
        return (
            local.date()
            if self.is_session(local.date())
            and self.session_open(local.date()) <= value <= self.session_close(local.date())
            else None
        )

    def next_session(self, day: date) -> date:
        if day >= self.audited_end:
            raise ValueError("Následující session je mimo auditované období kalendáře")
        self._validate_day(day)
        session = self._calendar.date_to_session(
            (day + timedelta(days=1)).isoformat(), direction="next"
        ).date()
        return cast(date, session)

    def previous_session(self, day: date) -> date:
        if day <= self.audited_start:
            raise ValueError("Předchozí session je mimo auditované období kalendáře")
        self._validate_day(day)
        session = self._calendar.date_to_session(
            (day - timedelta(days=1)).isoformat(), direction="previous"
        ).date()
        return cast(date, session)

    def sessions_between(self, start: date, end: date) -> tuple[date, ...]:
        if start > end:
            raise ValueError("Začátek rozsahu kalendáře musí být nejpozději na konci")
        self._validate_day(start)
        self._validate_day(end)
        return tuple(
            session.date()
            for session in self._calendar.sessions_in_range(start.isoformat(), end.isoformat())
        )

    def latest_completed_session(self, now: datetime) -> date:
        value = require_utc(now)
        local_day = value.astimezone(self.timezone).date()
        if self.is_session(local_day) and value >= self.session_close(local_day):
            return local_day
        return self.previous_session(local_day)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    instrument_id: str
    provider: str
    timeframe: str
    session_date: date
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    observed_at: datetime
    source_id: str
    source_hash: str
    ingestion_id: str
    revision: int = 1


def normalize_bar(
    bar: ProviderBar,
    instrument: Instrument,
    provider: str,
    observed_at: datetime,
    ingestion_id: str,
    calendar: XNYSCalendar,
) -> Observation:
    if not calendar.is_session(bar.session_date):
        raise InvalidMarketData("Provider bar neleží v platné session")
    values = (bar.open, bar.high, bar.low, bar.close)
    if (
        any(not value.is_finite() or value <= 0 for value in values)
        or not bar.volume.is_finite()
        or bar.volume < 0
    ):
        raise InvalidMarketData("Ceny musí být konečné a kladné a volume nezáporný")
    if bar.high < max(*values) or bar.low > min(*values):
        raise InvalidMarketData("Provider bar porušuje OHLC invariant")
    timeframe = "1d"
    payload = "|".join(
        map(
            str,
            (
                instrument.instrument_id,
                provider,
                timeframe,
                bar.session_date,
                *values,
                bar.volume,
                bar.source_id,
            ),
        )
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return Observation(
        digest,
        instrument.instrument_id,
        provider,
        timeframe,
        bar.session_date,
        calendar.session_close(bar.session_date),
        *values,
        bar.volume,
        require_utc(observed_at),
        bar.source_id,
        digest,
        ingestion_id,
    )


class CorporateActionKind(StrEnum):
    SPLIT = "SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    DELISTING = "DELISTING"


@dataclass(frozen=True)
class CorporateAction:
    action_id: str
    instrument_id: str
    kind: CorporateActionKind
    effective_at: datetime
    known_at: datetime
    value: Decimal | None = None
    new_symbol: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_at", require_utc(self.effective_at))
        object.__setattr__(self, "known_at", require_utc(self.known_at))
        if self.kind in {CorporateActionKind.SPLIT, CorporateActionKind.CASH_DIVIDEND} and (
            self.value is None or self.value <= 0
        ):
            raise ValueError("Split/dividend vyžaduje kladnou hodnotu")


class CorporateActionEventType(StrEnum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class CorporateActionEvent:
    """Neměnná evidence doručení změny corporate action z provider streamu."""

    event_id: str
    at: datetime
    action: CorporateActionEventType
    provider_action_id: str
    payload_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", require_utc(self.at))
        if not self.event_id or not self.provider_action_id:
            raise InvalidProviderResponse("Corporate-action event nemá stabilní identitu")
        if len(self.payload_hash) != 64:
            raise InvalidProviderResponse("Corporate-action event nemá platný payload hash")

    @classmethod
    def from_sse(cls, payload: bytes) -> CorporateActionEvent:
        """Normalizuje minimální Alpaca SSE envelope a připne přesné přijaté bajty."""
        try:
            envelope = json.loads(payload)
            if not isinstance(envelope, dict):
                raise TypeError
            return cls(
                event_id=str(envelope["event_id"]),
                at=datetime.fromisoformat(str(envelope["at"]).replace("Z", "+00:00")),
                action=CorporateActionEventType(str(envelope["action"])),
                provider_action_id=str(envelope["corporate_action_id"]),
                payload_hash=hashlib.sha256(payload).hexdigest(),
            )
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidProviderResponse("Alpaca SSE event nemá platný envelope") from exc


AlpacaTransport = Callable[[str, dict[str, str], float], tuple[int, dict[str, str], bytes]]
ActionEvidenceLoader = Callable[[str], tuple[CorporateActionEvent, ...]]


class AlpacaProvider:
    """REST adapter, který odvozuje ``known_at`` výhradně z persistentní SSE evidence."""

    metadata = ProviderMetadata("alpaca", "1", True, True)
    _base_url = "https://data.alpaca.markets"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        evidence_loader: ActionEvidenceLoader,
        instrument_ids: dict[str, str],
        transport: AlpacaTransport,
        timeout: float = 10,
    ) -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca credentials jsou povinné")
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._evidence_loader = evidence_loader
        self._instrument_ids = {key.upper(): value for key, value in instrument_ids.items()}
        self._transport = transport
        self._timeout = timeout

    def resolve(self, symbol: str) -> dict[str, str]:
        normalized = symbol.strip().upper()
        if normalized not in self._instrument_ids:
            raise InvalidSymbol("Symbol není v explicitní Alpaca instrument mapě")
        return {"symbol": normalized, "provider_symbol": normalized}

    def _get(self, path: str, query: dict[str, str]) -> dict[str, Any]:
        url = f"{self._base_url}{path}?{urllib.parse.urlencode(query)}"
        status, _, body = self._transport(url, self._headers.copy(), self._timeout)
        if status == 429:
            raise ProviderRateLimited()
        if status >= 500:
            raise ProviderUnavailable("Dočasná chyba Alpaca provideru")
        if status != 200:
            raise InvalidProviderResponse("Alpaca požadavek nebyl úspěšný")
        try:
            payload = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidProviderResponse("Alpaca vrátila neplatné JSON") from exc
        if not isinstance(payload, dict):
            raise InvalidProviderResponse("Alpaca JSON nemá objektový kořen")
        return cast(dict[str, Any], payload)

    def _get_all(self, path: str, query: dict[str, str], collection: str) -> list[Any]:
        """Vyčerpá stránkování; částečná odpověď nikdy nesmí dokazovat úplnost."""
        rows: list[Any] = []
        tokens: set[str] = set()
        while True:
            payload = self._get(path, query)
            page = payload.get(collection)
            if not isinstance(page, list):
                raise InvalidProviderResponse(f"Alpaca odpověď neobsahuje {collection}")
            rows.extend(page)
            token = payload.get("next_page_token")
            if token is None:
                return rows
            if not isinstance(token, str) or not token or token in tokens:
                raise InvalidProviderResponse("Alpaca vrátila neplatné stránkování")
            tokens.add(token)
            query = {**query, "page_token": token}

    def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]:
        provider_symbol = self.resolve(symbol)["provider_symbol"]
        rows = self._get_all(
            f"/v2/stocks/{urllib.parse.quote(provider_symbol, safe='')}/bars",
            {
                "timeframe": "1Day",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "adjustment": "raw",
            },
            "bars",
        )
        try:
            return [
                ProviderBar(
                    datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).date(),
                    Decimal(str(row["o"])),
                    Decimal(str(row["h"])),
                    Decimal(str(row["l"])),
                    Decimal(str(row["c"])),
                    Decimal(str(row["v"])),
                    str(row["t"]),
                )
                for row in rows
                if start
                <= datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).date()
                <= end
            ]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise InvalidProviderResponse("Alpaca vrátila neplatný OHLCV bar") from exc

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        normalized = self.resolve(symbol)["provider_symbol"]
        rows = self._get_all(
            "/v1/corporate-actions",
            {"symbols": normalized, "start": start.isoformat(), "end": end.isoformat()},
            "corporate_actions",
        )
        events = self._evidence_loader("alpaca")
        latest: dict[str, CorporateActionEvent] = {}
        for event in sorted(events, key=lambda item: (item.at, item.event_id)):
            latest[event.provider_action_id] = event
        result: list[CorporateAction] = []
        kinds = {
            "split": CorporateActionKind.SPLIT,
            "cash_dividend": CorporateActionKind.CASH_DIVIDEND,
            "symbol_change": CorporateActionKind.SYMBOL_CHANGE,
            "delisting": CorporateActionKind.DELISTING,
        }
        for row in rows:
            try:
                action_id = str(row["id"])
                evidence = latest.get(action_id)
                if evidence is None:
                    raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
                if evidence.action is CorporateActionEventType.DELETE:
                    # Aktuální REST fakt po delete neumí rekonstruovat předchozí verzi.
                    raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
                kind = kinds[str(row["type"])]
                effective = datetime.fromisoformat(str(row["effective_at"]).replace("Z", "+00:00"))
                value = Decimal(str(row["value"])) if row.get("value") is not None else None
                result.append(
                    CorporateAction(
                        action_id,
                        self._instrument_ids[normalized],
                        kind,
                        effective,
                        evidence.at,
                        value,
                        row.get("new_symbol"),
                    )
                )
            except DatasetInvalid:
                raise
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                raise InvalidProviderResponse("Alpaca vrátila neplatnou corporate action") from exc
        return result


def causal_adjusted_close(
    observations: Iterable[Observation], actions: Iterable[CorporateAction], as_of: datetime
) -> dict[date, Decimal]:
    """Adjustuje pouze akcemi známými a účinnými nejpozději v rozhodném čase."""
    cutoff = require_utc(as_of)
    known = [a for a in actions if a.known_at <= cutoff and a.effective_at <= cutoff]
    result: dict[date, Decimal] = {}
    for observation in observations:
        price = observation.close
        for action in known:
            if (
                action.instrument_id == observation.instrument_id
                and observation.timestamp < action.effective_at
            ):
                if action.kind is CorporateActionKind.SPLIT:
                    price /= action.value or Decimal("1")
                elif action.kind is CorporateActionKind.CASH_DIVIDEND:
                    price -= action.value or Decimal("0")
        result[observation.session_date] = price
    return result


Transport = Callable[[str, float], tuple[int, dict[str, str], bytes]]


def _validate_stooq_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "stooq.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ProviderUnavailable("Transport dovoluje pouze HTTPS endpoint stooq.com")


class _StooqRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_stooq_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class StooqProvider:
    """Auditovatelný CSV adapter; transport lze ve fixture testech plně nahradit."""

    metadata = ProviderMetadata("stooq", "1", False, False)

    def __init__(
        self, transport: Transport | None = None, timeout: float = 10, max_attempts: int = 3
    ) -> None:
        self.transport = transport or self._http
        self.timeout, self.max_attempts = timeout, max_attempts

    @staticmethod
    def _http(url: str, timeout: float) -> tuple[int, dict[str, str], bytes]:
        _validate_stooq_url(url)
        opener = urllib.request.build_opener(_StooqRedirectHandler())
        try:
            # Každý redirect znovu prochází stejným allowlistem schématu a hostu.
            with opener.open(url, timeout=timeout) as response:  # noqa: S310
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), b""
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderUnavailable("Provider není dostupný") from exc

    def resolve(self, symbol: str) -> dict[str, str]:
        normalized = symbol.strip().upper()
        if not normalized.isascii() or not normalized.replace("-", "").isalnum():
            raise InvalidSymbol("Symbol má neplatný formát")
        return {"symbol": normalized, "provider_symbol": f"{normalized.lower()}.us"}

    def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]:
        if start > end:
            raise ValueError("Počáteční datum musí předcházet koncovému")
        provider_symbol = self.resolve(symbol)["provider_symbol"]
        query = urllib.parse.urlencode(
            {
                "s": provider_symbol,
                "d1": start.strftime("%Y%m%d"),
                "d2": end.strftime("%Y%m%d"),
                "i": "d",
            }
        )
        url = f"https://stooq.com/q/d/l/?{query}"
        for attempt in range(self.max_attempts):
            try:
                status, headers, body = self.transport(url, self.timeout)
                if status == 429:
                    retry = float(headers.get("Retry-After", "0") or 0)
                    error: ProviderError = ProviderRateLimited(retry)
                elif status >= 500:
                    error = ProviderUnavailable("Dočasná chyba provideru")
                elif status != 200:
                    raise InvalidSymbol("Provider symbol odmítl")
                else:
                    return self._parse(body, start, end)
            except ProviderUnavailable as exc:
                error = exc
            if attempt + 1 == self.max_attempts:
                raise error
            time.sleep(min(getattr(error, "retry_after", 0) or 0.05 * 2**attempt, 1))
        raise AssertionError("Nedosažitelný stav")

    @staticmethod
    def _parse(body: bytes, start: date, end: date) -> list[ProviderBar]:
        try:
            text = body.decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
            if not rows:
                raise InvalidSymbol("Provider nevrátil data pro symbol")
            bars = [
                ProviderBar(
                    date.fromisoformat(r["Date"]),
                    Decimal(r["Open"]),
                    Decimal(r["High"]),
                    Decimal(r["Low"]),
                    Decimal(r["Close"]),
                    Decimal(r["Volume"]),
                    f"stooq:{r['Date']}",
                )
                for r in rows
            ]
        except (InvalidOperation, KeyError, ValueError, UnicodeError) as exc:
            raise InvalidProviderResponse("Provider vrátil neplatné CSV") from exc
        if len({bar.session_date for bar in bars}) != len(bars):
            raise InvalidProviderResponse("Provider vrátil duplicitní bary")
        return sorted(
            (bar for bar in bars if start <= bar.session_date <= end), key=lambda b: b.session_date
        )

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        self.resolve(symbol)
        return []


@dataclass(frozen=True)
class IngestionResult:
    ingestion_id: str
    requested_start: date
    requested_end: date
    status: str
    observations: tuple[Observation, ...]
    error: str | None = None


class InMemoryObservationStore:
    """Referenční immutable revision store; SQL varianta používá stejné identity."""

    def __init__(self) -> None:
        self._rows: list[Observation] = []

    def ingest(
        self,
        provider: MarketDataProvider,
        instrument: Instrument,
        start: date,
        end: date,
        observed_at: datetime,
        calendar: XNYSCalendar,
        overlap_days: int = 5,
    ) -> IngestionResult:
        if overlap_days < 0:
            raise ValueError("Overlap nesmí být záporný")
        ingestion_id = str(uuid4())
        current = [
            r
            for r in self._rows
            if r.instrument_id == instrument.instrument_id and r.provider == provider.metadata.name
        ]
        actual_start = max(
            start,
            max((r.session_date for r in current), default=start) - timedelta(days=overlap_days),
        )
        try:
            incoming = [
                normalize_bar(
                    b, instrument, provider.metadata.name, observed_at, ingestion_id, calendar
                )
                for b in provider.historical_daily(instrument.symbol, actual_start, end)
            ]
            added: list[Observation] = []
            for row in incoming:
                versions = [
                    old
                    for old in self._rows
                    if old.instrument_id == row.instrument_id
                    and old.provider == row.provider
                    and old.session_date == row.session_date
                ]
                if versions and versions[-1].source_hash == row.source_hash:
                    continue
                revised = replace(row, revision=len(versions) + 1)
                self._rows.append(revised)
                added.append(revised)
            return IngestionResult(ingestion_id, actual_start, end, "SUCCEEDED", tuple(added))
        except (ProviderError, InvalidMarketData) as exc:
            return IngestionResult(ingestion_id, actual_start, end, "FAILED", (), str(exc))

    def as_known_at(self, instant: datetime) -> tuple[Observation, ...]:
        cutoff = require_utc(instant)
        candidates = [r for r in self._rows if r.observed_at <= cutoff]
        latest: dict[tuple[str, str, date], Observation] = {}
        for row in candidates:
            key = row.instrument_id, row.provider, row.session_date
            if key not in latest or (row.observed_at, row.revision) > (
                latest[key].observed_at,
                latest[key].revision,
            ):
                latest[key] = row
        return tuple(sorted(latest.values(), key=lambda r: (r.timestamp, r.instrument_id)))


@dataclass(frozen=True)
class DatasetSnapshot:
    snapshot_id: str
    created_at: datetime
    as_of: datetime
    provider: str
    calendar_identity: str
    universe_id: str
    start: date
    end: date
    timeframe: str
    content_hash: str
    observation_ids: tuple[str, ...]
    status: str
    coverage: Decimal


def build_snapshot(
    store: InMemoryObservationStore,
    *,
    as_of: datetime,
    provider: str,
    calendar_identity: str,
    universe_id: str,
    instrument_ids: Iterable[str],
    start: date,
    end: date,
    minimum_coverage: Decimal = Decimal("0.8"),
    calendar: XNYSCalendar | None = None,
) -> DatasetSnapshot:
    ids = frozenset(instrument_ids)
    rows = tuple(
        r
        for r in store.as_known_at(as_of)
        if r.instrument_id in ids and r.provider == provider and start <= r.session_date <= end
    )
    active_calendar = calendar or XNYSCalendar()
    sessions = active_calendar.sessions_between(start, end)
    expected = len(ids) * len(sessions)
    present = {(r.instrument_id, r.session_date) for r in rows}
    coverage = Decimal(len(present)) / Decimal(expected) if expected else Decimal("1")
    canonical = [
        {"id": r.observation_id, "revision": r.revision, "hash": r.source_hash}
        for r in sorted(rows, key=lambda r: (r.instrument_id, r.session_date))
    ]
    content_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identity = hashlib.sha256(
        f"{provider}|{calendar_identity}|{universe_id}|{start}|{end}|{as_of.isoformat()}|{content_hash}".encode()
    ).hexdigest()
    return DatasetSnapshot(
        identity,
        datetime.now(UTC),
        require_utc(as_of),
        provider,
        calendar_identity,
        universe_id,
        start,
        end,
        "1d",
        content_hash,
        tuple(str(item["id"]) for item in canonical),
        "VALID" if coverage >= minimum_coverage else "INVALID",
        coverage,
    )
