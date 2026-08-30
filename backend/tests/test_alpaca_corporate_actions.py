import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantlab.market_data import (
    AlpacaProvider,
    CorporateActionEvent,
    CorporateActionEventType,
    DatasetInvalid,
)


def _transport(corporate_actions):  # type: ignore[no-untyped-def]
    def request(url, headers, timeout):  # type: ignore[no-untyped-def]
        assert url.startswith("https://data.alpaca.markets/")
        assert headers["APCA-API-KEY-ID"] == "key"
        assert timeout == 1
        payload = {"corporate_actions": corporate_actions}
        return 200, {}, json.dumps(payload).encode()

    return request


def _provider(actions, events=()):  # type: ignore[no-untyped-def]
    return AlpacaProvider(
        "key",
        "secret",
        lambda provider: tuple(events),
        {"AAPL": "instrument-aapl"},
        _transport(actions),
        timeout=1,
    )


def test_alpaca_action_known_at_comes_only_from_sse_event() -> None:
    at = datetime(2026, 8, 29, 15, tzinfo=UTC)
    event = CorporateActionEvent("event-1", at, CorporateActionEventType.INSERT, "ca-1", "a" * 64)
    provider = _provider(
        [{"id": "ca-1", "type": "split", "effective_at": "2026-09-01T13:30:00Z", "value": "4"}],
        [event],
    )

    actions = provider.corporate_actions("AAPL", date(2026, 8, 1), date(2026, 9, 2))

    assert len(actions) == 1
    assert actions[0].known_at == at
    assert actions[0].value == Decimal("4")


def test_alpaca_historical_action_without_sse_evidence_fails_closed() -> None:
    provider = _provider(
        [{"id": "ca-1", "type": "split", "effective_at": "2026-09-01T13:30:00Z", "value": "4"}]
    )

    with pytest.raises(DatasetInvalid, match="^CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE$"):
        provider.corporate_actions("AAPL", date(2026, 8, 1), date(2026, 9, 2))


def test_alpaca_sse_envelope_preserves_event_identity_and_payload_hash() -> None:
    payload = (
        b'{"event_id":"e-1","at":"2026-08-29T15:00:00Z","action":"update",'
        b'"corporate_action_id":"ca-1"}'
    )

    event = CorporateActionEvent.from_sse(payload)

    assert event.event_id == "e-1"
    assert event.at == datetime(2026, 8, 29, 15, tzinfo=UTC)
    assert event.action is CorporateActionEventType.UPDATE
    assert event.payload_hash == hashlib.sha256(payload).hexdigest()
