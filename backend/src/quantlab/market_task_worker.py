from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from quantlab.config import get_settings
from quantlab.market_pipeline import MarketPipeline
from quantlab.provider_factory import build_market_data_provider


def run_once() -> dict[str, str | None]:
    """Zpracuje nejvýše jeden durable MarketTask a proces pak skončí."""
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        return MarketPipeline(lambda: Session(engine)).step(
            lambda instrument: build_market_data_provider(
                settings,
                engine,
                instrument=instrument,
                request_budget=12,
            ),
            clock=lambda: datetime.now(UTC),
        )
    finally:
        engine.dispose()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    result = run_once()
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
