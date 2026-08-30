from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from quantlab.market_data import (
    CorporateActionEvent,
    InvalidProviderResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)

SSETransport = Callable[[str, dict[str, str], float], Iterable[bytes]]
EventSink = Callable[[str, CorporateActionEvent], None]


def _validate_alpaca_stream_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "stream.data.alpaca.markets"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/v1beta1/events/corporate-actions"
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderUnavailable("Alpaca SSE transport dovoluje pouze corporate-actions endpoint")


class AlpacaCorporateActionStream:
    """Bounded SSE consumer s inkluzivním Last-Event-Id replay a idempotentním sinkem."""

    _url = "https://stream.data.alpaca.markets/v1beta1/events/corporate-actions"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        sink: EventSink,
        *,
        transport: SSETransport | None = None,
        timeout: float = 60,
        max_reconnects: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca credentials jsou povinné")
        if timeout <= 0:
            raise ValueError("SSE timeout musí být kladný")
        if max_reconnects < 1:
            raise ValueError("SSE musí povolit alespoň jeden connection attempt")
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "text/event-stream",
        }
        self._sink = sink
        self._transport = transport or self._http
        self._timeout = timeout
        self._max_reconnects = max_reconnects
        self._sleep = sleep
        self._last_event_id: str | None = None
        self._last_batch_count = 0

    @property
    def last_event_id(self) -> str | None:
        return self._last_event_id

    @staticmethod
    def _http(url: str, headers: dict[str, str], timeout: float) -> Iterator[bytes]:
        _validate_alpaca_stream_url(url)
        # URL byl výše omezen na jediný HTTPS Alpaca endpoint; S310 je zde auditovaný.
        request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                status = getattr(response, "status", 200)
                if status == 429:
                    raise ProviderRateLimited()
                if status >= 500:
                    raise ProviderUnavailable("Dočasná chyba Alpaca SSE")
                if status != 200:
                    raise InvalidProviderResponse("Alpaca SSE požadavek nebyl úspěšný")
                data_lines: list[bytes] = []
                for raw_line in response:
                    line = raw_line.rstrip(b"\r\n")
                    if not line:
                        if data_lines:
                            yield b"\n".join(data_lines)
                            data_lines = []
                        continue
                    if line.startswith(b":"):
                        continue
                    if line.startswith(b"data:"):
                        value = line[5:]
                        if value.startswith(b" "):
                            value = value[1:]
                        data_lines.append(value)
                if data_lines:
                    yield b"\n".join(data_lines)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_header = exc.headers.get("Retry-After") if exc.headers is not None else None
                try:
                    retry_after = float(retry_header) if retry_header else None
                except ValueError:
                    retry_after = None
                raise ProviderRateLimited(retry_after) from exc
            if exc.code >= 500:
                raise ProviderUnavailable("Dočasná chyba Alpaca SSE") from exc
            raise InvalidProviderResponse("Alpaca SSE požadavek nebyl úspěšný") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderUnavailable("Alpaca SSE není dostupné") from exc

    @staticmethod
    def _events(payload: bytes) -> tuple[CorporateActionEvent, ...]:
        try:
            decoded: Any = json.loads(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidProviderResponse("Alpaca SSE vrátilo neplatné JSON") from exc
        items = decoded if isinstance(decoded, list) else [decoded]
        if not items or any(not isinstance(item, dict) for item in items):
            raise InvalidProviderResponse("Alpaca SSE event nemá objektový tvar")
        return tuple(
            CorporateActionEvent.from_sse(
                json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
            )
            for item in items
        )

    def consume_once(
        self, last_event_id: str | None = None, *, max_events: int | None = None
    ) -> str | None:
        """Spotřebuje jedno spojení; replay stejného ``last_event_id`` se záměrně přeskočí."""
        if max_events is not None and max_events < 0:
            raise ValueError("max_events nesmí být záporné")
        if max_events == 0:
            return last_event_id
        headers = self._headers.copy()
        if last_event_id:
            headers["Last-Event-Id"] = last_event_id
        cursor = last_event_id
        seen = {last_event_id} if last_event_id else set()
        self._last_event_id = last_event_id
        self._last_batch_count = 0
        for payload in self._transport(self._url, headers, self._timeout):
            for event in self._events(payload):
                if event.event_id in seen:
                    continue
                self._sink("alpaca", event)
                seen.add(event.event_id)
                cursor = event.event_id
                self._last_event_id = cursor
                self._last_batch_count += 1
                if max_events is not None and self._last_batch_count >= max_events:
                    return cursor
        return cursor

    def run(self, last_event_id: str | None = None, *, max_events: int | None = None) -> str | None:
        """Reconnectne nejvýše ``max_reconnects`` krát a pokračuje inkluzivním replayem."""
        if max_events is not None and max_events < 0:
            raise ValueError("max_events nesmí být záporné")
        if max_events == 0:
            return last_event_id
        cursor = last_event_id
        delivered = 0
        for attempt in range(self._max_reconnects):
            remaining = None if max_events is None else max_events - delivered
            try:
                cursor = self.consume_once(cursor, max_events=remaining)
                delivered += self._last_batch_count
                if max_events is not None and delivered >= max_events:
                    return cursor
                if attempt + 1 == self._max_reconnects:
                    return cursor
            except ProviderUnavailable as exc:
                delivered += self._last_batch_count
                cursor = self._last_event_id
                if attempt + 1 == self._max_reconnects:
                    raise
                delay = min(getattr(exc, "retry_after", None) or 0.05 * 2**attempt, 1.0)
                self._sleep(delay)
                continue
            self._sleep(min(0.05 * 2**attempt, 1.0))
        return cursor
