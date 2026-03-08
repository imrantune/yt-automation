"""End-to-end pipeline runner for a standalone short."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from config.settings import get_settings
from database.connection import (
    SessionLocal,
    create_video_job,
    log_job_step,
    mark_job_ready,
    set_episode_status,
)
from database.models import EpisodeStatus, JobStatus, StepStatus
from pipeline_control import clear_cancel, should_cancel
from pipeline.music import mix_episode_music
from pipeline.script_generator import ScriptGenerator
from pipeline.seo import SEOGenerator
from pipeline.sfx import mix_episode_sfx
from pipeline.subtitles import SubtitleGenerator
from pipeline.video_generator import VideoGenerator, merge_episode_assets
from pipeline.voiceover import VoiceoverGenerator
from pipeline.youtube_upload import YouTubeUploader


settings = get_settings()
logger = logging.getLogger(__name__)


def run_standalone_short(topic: str, character_name: str, custom_prompt: str = "") -> None:
    """Generate one standalone short end-to-end.
    
    Steps: Script (1 scene) -> SEO -> Voiceover -> Subtitles -> Video -> Merge -> Upload
    """
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

        job = create_video_job(session=session)
        session.commit()
        job_id = job.id

        # 1. Script generation
        generated_episode, _ = script_gen.generate_standalone_short(
            session=session, job_id=job.id, topic=topic, 
            character_name=character_name, custom_prompt=custom_prompt
        )
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

        # 3. Voiceover
        voice_gen.generate_episode_audio(session=session, job_id=job.id, episode=generated_episode)
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 4. Subtitles
        subtitle_gen.generate_episode_subtitles(session=session, job_id=job.id, episode=generated_episode)
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 5. Video clips (Should generate vertically natively now)
        video_gen.generate_episode_clips(session=session, job_id=job.id, episode=generated_episode)
        session.commit()
        if should_cancel():
            _cancel_and_exit(session, job, generated_episode)
            return

        # 6. Mix music and SFX
        scenes = list(generated_episode.scenes)
        log_job_step(session, job.id, "music", StepStatus.STARTED, "Mixing background music for short.")
        session.commit()
        mixed_audio_paths = mix_episode_music(generated_episode.episode_number, scenes)
        log_job_step(session, job.id, "music", StepStatus.SUCCESS, "Mixed music.")
        session.commit()

        log_job_step(session, job.id, "sfx", StepStatus.STARTED, "Layering SFX.")
        session.commit()
        mixed_audio_paths = mix_episode_sfx(generated_episode.episode_number, scenes, mixed_audio_paths)
        log_job_step(session, job.id, "sfx", StepStatus.SUCCESS, "SFX layered.")
        session.commit()

        # 7. Merge Assets
        set_episode_status(session, generated_episode, EpisodeStatus.EDITING)
        log_job_step(session, job.id, "editing", StepStatus.STARTED, "Merging short clips.")
        session.commit()

        clip_paths = [Path(s.video_clip_path) for s in scenes if s.video_clip_path]
        audio_paths = mixed_audio_paths if mixed_audio_paths else [
            Path(s.audio_file_path) for s in scenes if s.audio_file_path
        ]
        subtitle_paths = [
            Path(s.subtitle_file_path) for s in scenes if s.subtitle_file_path and Path(s.subtitle_file_path).exists()
        ]
        final_path = settings.final_dir / f"short_{generated_episode.episode_number:04d}.mp4"

        duration = merge_episode_assets(
            episode_number=generated_episode.episode_number,
            clip_paths=clip_paths,
            audio_paths=audio_paths,
            output_path=final_path,
            subtitle_paths=subtitle_paths,
            color_grade=True,
            add_intro=False,
            add_outro=False,
            is_short=True,
        )

        mark_job_ready(session, job=job, final_video_path=str(final_path), duration_seconds=duration)
        set_episode_status(session, generated_episode, EpisodeStatus.READY)
        log_job_step(session, job.id, "editing", StepStatus.SUCCESS, f"Final short video ({duration:.1f}s): {final_path}")
        session.commit()

        if youtube_enabled:
            # For standalone shorts we don't have a thumbnail currently, or we can use the first frame if needed
            uploader = YouTubeUploader()
            # We don't need the Shorts generator because this format is already 9:16.
            # However `upload_short` expects a `Short` DB record.
            # We bypass it and directly upload as a short (youtube uploader detects <60s and 9:16).
            # Actually, the YouTubeUpload.upload_video usually handles shorts too if vertical. 
            # We will just upload it.
            uploader.upload_video(
                session=session, job_id=job.id, episode=generated_episode,
                video_path=final_path, thumbnail_path=None, seo=seo,
            )
            session.commit()

        logger.info("Standalone short generation completed for episode %s", generated_episode.episode_number)

    except Exception as exc:
        session.rollback()
        logger.exception("Standalone short exception.")
        if job_id is not None:
            from main import _mark_failure
            _mark_failure(job_id, exc)
    finally:
        session.close()


def _cancel_and_exit(session, job, episode) -> None:
    from main import _cancel_and_exit
    _cancel_and_exit(session, job, episode)
