"""ElevenLabs voiceover generation with per-character voice selection."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated, set_episode_status
from database.models import Character, Episode, EpisodeStatus, Scene, SceneStatus, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()

DEFAULT_NARRATOR_VOICE = settings.elevenlabs_voice_id


class VoiceoverGenerator:
    """Generate per-scene narration audio using ElevenLabs API with character voices."""

    def __init__(self) -> None:
        self.base_url = "https://api.elevenlabs.io/v1"
        self.timeout_seconds = 240

    def _synthesize(self, text: str, output_path: Path, voice_id: str | None = None) -> None:
        from pipeline.retry import retry_api_call

        vid = voice_id or DEFAULT_NARRATOR_VOICE
        endpoint = f"{self.base_url}/text-to-speech/{vid}"
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

        @retry_api_call(max_retries=3, base_delay=5.0)
        def _call_elevenlabs():
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=self.timeout_seconds)
            resp.raise_for_status()
            return resp

        response = _call_elevenlabs()
        if len(response.content) < 1024:
            raise ValueError(f"ElevenLabs returned suspiciously small audio ({len(response.content)} bytes).")
        output_path.write_bytes(response.content)

    def regenerate_scene_audio(self, scene: Scene, voice_id: str | None = None) -> str:
        """Regenerate voiceover for a single scene. Returns the output file path."""
        if not scene.narration_text.strip():
            raise ValueError("Scene has empty narration text.")
        episode_number = scene.episode.episode_number if scene.episode else 0
        output_path = settings.audio_dir / f"episode_{episode_number:04d}_scene_{scene.scene_order:02d}.mp3"
        self._synthesize(scene.narration_text, output_path, voice_id=voice_id)
        return str(output_path)

    def list_voices(self) -> list[dict]:
        """Fetch available voices from ElevenLabs."""
        endpoint = f"{self.base_url}/voices"
        headers = {"xi-api-key": settings.elevenlabs_api_key}
        response = requests.get(endpoint, headers=headers, timeout=30)
        response.raise_for_status()
        voices = response.json().get("voices", [])
        return [
            {
                "voice_id": v["voice_id"],
                "name": v.get("name", "Unknown"),
                "category": v.get("category", ""),
                "preview_url": v.get("preview_url", ""),
                "labels": v.get("labels", {}),
            }
            for v in voices
        ]

    def _pick_voice_for_scene(self, session: Session, scene: Scene) -> str:
        """Select a voice for the scene based on character mentions in narration.

        Strategy: scan narration text for known character names, pick the first
        character who has a voice_id set. Fallback to the default narrator voice.
        """
        characters = list(
            session.execute(
                select(Character).where(Character.is_alive.is_(True), Character.voice_id.isnot(None))
            ).scalars()
        )
        if not characters:
            return DEFAULT_NARRATOR_VOICE

        narration_lower = scene.narration_text.lower()
        for char in sorted(characters, key=lambda c: c.name):
            if re.search(r'\b' + re.escape(char.name.lower()) + r'\b', narration_lower):
                logger.debug("Scene %d: using voice of %s (%s)", scene.scene_order, char.name, char.voice_id)
                return char.voice_id

        return DEFAULT_NARRATOR_VOICE

    def generate_episode_audio(self, session: Session, job_id: int, episode: Episode) -> list[Scene]:
        """Generate voiceover audio files for every scene using character-appropriate voices."""
        try:
            log_job_step(session, job_id, "voiceover", StepStatus.STARTED, "Starting ElevenLabs voice generation.")
            scenes = list(
                session.execute(
                    select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.scene_order.asc())
                ).scalars()
            )
            if not scenes:
                raise ValueError("No scenes found for voiceover generation.")

            total_chars = 0
            voice_summary: list[str] = []
            for scene in scenes:
                if not scene.narration_text.strip():
                    logger.warning("Scene %s has empty narration, skipping voiceover.", scene.scene_order)
                    continue

                voice_id = self._pick_voice_for_scene(session, scene)
                output_path = settings.audio_dir / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.mp3"
                self._synthesize(scene.narration_text, output_path, voice_id=voice_id)
                total_chars += len(scene.narration_text)
                scene.audio_file_path = str(output_path)
                scene.status = SceneStatus.VOICEOVER_DONE
                session.add(scene)
                session.flush()

                char_name = "narrator"
                characters = list(session.execute(
                    select(Character).where(Character.voice_id == voice_id)
                ).scalars())
                if characters:
                    char_name = characters[0].name
                voice_summary.append(f"S{scene.scene_order}={char_name}")

            if total_chars > 0:
                from pipeline.cost_tracker import log_elevenlabs
                log_elevenlabs(session, episode.id, job_id, "voiceover_generation", total_chars)

            set_episode_status(session, episode, EpisodeStatus.VIDEO_GEN)
            summary = ", ".join(voice_summary) if voice_summary else "none"
            log_job_step(
                session,
                job_id,
                "voiceover",
                StepStatus.SUCCESS,
                f"Generated {len(scenes)} voiceover files ({total_chars:,} chars). Voices: {summary}",
            )
            return scenes
        except Exception as exc:
            log_job_step_isolated(job_id, "voiceover", StepStatus.FAILED, f"Voiceover generation failed: {exc}")
            logger.exception("Voiceover generation failed for episode_id=%s", episode.id)
            raise
