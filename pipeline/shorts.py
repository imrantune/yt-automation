"""YouTube Shorts extractor from full episodes."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated
from database.models import Episode, Scene, SceneType, Short, StepStatus
from pipeline.video_generator import _run_ffmpeg


logger = logging.getLogger(__name__)
settings = get_settings()

SHORTS_MAX_DURATION = 59


class ShortsGenerator:
    """Extract YouTube Shorts from episode climax scenes."""

    def __init__(self) -> None:
        self.shorts_dir = settings.output_root / "shorts"
        self.shorts_dir.mkdir(parents=True, exist_ok=True)

    def _pick_climax_scene(self, scenes: list[Scene]) -> Scene | None:
        """Find the climax scene, fallback to the longest scene."""
        for scene in scenes:
            if scene.scene_type == SceneType.CLIMAX:
                return scene
        return scenes[-1] if scenes else None

    def _get_clip_duration(self, clip_path: Path) -> float:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(clip_path)],
            capture_output=True, text=True,
        )
        try:
            return float(probe.stdout.strip())
        except (ValueError, TypeError):
            return 0.0

    def generate_short(self, session: Session, job_id: int, episode: Episode) -> Short | None:
        """Extract a vertical short from the climax scene."""
        try:
            log_job_step(session, job_id, "shorts", StepStatus.STARTED, "Extracting YouTube Short.")

            scenes = list(
                session.execute(
                    select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.scene_order.asc())
                ).scalars()
            )
            scene = self._pick_climax_scene(scenes)
            if not scene or not scene.video_clip_path or not scene.audio_file_path:
                log_job_step(session, job_id, "shorts", StepStatus.SUCCESS, "No suitable scene for Short, skipping.")
                return None

            clip_path = Path(scene.video_clip_path)
            audio_path = Path(scene.audio_file_path)
            if not clip_path.exists() or not audio_path.exists():
                log_job_step(session, job_id, "shorts", StepStatus.SUCCESS, "Scene files missing, skipping Short.")
                return None

            duration = min(self._get_clip_duration(clip_path), SHORTS_MAX_DURATION)
            if duration <= 0:
                duration = SHORTS_MAX_DURATION

            short_path = self.shorts_dir / f"episode_{episode.episode_number:04d}_short.mp4"

            vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"

            srt_path = Path(scene.subtitle_file_path) if scene.subtitle_file_path else None
            has_subs = srt_path and srt_path.exists()

            cmd = ["ffmpeg", "-y",
                   "-i", str(clip_path.resolve()),
                   "-i", str(audio_path.resolve())]

            if has_subs:
                cmd.extend(["-i", str(srt_path.resolve())])

            cmd.extend([
                "-t", str(duration),
                "-vf", vf,
                "-map", "0:v", "-map", "1:a",
            ])

            if has_subs:
                cmd.extend(["-map", "2:s", "-c:s", "mov_text"])

            cmd.extend([
                "-c:v", settings.video_codec, "-preset", "veryfast", "-b:v", "4000k",
                "-c:a", settings.audio_codec, "-b:a", settings.audio_bitrate,
                "-shortest",
                str(short_path.resolve()),
            ])

            _run_ffmpeg(cmd, "generate short")

            short = Short(episode_id=episode.id, file_path=str(short_path))
            session.add(short)
            session.flush()

            log_job_step(
                session, job_id, "shorts", StepStatus.SUCCESS,
                f"Short generated: {short_path}",
            )
            return short
        except Exception as exc:
            log_job_step_isolated(job_id, "shorts", StepStatus.FAILED, f"Short generation failed: {exc}")
            logger.exception("Short generation failed for episode_id=%s", episode.id)
            raise
