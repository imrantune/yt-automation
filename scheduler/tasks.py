"""Celery scheduled tasks for generation and upload workflows."""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab

from config.settings import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)

celery_app = Celery("spartacus_tasks", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    "daily-generate-episode-2am": {
        "task": "scheduler.tasks.generate_episode",
        "schedule": crontab(hour=2, minute=0),
    },
    "weekly-batch-generate-sunday-1am": {
        "task": "scheduler.tasks.generate_week_batch",
        "schedule": crontab(hour=1, minute=0, day_of_week="sun"),
    },
}


@celery_app.task(
    name="scheduler.tasks.generate_episode",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=600,
    max_retries=3,
    soft_time_limit=3600,
    time_limit=3900,
)
def generate_episode(self) -> str:
    """Trigger one end-to-end generation run."""
    from main import run_pipeline

    logger.info("Triggered generate_episode task (attempt %s).", self.request.retries + 1)
    run_pipeline()
    return "ok"


@celery_app.task(
    name="scheduler.tasks.generate_week_batch",
    bind=True,
    soft_time_limit=28800,
    time_limit=29000,
)
def generate_week_batch(self) -> str:
    """Generate seven episodes for weekly buffer, continuing past individual failures."""
    from main import run_pipeline

    logger.info("Triggered generate_week_batch task.")
    successes = 0
    failures = 0
    for i in range(7):
        try:
            run_pipeline()
            successes += 1
            logger.info("Batch episode %d/7 completed.", i + 1)
        except Exception:
            failures += 1
            logger.exception("Batch episode %d/7 failed, continuing with remaining.", i + 1)
    result = f"Batch complete: {successes} succeeded, {failures} failed."
    logger.info(result)
    return result
