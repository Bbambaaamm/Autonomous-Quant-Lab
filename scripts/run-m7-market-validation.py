#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from quantlab.m7_validation import run_m7_validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-id")
    parser.add_argument("--env-file")
    parser.add_argument("--db-host")
    args = parser.parse_args()
    if args.env_file:
        values = dotenv_values(Path(args.env_file))
        for key, value in values.items():
            if value is not None:
                os.environ.setdefault(key, value)
    raw = os.environ["DATABASE_URL"]
    url = make_url(raw)
    if args.db_host:
        url = url.set(host=args.db_host)
    engine = create_engine(
        url,
        connect_args={
            "connect_timeout": 5,
            "options": "-c default_transaction_read_only=on -c statement_timeout=30000",
        },
    )

    def sessions() -> Session:
        return Session(engine)

    try:
        report = run_m7_validation(sessions, deployment_id=args.deployment_id)
        print(json.dumps(report, sort_keys=True, separators=(",", ":"), default=str))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
