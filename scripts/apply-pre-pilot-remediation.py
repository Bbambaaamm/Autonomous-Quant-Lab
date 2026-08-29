from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one match in {path}, got {count}: {old[:80]!r}")
    file.write_text(text.replace(old, new))


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    file = Path(path)
    text = file.read_text()
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"Start marker not found in {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"End marker not found in {path}: {end!r}")
    file.write_text(text[:start_index] + replacement + text[end_index:])


# The earlier five-minute opening window was unsafe: the existing economic path still
# constructs target weights/order intent after observing the opening print. Keep the
# strict one-second fail-closed gate until a true pre-open immutable order-intent layer exists.
market = Path("backend/src/quantlab/market_data.py")
market_text = market.read_text()
market_text = market_text.replace(
    "    # Execution intent je deterministicky připnutý na XNYS open. Bounded window pouze\n"
    "    # dovoluje workeru po open získat a ověřit raw opening print bez retroaktivního intentu.\n"
    "    executable_open_window = timedelta(minutes=5)",
    "    executable_open_window = timedelta(seconds=1)",
)
market_text = market_text.replace(
    '        """Vrátí exkluzivní konec bounded okna pro kauzální raw-open execution."""',
    '        """Vrátí exkluzivní konec krátkého okna pro kauzální raw-open execution."""',
)
market_text = market_text.replace(
    '        """Ověří, že knowledge/run čas leží v bounded XNYS open okně."""',
    '        """Ověří, že skutečný knowledge/run čas leží v krátkém XNYS open okně."""',
)
if "executable_open_window = timedelta(minutes=5)" in market_text:
    raise SystemExit("Unsafe five-minute opening window still present")
if "executable_open_window = timedelta(seconds=1)" not in market_text:
    raise SystemExit("Expected strict one-second opening window is missing")
market.write_text(market_text)


