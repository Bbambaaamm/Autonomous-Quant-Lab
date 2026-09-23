from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from quantlab.config import Settings
from quantlab.market_data import (
    AlpacaProvider,
    Instrument,
    InvalidProviderResponse,
    MarketDataProvider,
    ProviderUnavailable,
    StooqProvider,
)
from quantlab.market_data_service import PersistentMarketDataService
from quantlab.persistence import InstrumentRecord


def _validate_alpaca_rest_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    allowed_path = parsed.path == "/v1/corporate-actions" or (
        parsed.path.startswith("/v2/stocks/") and parsed.path.endswith("/bars")
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != "data.alpaca.markets"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or not allowed_path
        or parsed.fragment
    ):
        raise ProviderUnavailable("Alpaca REST transport dovoluje pouze market-data endpointy")


class _AlpacaRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_alpaca_rest_url(newurl)
        raise ProviderUnavailable("Alpaca REST redirect není povolen")


def alpaca_rest_transport(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[int, dict[str, str], bytes]:
    _validate_alpaca_rest_url(url)
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310
    opener = urllib.request.build_opener(_AlpacaRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderUnavailable("Alpaca REST není dostupné") from exc


TransportTelemetrySink = Callable[[int, int], None]


class _BudgetedAlpacaTransport:
    def __init__(self, request_budget: int | None) -> None:
        self.remaining = request_budget
        self.deadline = time.monotonic() + 45
        self.request_count = 0
        self.response_bytes = 0
        self.telemetry_complete = True
        self.fail_on_access_denied = request_budget is not None
        self._telemetry_sink: TransportTelemetrySink | None = None

    def set_telemetry_sink(self, sink: TransportTelemetrySink) -> None:
        self._telemetry_sink = sink

    def _record(self, requests: int, response_bytes: int) -> None:
        if self._telemetry_sink is not None:
            try:
                self._telemetry_sink(requests, response_bytes)
            except Exception as exc:
                self.telemetry_complete = False
                raise ProviderUnavailable("MARKET_TELEMETRY_PERSIST_FAILED") from exc
        self.request_count += requests
        self.response_bytes += response_bytes

    def __call__(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        if self.remaining is not None:
            if self.remaining <= 0 or time.monotonic() >= self.deadline:
                raise ProviderUnavailable("MARKET_REQUEST_BUDGET_EXHAUSTED")
            self.remaining -= 1
            timeout = min(timeout, max(0.1, self.deadline - time.monotonic()))
        # Persist the request before sending it. Redirects are rejected by the
        # transport, so this corresponds to exactly one outbound HTTP request.
        self._record(1, 0)
        response = alpaca_rest_transport(url, headers, timeout)
        self._record(0, len(response[2]))
        if self.fail_on_access_denied and response[0] in {401, 403}:
            raise ProviderUnavailable("MARKET_DATA_ACCESS_DENIED")
        return response


def build_market_data_provider(
    settings: Settings,
    engine: Engine,
    *,
    instrument: Instrument | None = None,
    request_budget: int | None = None,
) -> MarketDataProvider:
    """Vrátí jediný allowlisted production provider podle validované konfigurace."""
    if settings.market_data_provider == "stooq":
        return StooqProvider(
            timeout=settings.market_data_timeout,
            max_attempts=settings.market_data_max_attempts,
        )
    if settings.market_data_provider != "alpaca":
        raise InvalidProviderResponse("Market-data provider není na production allowlistu")

    def sessions() -> Session:
        return Session(engine)

    if instrument is None:
        with sessions() as session:
            instruments = tuple(session.scalars(select(InstrumentRecord)))
        instrument_ids = {row.symbol.upper(): row.instrument_id for row in instruments}
    else:
        instrument_ids = {instrument.symbol.upper(): instrument.instrument_id}
    transport = _BudgetedAlpacaTransport(request_budget)
    service = PersistentMarketDataService(sessions)
    return AlpacaProvider(
        settings.alpaca_key_id,
        settings.alpaca_secret_key,
        service.corporate_action_events,
        instrument_ids,
        transport,
        timeout=settings.market_data_timeout,
        feed=settings.alpaca_feed,
    )
