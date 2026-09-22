"""Bounded read-only connectivity probe. Never returns provider payloads or secrets."""

from __future__ import annotations

import socket
import ssl
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from quantlab.config import Settings
from quantlab.domain import require_utc
from quantlab.market_data import XNYSCalendar
from quantlab.provider_factory import alpaca_rest_transport


def probe_market_source(settings: Settings, now: datetime) -> dict[str, Any]:
    now = require_utc(now)
    if (
        settings.market_data_provider != "alpaca"
        or not settings.alpaca_key_id
        or not settings.alpaca_secret_key
    ):
        raise ValueError("Kontrola vyžaduje nakonfigurovaný zdroj Alpaca")
    end = XNYSCalendar().latest_completed_session(now)
    queries = [
        (
            "Denní ceny IBM",
            "/v2/stocks/IBM/bars",
            {
                "timeframe": "1Day",
                "start": f"{end}T00:00:00Z",
                "end": f"{end + timedelta(days=1)}T00:00:00Z",
                "feed": settings.alpaca_feed,
                "limit": "1",
            },
        ),
        (
            "Corporate actions IBM",
            "/v1/corporate-actions",
            {"symbols": "IBM", "start": str(end), "end": str(end), "limit": "1"},
        ),
    ]
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_key_id,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }
    checks = []
    for label, path, query in queries:
        started = time.monotonic()
        status: int | None = None
        try:
            status, _, _ = alpaca_rest_transport(
                "https://data.alpaca.markets" + path + "?" + urlencode(query),
                headers,
                min(settings.market_data_timeout, 5),
            )
            outcome = {
                200: "Spojení a přístup fungují",
                400: "Zdroj odmítl parametry požadavku",
                401: "Zdroj odmítl přihlášení",
                403: "Zdroj odmítl oprávnění",
                429: "Dosažen limit požadavků",
            }.get(status, "Zdroj vrátil jiný HTTP stav")
        except Exception as exc:
            cause: object = exc.__cause__ or exc
            if isinstance(cause, urllib.error.URLError):
                cause = cause.reason
            outcome = (
                "Chyba ověření TLS certifikátu"
                if isinstance(cause, ssl.SSLError)
                else "Nelze přeložit DNS jméno"
                if isinstance(cause, socket.gaierror)
                else "Vypršel čas spojení"
                if isinstance(cause, TimeoutError)
                else "Síťové spojení se nezdařilo"
            )
        checks.append(
            {
                "name": label,
                "http_status": status,
                "result": outcome,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        )
    return {
        "checked_at": datetime.now(UTC),
        "scope": "backend",
        "feed": settings.alpaca_feed,
        "requests": len(checks),
        "checks": checks,
    }
