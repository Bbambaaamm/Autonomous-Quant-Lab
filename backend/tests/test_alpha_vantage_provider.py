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
    ("symbol", "valid"),
    [
        ("LSE:BP.", True),
        ("XETRA:BAS.DE", True),
        ("ASX:BHP.AX", True),
        ("AAPL", False),
        ("XNYS:SPY", False),
        ("INVALID:XYZ", False),
        ("", False),
    ],
)
def test_alpha_vantage_resolve_validates_non_us_allowlist(symbol: str, valid: bool) -> None:
    provider = AlphaVantageProvider("test-key", transport=lambda *a: (200, {}, b"{}"))
    if valid:
        assert provider.resolve(symbol)["provider_symbol"] == symbol.strip().upper()
    else:
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
        assert "symbol=LSE%3ABP." in url
        assert "apikey=fake-key" in url
        return (200, {}, body)

    provider = AlphaVantageProvider("fake-key", transport=transport, max_attempts=1)
    bars = provider.historical_daily("LSE:BP.", date(2024, 1, 1), date(2024, 1, 2))
    assert len(bars) == 2
    assert bars[0] == ProviderBar(
        date(2024, 1, 1),
        Decimal("99.00"),
        Decimal("101.50"),
        Decimal("97.50"),
        Decimal("100.00"),
        Decimal("2000"),
        "alphavantage:LSE:BP.:2024-01-01",
    )
    assert bars[1] == ProviderBar(
        date(2024, 1, 2),
        Decimal("100.00"),
        Decimal("102.00"),
        Decimal("98.00"),
        Decimal("101.00"),
        Decimal("1000"),
        "alphavantage:LSE:BP.:2024-01-02",
    )
    assert len(captured) == 1


def test_alpha_vantage_rate_limit_raises_provider_rate_limited() -> None:
    provider = AlphaVantageProvider(
        "fake-key",
        transport=lambda url, timeout: (429, {"Retry-After": "60"}, b""),
        max_attempts=1,
    )
    with pytest.raises(ProviderRateLimited):
        provider.historical_daily("LSE:BP.", date(2024, 1, 1), date(2024, 1, 2))


def test_alpha_vantage_empty_series_raises_invalid_symbol() -> None:
    provider = AlphaVantageProvider(
        "fake-key",
        transport=lambda url, timeout: (200, {}, b'{"Time Series (Daily)": {}}'),
        max_attempts=1,
    )
    with pytest.raises(InvalidSymbol, match="data pro symbol"):
        provider.historical_daily("LSE:BP.", date(2024, 1, 1), date(2024, 1, 2))


def test_alpha_vantage_malformed_json_raises_invalid_provider_response() -> None:
    provider = AlphaVantageProvider(
        "fake-key",
        transport=lambda url, timeout: (200, {}, b"{broken"),
        max_attempts=1,
    )
    with pytest.raises(InvalidProviderResponse, match="neplat"):
        provider.historical_daily("LSE:BP.", date(2024, 1, 1), date(2024, 1, 2))


def test_alpha_vantage_corporate_actions_fail_closed() -> None:
    provider = AlphaVantageProvider("test-key", transport=lambda *a: (200, {}, b"{}"))
    assert provider.corporate_actions("LSE:BP.", date(2024, 1, 1), date(2024, 1, 2)) == []
