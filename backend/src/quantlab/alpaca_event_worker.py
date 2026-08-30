from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantlab.alpaca_sse import AlpacaCorporateActionStream
from quantlab.config import get_settings
from quantlab.market_data_service import PersistentMarketDataService


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} je povinné pro Alpaca corporate-action event worker")
    return value


def main() -> None:
    """Spustí samostatný fail-closed ingest Alpaca corporate-action SSE evidence."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("quantlab.alpaca_event_worker")
    key_id = _required_env("ALPACA_KEY_ID")
    secret_key = _required_env("ALPACA_SECRET_KEY")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    service = PersistentMarketDataService(factory)
    persisted = service.corporate_action_events("alpaca")
    cursor = persisted[-1].event_id if persisted else None
    stream = AlpacaCorporateActionStream(
        key_id,
        secret_key,
        service.record_corporate_action_event,
        timeout=settings.market_data_timeout,
        max_reconnects=settings.market_data_max_attempts,
    )
    logger.info(
        "Alpaca corporate-action event worker startuje: replay_cursor_present=%s",
        cursor is not None,
    )
    try:
        stream.run(cursor)
    finally:
        engine.dispose()
        logger.info("Alpaca corporate-action event worker skončil")


if __name__ == "__main__":
    main()
