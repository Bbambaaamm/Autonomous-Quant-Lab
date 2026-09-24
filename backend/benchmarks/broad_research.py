from __future__ import annotations

import argparse
import json
import resource
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from quantlab.market_data import Observation
from quantlab.multi_asset import (
    ObservationKnowledgeMode,
    RebalanceFrequency,
    TrendStrategy,
    run_multi_asset,
)
from quantlab.universe import (
    PointInTimeUniverse,
    UniverseDefinition,
    UniverseKind,
    UniverseMembership,
)

START = datetime(2024, 1, 2, 21, tzinfo=UTC)


def peak_rss_mib() -> float:
    # Linux ru_maxrss is KiB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def build_fixture(instruments: int, sessions: int) -> tuple[list[Observation], PointInTimeUniverse]:
    if instruments < 1 or sessions < 3:
        raise ValueError("Benchmark vyžaduje kladný universe a alespoň tři sessions")

    memberships = [
        UniverseMembership(
            "benchmark-pit",
            f"inst-{instrument:05d}",
            START - timedelta(days=1),
            None,
            START - timedelta(days=1),
        )
        for instrument in range(instruments)
    ]
    universe = PointInTimeUniverse(
        UniverseDefinition(
            "benchmark-pit",
            "Synthetic broad research benchmark",
            UniverseKind.POINT_IN_TIME_MEMBERSHIP,
        ),
        memberships,
    )

    rows: list[Observation] = []
    append = rows.append
    for session in range(sessions):
        timestamp = START + timedelta(days=session)
        day = timestamp.date()
        for instrument in range(instruments):
            instrument_id = f"inst-{instrument:05d}"
            cents = 10_000 + session * 7 + instrument % 97
            open_price = Decimal(cents) / Decimal(100)
            high = Decimal(cents + 30) / Decimal(100)
            low = Decimal(cents - 20) / Decimal(100)
            close = Decimal(cents + 10) / Decimal(100)
            volume = Decimal(100_000 + (instrument % 1000) * 10 + session)
            identity = f"{instrument:05d}{session:05d}".ljust(64, "0")
            source_hash = f"{session:05d}{instrument:05d}".ljust(64, "f")
            append(
                Observation(
                    observation_id=identity,
                    instrument_id=instrument_id,
                    provider="benchmark",
                    timeframe="1d",
                    session_date=day,
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    observed_at=timestamp,
                    source_id=f"benchmark:{instrument}:{session}",
                    source_hash=source_hash,
                    ingestion_id="benchmark-ingestion",
                    revision=1,
                )
            )
    return rows, universe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruments", type=int, default=2000)
    parser.add_argument("--sessions", type=int, default=250)
    parser.add_argument("--max-rss-mib", type=float, default=1024)
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    started = time.perf_counter()
    rows, universe = build_fixture(args.instruments, args.sessions)
    built_at = time.perf_counter()

    strategy = TrendStrategy(
        fast=20,
        slow=100,
        rebalance_frequency=RebalanceFrequency.MONTHLY,
    )
    evaluation_start = START + timedelta(days=strategy.required_lookback)
    result = run_multi_asset(
        rows,
        universe,
        strategy,
        initial_cash=Decimal("10000000"),
        commission_bps=Decimal("1"),
        evaluation_start=evaluation_start,
        observation_knowledge_mode=ObservationKnowledgeMode.SNAPSHOT_PINNED,
    )
    finished = time.perf_counter()

    bar_count = args.instruments * args.sessions
    report = {
        "schema_version": 1,
        "bars": bar_count,
        "instruments": args.instruments,
        "sessions": args.sessions,
        "build_seconds": round(built_at - started, 3),
        "engine_seconds": round(finished - built_at, 3),
        "wall_seconds": round(finished - started, 3),
        "peak_rss_mib": round(peak_rss_mib(), 1),
        "decisions": len(result.decisions),
        "fills": len(result.fills),
        "requested_assets": result.requested_assets,
        "used_assets": result.used_assets,
        "max_rss_mib": args.max_rss_mib,
        "max_seconds": args.max_seconds,
    }
    encoded = json.dumps(report, sort_keys=True, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n")

    if bar_count < 500_000:
        raise RuntimeError("Acceptance benchmark musí obsahovat alespoň 500000 barů")
    if result.requested_assets != args.instruments:
        raise RuntimeError("Benchmark nevyhodnotil celý PIT universe")
    if not result.decisions or not result.equity:
        raise RuntimeError("Benchmark nevytvořil research decisions/equity")
    if report["peak_rss_mib"] > args.max_rss_mib:
        raise RuntimeError(
            f"Peak RSS {report['peak_rss_mib']} MiB překročilo budget {args.max_rss_mib} MiB"
        )
    if report["wall_seconds"] > args.max_seconds:
        raise RuntimeError(
            f"Wall time {report['wall_seconds']} s překročil budget {args.max_seconds} s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
