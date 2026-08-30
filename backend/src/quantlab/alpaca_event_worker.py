from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantlab.alpaca_sse import AlpacaCorporateActionStream
from quantlab.config import get_settings
from quantlab.market_data import ProviderUnavailable
from quantlab.market_data_service import PersistentMarketDataService


def main() -> None:
    """Spustí samostatný fail-closed ingest Alpaca corporate-action SSE evidence."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("quantlab.alpaca_event_worker")
    if settings.market_data_provider != "alpaca":
        logger.info("Alpaca event worker je pro aktuální market-data provider vypnutý")
        return

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    service = PersistentMarketDataService(factory)
    persisted = service.corporate_action_events("alpaca")
    cursor = persisted[-1].event_id if persisted else None
    stream = AlpacaCorporateActionStream(
        settings.alpaca_key_id,
        settings.alpaca_secret_key,
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
        raise ProviderUnavailable("Alpaca SSE stream vyčerpal povolené reconnect pokusy")
    finally:
        engine.dispose()
        logger.info("Alpaca corporate-action event worker skončil")


if __name__ == "__main__":
    main()
