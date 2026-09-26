import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DASHBOARD))

from agent_platform_dashboard import production_contract as c  # noqa: E402
from agent_platform_dashboard import production_sources as sources  # noqa: E402
from agent_platform_dashboard.production_export import collect  # noqa: E402


def queue_row(**overrides):
    row = {
        "task_id": "github-issue-234-20260926T141907Z",
        "issue": 234,
        "issue_title": "Unified swarm telemetry",
        "issue_open": True,
        "scheduler_state": "active",
        "status": "running",
        "attempts": 1,
        "max_attempts": 4,
        "not_before": None,
        "updated_at": 100,
        "agent": "quantlab-hermes",
        "kind": "github_issue_slice",
        "blocker": None,
        "pr_number": None,
    }
    row.update(overrides)
    return row
def codex_payload(observed_at=100):
    return {
        "version": 1,
        "observed_at": observed_at,
        "rate_limit": {
            "used_percent": 82,
            "window_minutes": 10080,
            "resets_at": 200,
            "ordinary_usage_allowed": True,
            "has_credits": False,
            "credits_unlimited": False,
            "credits_balance": "0",
            "reset_credits_available": 0,
        },
        "usage": {
            "lifetime_tokens": 123456789,
            "peak_daily_tokens": 9000000,
            "longest_running_turn_sec": 1234,
            "current_streak_days": 7,
            "longest_streak_days": 9,
            "daily": [
                {"day": "2026-09-24", "tokens": 1000},
                {"day": "2026-09-25", "tokens": 2000},
            ],
        },
        "limit_history": [
            {"at": 90, "used_percent": 79},
            {"at": 100, "used_percent": 82},
        ],
    }


class ObservabilityContractTests(unittest.TestCase):
    def test_source_matrix_is_closed_and_asymmetric(self):
        self.assertEqual(len(c.SOURCE_PAIRS), 14)
        self.assertIn(("quantlab", "queue"), c.SOURCE_PAIRS)
        self.assertNotIn(("majak", "queue"), c.SOURCE_PAIRS)
        self.assertIn(("majak", "codex"), c.SOURCE_PAIRS)
        self.assertNotIn(("quantlab", "codex"), c.SOURCE_PAIRS)
    def test_queue_v2_projection_accepts_only_sanitized_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.json"
            payload = {
                "version": 2,
                "profile": "quantlab",
                "observed_at": 100,
                "tasks": [queue_row()],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            original = sources.QUEUE_PATH
            sources.QUEUE_PATH = str(path)
            try:
                rows, stamp = sources.queue(str(path), "quantlab")
                self.assertEqual(stamp, 100)
                self.assertEqual(rows, [payload["tasks"][0]])
                self.assertNotIn("prompt", json.dumps(rows))
                with self.assertRaises(ValueError):
                    sources.queue(str(path), "majak")
                payload["tasks"][0]["prompt"] = "PRIVATE"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    sources.queue(str(path), "quantlab")
            finally:
                sources.QUEUE_PATH = original

    def test_codex_usage_projection_exposes_allowance_without_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "codex.json"
            path.write_text(json.dumps(codex_payload()), encoding="utf-8")
            original = sources.CODEX_USAGE_PATH
            sources.CODEX_USAGE_PATH = str(path)
            try:
                rows, stamp = sources.codex(str(path), "majak")
                self.assertEqual(stamp, 100)
                self.assertEqual(rows[0]["used_percent"], 82)
                self.assertEqual(rows[0]["lifetime_tokens"], 123456789)
                rendered = json.dumps(rows).lower()
                self.assertNotIn("account_id", rendered)
                self.assertNotIn("access_token", rendered)
                self.assertNotIn("prompt", rendered)
                with self.assertRaises(ValueError):
                    sources.codex(str(path), "quantlab")
            finally:
                sources.CODEX_USAGE_PATH = original
    def test_search_projection_is_bounded_operational_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "search.db"
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE searches("
                "id INTEGER PRIMARY KEY,started_at INTEGER,ended_at INTEGER,"
                "route_mode TEXT,actual_provider TEXT,fallback_provider TEXT,"
                "fallback_used INTEGER,duration_ms INTEGER,result_count INTEGER,"
                "extract_count INTEGER,success INTEGER,cost_usd REAL,"
                "query_hash TEXT,query_chars INTEGER)"
            )
            db.execute(
                "INSERT INTO searches VALUES("
                "1,10,11,'fast','exa-keyless','exa-keyless',1,125,5,0,1,0.0,"
                "'PRIVATEHASH',99)"
            )
            db.commit()
            db.close()
            rows, stamp = sources.search(str(path), "majak")
            self.assertEqual(stamp, 11)
            self.assertEqual(rows[0]["searches"], 1)
            self.assertEqual(rows[0]["fallback_count"], 1)
            self.assertEqual(rows[0]["cost_microusd"], 0)
            self.assertNotIn("PRIVATEHASH", json.dumps(rows))

    def test_collect_materializes_queue_and_codex_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = root / "queue.json"
            codex = root / "codex.json"
            queue.write_text(json.dumps({
                "version": 2,
                "profile": "quantlab",
                "observed_at": 100,
                "tasks": [queue_row()],
            }), encoding="utf-8")
            codex.write_text(json.dumps(codex_payload()), encoding="utf-8")
            old_queue, old_codex = sources.QUEUE_PATH, sources.CODEX_USAGE_PATH
            sources.QUEUE_PATH, sources.CODEX_USAGE_PATH = str(queue), str(codex)
            try:
                config = {
                    "version": 1,
                    "output": str(root / "snapshot.json"),
                    "herdr": None,
                    "profiles": {
                        profile: {
                            "router": None,
                            "search": None,
                            "kanban": None,
                            "git": None,
                            "tests": None,
                        }
                        for profile in c.PROFILES
                    },
                }
                snapshot = collect(config, 100)
                self.assertEqual(len(snapshot["sources"]), 14)
                q = next(s for s in snapshot["sources"] if s["kind"] == "queue")
                x = next(s for s in snapshot["sources"] if s["kind"] == "codex")
                self.assertEqual(q["status"], "available")
                self.assertEqual(x["status"], "available")
                self.assertEqual(x["rows"][0]["used_percent"], 82)

                stale = collect(config, 1001)
                x = next(s for s in stale["sources"] if s["kind"] == "codex")
                self.assertEqual((x["status"], x["reason"], x["rows"]),
                                 ("unavailable", "stale", []))
            finally:
                sources.QUEUE_PATH, sources.CODEX_USAGE_PATH = old_queue, old_codex


if __name__ == "__main__":
    unittest.main()