# Automation managed-job policy and deterministic schedules.
replace_once(
    "backend/src/quantlab/automation.py",
    "TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.DEAD_LETTER, RunStatus.CANCELLED}\n",
    '''MANAGED_JOB_TYPES = frozenset(
    {
        JobType.RUN_PAPER_DEPLOYMENT,
        JobType.PREPARE_PAPER_SESSION,
        JobType.MONITOR_PAPER_DEPLOYMENT,
    }
)
AUTONOMOUS_DAILY_TIME = "09:30"
AUTONOMOUS_TIMEZONE = "America/New_York"
MONITOR_INTERVAL_SECONDS = 3600

TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.DEAD_LETTER, RunStatus.CANCELLED}
''',
)
replace_once(
    "backend/src/quantlab/automation.py",
    "\n\nclass AutomationRepository:\n",
    '''

def daily_occurrence_at_or_after(value: datetime, daily_time: str, timezone: str) -> datetime:
    """Vrátí první timezone-aware daily occurrence, která není před value."""
    current = utc(value)
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Neplatná časová zóna") from exc
    hour, minute = parse_daily_time(daily_time)
    local = current.astimezone(zone)
    candidate = datetime.combine(local.date(), time(hour, minute), zone).replace(fold=0)
    roundtrip = candidate.astimezone(UTC).astimezone(zone)
    if (roundtrip.hour, roundtrip.minute) != (hour, minute):
        candidate = roundtrip
    candidate_utc = candidate.astimezone(UTC)
    if candidate_utc < current:
        next_day = local.date() + timedelta(days=1)
        candidate = datetime.combine(next_day, time(hour, minute), zone).replace(fold=0)
        roundtrip = candidate.astimezone(UTC).astimezone(zone)
        if (roundtrip.hour, roundtrip.minute) != (hour, minute):
            candidate = roundtrip
        candidate_utc = candidate.astimezone(UTC)
    return candidate_utc


class AutomationRepository:
''',
)
replace_once(
    "backend/src/quantlab/automation.py",
    "    def create_deployment_job(self, *, deployment_id: str, **schedule: Any) -> ScheduledJob:\n",
    '''    @staticmethod
    def monitoring_job_id(monitoring_id: str) -> str:
        return hashlib.sha256(f"monitor-paper-deployment:{monitoring_id}".encode()).hexdigest()

    def ensure_monitoring_job(
        self,
        *,
        monitoring_id: str,
        account_id: str,
        now: datetime | None = None,
    ) -> ScheduledJob:
        """Idempotentně vytvoří nebo opraví deterministický monitoring schedule."""
        now = utc(now or datetime.now(UTC))
        job_id = self.monitoring_job_id(monitoring_id)
        expected_config = json.dumps({"monitoring_id": monitoring_id}, sort_keys=True)
        with Session(self.engine) as session, session.begin():
            row = session.get(ScheduledJob, job_id, with_for_update=True)
            if row is None:
                row = ScheduledJob(
                    id=job_id,
                    job_type=JobType.MONITOR_PAPER_DEPLOYMENT,
                    account_id=account_id,
                    strategy_id=None,
                    enabled=True,
                    schedule_type=ScheduleType.INTERVAL,
                    interval_seconds=MONITOR_INTERVAL_SECONDS,
                    daily_time=None,
                    timezone="UTC",
                    misfire_policy=MisfirePolicy.RUN_ONCE_IF_MISSED,
                    misfire_grace_seconds=MONITOR_INTERVAL_SECONDS,
                    next_run_at=now,
                    max_attempts=5,
                    config_json=expected_config,
                    correlation_metadata_json="{}",
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
            else:
                if (
                    row.job_type != JobType.MONITOR_PAPER_DEPLOYMENT
                    or row.account_id != account_id
                    or row.strategy_id is not None
                    or row.config_json != expected_config
                ):
                    raise ValueError("Monitoring job má konfliktní deterministickou identitu")
                drifted = (
                    not row.enabled
                    or row.schedule_type != ScheduleType.INTERVAL
                    or row.interval_seconds != MONITOR_INTERVAL_SECONDS
                    or row.daily_time is not None
                    or row.timezone != "UTC"
                    or row.misfire_policy != MisfirePolicy.RUN_ONCE_IF_MISSED
                    or row.misfire_grace_seconds != MONITOR_INTERVAL_SECONDS
                    or row.max_attempts != 5
                )
                row.enabled = True
                row.schedule_type = ScheduleType.INTERVAL
                row.interval_seconds = MONITOR_INTERVAL_SECONDS
                row.daily_time = None
                row.timezone = "UTC"
                row.misfire_policy = MisfirePolicy.RUN_ONCE_IF_MISSED
                row.misfire_grace_seconds = MONITOR_INTERVAL_SECONDS
                row.max_attempts = 5
                if drifted:
                    row.next_run_at = now
                row.updated_at = now
                session.flush()
            session.refresh(row)
            session.expunge(row)
            return row

    def create_deployment_job(self, *, deployment_id: str, **schedule: Any) -> ScheduledJob:
''',
)
replace_between(
    "backend/src/quantlab/automation.py",
    "    def set_autonomous_deployment(\n",
    "    def materialize_execution_session(\n",
    '''    def set_autonomous_deployment(
        self, *, deployment_id: str, enabled: bool, now: datetime | None = None
    ) -> ScheduledJob:
        """Idempotentně zapne nebo vypne opt-in XNYS-open orchestrace deploymentu."""
        from quantlab.persistence import StrategyDeploymentRecord
        from quantlab.phase7 import PaperMonitoringRunRecord

        now = utc(now or datetime.now(UTC))
        job_id = hashlib.sha256(f"paper-session:{deployment_id}".encode()).hexdigest()
        expected_config = json.dumps({"deployment_id": deployment_id}, sort_keys=True)
        next_open_schedule = daily_occurrence_at_or_after(
            now, AUTONOMOUS_DAILY_TIME, AUTONOMOUS_TIMEZONE
        )
        with Session(self.engine) as session:
            deployment = session.get(StrategyDeploymentRecord, deployment_id)
            if deployment is None:
                raise KeyError(deployment_id)
            if deployment.status != "APPROVED":
                raise ValueError("Autonomous orchestrace vyžaduje APPROVED deployment")
            active = session.scalar(
                select(PaperMonitoringRunRecord).where(
                    PaperMonitoringRunRecord.deployment_id == deployment_id,
                    PaperMonitoringRunRecord.state == "ACTIVE",
                )
            )
            if enabled and active is None:
                raise ValueError("Autonomous orchestrace vyžaduje ACTIVE monitoring")
            if enabled and active is not None:
                monitoring_job = session.get(
                    ScheduledJob, self.monitoring_job_id(active.monitoring_id)
                )
                expected_monitor_config = json.dumps(
                    {"monitoring_id": active.monitoring_id}, sort_keys=True
                )
                if (
                    monitoring_job is None
                    or not monitoring_job.enabled
                    or monitoring_job.job_type != JobType.MONITOR_PAPER_DEPLOYMENT
                    or monitoring_job.account_id != deployment.paper_account_id
                    or monitoring_job.strategy_id is not None
                    or monitoring_job.config_json != expected_monitor_config
                    or monitoring_job.schedule_type != ScheduleType.INTERVAL
                    or monitoring_job.interval_seconds != MONITOR_INTERVAL_SECONDS
                ):
                    raise ValueError(
                        "Autonomous orchestrace vyžaduje validní enabled monitoring schedule"
                    )
            existing = session.get(ScheduledJob, job_id)
            if existing is not None:
                if (
                    existing.job_type != JobType.PREPARE_PAPER_SESSION
                    or existing.account_id != deployment.paper_account_id
                    or existing.strategy_id is not None
                    or existing.config_json != expected_config
                ):
                    raise ValueError("Autonomous job má konfliktní deterministickou identitu")
                existing.enabled = enabled
                if enabled:
                    existing.schedule_type = ScheduleType.DAILY
                    existing.interval_seconds = None
                    existing.daily_time = AUTONOMOUS_DAILY_TIME
                    existing.timezone = AUTONOMOUS_TIMEZONE
                    existing.misfire_policy = MisfirePolicy.SKIP_IF_TOO_OLD
                    existing.misfire_grace_seconds = 1
                    existing.max_attempts = 1
                    existing.next_run_at = next_open_schedule
                existing.updated_at = now
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            account_id = deployment.paper_account_id
        return self.create_job(
            job_type=JobType.PREPARE_PAPER_SESSION,
            account_id=account_id,
            schedule_type=ScheduleType.DAILY,
            daily_time=AUTONOMOUS_DAILY_TIME,
            timezone=AUTONOMOUS_TIMEZONE,
            next_run_at=next_open_schedule,
            misfire_policy=MisfirePolicy.SKIP_IF_TOO_OLD,
            misfire_grace_seconds=1,
            max_attempts=1,
            config={"deployment_id": deployment_id},
            enabled=enabled,
            job_id=job_id,
        )

''',
)


