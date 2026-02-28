"""Video generation providers and final assembly utilities."""

from __future__ import annotations

import logging
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated
from database.models import Episode, Scene, SceneStatus, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()


class ProviderError(RuntimeError):
    """Raised when a provider fails to generate clip output."""


class BaseVideoProvider(ABC):
    """Common contract for all scene video generators."""

    name: str

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return whether this provider should be attempted."""

    @abstractmethod
    def generate_clip(self, prompt: str, output_path: Path, duration_seconds: int) -> None:
        """Generate clip to output_path or raise ProviderError."""


class Wan21Provider(BaseVideoProvider):
    """Local Wan 2.1 generation via diffusers on Apple Silicon."""

    name = "wan21"

    def __init__(self) -> None:
        self._pipe: Any | None = None

    def is_enabled(self) -> bool:
        return settings.wan21_enabled

    def _ensure_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe
        try:
            import torch
            from diffusers import DiffusionPipeline
            from diffusers.utils import export_to_video
        except ImportError as exc:
            raise ProviderError("Wan21 dependencies missing. Install diffusers/torch.") from exc

        device = settings.wan21_device
        dtype = torch.float16 if device in {"cuda", "mps"} else torch.float32
        pipe = DiffusionPipeline.from_pretrained(settings.wan21_model_id, torch_dtype=dtype)
        pipe = pipe.to(device)
        self._pipe = (pipe, export_to_video)
        return self._pipe

    def generate_clip(self, prompt: str, output_path: Path, duration_seconds: int) -> None:
        try:
            pipe, export_to_video = self._ensure_pipeline()
            num_frames = min(settings.wan21_max_frames, max(24, duration_seconds * settings.video_fps))
            result = pipe(
                prompt=prompt,
                num_frames=num_frames,
                num_inference_steps=30,
                guidance_scale=7.0,
            )
            frames = result.frames[0] if isinstance(result.frames, list) and result.frames else result.frames
            export_to_video(frames, str(output_path), fps=settings.video_fps)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Wan21 generation failed: {exc}") from exc


class MinimaxProvider(BaseVideoProvider):
    """Minimax API video generation provider."""

    name = "minimax"

    def is_enabled(self) -> bool:
        return settings.minimax_enabled and bool(settings.minimax_api_key)

    def generate_clip(self, prompt: str, output_path: Path, duration_seconds: int) -> None:
        try:
            create_endpoint = f"{settings.minimax_api_base.rstrip('/')}/v1/video/generations"
            headers = {"Authorization": f"Bearer {settings.minimax_api_key}", "Content-Type": "application/json"}
            payload = {
                "prompt": prompt,
                "duration": duration_seconds,
                "resolution": settings.video_resolution,
                "fps": settings.video_fps,
            }
            create_res = requests.post(create_endpoint, headers=headers, json=payload, timeout=120)
            create_res.raise_for_status()
            job_id = create_res.json()["id"]

            status_endpoint = f"{settings.minimax_api_base.rstrip('/')}/v1/video/generations/{job_id}"
            for attempt in range(40):
                poll_res = requests.get(status_endpoint, headers=headers, timeout=60)
                poll_res.raise_for_status()
                data = poll_res.json()
                status = str(data.get("status", "")).lower()
                logger.debug("Minimax job %s poll #%d: status=%s", job_id, attempt + 1, status)
                if status == "succeeded":
                    media_url = data["output_url"]
                    media_res = requests.get(media_url, timeout=180)
                    media_res.raise_for_status()
                    output_path.write_bytes(media_res.content)
                    return
                if status in {"failed", "cancelled"}:
                    raise ProviderError(f"Minimax job {job_id} ended with status={status}")
                time.sleep(8)
            raise ProviderError("Minimax timed out waiting for generation result.")
        except ProviderError:
            raise
        except (requests.RequestException, KeyError, OSError) as exc:
            raise ProviderError(f"Minimax generation failed: {exc}") from exc


class RunwayProvider(BaseVideoProvider):
    """Runway API video generation provider."""

    name = "runway"

    def is_enabled(self) -> bool:
        return settings.runway_enabled and bool(settings.runway_api_key)

    def generate_clip(self, prompt: str, output_path: Path, duration_seconds: int) -> None:
        try:
            create_endpoint = f"{settings.runway_api_base.rstrip('/')}/v1/generate/video"
            headers = {"Authorization": f"Bearer {settings.runway_api_key}", "Content-Type": "application/json"}
            payload = {
                "promptText": prompt,
                "seconds": duration_seconds,
                "resolution": settings.video_resolution,
            }
            create_res = requests.post(create_endpoint, headers=headers, json=payload, timeout=120)
            create_res.raise_for_status()
            task_id = create_res.json()["id"]

            status_endpoint = f"{settings.runway_api_base.rstrip('/')}/v1/tasks/{task_id}"
            for attempt in range(40):
                poll_res = requests.get(status_endpoint, headers=headers, timeout=60)
                poll_res.raise_for_status()
                data = poll_res.json()
                status = str(data.get("status", "")).lower()
                logger.debug("Runway task %s poll #%d: status=%s", task_id, attempt + 1, status)
                if status == "succeeded":
                    media_url = data["output"][0]
                    media_res = requests.get(media_url, timeout=180)
                    media_res.raise_for_status()
                    output_path.write_bytes(media_res.content)
                    return
                if status in {"failed", "cancelled"}:
                    raise ProviderError(f"Runway task {task_id} ended with status={status}")
                time.sleep(8)
            raise ProviderError("Runway timed out waiting for generation result.")
        except ProviderError:
            raise
        except (requests.RequestException, KeyError, IndexError, OSError) as exc:
            raise ProviderError(f"Runway generation failed: {exc}") from exc


class VideoGenerator:
    """Generate scene clips with provider failover and final merge."""

    def __init__(self) -> None:
        self.providers: dict[str, BaseVideoProvider] = {
            "wan21": Wan21Provider(),
            "minimax": MinimaxProvider(),
            "runway": RunwayProvider(),
        }

    def _scene_prompt(self, episode: Episode, scene: Scene) -> str:
        return (
            f"Spartacus arena cinematic scene for episode {episode.episode_number}. "
            f"Scene type: {scene.scene_type.value}. "
            "Dark high-contrast warm shadows, ancient Roman colosseum atmosphere, "
            f"narration context: {scene.narration_text[:750]}"
        )

    def _generate_with_fallback(self, prompt: str, output_path: Path, duration_seconds: int) -> str:
        errors: list[str] = []
        for provider_key in settings.video_provider_order:
            provider = self.providers.get(provider_key)
            if not provider:
                errors.append(f"Unknown provider '{provider_key}'.")
                continue
            if not provider.is_enabled():
                errors.append(f"Provider '{provider.name}' disabled or missing credentials.")
                continue
            try:
                provider.generate_clip(prompt=prompt, output_path=output_path, duration_seconds=duration_seconds)
                return provider.name
            except ProviderError as exc:
                logger.warning("Provider '%s' failed: %s", provider.name, exc)
                errors.append(str(exc))
                continue
        raise ProviderError("All providers failed. " + " | ".join(errors))

    def generate_episode_clips(self, session: Session, job_id: int, episode: Episode) -> list[Scene]:
        """Generate video clips for all scenes using configured provider failover."""
        try:
            log_job_step(session, job_id, "video_gen", StepStatus.STARTED, "Starting scene clip generation.")
            scenes = list(
                session.execute(
                    select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.scene_order.asc())
                ).scalars()
            )
            if not scenes:
                raise ValueError("No scenes found for video generation.")

            for scene in scenes:
                clip_path = settings.clips_dir / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.mp4"
                provider_name = self._generate_with_fallback(
                    prompt=self._scene_prompt(episode, scene),
                    output_path=clip_path,
                    duration_seconds=settings.default_scene_duration_seconds,
                )
                scene.video_clip_path = str(clip_path)
                scene.status = SceneStatus.VIDEO_DONE
                session.add(scene)
                session.flush()
                log_job_step(
                    session,
                    job_id,
                    "video_gen_scene",
                    StepStatus.SUCCESS,
                    f"Scene {scene.scene_order} generated using {provider_name}.",
                )

            log_job_step(
                session,
                job_id,
                "video_gen",
                StepStatus.SUCCESS,
                f"Generated {len(scenes)} clips for episode {episode.episode_number}.",
            )
            return scenes
        except Exception as exc:
            log_job_step_isolated(job_id, "video_gen", StepStatus.FAILED, f"Scene clip generation failed: {exc}")
            logger.exception("Video generation failed for episode_id=%s", episode.id)
            raise


def _run_ffmpeg(cmd: list[str], step_label: str) -> None:
    """Run an FFmpeg command with stderr capture and clear error reporting."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg {step_label} failed (exit {result.returncode}): {result.stderr[-2000:]}")


