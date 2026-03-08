"""End-to-end pipeline runner for one episode (Phase 1 + Phase 2)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select

from config.settings import get_settings
from database.connection import (
    SessionLocal,
    create_video_job,
    log_job_step,
    mark_job_ready,
    set_episode_status,
)
from database.models import EpisodeStatus, JobStatus, StepStatus, VideoJob
from pipeline_control import clear_cancel, should_cancel
from pipeline.music import mix_episode_music
from pipeline.script_generator import ScriptGenerator
from pipeline.seo import SEOGenerator
from pipeline.sfx import mix_episode_sfx
from pipeline.shorts import ShortsGenerator
from pipeline.subtitles import SubtitleGenerator
from pipeline.thumbnail import ThumbnailGenerator
from pipeline.video_generator import VideoGenerator, merge_episode_assets
from pipeline.voiceover import VoiceoverGenerator
from pipeline.youtube_upload import YouTubeUploader


settings = get_settings()
_REQUIRED_API_KEYS = ["openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"]

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def run_pipeline() -> None:
    """Generate one full episode end-to-end with all Phase 2 enhancements.

    Steps: Script → SEO → Voiceover → Subtitles → Video Clips →
           Music Mix → FFmpeg Merge (with color grade + intro/outro) →
           Thumbnail → Shorts → YouTube Upload
    """
    missing = [k for k in _REQUIRED_API_KEYS if not getattr(settings, k, "")]
    if missing:
        raise ValueError(f"Missing required API keys: {', '.join(missing)}. Set them in .env before running.")

    youtube_enabled = bool(os.getenv("YOUTUBE_CLIENT_SECRET_PATH", ""))

    clear_cancel()
    session = SessionLocal()
    job_id: int | None = None
    try:
        script_gen = ScriptGenerator()
        seo_gen = SEOGenerator()
        voice_gen = VoiceoverGenerator()
        subtitle_gen = SubtitleGenerator()
        video_gen = VideoGenerator()
        thumb_gen = ThumbnailGenerator()
        shorts_gen = ShortsGenerator()

        try:
            job = create_video_job(session=session)
            session.commit()
            job_id = job.id
        except Exception as job_exc:
            session.rollback()
            raise RuntimeError(f"Failed to create video job: {job_exc}") from job_exc

        # 1. Script generation
        generated_episode, _ = script_gen.generate_and_persist(session=session, job_id=job.id)
        job.episode_id = generated_episode.id
        session.add(job)
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 2. SEO metadata
        seo = seo_gen.generate_seo(session=session, job_id=job.id, episode=generated_episode)
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 3. Voiceover (per-character voices)
        voice_gen.generate_episode_audio(
            session=session, job_id=job.id, episode=generated_episode,
        )
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 4. Subtitles from audio
        subtitle_gen.generate_episode_subtitles(
            session=session, job_id=job.id, episode=generated_episode,
        )
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 5. Video clips (Minimax default, with fallback)
        video_gen.generate_episode_clips(
            session=session, job_id=job.id, episode=generated_episode,
        )
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 6. Mix background music with narration audio
        scenes = list(generated_episode.scenes)
        log_job_step(session, job.id, "music", StepStatus.STARTED, "Mixing background music per scene type.")
        session.commit()
        mixed_audio_paths = mix_episode_music(generated_episode.episode_number, scenes)
        log_job_step(session, job.id, "music", StepStatus.SUCCESS,
                     f"Mixed music for {len(scenes)} scenes.")
        session.commit()

        # 6b. Layer SFX (sword clashes, crowd roars, etc.) onto music+narration
        log_job_step(session, job.id, "sfx", StepStatus.STARTED, "Layering SFX (sword clashes, crowd roars).")
        session.commit()
        mixed_audio_paths = mix_episode_sfx(generated_episode.episode_number, scenes, mixed_audio_paths)
        log_job_step(session, job.id, "sfx", StepStatus.SUCCESS,
                     f"SFX layered for {len(scenes)} scenes.")
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 7. FFmpeg merge (video + mixed audio + subtitles + color grade + intro/outro)
        set_episode_status(session, generated_episode, EpisodeStatus.EDITING)
        log_job_step(session, job.id, "editing", StepStatus.STARTED, "Merging clips with color grade, intro/outro.")
        session.commit()

        clip_paths = [Path(s.video_clip_path) for s in scenes if s.video_clip_path]
        audio_paths = mixed_audio_paths if mixed_audio_paths else [
            Path(s.audio_file_path) for s in scenes if s.audio_file_path
        ]
        subtitle_paths = [
            Path(s.subtitle_file_path) for s in scenes if s.subtitle_file_path and Path(s.subtitle_file_path).exists()
        ]
        final_path = settings.final_dir / f"episode_{generated_episode.episode_number:04d}.mp4"

        duration = merge_episode_assets(
            episode_number=generated_episode.episode_number,
            clip_paths=clip_paths,
            audio_paths=audio_paths,
            output_path=final_path,
            subtitle_paths=subtitle_paths,
            color_grade=True,
            add_intro=not generated_episode.is_short,
            add_outro=not generated_episode.is_short,
            is_short=generated_episode.is_short,
        )

        mark_job_ready(session, job=job, final_video_path=str(final_path), duration_seconds=duration)
        set_episode_status(session, generated_episode, EpisodeStatus.READY)
        log_job_step(
            session, job.id, "editing", StepStatus.SUCCESS,
            f"Final video ({duration:.1f}s) with color grade and intro/outro: {final_path}",
        )
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 8. Thumbnail
        thumb_path = thumb_gen.generate_thumbnail(
            session=session, job_id=job.id, episode=generated_episode, job=job,
        )
        session.commit()

        # 9. Shorts
        short = shorts_gen.generate_short(
            session=session, job_id=job.id, episode=generated_episode,
        )
        session.commit()

        # 10. YouTube upload (if configured)
        if youtube_enabled:
            uploader = YouTubeUploader()
            uploader.upload_video(
                session=session, job_id=job.id, episode=generated_episode,
                video_path=final_path, thumbnail_path=thumb_path, seo=seo,
            )
            if short:
                uploader.upload_short(
                    session=session, job_id=job.id, episode=generated_episode,
                    short=short, seo=seo,
                )
            session.commit()

        logger.info(
            "Pipeline completed for episode %s (%s, %.1fs)",
            generated_episode.episode_number,
            final_path,
            duration,
        )
    except Exception as exc:
        session.rollback()
        logger.exception("Pipeline execution failed.")
        if job_id is not None:
            _mark_failure(job_id, exc)
        raise
    finally:
        session.close()


def _cancel_and_exit(session, job, episode) -> None:
    """Mark job and episode as failed due to user cancel, clear cancel flag, commit."""
    from pipeline_control import clear_cancel
    clear_cancel()
    job.status = JobStatus.FAILED
    episode.status = EpisodeStatus.FAILED
    session.add(job)
    session.add(episode)
    log_job_step(session, job.id, "pipeline", StepStatus.FAILED, "Cancelled by user.")
    session.commit()
    logger.info("Pipeline cancelled by user for job_id=%s", job.id)


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
