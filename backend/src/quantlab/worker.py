from quantlab.automation import AutomationRepository, SchedulerService, WorkerService
from quantlab.config import get_settings


def main() -> None:
    settings = get_settings()
    repository = AutomationRepository(settings.database_url)
    scheduler = SchedulerService(repository)
    worker = WorkerService(repository, settings)
    worker.install_signal_handlers()
    while not worker.stop_event.is_set():
        worker.heartbeat()
        if settings.automation_enabled:
            scheduler.tick()
            worker.execute_one()
        worker.stop_event.wait(settings.worker_poll_interval)


if __name__ == "__main__":
    main()
