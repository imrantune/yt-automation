"""Celery scheduled tasks for generation, upload, and retry workflows."""

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
    "daily-upload-episode-9am": {
        "task": "scheduler.tasks.upload_next_episode",
        "schedule": crontab(hour=9, minute=0),
    },
    "weekly-batch-generate-sunday-1am": {
        "task": "scheduler.tasks.generate_week_batch",
        "schedule": crontab(hour=1, minute=0, day_of_week="sun"),
    },
    "retry-failed-jobs-every-30min": {
        "task": "scheduler.tasks.retry_failed_jobs",
        "schedule": crontab(minute="*/30"),
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
    """Trigger one end-to-end generation run if fewer than 3 episodes are ready."""
    from sqlalchemy import func, select

    from database.connection import SessionLocal
    from database.models import Episode, EpisodeStatus

    session = SessionLocal()
    try:
        ready_count = session.execute(
            select(func.count()).select_from(Episode).where(Episode.status == EpisodeStatus.READY)
        ).scalar_one()
        if ready_count >= 3:
            logger.info("Already %d ready episodes, skipping generation.", ready_count)
            return f"skipped (ready={ready_count})"
    finally:
        session.close()

    from main import run_pipeline

    logger.info("Triggered generate_episode task (attempt %s).", self.request.retries + 1)
    run_pipeline()
    return "ok"


@celery_app.task(
    name="scheduler.tasks.upload_next_episode",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=600,
    max_retries=2,
    soft_time_limit=1800,
    time_limit=2000,
)
def upload_next_episode(self) -> str:
    """Upload the next ready episode to YouTube."""
    import os

    if not os.getenv("YOUTUBE_CLIENT_SECRET_PATH", ""):
        logger.info("YouTube upload not configured, skipping.")
        return "skipped (no credentials)"

    from pathlib import Path

    from sqlalchemy import select

    from database.connection import SessionLocal, log_job_step
    from database.models import Episode, EpisodeStatus, EpisodeSEO, Short, StepStatus, VideoJob
    from pipeline.youtube_upload import YouTubeUploader

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode)
            .where(Episode.status == EpisodeStatus.READY)
            .order_by(Episode.episode_number.asc())
            .limit(1)
        ).scalar_one_or_none()
        if not episode:
            logger.info("No ready episodes to upload.")
            return "skipped (none ready)"

        job = session.execute(
            select(VideoJob).where(VideoJob.episode_id == episode.id).order_by(VideoJob.id.desc()).limit(1)
        ).scalar_one_or_none()
        if not job or not job.final_video_path:
            logger.warning("Episode %d has no completed job with final video.", episode.episode_number)
            return f"skipped (episode {episode.episode_number} missing video)"

        seo = session.execute(
            select(EpisodeSEO).where(EpisodeSEO.episode_id == episode.id)
        ).scalar_one_or_none()

        uploader = YouTubeUploader()
        video_path = Path(job.final_video_path)
        thumb_path = Path(job.thumbnail_path) if job.thumbnail_path else None

        video_id = uploader.upload_video(
            session=session, job_id=job.id, episode=episode,
            video_path=video_path, thumbnail_path=thumb_path, seo=seo,
        )

        short = session.execute(
            select(Short).where(Short.episode_id == episode.id).limit(1)
        ).scalar_one_or_none()
        if short and short.file_path and Path(short.file_path).exists():
            uploader.upload_short(
                session=session, job_id=job.id, episode=episode,
                short=short, seo=seo,
            )

        session.commit()
        logger.info("Uploaded episode %d as %s.", episode.episode_number, video_id)
        return f"uploaded episode {episode.episode_number} ({video_id})"
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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


@celery_app.task(
    name="scheduler.tasks.retry_failed_jobs",
    bind=True,
    soft_time_limit=3600,
    time_limit=3900,
)
def retry_failed_jobs(self) -> str:
    """Find failed jobs and retry them with exponential backoff.

    Only retries jobs that have failed fewer than 3 times (tracked via job_logs).
    """
    from datetime import timedelta

    from sqlalchemy import func, select

    from database.connection import SessionLocal, log_job_step
    from database.models import JobLog, JobStatus, StepStatus, VideoJob

    session = SessionLocal()
    retried = 0
    skipped = 0
    try:
        failed_jobs = list(
            session.execute(
                select(VideoJob).where(VideoJob.status == JobStatus.FAILED).order_by(VideoJob.id.asc())
            ).scalars()
        )
        for job in failed_jobs:
            fail_count = session.execute(
                select(func.count())
                .select_from(JobLog)
                .where(JobLog.job_id == job.id, JobLog.step == "pipeline", JobLog.status == StepStatus.FAILED)
            ).scalar_one()
            if fail_count >= 3:
                skipped += 1
                continue

            if not job.episode_id:
                skipped += 1
                continue

            try:
                from main import run_pipeline

                job.status = JobStatus.RUNNING
                session.add(job)
                log_job_step(session, job.id, "retry", StepStatus.STARTED, f"Auto-retry #{fail_count + 1}")
                session.commit()

                run_pipeline()
                retried += 1
            except Exception as exc:
                session.rollback()
                logger.exception("Retry for job %d failed: %s", job.id, exc)

        result = f"Retry sweep: {retried} retried, {skipped} skipped (max retries or no episode)."
        logger.info(result)
        return result
    finally:
        session.close()
