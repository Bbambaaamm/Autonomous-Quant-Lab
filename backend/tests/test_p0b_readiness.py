from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from quantlab.automation import AutomationRepository, WorkerService
from quantlab.config import Settings
from quantlab.operator_read_model import OperatorReadModel
from quantlab.phase4 import Phase4Repository


def test_autonomous_readiness_tracks_missing_stale_fresh_and_disabled_worker(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{tmp_path / 'readiness.db'}"
    Phase4Repository(database_url, bootstrap_test_schema=True).seed_account()
    repository = AutomationRepository(database_url)
    settings = Settings(
        database_url=database_url,
        automation_enabled=True,
        worker_lease_timeout=10,
        worker_heartbeat_interval=2,
    )
    model = OperatorReadModel(lambda: Session(repository.engine), settings)
    now = datetime(2026, 8, 27, 12, tzinfo=UTC)

    assert model.overview(now)["autonomous_readiness"] == "UNAVAILABLE"
    worker = WorkerService(repository, settings, worker_id="readiness-worker")
    worker.heartbeat(now=now - timedelta(seconds=11), scheduler_ticked=True)
    assert model.overview(now)["autonomous_readiness"] == "STALE"
    worker.heartbeat(now=now, scheduler_ticked=True)
    assert model.overview(now)["autonomous_readiness"] == "HEALTHY"

    disabled = Settings(database_url=database_url, automation_enabled=False)
    assert (
        OperatorReadModel(lambda: Session(repository.engine), disabled).overview(now)[
            "autonomous_readiness"
        ]
        == "DISABLED"
    )