# API: fail closed on unsupported production corporate actions, ensure monitoring schedule,
# protect managed jobs from generic mutation, and add an audited operator retry.
replace_once(
    "backend/src/quantlab/api.py",
    "    JobType,\n    MisfirePolicy,",
    "    JobType,\n    MANAGED_JOB_TYPES,\n    MisfirePolicy,",
)
replace_once(
    "backend/src/quantlab/api.py",
    '''    try:
        job = automation_repository.set_autonomous_deployment(
            deployment_id=deployment_id, enabled=enabled
        )''',
    '''    try:
        if enabled and not StooqProvider().metadata.supports_actions:
            raise DatasetInvalid(
                "CORPORATE_ACTIONS_UNSUPPORTED: production provider není způsobilý pro equity autonomous pilot"
            )
        job = automation_repository.set_autonomous_deployment(
            deployment_id=deployment_id, enabled=enabled
        )''',
)
replace_between(
    "backend/src/quantlab/api.py",
    "        monitoring_job_id = hashlib.sha256(\n",
    "        correlation_id = _correlation(request)\n",
    '''        monitoring_job = automation_repository.ensure_monitoring_job(
            monitoring_id=row.monitoring_id,
            account_id=account_id,
            now=now,
        )
''',
)
replace_once(
    "backend/src/quantlab/api.py",
    '''    if request.job_type in {JobType.RUN_PAPER_CYCLE, JobType.RUN_PAPER_DEPLOYMENT}:
        raise HTTPException(
            status_code=422,
            detail="Paper deployment lze plánovat pouze podporovanou operator deployment mutation",
        )''',
    '''    if request.job_type == JobType.RUN_PAPER_CYCLE or request.job_type in MANAGED_JOB_TYPES:
        raise HTTPException(
            status_code=422,
            detail="Managed PAPER job lze plánovat pouze podporovanou operator control-plane mutation",
        )''',
)
replace_once(
    "backend/src/quantlab/api.py",
    '''        if row is None:
            raise HTTPException(status_code=404, detail="Job nebyl nalezen")
        if request.enabled is not None:''',
    '''        if row is None:
            raise HTTPException(status_code=404, detail="Job nebyl nalezen")
        if JobType(row.job_type) in MANAGED_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail="Managed PAPER job lze měnit pouze operator control-plane cestou",
            )
        if request.enabled is not None:''',
)
replace_once(
    "backend/src/quantlab/api.py",
    '''    if not settings.automation_enabled:
        raise HTTPException(status_code=503, detail="Automation je globálně vypnutá")
    try:
        return {"id": automation_scheduler.run_now(job_id, idempotency_key)}''',
    '''    if not settings.automation_enabled:
        raise HTTPException(status_code=503, detail="Automation je globálně vypnutá")
    with Session(automation_repository.engine) as session:
        job = session.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job nebyl nalezen")
        if JobType(job.job_type) in MANAGED_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail="Managed PAPER job nepodporuje generic run-now",
            )
    try:
        return {"id": automation_scheduler.run_now(job_id, idempotency_key)}''',
)
replace_between(
    "backend/src/quantlab/api.py",
    '@app.post("/automation/runs/{run_id}/retry")\n',
    '@app.get("/operations/workers")\n',
    '''@app.post("/operator/automation/runs/{run_id}/retry")
def operator_retry_automation_run(
    run_id: str, body: ReasonedMutation, request: Request
) -> dict[str, object]:
    with Session(automation_repository.engine) as session:
        run = session.get(JobRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run nebyl nalezen")
        job = session.get(ScheduledJob, run.scheduled_job_id)
        if job is None or JobType(job.job_type) not in MANAGED_JOB_TYPES:
            raise HTTPException(
                status_code=409,
                detail="Operator retry je určen pouze pro managed PAPER jobs",
            )
    correlation_id = _correlation(request)
    try:
        automation_worker.retry(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run nebyl nalezen") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit_control_mutation(
        "CONTROL_AUTOMATION_RUN_RETRY",
        "job_run",
        run_id,
        _actor(request),
        body.reason,
        correlation_id,
    )
    with Session(automation_repository.engine) as session:
        refreshed = session.get(JobRun, run_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Run nebyl nalezen")
        return _row(refreshed)


@app.post("/automation/runs/{run_id}/retry")
def retry_automation_run(run_id: str) -> dict[str, str]:
    with Session(automation_repository.engine) as session:
        run = session.get(JobRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run nebyl nalezen")
        job = session.get(ScheduledJob, run.scheduled_job_id)
        if job is not None and JobType(job.job_type) in MANAGED_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail="Managed PAPER run vyžaduje auditovaný operator retry",
            )
    try:
        automation_worker.retry(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run nebyl nalezen") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": run_id, "status": "RETRY_SCHEDULED"}


''',
)


