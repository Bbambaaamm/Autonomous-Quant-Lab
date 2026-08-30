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
    lineage: str | None = None

    @property
    def persistent_name(self) -> str:
        """Vrátí identitu zdroje, která nesmí sloučit odlišné datové lineage."""
        return self.lineage or self.name


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


def corporate_action_logical_id(provider: str, provider_action_id: str) -> str:
    """Mapuje libovolně dlouhou provider identitu na stabilní interní 64znakový klíč."""
    if not provider or not provider_action_id:
        raise InvalidProviderResponse("Corporate action nemá stabilní provider identitu")
    if len(provider_action_id) > 128:
        raise InvalidProviderResponse("Provider corporate-action ID překračuje limit")
    return hashlib.sha256(f"{provider}:{provider_action_id}".encode()).hexdigest()


@dataclass(frozen=True)
class CorporateAction:
    action_id: str
    instrument_id: str
    kind: CorporateActionKind
    effective_at: datetime
    known_at: datetime
    value: Decimal | None = None
    new_symbol: str | None = None
    provider_action_id: str | None = None
    payload_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_at", require_utc(self.effective_at))
        object.__setattr__(self, "known_at", require_utc(self.known_at))
        if not self.action_id or len(self.action_id) > 64:
            raise ValueError("Corporate action má neplatnou interní identitu")
        if self.provider_action_id is not None and (
            not self.provider_action_id or len(self.provider_action_id) > 128
        ):
            raise ValueError("Corporate action má neplatnou provider identitu")
        if self.payload_hash is not None and len(self.payload_hash) != 64:
            raise ValueError("Corporate action má neplatný payload hash")
        if self.kind in {CorporateActionKind.SPLIT, CorporateActionKind.CASH_DIVIDEND} and (
            self.value is None or not self.value.is_finite() or self.value <= 0
        ):
            raise ValueError("Split/dividend vyžaduje konečnou kladnou hodnotu")


class CorporateActionEventType(StrEnum):
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"


