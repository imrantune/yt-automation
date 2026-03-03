"""Whisper-based subtitle generation and SRT file creation."""

from __future__ import annotations

import logging
from pathlib import Path

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated
from database.models import Episode, Scene, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()


def _format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: list) -> str:
    """Convert Whisper TranscriptionSegment objects to SRT formatted string."""
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _format_srt_time(seg.start)
        end = _format_srt_time(seg.end)
        text = seg.text.strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


class SubtitleGenerator:
    """Generate SRT subtitle files from scene audio using Whisper API."""

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _transcribe(self, audio_path: Path) -> list:
        """Transcribe audio file and return segments with timestamps."""
        from pipeline.retry import retry_api_call

        @retry_api_call(max_retries=3, base_delay=3.0)
        def _call_whisper(client, path):
            with open(path, "rb") as f:
                resp = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            return resp

        response = _call_whisper(self.client, audio_path)
        return response.segments or []

    def generate_episode_subtitles(self, session: Session, job_id: int, episode: Episode) -> list[Scene]:
        """Generate SRT files for all scenes with audio."""
        try:
            log_job_step(session, job_id, "subtitles", StepStatus.STARTED, "Starting Whisper subtitle generation.")
            scenes = list(
                session.execute(
                    select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.scene_order.asc())
                ).scalars()
            )

            subs_dir = settings.output_root / "subtitles"
            subs_dir.mkdir(parents=True, exist_ok=True)

            generated = 0
            total_audio_seconds = 0.0
            for scene in scenes:
                if not scene.audio_file_path:
                    continue
                audio_path = Path(scene.audio_file_path)
                if not audio_path.exists():
                    logger.warning("Audio file missing for scene %s: %s", scene.scene_order, audio_path)
                    continue

                segments = self._transcribe(audio_path)
                if not segments:
                    logger.warning("Whisper returned no segments for scene %s", scene.scene_order)
                    continue

                if segments:
                    total_audio_seconds += segments[-1].end

                srt_path = subs_dir / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.srt"
                srt_path.write_text(_segments_to_srt(segments), encoding="utf-8")
                scene.subtitle_file_path = str(srt_path)
                session.add(scene)
                session.flush()
                generated += 1

            if total_audio_seconds > 0:
                from pipeline.cost_tracker import log_whisper
                log_whisper(session, episode.id, job_id, "subtitle_transcription", total_audio_seconds)

            log_job_step(
                session, job_id, "subtitles", StepStatus.SUCCESS,
                f"Generated {generated} subtitle files ({total_audio_seconds:.0f}s audio) for episode {episode.episode_number}.",
            )
            return scenes
        except Exception as exc:
            log_job_step_isolated(job_id, "subtitles", StepStatus.FAILED, f"Subtitle generation failed: {exc}")
            logger.exception("Subtitle generation failed for episode_id=%s", episode.id)
            raise
