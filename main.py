"""End-to-end Phase 1 pipeline runner for one episode."""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import get_settings
from database.connection import (
    SessionLocal,
    create_video_job,
    log_job_step,
    mark_job_ready,
    set_episode_status,
)
from sqlalchemy import select

from database.models import EpisodeStatus, JobStatus, StepStatus, VideoJob
from pipeline.script_generator import ScriptGenerator
from pipeline.video_generator import VideoGenerator, merge_episode_assets
from pipeline.voiceover import VoiceoverGenerator


settings = get_settings()
_REQUIRED_API_KEYS = ["openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"]

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def run_pipeline() -> None:
    """Generate one full test episode end-to-end."""
    missing = [k for k in _REQUIRED_API_KEYS if not getattr(settings, k, "")]
    if missing:
        raise ValueError(f"Missing required API keys: {', '.join(missing)}. Set them in .env before running.")

    session = SessionLocal()
    job_id: int | None = None
    try:
        script_gen = ScriptGenerator()
        voice_gen = VoiceoverGenerator()
        video_gen = VideoGenerator()

        job = create_video_job(session=session)
        session.commit()
        job_id = job.id

        generated_episode, _ = script_gen.generate_and_persist(session=session, job_id=job.id)
        job.episode_id = generated_episode.id
        session.add(job)
        session.commit()

        voice_gen.generate_episode_audio(
            session=session,
            job_id=job.id,
            episode=generated_episode,
        )
        session.commit()

        video_gen.generate_episode_clips(
            session=session,
            job_id=job.id,
            episode=generated_episode,
        )
        session.commit()

        set_episode_status(session, generated_episode, EpisodeStatus.EDITING)
        log_job_step(session, job.id, "editing", StepStatus.STARTED, "Merging clips and audio with FFmpeg.")
        session.commit()

        scenes = list(generated_episode.scenes)
        clip_paths = [Path(s.video_clip_path) for s in scenes if s.video_clip_path]
        audio_paths = [Path(s.audio_file_path) for s in scenes if s.audio_file_path]
        final_path = settings.final_dir / f"episode_{generated_episode.episode_number:04d}.mp4"

        duration = merge_episode_assets(
            episode_number=generated_episode.episode_number,
            clip_paths=clip_paths,
            audio_paths=audio_paths,
            output_path=final_path,
        )

        mark_job_ready(session, job=job, final_video_path=str(final_path), duration_seconds=duration)
        set_episode_status(session, generated_episode, EpisodeStatus.READY)
        log_job_step(session, job.id, "editing", StepStatus.SUCCESS, f"Final video created: {final_path}")
        session.commit()

        logger.info(
            "Pipeline completed for episode %s at %s",
            generated_episode.episode_number,
            final_path,
        )
    except Exception as exc:
        session.rollback()
        logger.exception("Pipeline execution failed.")
        if job_id is not None:
            _mark_failure(job_id, exc)
        raise
    finally:
        session.close()


def _mark_failure(job_id: int, exc: Exception) -> None:
    """Mark job and episode as failed in an isolated session."""
    fail_session = SessionLocal()
    try:
        job = fail_session.execute(select(VideoJob).where(VideoJob.id == job_id)).scalar_one_or_none()
        if job is None:
            return
        job.status = JobStatus.FAILED
        if job.episode:
            job.episode.status = EpisodeStatus.FAILED
        fail_session.add(job)
        log_job_step(fail_session, job.id, "pipeline", StepStatus.FAILED, f"Pipeline failed: {exc}")
        fail_session.commit()
    except Exception:
        fail_session.rollback()
        logger.exception("Failed to persist failure state for job_id=%s", job_id)
    finally:
        fail_session.close()


if __name__ == "__main__":
    run_pipeline()
