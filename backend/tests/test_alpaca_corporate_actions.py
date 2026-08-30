import json
import urllib.parse
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from quantlab.alpaca_sse import AlpacaCorporateActionStream
from quantlab.market_data import (
    AlpacaProvider,
    CorporateActionEvent,
    CorporateActionEventType,
    CorporateActionKind,
    DatasetInvalid,
    InvalidProviderResponse,
    ProviderUnavailable,
    canonical_corporate_action_payload_hash,
)

Transport = Callable[[str, dict[str, str], float], tuple[int, dict[str, str], bytes]]


def _split(action_id: str = "ca-1", *, new_rate: int = 4) -> dict[str, Any]:
    return {
        "id": action_id,
        "symbol": "AAPL",
        "old_rate": 1,
        "new_rate": new_rate,
        "process_date": "2026-08-29",
        "ex_date": "2026-09-01",
    }


def _cash_dividend(action_id: str = "ca-div") -> dict[str, Any]:
    return {
        "id": action_id,
        "symbol": "AAPL",
        "rate": 0.25,
        "process_date": "2026-08-30",
        "ex_date": "2026-09-02",
        "record_date": "2026-09-03",
        "payable_date": "2026-09-10",
    }


def _sse_payload(
    ca: dict[str, Any],
    *,
    event_id: str = "event-1",
    action: str = "insert",
    event_type: str = "forward_split_corporateaction_event",
    at: str = "2026-08-29T15:00:00Z",
) -> bytes:
    return json.dumps(
        {
            "event_id": event_id,
            "at": at,
            "action": action,
            "region": "us",
            "event_type": event_type,
            "ca": ca,
        },
        separators=(",", ":"),
    ).encode()


def _response(groups: dict[str, list[dict[str, Any]]], token: str | None = None) -> bytes:
    payload: dict[str, Any] = {"corporate_actions": groups, "next_page_token": token}
    return json.dumps(payload).encode()


