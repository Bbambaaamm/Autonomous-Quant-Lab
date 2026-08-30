from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from quantlab.config import Settings
from quantlab.market_data import (
    AlpacaProvider,
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
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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


def build_market_data_provider(settings: Settings, engine: Engine) -> MarketDataProvider:
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

    with sessions() as session:
        instruments = tuple(session.query(InstrumentRecord).all())
    instrument_ids = {row.symbol.upper(): row.instrument_id for row in instruments}
    service = PersistentMarketDataService(sessions)
    return AlpacaProvider(
        settings.alpaca_key_id,
        settings.alpaca_secret_key,
        service.corporate_action_events,
        instrument_ids,
        alpaca_rest_transport,
        timeout=settings.market_data_timeout,
    )
