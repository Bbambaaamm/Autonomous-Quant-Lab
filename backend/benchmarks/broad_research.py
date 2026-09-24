from __future__ import annotations

import argparse
import gc
import json
import resource
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker

from quantlab.market_data import XNYSCalendar
from quantlab.market_data_service import canonical_snapshot_content_hash
from quantlab.multi_asset import MultiAssetFill, MultiAssetPortfolio, RebalanceFrequency
from quantlab.persistence import (
    DatasetSnapshotRecord,
    InstrumentRecord,
    MarketDataIngestionRecord,
    MarketObservationRecord,
    RunRepository,
    StrategyRecord,
    UniverseDefinitionRecord,
    UniverseMembershipRecord,
)
from quantlab.phase6_runtime import Phase6ExperimentRequest, Phase6ExperimentRunner

PROVIDER = "benchmark"
SNAPSHOT_ID = "broad-research-benchmark-snapshot"
UNIVERSE_ID = "benchmark-pit"
INGESTION_ID = "benchmark-ingestion"
CODE_SHA = "b" * 40
SEED_BATCH_SIZE = 10_000


def peak_rss_mib() -> float:
    # Linux ru_maxrss is KiB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def trading_sessions(count: int) -> tuple[date, ...]:
    if count < 3:
        raise ValueError("Benchmark vyžaduje alespoň tři sessions")
    calendar = XNYSCalendar()
    candidates = calendar.sessions_between(date(2024, 1, 2), date(2026, 1, 30))
    if len(candidates) < count:
        raise ValueError("Požadovaný počet sessions je mimo benchmark calendar range")
    return candidates[:count]


def instrument_id(index: int) -> str:
    return f"inst-{index:05d}"


def observation_identity(instrument: int, session: int) -> str:
    return f"{instrument:08x}{session:08x}".ljust(64, "0")


def source_hash(instrument: int, session: int) -> str:
    return f"{session:08x}{instrument:08x}".ljust(64, "f")


