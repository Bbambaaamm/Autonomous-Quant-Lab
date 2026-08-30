import logging

from quantlab.automation import AutomationRepository, JobExecutor, SchedulerService, WorkerService
from quantlab.config import get_settings
from quantlab.provider_factory import build_market_data_provider


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
    executor = JobExecutor(
        repository,
        provider_factory=lambda: build_market_data_provider(settings, repository.engine),
    )
    worker = WorkerService(repository, settings, executor=executor)
    reconciled = repository.reconcile_managed_schedules()
    if reconciled:
        logger.warning("Fail-closed disabled %s legacy/drifted autonomous schedules", reconciled)
    logger.info(
        "Worker startuje: worker_id=%s scheduler_enabled=%s market_data_provider=%s",
        worker.worker_id,
        settings.automation_enabled,
        settings.market_data_provider,
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
