import json
import socket
import ssl
import urllib.error
from datetime import UTC, datetime

import pytest

from quantlab.config import Settings
from quantlab.market_data import ProviderUnavailable
from quantlab.market_probe import probe_market_source

NOW = datetime(2026, 9, 22, 5, tzinfo=UTC)


def settings():
    return Settings(
        _env_file=None,
        market_data_provider="alpaca",
        alpaca_key_id="test-key",
        alpaca_secret_key="test-secret",
    )


def test_probe_is_bounded_and_never_returns_headers_or_payloads(monkeypatch):
    calls = []

    def transport(url, headers, timeout):
        calls.append((url, timeout))
        return 403, {"secret": "test-secret"}, b'{"message":"test-secret"}'

    monkeypatch.setattr("quantlab.market_probe.alpaca_rest_transport", transport)
    result = probe_market_source(settings(), NOW)
    assert result["requests"] == 3
    assert all(c["http_status"] == 403 for c in result["checks"])
    assert all(timeout <= 5 for _, timeout in calls)
    assert all(url.startswith("https://data.alpaca.markets/") for url, _ in calls)
    assert all("limit=1" in url for url, _ in calls)
    assert "test-secret" not in json.dumps(result, default=str)
    assert "test-key" not in json.dumps(result, default=str)
    assert result["scope"] == "backend"


@pytest.mark.parametrize(
    "cause,expected",
    [
        (socket.gaierror("private-details"), "Nelze přeložit DNS jméno"),
        (ssl.SSLError("private-details"), "Chyba ověření TLS certifikátu"),
        (TimeoutError("private-details"), "Vypršel čas spojení"),
    ],
)
def test_probe_classifies_network_errors_without_leaking_messages(monkeypatch, cause, expected):
    def transport(*args):
        raise ProviderUnavailable("test-secret") from urllib.error.URLError(cause)

    monkeypatch.setattr("quantlab.market_probe.alpaca_rest_transport", transport)
    result = probe_market_source(settings(), NOW)
    assert result["checks"][0]["result"] == expected
    assert "private-details" not in json.dumps(result, default=str)
    assert "test-secret" not in json.dumps(result, default=str)


def test_probe_requires_admin(tmp_path, monkeypatch):
    from test_phase8_api import client

    from quantlab import api as module

    api = client(tmp_path, monkeypatch)
    api.headers["Authorization"] = f"Bearer {module.settings.api_viewer_token}"
    monkeypatch.setattr(
        "quantlab.market_probe.alpaca_rest_transport",
        lambda *args: pytest.fail("Viewer must not reach provider"),
    )
    assert (
        api.post("/operator/market-pipeline/probe", json={"reason": "Check connection"}).status_code
        == 403
    )


def test_probe_compares_short_and_full_inventory_ranges(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    calls = []

    def transport(url, headers, timeout):
        query = parse_qs(urlsplit(url).query)
        calls.append(query)
        return (500 if query.get("end") == ["9999-12-31"] else 200), {}, b"{}"

    monkeypatch.setattr("quantlab.market_probe.alpaca_rest_transport", transport)
    result = probe_market_source(settings(), NOW)
    assert [c["http_status"] for c in result["checks"]] == [200, 200, 500]
    assert calls[2]["data_quality"] == ["all"]
