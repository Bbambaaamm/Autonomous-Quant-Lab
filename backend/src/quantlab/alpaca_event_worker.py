from __future__ import annotations

import logging
import resource
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Thread

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantlab.alpaca_sse import AlpacaCorporateActionStream
from quantlab.config import get_settings
from quantlab.market_data import CorporateActionEvent, ProviderUnavailable
from quantlab.market_data_service import PersistentMarketDataService


def _current_rss_kib() -> int | None:
    """Vrátí Linux current RSS bez další runtime dependency."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                fields = line.split()
                return int(fields[1]) if len(fields) >= 2 else None
    except (OSError, ValueError):
        return None
    return None


def _peak_rss_kib() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


class _ListenerTelemetry:
    """Levná process telemetry nezávislá na frekvenci příchozích SSE eventů."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        interval: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] = time.process_time,
        rss_reader: Callable[[], int | None] = _current_rss_kib,
        peak_reader: Callable[[], int] = _peak_rss_kib,
    ) -> None:
        self._logger = logger
        self._interval = interval
        self._monotonic = monotonic
        self._cpu_clock = cpu_clock
        self._rss_reader = rss_reader
        self._peak_reader = peak_reader
        self._started_at = monotonic()
        self._last_useful_at = self._started_at
        self._events_received = 0
        self._events_written = 0
        self._events_duplicate = 0
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(
            target=self._run,
            name="alpaca-event-telemetry",
            daemon=True,
        )

    def record_received(self) -> None:
        with self._lock:
            self._events_received += 1

    def record_result(self, inserted: bool) -> None:
        with self._lock:
            if inserted:
                self._events_written += 1
                self._last_useful_at = self._monotonic()
            else:
                self._events_duplicate += 1

    def snapshot(self) -> dict[str, int | float | None]:
        now = self._monotonic()
        with self._lock:
            received = self._events_received
            written = self._events_written
            duplicate = self._events_duplicate
            last_useful = self._last_useful_at
        return {
            "rss_current_kib": self._rss_reader(),
            "rss_peak_kib": self._peak_reader(),
            "cpu_seconds": self._cpu_clock(),
            "events_received": received,
            "events_written": written,
            "events_duplicate": duplicate,
            "idle_seconds": max(0.0, now - last_useful),
        }

    def log(self) -> None:
        metrics = self.snapshot()
        self._logger.info(
            "Alpaca event telemetry: rss_current_kib=%s rss_peak_kib=%s "
            "cpu_seconds=%.6f events_received=%d events_written=%d "
            "events_duplicate=%d idle_seconds=%.1f",
            metrics["rss_current_kib"],
            metrics["rss_peak_kib"],
            metrics["cpu_seconds"],
            metrics["events_received"],
            metrics["events_written"],
            metrics["events_duplicate"],
            metrics["idle_seconds"],
        )

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.log()

    def start(self) -> None:
        self.log()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.log()


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
    cursor = service.latest_corporate_action_event_id("alpaca")
    telemetry = _ListenerTelemetry(logger)

    def persist_event(provider: str, event: CorporateActionEvent) -> bool:
        telemetry.record_received()
        inserted = service.record_corporate_action_event(provider, event)
        telemetry.record_result(inserted)
        return inserted

    stream = AlpacaCorporateActionStream(
        settings.alpaca_key_id,
        settings.alpaca_secret_key,
        persist_event,
        timeout=settings.market_data_timeout,
        max_reconnects=settings.market_data_max_attempts,
    )
    logger.info(
        "Alpaca corporate-action event worker startuje: replay_cursor_present=%s",
        cursor is not None,
    )
    telemetry.start()
    try:
        stream.run(cursor)
        raise ProviderUnavailable("Alpaca SSE stream vyčerpal povolené reconnect pokusy")
    finally:
        telemetry.stop()
        engine.dispose()
        logger.info("Alpaca corporate-action event worker skončil")


if __name__ == "__main__":
    main()