def merge_episode_assets(
    episode_number: int,
    clip_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
) -> float:
    """Re-encode clips to canonical format, concat, mix narration, and output final video."""
    if not clip_paths or not audio_paths:
        raise ValueError("clip_paths and audio_paths must not be empty.")

    temp = settings.temp_dir
    normalized_clips: list[Path] = []

    width, height = settings.video_resolution.split("x")
    for i, clip in enumerate(clip_paths):
        norm_path = temp / f"episode_{episode_number:04d}_norm_{i:02d}.mp4"
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", str(clip),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", settings.video_codec, "-b:v", settings.video_bitrate,
            "-r", str(settings.video_fps), "-an",
            str(norm_path),
        ], f"normalize clip {i}")
        normalized_clips.append(norm_path)

    video_manifest = temp / f"episode_{episode_number:04d}_video_concat.txt"
    video_manifest.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in normalized_clips),
        encoding="utf-8",
    )
    merged_video = temp / f"episode_{episode_number:04d}_video_only.mp4"
    _run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(video_manifest), "-c", "copy", str(merged_video),
    ], "concat video")

    audio_manifest = temp / f"episode_{episode_number:04d}_audio_concat.txt"
    audio_manifest.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in audio_paths),
        encoding="utf-8",
    )
    merged_audio = temp / f"episode_{episode_number:04d}_audio_only.mp3"
    _run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(audio_manifest),
        "-c:a", "libmp3lame", "-b:a", settings.audio_bitrate,
        str(merged_audio),
    ], "concat audio")

    _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(merged_video), "-i", str(merged_audio),
        "-c:v", settings.video_codec, "-b:v", settings.video_bitrate,
        "-c:a", settings.audio_codec, "-b:a", settings.audio_bitrate,
        "-shortest", str(output_path),
    ], "final mux")

    for f in normalized_clips + [video_manifest, audio_manifest, merged_video, merged_audio]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(output_path),
    ]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
    raw_duration = probe_result.stdout.strip()
    try:
        return float(raw_duration)
    except (ValueError, TypeError):
        logger.warning("Could not parse duration from ffprobe output: '%s'", raw_duration)
        return 0.0