# Extend the authoritative PostgreSQL B1 control-plane test rather than creating a synthetic API stack.
replace_once(
    "backend/tests/test_b1_control_plane_postgres.py",
    "from datetime import date, datetime\n",
    "from datetime import UTC, date, datetime\n",
)
replace_once(
    "backend/tests/test_b1_control_plane_postgres.py",
    "import quantlab.api as api_module\n",
    "import quantlab.api as api_module\nfrom quantlab.automation import JobRun, RunStatus, ScheduledJob\n",
)
replace_once(
    "backend/tests/test_b1_control_plane_postgres.py",
    "from quantlab.phase7 import PaperMonitoringRunRecord\n",
    "from quantlab.phase4 import AuditEventRecord\nfrom quantlab.phase7 import PaperMonitoringRunRecord\n",
)
replace_once(
    "backend/tests/test_b1_control_plane_postgres.py",
    '''    retry = client.post(
        "/operator/monitoring/enrollments",
        json={
            "deployment_id": deployment_id,
            "policy_id": policy.json()["policy_id"],
            "reason": reason,
        },
    )
    assert retry.json()["monitoring_id"] == enrollment.json()["monitoring_id"]
    autonomous = client.post(
        f"/operator/deployments/{deployment_id}/autonomous/enable",
        json={"reason": reason},
    )
    assert autonomous.status_code == 200, autonomous.text
    assert autonomous.json()["enabled"] is True
''',
    '''    retry = client.post(
        "/operator/monitoring/enrollments",
        json={
            "deployment_id": deployment_id,
            "policy_id": policy.json()["policy_id"],
            "reason": reason,
        },
    )
    assert retry.json()["monitoring_id"] == enrollment.json()["monitoring_id"]
    monitoring_job_id = enrollment.json()["monitoring_job"]["id"]
    with Session(api_module.automation_repository.engine) as session:
        monitoring_job = session.get(ScheduledJob, monitoring_job_id)
        assert monitoring_job is not None
        monitoring_job.enabled = False
        session.commit()
    blocked_autonomous = client.post(
        f"/operator/deployments/{deployment_id}/autonomous/enable",
        json={"reason": reason},
    )
    assert blocked_autonomous.status_code == 409
    ensured = client.post(
        "/operator/monitoring/enrollments",
        json={
            "deployment_id": deployment_id,
            "policy_id": policy.json()["policy_id"],
            "reason": reason,
        },
    )
    assert ensured.status_code == 200, ensured.text
    assert ensured.json()["monitoring_job"]["id"] == monitoring_job_id
    assert ensured.json()["monitoring_job"]["enabled"] is True
    generic_disable = client.post(f"/automation/jobs/{monitoring_job_id}/disable")
    assert generic_disable.status_code == 422
    autonomous = client.post(
        f"/operator/deployments/{deployment_id}/autonomous/enable",
        json={"reason": reason},
    )
    assert autonomous.status_code == 200, autonomous.text
    assert autonomous.json()["enabled"] is True
    assert autonomous.json()["schedule_type"] == "DAILY"
    assert autonomous.json()["daily_time"] == "09:30"
    assert autonomous.json()["timezone"] == "America/New_York"

    recovery_run_id = api_module.automation_scheduler.run_now(
        autonomous.json()["id"], f"recovery-{suffix}"
    )
    with Session(api_module.automation_repository.engine) as session:
        recovery_run = session.get(JobRun, recovery_run_id)
        assert recovery_run is not None
        recovery_run.status = RunStatus.DEAD_LETTER
        recovery_run.finished_at = datetime.now(UTC)
        session.commit()
    legacy_retry = client.post(f"/automation/runs/{recovery_run_id}/retry")
    assert legacy_retry.status_code == 422
    audited_retry = client.post(
        f"/operator/automation/runs/{recovery_run_id}/retry",
        json={"reason": reason},
    )
    assert audited_retry.status_code == 200, audited_retry.text
    assert audited_retry.json()["status"] == "RETRY_SCHEDULED"
    with Session(api_module.paper_repository.engine) as session:
        audit = session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.event_type == "CONTROL_AUTOMATION_RUN_RETRY",
                AuditEventRecord.entity_id == recovery_run_id,
            )
        )
        assert audit is not None
        assert reason in audit.payload_json
        assert "api-admin" in audit.payload_json
''',
)