def seed_database(database_path: Path, instruments: int, sessions: tuple[date, ...]) -> None:
    if instruments < 1:
        raise ValueError("Benchmark vyžaduje kladný universe")
    repository = RunRepository(f"sqlite:///{database_path}")
    calendar = XNYSCalendar()
    first_timestamp = calendar.session_close(sessions[0])
    last_timestamp = calendar.session_close(sessions[-1])
    valid_from = first_timestamp - timedelta(days=1)

    instrument_rows = [
        {
            "instrument_id": instrument_id(index),
            "symbol": f"B{index:05d}",
            "exchange": "XNYS",
            "calendar": "XNYS",
            "currency": "USD",
            "asset_type": "EQUITY",
            "active_from": valid_from,
            "active_to": None,
            "created_at": valid_from,
        }
        for index in range(instruments)
    ]
    membership_rows = [
        {
            "universe_id": UNIVERSE_ID,
            "instrument_id": instrument_id(index),
            "valid_from": valid_from,
            "valid_to": None,
            "known_at": valid_from,
        }
        for index in range(instruments)
    ]
    canonical_memberships = [
        {
            "instrument_id": row["instrument_id"],
            "valid_from": valid_from.isoformat(),
            "valid_to": None,
            "known_at": valid_from.isoformat(),
        }
        for row in membership_rows
    ]
    observation_entries: list[dict[str, object]] = []

    with repository.engine.begin() as connection:
        connection.execute(insert(InstrumentRecord), instrument_rows)
        connection.execute(
            insert(UniverseDefinitionRecord),
            [
                {
                    "universe_id": UNIVERSE_ID,
                    "name": "Synthetic broad research benchmark",
                    "kind": "POINT_IN_TIME_MEMBERSHIP",
                    "created_at": valid_from,
                }
            ],
        )
        connection.execute(insert(UniverseMembershipRecord), membership_rows)
        connection.execute(
            insert(StrategyRecord),
            [
                {
                    "strategy_identity": "benchmark-trend-1",
                    "strategy_name": "multi_asset_trend",
                    "strategy_version": "1.0.0",
                    "created_at": valid_from,
                    "metadata_json": "{}",
                }
            ],
        )
        connection.execute(
            insert(MarketDataIngestionRecord),
            [
                {
                    "id": INGESTION_ID,
                    "provider": PROVIDER,
                    "scope_hash": "c" * 64,
                    "started_at": first_timestamp,
                    "finished_at": last_timestamp,
                    "status": "SUCCEEDED",
                    "requested_start": first_timestamp,
                    "requested_end": last_timestamp,
                    "instrument_count": instruments,
                    "row_count": instruments * len(sessions),
                    "error_summary": None,
                }
            ],
        )

        observation_batch: list[dict[str, object]] = []
        for session_index, session_day in enumerate(sessions):
            timestamp = calendar.session_close(session_day)
            session_date = datetime.combine(session_day, datetime_time.min, UTC)
            for instrument_index in range(instruments):
                identity = observation_identity(instrument_index, session_index)
                digest = source_hash(instrument_index, session_index)
                cents = 10_000 + session_index * 7 + instrument_index % 97
                observation_batch.append(
                    {
                        "observation_id": identity,
                        "instrument_id": instrument_id(instrument_index),
                        "ingestion_id": INGESTION_ID,
                        "provider": PROVIDER,
                        "timeframe": "1d",
                        "session_date": session_date,
                        "timestamp": timestamp,
                        "open": str(Decimal(cents) / Decimal(100)),
                        "high": str(Decimal(cents + 30) / Decimal(100)),
                        "low": str(Decimal(cents - 20) / Decimal(100)),
                        "close": str(Decimal(cents + 10) / Decimal(100)),
                        "volume": str(100_000 + (instrument_index % 1000) * 10 + session_index),
                        "observed_at": timestamp,
                        "source_id": f"benchmark:{instrument_index}:{session_index}",
                        "source_hash": digest,
                        "revision": 1,
                    }
                )
                observation_entries.append({"id": identity, "revision": 1, "hash": digest})
                if len(observation_batch) == SEED_BATCH_SIZE:
                    connection.execute(insert(MarketObservationRecord), observation_batch)
                    observation_batch.clear()
        if observation_batch:
            connection.execute(insert(MarketObservationRecord), observation_batch)

        immutable_content = {
            "observations": observation_entries,
            "corporate_actions": [],
            "universe_memberships": canonical_memberships,
        }
        content_hash = canonical_snapshot_content_hash(immutable_content)
        manifest = json.dumps(
            {
                "schema_version": "4",
                "logical_identity": "broad-research-benchmark",
                "universe": {
                    "kind": "POINT_IN_TIME_MEMBERSHIP",
                    "survivorship_bias_status": "POINT_IN_TIME_SAFE",
                    "knowledge_as_of": last_timestamp.isoformat(),
                },
                **immutable_content,
                "expected_count": instruments * len(sessions),
                "present_count": instruments * len(sessions),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            insert(DatasetSnapshotRecord),
            [
                {
                    "snapshot_id": SNAPSHOT_ID,
                    "created_at": last_timestamp,
                    "as_of": last_timestamp,
                    "provider": PROVIDER,
                    "calendar_identity": calendar.identity,
                    "universe_id": UNIVERSE_ID,
                    "start_at": first_timestamp,
                    "end_at": last_timestamp,
                    "timeframe": "1d",
                    "content_hash": content_hash,
                    "status": "VALID",
                    "coverage": "1",
                    "manifest_json": manifest,
                }
            ],
        )
    repository.engine.dispose()


def run_child(database_path: Path, output_path: Path) -> int:
    engine = create_engine(f"sqlite:///{database_path}")
    sessions: sessionmaker[Session] = sessionmaker(engine, expire_on_commit=False)
    executed_assets: set[str] = set()
    original_apply_fill = MultiAssetPortfolio.apply_fill

    def audited_apply_fill(portfolio: MultiAssetPortfolio, fill: MultiAssetFill) -> None:
        executed_assets.add(fill.instrument_id)
        original_apply_fill(portfolio, fill)

    MultiAssetPortfolio.apply_fill = audited_apply_fill
    started = time.perf_counter()
    try:
        replay = Phase6ExperimentRunner(sessions).replay(
            Phase6ExperimentRequest(
                snapshot_id=SNAPSHOT_ID,
                strategy_name="multi_asset_trend",
                strategy_version="1.0.0",
                parameter_configs=(
                    {
                        "fast": 20,
                        "slow": 100,
                        "rebalance_frequency": RebalanceFrequency.MONTHLY,
                    },
                ),
                initial_cash=Decimal("10000000"),
                commission_bps=Decimal("1"),
                code_sha=CODE_SHA,
            )
        )
    finally:
        MultiAssetPortfolio.apply_fill = original_apply_fill
        engine.dispose()
    report = {
        "runner_seconds": round(time.perf_counter() - started, 3),
        "child_peak_rss_mib": round(peak_rss_mib(), 1),
        "execution_assets": len(executed_assets),
        "oos_trade_count": replay.oos.trade_count,
        "oos_sessions": len(replay.oos_sessions),
    }
    output_path.write_text(json.dumps(report, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruments", type=int, default=2000)
    parser.add_argument("--sessions", type=int, default=250)
    parser.add_argument("--max-rss-mib", type=float, default=1024)
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child-database", type=Path)
    parser.add_argument("--child-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.child_database is not None:
        if args.child_output is None:
            raise ValueError("Child benchmark vyžaduje output path")
        return run_child(args.child_database, args.child_output)

    selected_sessions = trading_sessions(args.sessions)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="quantlab-broad-research-") as temp_dir:
        temp_path = Path(temp_dir)
        database_path = temp_path / "benchmark.sqlite3"
        child_output = temp_path / "child.json"
        seed_database(database_path, args.instruments, selected_sessions)
        seeded_at = time.perf_counter()
        gc.collect()
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child-database",
                str(database_path),
                "--child-output",
                str(child_output),
            ],
            check=True,
        )
        child_report: dict[str, Any] = json.loads(child_output.read_text())
    finished = time.perf_counter()

    bar_count = args.instruments * args.sessions
    report = {
        "schema_version": 2,
        "path": "Phase6ExperimentRunner.replay",
        "bars": bar_count,
        "instruments": args.instruments,
        "sessions": args.sessions,
        "first_session": selected_sessions[0].isoformat(),
        "last_session": selected_sessions[-1].isoformat(),
        "fixture_seconds": round(seeded_at - started, 3),
        "wall_seconds": round(finished - started, 3),
        **child_report,
        "max_rss_mib": args.max_rss_mib,
        "max_seconds": args.max_seconds,
    }
    encoded = json.dumps(report, sort_keys=True, indent=2)
    print(encoded)
    if args.output is not None:
        args.output.write_text(encoded + "\n")

    if bar_count < 500_000:
        raise RuntimeError("Acceptance benchmark musí obsahovat alespoň 500000 barů")
    if report["execution_assets"] != args.instruments:
        raise RuntimeError("Phase6 runner neprovedl exekuci pro celý PIT universe")
    if report["oos_trade_count"] < args.instruments or report["oos_sessions"] < 1:
        raise RuntimeError("Phase6 OOS replay neprokázal úplné zpracování universe")
    if report["child_peak_rss_mib"] > args.max_rss_mib:
        raise RuntimeError(
            f"Child peak RSS {report['child_peak_rss_mib']} MiB překročilo "
            f"budget {args.max_rss_mib} MiB"
        )
    if report["wall_seconds"] > args.max_seconds:
        raise RuntimeError(
            f"Wall time {report['wall_seconds']} s překročil budget {args.max_seconds} s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
