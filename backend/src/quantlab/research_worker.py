from __future__ import annotations

import json
import sys
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from quantlab.config import get_settings
from quantlab.market_data import DatasetInvalid
from quantlab.phase6_runtime import Phase6ExperimentRequest, Phase6ExperimentRunner

MAX_REQUEST_BYTES = 1024 * 1024


def _request_from_payload(payload: object) -> Phase6ExperimentRequest:
    if not isinstance(payload, dict):
        raise ValueError("Research request musí být objekt")
    required = {
        "snapshot_id",
        "strategy_name",
        "strategy_version",
        "parameter_configs",
        "train_fraction",
        "validation_fraction",
        "initial_cash",
        "commission_bps",
        "seed",
        "code_sha",
    }
    if set(payload) != required:
        raise ValueError("Research request má neplatný kontrakt")
    parameter_configs = payload["parameter_configs"]
    if not isinstance(parameter_configs, list) or any(
        not isinstance(item, dict) for item in parameter_configs
    ):
        raise ValueError("Research parameter space má neplatný tvar")
    code_sha = payload["code_sha"]
    if code_sha is not None and not isinstance(code_sha, str):
        raise ValueError("Research code SHA má neplatný tvar")
    seed = payload["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Research seed má neplatný tvar")
    return Phase6ExperimentRequest(
        snapshot_id=str(payload["snapshot_id"]),
        strategy_name=str(payload["strategy_name"]),
        strategy_version=str(payload["strategy_version"]),
        parameter_configs=tuple(dict(item) for item in parameter_configs),
        train_fraction=Decimal(str(payload["train_fraction"])),
        validation_fraction=Decimal(str(payload["validation_fraction"])),
        initial_cash=Decimal(str(payload["initial_cash"])),
        commission_bps=Decimal(str(payload["commission_bps"])),
        seed=seed,
        code_sha=code_sha,
    )


def run_payload(payload: object) -> dict[str, Any]:
    request = _request_from_payload(payload)
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        row = Phase6ExperimentRunner(lambda: Session(engine)).run(request)
        return {"status": "ok", "experiment_id": row.id}
    finally:
        engine.dispose()


def main() -> None:
    raw = sys.stdin.read(MAX_REQUEST_BYTES + 1)
    if len(raw.encode()) > MAX_REQUEST_BYTES:
        sys.stdout.write(
            json.dumps(
                {"status": "validation_error", "error": "RESEARCH_REQUEST_TOO_LARGE"},
                separators=(",", ":"),
            )
            + "\n"
        )
        raise SystemExit(2)
    try:
        payload = json.loads(raw)
        result = run_payload(payload)
    except (ValueError, DatasetInvalid, json.JSONDecodeError) as exc:
        sys.stdout.write(
            json.dumps(
                {"status": "validation_error", "error": str(exc)[:1000]},
                separators=(",", ":"),
            )
            + "\n"
        )
        raise SystemExit(2) from None
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
