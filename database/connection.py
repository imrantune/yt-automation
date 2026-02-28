"""Database engine/session management and transactional helpers."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings
from database.models import Episode, EpisodeStatus, JobLog, JobStatus, StepStatus, VideoJob


logger = logging.getLogger(__name__)
_settings = get_settings(require_api_keys=False)

engine = create_engine(_settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional session with automatic rollback and close."""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_video_job(session: Session, episode: Episode | None = None) -> VideoJob:
    """Create and persist a video job record."""
    job = VideoJob(
        episode_id=episode.id if episode else None,
        status=JobStatus.RUNNING,
        started_at=_utcnow(),
    )
    session.add(job)
    session.flush()
    return job


def log_job_step(session: Session, job_id: int, step: str, status: StepStatus, message: str) -> JobLog:
    """Write a structured job log row."""
    log = JobLog(job_id=job_id, step=step, status=status, message=message)
    session.add(log)
    session.flush()
    return log


def log_job_step_isolated(job_id: int, step: str, status: StepStatus, message: str) -> None:
    """Write a failure log in its own independent session/transaction so it survives rollbacks."""
    isolated = SessionLocal()
    try:
        log = JobLog(job_id=job_id, step=step, status=status, message=message)
        isolated.add(log)
        isolated.commit()
    except SQLAlchemyError:
        isolated.rollback()
        logger.exception("Failed writing isolated JobLog for job_id=%s step=%s", job_id, step)
    finally:
        isolated.close()


def set_episode_status(session: Session, episode: Episode, status: EpisodeStatus) -> None:
    """Update episode status."""
    episode.status = status
    session.add(episode)
    session.flush()


def mark_job_ready(session: Session, job: VideoJob, final_video_path: str, duration_seconds: float) -> None:
    """Mark video job complete and store output metadata."""
    job.status = JobStatus.READY
    job.final_video_path = final_video_path
    job.duration_seconds = duration_seconds
    job.completed_at = _utcnow()
    session.add(job)
    session.flush()


def mark_job_failed(session: Session, job: VideoJob) -> None:
    """Mark video job as failed."""
    job.status = JobStatus.FAILED
    job.completed_at = _utcnow()
    session.add(job)
    session.flush()
