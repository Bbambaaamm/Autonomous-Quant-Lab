import logging

from quantlab.automation import AutomationRepository, SchedulerService, WorkerService
from quantlab.config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger = logging.getLogger("quantlab.worker")
    try:
        settings.validate_worker_runtime()
    except ValueError:
        logger.exception("Worker odmítl neplatnou production konfiguraci")
        raise
    repository = AutomationRepository(settings.database_url)
    scheduler = SchedulerService(repository)
    worker = WorkerService(repository, settings)
    logger.info(
        "Worker startuje: worker_id=%s scheduler_enabled=%s",
        worker.worker_id,
        settings.automation_enabled,
    )
    worker.install_signal_handlers()
    try:
        while not worker.stop_event.is_set():
            worker.heartbeat()
            scheduler.tick()
            worker.heartbeat(scheduler_ticked=True)
            worker.execute_one()
            worker.stop_event.wait(settings.worker_poll_interval)
    finally:
        worker.mark_stopped()
        logger.info("Worker byl korektně zastaven: worker_id=%s", worker.worker_id)


if __name__ == "__main__":
    main()