# Fast unit regressions: monitor ensure, DST anchoring and strict fail-closed open window.
Path("backend/tests/test_pre_pilot_review_remediation.py").write_text('''from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from quantlab.automation import (
    AUTONOMOUS_DAILY_TIME,
    AUTONOMOUS_TIMEZONE,
    MONITOR_INTERVAL_SECONDS,
    AutomationRepository,
    MisfirePolicy,
    ScheduleType,
    ScheduledJob,
    daily_occurrence_at_or_after,
)
from quantlab.market_data import XNYSCalendar
from quantlab.phase4 import Phase4Repository


def test_monitoring_schedule_ensure_reenables_and_normalizes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'monitoring.db'}"
    Phase4Repository(database_url, bootstrap_test_schema=True).seed_account()
    repository = AutomationRepository(database_url)
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)
    job = repository.ensure_monitoring_job(
        monitoring_id="monitoring-test", account_id="paper-main", now=now
    )
    with Session(repository.engine) as session:
        stored = session.get(ScheduledJob, job.id)
        assert stored is not None
        stored.enabled = False
        stored.interval_seconds = 60
        session.commit()
    repaired = repository.ensure_monitoring_job(
        monitoring_id="monitoring-test",
        account_id="paper-main",
        now=now + timedelta(minutes=1),
    )
    assert repaired.id == job.id
    assert repaired.enabled is True
    assert repaired.schedule_type == ScheduleType.INTERVAL
    assert repaired.interval_seconds == MONITOR_INTERVAL_SECONDS
    assert repaired.misfire_policy == MisfirePolicy.RUN_ONCE_IF_MISSED
    assert repaired.next_run_at == now + timedelta(minutes=1)


def test_autonomous_daily_occurrence_tracks_new_york_dst() -> None:
    summer = daily_occurrence_at_or_after(
        datetime(2026, 7, 6, 12, tzinfo=UTC),
        AUTONOMOUS_DAILY_TIME,
        AUTONOMOUS_TIMEZONE,
    )
    winter = daily_occurrence_at_or_after(
        datetime(2026, 1, 5, 13, tzinfo=UTC),
        AUTONOMOUS_DAILY_TIME,
        AUTONOMOUS_TIMEZONE,
    )
    assert summer == datetime(2026, 7, 6, 13, 30, tzinfo=UTC)
    assert winter == datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def test_executable_open_window_remains_fail_closed() -> None:
    calendar = XNYSCalendar()
    session = date(2026, 7, 6)
    opened = calendar.session_open(session)
    assert calendar.is_executable_open_time(session, opened)
    assert calendar.is_executable_open_time(session, opened + timedelta(milliseconds=999))
    assert not calendar.is_executable_open_time(session, opened + timedelta(seconds=1))
''')

