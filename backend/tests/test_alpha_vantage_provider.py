from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from quantlab.market_data import (
    AlphaVantageProvider,
    InvalidProviderResponse,
    InvalidSymbol,
    ProviderBar,
    ProviderMetadata,
    ProviderRateLimited,
)


def _series_body(dates: dict[str, dict[str, str]]) -> bytes:
    return json.dumps({"Time Series (Daily)": dates}).encode()


def test_alpha_vantage_metadata_is_non_us_allowlisted() -> None:
    provider = AlphaVantageProvider("test-key", transport=lambda *a: (200, {}, b"{}"))
    assert provider.metadata == ProviderMetadata(
        "alphavantage", "1", False, True, "alphavantage:global"
    )


@pytest.mark.parametrize(
    ("symbol", "provider_symbol"),
    [
        ("LSE:TSCO", "TSCO.LON"),
        ("XETRA:MBG", "MBG.DEX"),
        ("TSX:SHOP", "SHOP.TRT"),
        ("SH:600104", "600104.SHH"),
        ("SZ:000002", "000002.SHZ"),
    ],
)
def test_alpha_vantage_resolve_maps_verified_non_us_suffixes(
    symbol: str, provider_symbol: str
) -> None:
    provider = AlphaVantageProvider("test-key", transport=lambda *a: (200, {}, b"{}"))
    assert provider.resolve(symbol) == {
        "symbol": symbol,
        "provider_symbol": provider_symbol,
    }


@pytest.mark.parametrize("symbol", ["AAPL", "XNYS:SPY", "ASX:BHP", "INVALID:XYZ", "LSE:", ""])
def test_alpha_vantage_resolve_rejects_unverified_or_invalid_symbols(symbol: str) -> None:
    provider = AlphaVantageProvider("test-key", transport=lambda *a: (200, {}, b"{}"))
    with pytest.raises(InvalidSymbol):
        provider.resolve(symbol)


def test_alpha_vantage_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        AlphaVantageProvider("", transport=lambda *a: (200, {}, b"{}"))


def test_alpha_vantage_historical_daily_parses_bars() -> None:
    body = _series_body(
        {
            "2024-01-02": {
                "1. open": "100.00",
                "2. high": "102.00",
                "3. low": "98.00",
                "4. close": "101.00",
                "5. volume": "1000",
            },
            "2024-01-01": {
                "1. open": "99.00",
                "2. high": "101.50",
                "3. low": "97.50",
                "4. close": "100.00",
                "5. volume": "2000",
            },
        }
    )
    captured: list[str] = []

    def transport(url: str, timeout: float) -> tuple[int, dict[str, str], bytes]:
        captured.append(url)
        assert "function=TIME_SERIES_DAILY" in url
        assert "symbol=TSCO.LON" in url
        assert "apikey=fake-key" in url
        return (200, {}, body)

    provider = AlphaVantageProvider("fake-key", transport=transport, max_attempts=1)
    bars = provider.historical_daily("LSE:TSCO", date(2024, 1, 1), date(2024, 1, 2))
    assert len(bars) == 2
    assert bars[0] == ProviderBar(
        date(2024, 1, 1),
        Decimal("99.00"),
        Decimal("101.50"),
        Decimal("97.50"),
        Decimal("100.00"),
        Decimal("2000"),
        "alphavantage:TSCO.LON:2024-01-01",
    )
    assert bars[1] == ProviderBar(
        date(2024, 1, 2),
        Decimal("100.00"),
        Decimal("102.00"),
        Decimal("98.00"),
        Decimal("101.00"),
        Decimal("1000"),
        "alphavantage:TSCO.LON:2024-01-02",
    )
    assert len(captured) == 1


def test_alpha_vantage_rate_limit_raises_provider_rate_limited() -> None:
    provider = AlphaVantageProvider(
        "fake-key",
        transport=lambda url, timeout: (429, {"Retry-After": "60"}, b""),
        max_attempts=1,
    )
    with pytest.raises(ProviderRateLimited):
        provider.historical_daily("LSE:TSCO", date(2024, 1, 1), date(2024, 1, 2))


def test_alpha_vantage_empty_series_raises_invalid_symbol() -> None:
    provider = AlphaVantageProvider(
        "fake-key",
        transport=lambda url, timeout: (200, {}, b'{"Time Series (Daily)": {}}'),
        max_attempts=1,
    )
    with pytest.raises(InvalidSymbol, match="data pro symbol"):
        provider.historical_daily("LSE:TSCO", date(2024, 1, 1), date(2024, 1, 2))


def test_alpha_vantage_malformed_json_raises_invalid_provider_response() -> None:
    provider = AlphaVantageProvider(
        "fake-key",
        transport=lambda url, timeout: (200, {}, b"{broken"),
        max_attempts=1,
    )
    with pytest.raises(InvalidProviderResponse, match="neplat"):
        provider.historical_daily("LSE:TSCO", date(2024, 1, 1), date(2024, 1, 2))


def test_alpha_vantage_corporate_actions_fail_closed() -> None:
    provider = AlphaVantageProvider("test-key", transport=lambda *a: (200, {}, b"{}"))
    assert provider.corporate_actions("LSE:TSCO", date(2024, 1, 1), date(2024, 1, 2)) == []