def _canonical_provider_value(value: Any) -> Any:
    """Sjednotí REST number a SSE decimal-string reprezentace pro stabilní hash CA verze."""
    if isinstance(value, dict):
        return {
            str(key): _canonical_provider_value(item)
            for key, item in sorted(value.items())
            if item is not None
        }
    if isinstance(value, list):
        return [_canonical_provider_value(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        decimal = Decimal(str(value))
        return format(decimal.normalize(), "f")
    return value


def canonical_corporate_action_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        _canonical_provider_value(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class CorporateActionEvent:
    """Neměnná evidence konkrétní verze corporate action doručené Alpaca SSE."""

    event_id: str
    at: datetime
    action: CorporateActionEventType
    provider_action_id: str
    payload_hash: str
    received_at: datetime | None = None
    symbols: tuple[str, ...] = ()
    scope_date: date | None = None

    def __post_init__(self) -> None:
        provider_at = require_utc(self.at)
        received_at = require_utc(self.received_at or provider_at)
        object.__setattr__(self, "at", provider_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(
            self,
            "symbols",
            tuple(sorted({symbol.strip().upper() for symbol in self.symbols if symbol.strip()})),
        )
        if not self.event_id or len(self.event_id) > 128 or not self.provider_action_id:
            raise InvalidProviderResponse("Corporate-action event nemá stabilní identitu")
        if len(self.provider_action_id) > 128:
            raise InvalidProviderResponse("Corporate-action provider ID překračuje limit")
        if len(self.payload_hash) != 64:
            raise InvalidProviderResponse("Corporate-action event nemá platný payload hash")

    @classmethod
    def from_sse(
        cls, payload: bytes, *, received_at: datetime | None = None
    ) -> CorporateActionEvent:
        """Parsuje dokumentovaný Alpaca envelope a oddělí provider time od receipt time."""
        try:
            envelope = json.loads(payload)
            if not isinstance(envelope, dict):
                raise TypeError
            event_type = envelope["event_type"]
            ca = envelope["ca"]
            region = envelope["region"]
            if not isinstance(event_type, str) or not event_type.endswith("_corporateaction_event"):
                raise TypeError
            if not isinstance(ca, dict) or region not in {"us", "non_us"}:
                raise TypeError
            action_id = ca["id"]
            if not isinstance(action_id, str) or not action_id:
                raise TypeError
            provider_at = datetime.fromisoformat(str(envelope["at"]).replace("Z", "+00:00"))
            symbols = tuple(
                str(ca[key])
                for key in ("symbol", "old_symbol", "new_symbol")
                if isinstance(ca.get(key), str) and str(ca[key]).strip()
            )
            scope_raw = ca.get("ex_date") or ca.get("process_date")
            scope_date = date.fromisoformat(str(scope_raw)) if scope_raw is not None else None
            return cls(
                event_id=str(envelope["event_id"]),
                at=provider_at,
                action=CorporateActionEventType(str(envelope["action"])),
                provider_action_id=action_id,
                payload_hash=canonical_corporate_action_payload_hash(ca),
                received_at=received_at or provider_at,
                symbols=symbols,
                scope_date=scope_date,
            )
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidProviderResponse("Alpaca SSE event nemá platný envelope") from exc


AlpacaTransport = Callable[[str, dict[str, str], float], tuple[int, dict[str, str], bytes]]
ActionEvidenceLoader = Callable[[str], tuple[CorporateActionEvent, ...]]


class AlpacaProvider:
    """REST adapter; ``known_at`` je lokální receipt time přesně odpovídající SSE revize."""

    _base_url = "https://data.alpaca.markets"
    _corporate_actions_inventory_end = "9999-12-31"
    _supported_collections = frozenset(
        {
            "forward_splits",
            "reverse_splits",
            "cash_dividends",
            "name_changes",
            "worthless_removals",
        }
    )

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        evidence_loader: ActionEvidenceLoader,
        instrument_ids: dict[str, str],
        transport: AlpacaTransport,
        timeout: float = 10,
        feed: str = "iex",
    ) -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca credentials jsou povinné")
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._evidence_loader = evidence_loader
        self._instrument_ids = {key.upper(): value for key, value in instrument_ids.items()}
        self._transport = transport
        self._timeout = timeout
        if feed not in {"iex", "sip", "delayed_sip", "otc", "boats", "overnight"}:
            raise ValueError("Alpaca feed není na allowlistu")
        self._feed = feed
        self.metadata = ProviderMetadata("alpaca", "5", True, True, f"alpaca:{feed}")

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
        """Vyčerpá stránkování jednoduché list kolekce (např. OHLCV bars)."""
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

    def _get_corporate_action_rows(self, symbol: str) -> list[tuple[str, dict[str, Any]]]:
        """Načte všechny CA typy; research interval se nepředstírá jako process-date interval."""
        query = {
            "symbols": symbol,
            "start": "1970-01-01",
            "end": self._corporate_actions_inventory_end,
            "limit": "1000",
            "data_quality": "all",
        }
        rows: list[tuple[str, dict[str, Any]]] = []
        tokens: set[str] = set()
        while True:
            payload = self._get("/v1/corporate-actions", query)
            groups = payload.get("corporate_actions")
            if not isinstance(groups, dict):
                raise InvalidProviderResponse("Alpaca odpověď neobsahuje corporate_actions objekt")
            for collection, page in groups.items():
                if not isinstance(collection, str) or not isinstance(page, list):
                    raise InvalidProviderResponse("Alpaca corporate_actions má neplatný tvar")
                for row in page:
                    if not isinstance(row, dict):
                        raise InvalidProviderResponse("Alpaca corporate action není objekt")
                    rows.append((collection, cast(dict[str, Any], row)))
            token = payload.get("next_page_token")
            if token is None:
                return rows
            if not isinstance(token, str) or not token or token in tokens:
                raise InvalidProviderResponse("Alpaca vrátila neplatné stránkování")
            tokens.add(token)
            query = {**query, "page_token": token}

    def historical_daily(self, symbol: str, start: date, end: date) -> list[ProviderBar]:
        if start > end:
            raise ValueError("Počáteční datum musí předcházet koncovému")
        provider_symbol = self.resolve(symbol)["provider_symbol"]
        exclusive_end = f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z"
        rows = self._get_all(
            f"/v2/stocks/{urllib.parse.quote(provider_symbol, safe='')}/bars",
            {
                "timeframe": "1Day",
                "start": f"{start.isoformat()}T00:00:00Z",
                "end": exclusive_end,
                "adjustment": "raw",
                "feed": self._feed,
            },
            "bars",
        )
        try:
            bars = [
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
        if len({bar.session_date for bar in bars}) != len(bars):
            raise InvalidProviderResponse("Alpaca vrátila duplicitní daily session")
        return sorted(bars, key=lambda bar: bar.session_date)

    @staticmethod
    def _midnight(day: str) -> datetime:
        return datetime.fromisoformat(f"{day}T00:00:00+00:00")

    @staticmethod
    def _scope_date(row: dict[str, Any]) -> date:
        raw = row.get("ex_date") or row.get("process_date")
        if raw is None:
            raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
        try:
            return date.fromisoformat(str(raw))
        except ValueError as exc:
            raise InvalidProviderResponse("Alpaca corporate action má neplatné datum") from exc

    def _normalize_action(
        self,
        collection: str,
        row: dict[str, Any],
        instrument_id: str,
        evidence: CorporateActionEvent,
    ) -> CorporateAction:
        provider_action_id = str(row["id"])
        action_id = corporate_action_logical_id("alpaca", provider_action_id)
        known_at = cast(datetime, evidence.received_at)
        payload_hash = evidence.payload_hash
        if collection in {"forward_splits", "reverse_splits"}:
            old_rate = Decimal(str(row["old_rate"]))
            new_rate = Decimal(str(row["new_rate"]))
            if (
                not old_rate.is_finite()
                or not new_rate.is_finite()
                or old_rate <= 0
                or new_rate <= 0
            ):
                raise InvalidProviderResponse("Alpaca split má neplatný poměr")
            return CorporateAction(
                action_id,
                instrument_id,
                CorporateActionKind.SPLIT,
                self._midnight(str(row["ex_date"])),
                known_at,
                new_rate / old_rate,
                None,
                provider_action_id,
                payload_hash,
            )
        if collection == "cash_dividends":
            rate = Decimal(str(row["rate"]))
            if not rate.is_finite() or rate <= 0:
                raise InvalidProviderResponse("Alpaca dividend má neplatnou hodnotu")
            return CorporateAction(
                action_id,
                instrument_id,
                CorporateActionKind.CASH_DIVIDEND,
                self._midnight(str(row["ex_date"])),
                known_at,
                rate,
                None,
                provider_action_id,
                payload_hash,
            )
        if collection == "name_changes":
            return CorporateAction(
                action_id,
                instrument_id,
                CorporateActionKind.SYMBOL_CHANGE,
                self._midnight(str(row["process_date"])),
                known_at,
                None,
                str(row["new_symbol"]),
                provider_action_id,
                payload_hash,
            )
        if collection == "worthless_removals":
            return CorporateAction(
                action_id,
                instrument_id,
                CorporateActionKind.DELISTING,
                self._midnight(str(row["process_date"])),
                known_at,
                None,
                None,
                provider_action_id,
                payload_hash,
            )
        raise DatasetInvalid("CORPORATE_ACTIONS_UNSUPPORTED")

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        if start > end:
            raise ValueError("Počáteční datum musí předcházet koncovému")
        normalized = self.resolve(symbol)["provider_symbol"]
        events = self._evidence_loader("alpaca")
        latest: dict[str, CorporateActionEvent] = {}
        for event in sorted(events, key=lambda item: (item.at, item.event_id)):
            latest[event.provider_action_id] = event

        result: list[CorporateAction] = []
        rows = self._get_corporate_action_rows(normalized)
        returned_ids = {
            str(row.get("id"))
            for _, row in rows
            if isinstance(row.get("id"), str) and row.get("id")
        }
        try:
            for collection, row in rows:
                scope_date = self._scope_date(row)
                if not start <= scope_date <= end:
                    continue
                if collection not in self._supported_collections:
                    raise DatasetInvalid("CORPORATE_ACTIONS_UNSUPPORTED")
                provider_action_id = str(row["id"])
                evidence = latest.get(provider_action_id)
                if evidence is None or evidence.action is CorporateActionEventType.DELETE:
                    raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
                if canonical_corporate_action_payload_hash(row) != evidence.payload_hash:
                    # REST může obsahovat novější opravenou verzi než známá SSE historie.
                    # Takové hodnotě nesmíme přiřadit starší known_at; raději fail closed.
                    raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
                action = self._normalize_action(
                    collection, row, self._instrument_ids[normalized], evidence
                )
                if start <= action.effective_at.date() <= end:
                    result.append(action)
            for evidence in latest.values():
                if evidence.action is not CorporateActionEventType.DELETE:
                    continue
                if normalized not in evidence.symbols:
                    continue
                if evidence.provider_action_id in returned_ids:
                    continue
                if evidence.scope_date is not None and not start <= evidence.scope_date <= end:
                    continue
                # DELETE z REST nesmí znamenat prázdný COMPLETE interval.
                raise DatasetInvalid("CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE")
        except DatasetInvalid:
            raise
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise InvalidProviderResponse("Alpaca vrátila neplatnou corporate action") from exc
        return sorted(result, key=lambda item: (item.effective_at, item.action_id))


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
            if r.instrument_id == instrument.instrument_id
            and r.provider == provider.metadata.persistent_name
        ]
        actual_start = max(
            start,
            max((r.session_date for r in current), default=start) - timedelta(days=overlap_days),
        )
        try:
            incoming = [
                normalize_bar(
                    b,
                    instrument,
                    provider.metadata.persistent_name,
                    observed_at,
                    ingestion_id,
                    calendar,
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