replace_once(
    ".github/workflows/ci.yml",
    "          tests/test_phase7.py\n",
    "          tests/test_phase7.py\n          tests/test_pre_pilot_review_remediation.py\n",
)

# Give the production worker a chance to claim an exactly aligned one-second occurrence.
# This does not make the pilot READY; provider latency and full staging evidence remain hard gates.
replace_once(
    "docker-compose.production.yml",
    '      WORKER_ID_PREFIX: "${WORKER_ID_PREFIX:-production-worker}"\n',
    '      WORKER_ID_PREFIX: "${WORKER_ID_PREFIX:-production-worker}"\n      WORKER_POLL_INTERVAL: "0.2"\n',
)

Path("scripts/provider-egress-smoke.sh").write_text('''#!/bin/bash
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$REPO/.env.production}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REPO/docker-compose.production.yml")
if [ -n "${COMPOSE_OVERRIDE:-}" ]; then
  COMPOSE+=(-f "$COMPOSE_OVERRIDE")
fi

for service in backend worker; do
  echo "Checking market-data egress from $service..."
  "${COMPOSE[@]}" exec -T "$service" /app/backend/.venv/bin/python - <<'PY'
from datetime import date, timedelta
from quantlab.market_data import StooqProvider

end = date.today()
start = end - timedelta(days=14)
bars = StooqProvider(timeout=10, max_attempts=2).historical_daily("SPY", start, end)
if not bars:
    raise SystemExit("Stooq egress returned no daily bars")
print(f"Stooq egress OK: {len(bars)} bars, last={bars[-1].session_date}")
PY
done
''')
Path("scripts/provider-egress-smoke.sh").chmod(0o755)

# This patcher and its one-shot workflow must not remain in the final repository.
Path("scripts/apply-pre-pilot-remediation.py").unlink()
Path(".github/workflows/apply-pre-pilot-remediation.yml").unlink()