def _transport(
    pages: dict[str | None, bytes], calls: list[dict[str, list[str]]]
) -> Transport:
    def request(
        url: str, headers: dict[str, str], timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        assert url.startswith("https://data.alpaca.markets/")
        assert headers["APCA-API-KEY-ID"] == "key"
        assert headers["APCA-API-SECRET-KEY"] == "secret"
        assert timeout == 1
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        calls.append(query)
        token = query.get("page_token", [None])[0]
        return 200, {}, pages[token]

    return request


def _provider(
    pages: dict[str | None, bytes],
    events: tuple[CorporateActionEvent, ...] = (),
    calls: list[dict[str, list[str]]] | None = None,
) -> AlpacaProvider:
    captured = calls if calls is not None else []
    return AlpacaProvider(
        "key",
        "secret",
        lambda provider: tuple(events) if provider == "alpaca" else (),
        {"AAPL": "instrument-aapl"},
        _transport(pages, captured),
        timeout=1,
    )


def test_alpaca_action_known_at_comes_only_from_matching_sse_version() -> None:
    row = _split()
    event = CorporateActionEvent.from_sse(_sse_payload(row))
    provider = _provider({None: _response({"forward_splits": [row]})}, (event,))

    actions = provider.corporate_actions("AAPL", date(2026, 8, 1), date(2026, 9, 2))

    assert len(actions) == 1
    assert actions[0].kind is CorporateActionKind.SPLIT
    assert actions[0].known_at == datetime(2026, 8, 29, 15, tzinfo=UTC)
    assert actions[0].effective_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert actions[0].value == Decimal("4")


def test_alpaca_historical_action_without_sse_evidence_fails_closed() -> None:
    provider = _provider({None: _response({"forward_splits": [_split()]})})

    with pytest.raises(DatasetInvalid, match="^CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE$"):
        provider.corporate_actions("AAPL", date(2026, 8, 1), date(2026, 9, 2))


def test_alpaca_newer_rest_revision_cannot_reuse_older_known_at() -> None:
    old_version = _split(new_rate=2)
    current_version = _split(new_rate=4)
    event = CorporateActionEvent.from_sse(_sse_payload(old_version))
    provider = _provider(
        {None: _response({"forward_splits": [current_version]})},
        (event,),
    )

    with pytest.raises(DatasetInvalid, match="^CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE$"):
        provider.corporate_actions("AAPL", date(2026, 8, 1), date(2026, 9, 2))


def test_alpaca_delete_event_cannot_prove_current_rest_fact() -> None:
    row = _split()
    event = CorporateActionEvent.from_sse(_sse_payload(row, action="delete"))
    provider = _provider({None: _response({"forward_splits": [row]})}, (event,))

    with pytest.raises(DatasetInvalid, match="^CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE$"):
        provider.corporate_actions("AAPL", date(2026, 8, 1), date(2026, 9, 2))


def test_alpaca_sse_envelope_uses_nested_ca_identity_and_version_hash() -> None:
    ca = _split()
    event = CorporateActionEvent.from_sse(
        _sse_payload(ca, event_id="e-1", action="update", at="2026-08-29T16:00:00Z")
    )

    assert event.event_id == "e-1"
    assert event.provider_action_id == "ca-1"
    assert event.at == datetime(2026, 8, 29, 16, tzinfo=UTC)
    assert event.action is CorporateActionEventType.UPDATE
    assert event.payload_hash == canonical_corporate_action_payload_hash(ca)


def test_alpaca_rest_uses_nested_collections_and_exhausts_pagination() -> None:
    split = _split()
    dividend = _cash_dividend()
    events = (
        CorporateActionEvent.from_sse(_sse_payload(split, event_id="e-split")),
        CorporateActionEvent.from_sse(
            _sse_payload(
                dividend,
                event_id="e-dividend",
                event_type="cash_dividend_corporateaction_event",
                at="2026-08-30T15:00:00Z",
            )
        ),
    )
    calls: list[dict[str, list[str]]] = []
    provider = _provider(
        {
            None: _response({"forward_splits": [split]}, "page-2"),
            "page-2": _response({"cash_dividends": [dividend]}),
        },
        events,
        calls,
    )

    actions = provider.corporate_actions("AAPL", date(2026, 9, 1), date(2026, 9, 2))

    assert [action.kind for action in actions] == [
        CorporateActionKind.SPLIT,
        CorporateActionKind.CASH_DIVIDEND,
    ]
    assert calls[0]["start"] == ["1970-01-01"]
    assert "page_token" not in calls[0]
    assert calls[1]["page_token"] == ["page-2"]


def test_alpaca_research_interval_is_not_sent_as_process_date_filter() -> None:
    row = _split()
    event = CorporateActionEvent.from_sse(_sse_payload(row))
    calls: list[dict[str, list[str]]] = []
    provider = _provider({None: _response({"forward_splits": [row]})}, (event,), calls)

    provider.corporate_actions("AAPL", date(2026, 9, 1), date(2026, 9, 1))

    assert calls[0]["start"] != ["2026-09-01"]
    assert calls[0]["start"] == ["1970-01-01"]


def test_alpaca_name_change_and_worthless_removal_map_to_canonical_kinds() -> None:
    name_change = {
        "id": "ca-name",
        "old_symbol": "AAPL",
        "new_symbol": "APPL",
        "process_date": "2026-09-01",
    }
    removal = {"id": "ca-remove", "symbol": "AAPL", "process_date": "2026-09-02"}
    events = (
        CorporateActionEvent.from_sse(
            _sse_payload(
                name_change,
                event_id="e-name",
                event_type="name_change_corporateaction_event",
            )
        ),
        CorporateActionEvent.from_sse(
            _sse_payload(
                removal,
                event_id="e-remove",
                event_type="worthless_removal_corporateaction_event",
            )
        ),
    )
    provider = _provider(
        {None: _response({"name_changes": [name_change], "worthless_removals": [removal]})},
        events,
    )

    actions = provider.corporate_actions("AAPL", date(2026, 9, 1), date(2026, 9, 2))

    assert actions[0].kind is CorporateActionKind.SYMBOL_CHANGE
    assert actions[0].new_symbol == "APPL"
    assert actions[1].kind is CorporateActionKind.DELISTING


def test_alpaca_malformed_nested_response_is_rejected() -> None:
    provider = _provider({None: json.dumps({"corporate_actions": []}).encode()})

    with pytest.raises(InvalidProviderResponse, match="corporate_actions objekt"):
        provider.corporate_actions("AAPL", date(2026, 8, 1), date(2026, 9, 2))


def test_alpaca_sse_reconnect_uses_last_event_id_and_skips_inclusive_replay() -> None:
    first_ca = _split("ca-1")
    second_ca = _split("ca-2", new_rate=3)
    first = _sse_payload(first_ca, event_id="e-1")
    second = _sse_payload(second_ca, event_id="e-2", at="2026-08-29T16:00:00Z")
    headers_seen: list[dict[str, str]] = []
    attempts = 0

    def stream_transport(
        url: str, headers: dict[str, str], timeout: float
    ) -> tuple[bytes, ...] | Any:
        nonlocal attempts
        assert url == "https://stream.data.alpaca.markets/v1beta1/events/corporate-actions"
        assert timeout == 1
        headers_seen.append(headers.copy())
        attempts += 1
        if attempts == 1:
            def interrupted():  # type: ignore[no-untyped-def]
                yield first
                raise ProviderUnavailable("simulovaný reconnect")

            return interrupted()
        return (json.dumps([json.loads(first), json.loads(second)]).encode(),)

    stored: list[CorporateActionEvent] = []
    consumer = AlpacaCorporateActionStream(
        "key",
        "secret",
        lambda provider, event: stored.append(event) if provider == "alpaca" else None,
        transport=stream_transport,
        timeout=1,
        max_reconnects=2,
        sleep=lambda _: None,
    )

    cursor = consumer.run(max_events=2)

    assert cursor == "e-2"
    assert [event.event_id for event in stored] == ["e-1", "e-2"]
    assert "Last-Event-Id" not in headers_seen[0]
    assert headers_seen[1]["Last-Event-Id"] == "e-1"
