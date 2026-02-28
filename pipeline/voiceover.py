"""ElevenLabs voiceover generation for episode scenes."""

from __future__ import annotations

import logging
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated, set_episode_status
from database.models import Episode, EpisodeStatus, Scene, SceneStatus, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()


class VoiceoverGenerator:
    """Generate per-scene narration audio using ElevenLabs API."""

    def __init__(self) -> None:
        self.base_url = "https://api.elevenlabs.io/v1"
        self.timeout_seconds = 240

    def _synthesize(self, text: str, output_path: Path) -> None:
        endpoint = f"{self.base_url}/text-to-speech/{settings.elevenlabs_voice_id}"
        headers = {
            "xi-api-key": settings.elevenlabs_api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": settings.elevenlabs_model_id,
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
        }
        response = requests.post(endpoint, headers=headers, json=payload, timeout=self.timeout_seconds)
        response.raise_for_status()
        if len(response.content) < 1024:
            raise ValueError(f"ElevenLabs returned suspiciously small audio ({len(response.content)} bytes).")
        output_path.write_bytes(response.content)

    def generate_episode_audio(self, session: Session, job_id: int, episode: Episode) -> list[Scene]:
        """Generate voiceover audio files for every scene in an episode."""
        try:
            log_job_step(session, job_id, "voiceover", StepStatus.STARTED, "Starting ElevenLabs voice generation.")
            scenes = list(
                session.execute(
                    select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.scene_order.asc())
                ).scalars()
            )
            if not scenes:
                raise ValueError("No scenes found for voiceover generation.")

            for scene in scenes:
                if not scene.narration_text.strip():
                    logger.warning("Scene %s has empty narration, skipping voiceover.", scene.scene_order)
                    continue
                output_path = settings.audio_dir / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.mp3"
                self._synthesize(scene.narration_text, output_path)
                scene.audio_file_path = str(output_path)
                scene.status = SceneStatus.VOICEOVER_DONE
                session.add(scene)
                session.flush()

            set_episode_status(session, episode, EpisodeStatus.VIDEO_GEN)
            log_job_step(
                session,
                job_id,
                "voiceover",
                StepStatus.SUCCESS,
                f"Generated {len(scenes)} voiceover files for episode {episode.episode_number}.",
            )
            return scenes
        except Exception as exc:
            log_job_step_isolated(job_id, "voiceover", StepStatus.FAILED, f"Voiceover generation failed: {exc}")
            logger.exception("Voiceover generation failed for episode_id=%s", episode.id)
            raise
