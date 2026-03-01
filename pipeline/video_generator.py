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
    """Minimax (Hailuo) API video generation provider."""

    name = "minimax"

    def is_enabled(self) -> bool:
        return settings.minimax_enabled and bool(settings.minimax_api_key)

    def generate_clip(self, prompt: str, output_path: Path, duration_seconds: int) -> None:
        base = settings.minimax_api_base.rstrip("/")
        headers = {"Authorization": f"Bearer {settings.minimax_api_key}", "Content-Type": "application/json"}
        try:
            clip_dur = 6 if duration_seconds <= 8 else 10
            payload = {
                "model": "T2V-01",
                "prompt": prompt[:2000],
                "duration": clip_dur,
                "resolution": "720P",
                "prompt_optimizer": True,
            }
            create_res = requests.post(f"{base}/v1/video_generation", headers=headers, json=payload, timeout=120)
            create_res.raise_for_status()
            resp_data = create_res.json()
            if resp_data.get("base_resp", {}).get("status_code", -1) != 0:
                raise ProviderError(f"Minimax create failed: {resp_data.get('base_resp', {}).get('status_msg', 'unknown')}")
            task_id = resp_data["task_id"]
            logger.info("Minimax task created: %s (duration=%ds)", task_id, clip_dur)

            for attempt in range(60):
                time.sleep(10)
                poll_res = requests.get(
                    f"{base}/v1/query/video_generation",
                    headers=headers,
                    params={"task_id": task_id},
                    timeout=60,
                )
                poll_res.raise_for_status()
                data = poll_res.json()
                status = str(data.get("status", "")).lower()
                logger.debug("Minimax task %s poll #%d: status=%s", task_id, attempt + 1, status)

                if status == "success":
                    file_id = data.get("file_id")
                    if not file_id:
                        raise ProviderError("Minimax succeeded but no file_id returned")
                    dl_res = requests.get(
                        f"{base}/v1/files/retrieve",
                        headers=headers,
                        params={"file_id": file_id},
                        timeout=60,
                    )
                    dl_res.raise_for_status()
                    dl_data = dl_res.json()
                    download_url = dl_data.get("file", {}).get("download_url", "")
                    if not download_url:
                        raise ProviderError("Minimax file retrieve returned no download_url")
                    media_res = requests.get(download_url, timeout=300)
                    media_res.raise_for_status()
                    output_path.write_bytes(media_res.content)
                    logger.info("Minimax clip saved: %s (%.1f MB)", output_path, len(media_res.content) / (1024 * 1024))
                    return

                if status in {"failed", "cancelled"}:
                    raise ProviderError(f"Minimax task {task_id} ended with status={status}")

            raise ProviderError("Minimax timed out after 10 minutes waiting for video.")
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
        safe_narration = scene.narration_text[:600].replace("blood", "dust").replace("gore", "sand")
        return (
            f"Cinematic animated scene in an ancient Roman colosseum, episode {episode.episode_number}. "
            f"Scene type: {scene.scene_type.value}. "
            "Epic dramatic lighting, warm golden shadows, stylized animation style. "
            "Athletic gladiators in armor competing in an arena, crowds cheering. "
            "NO blood, NO gore, NO graphic violence, NO injuries. "
            "Style: like an animated historical drama, PG-13 action. "
            f"Scene context: {safe_narration}"
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

    def generate_episode_clips(
        self,
        session: Session,
        job_id: int,
        episode: Episode,
        scenes_override: list[Scene] | None = None,
    ) -> list[Scene]:
        """Generate video clips for scenes using configured provider failover.

        If *scenes_override* is provided only those scenes are processed;
        otherwise all episode scenes are generated.
        """
        try:
            log_job_step(session, job_id, "video_gen", StepStatus.STARTED, "Starting scene clip generation.")
            if scenes_override is not None:
                scenes = scenes_override
            else:
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
                session.commit()

                from pipeline.cost_tracker import log_video_provider
                log_video_provider(
                    session, episode.id, job_id, provider_name,
                    f"scene_{scene.scene_order}_clip",
                    duration_seconds=6.0 if provider_name == "minimax" else settings.default_scene_duration_seconds,
                    model="T2V-01" if provider_name == "minimax" else "",
                    resolution="720P" if provider_name == "minimax" else "",
                )

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


def _merge_srt_files(srt_paths: list[Path], output_path: Path) -> None:
    """Concatenate multiple SRT files into one with re-indexed entries."""
    combined: list[str] = []
    idx = 1
    offset = 0.0

    for srt_path in srt_paths:
        if not srt_path.exists():
            continue
        content = srt_path.read_text(encoding="utf-8").strip()
        if not content:
            continue

        blocks = content.split("\n\n")
        last_end = 0.0
        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue
            time_line = lines[1]
            text = "\n".join(lines[2:])
            parts = time_line.split(" --> ")
            if len(parts) != 2:
                continue

            start = _parse_srt_time(parts[0].strip()) + offset
            end = _parse_srt_time(parts[1].strip()) + offset
            last_end = max(last_end, end)

            combined.append(f"{idx}\n{_fmt_srt(start)} --> {_fmt_srt(end)}\n{text}\n")
            idx += 1

        offset = last_end + 0.5

    output_path.write_text("\n".join(combined), encoding="utf-8")


def _parse_srt_time(ts: str) -> float:
    """Parse HH:MM:SS,mmm to seconds."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])


def _fmt_srt(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def merge_episode_assets(
    episode_number: int,
    clip_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
    subtitle_paths: list[Path] | None = None,
) -> float:
    """Re-encode clips to canonical format, concat, mix narration, burn subtitles, output final video.
    Uses fast encoding preset and stream-copy when no subtitles to minimize merge time."""
    if not clip_paths or not audio_paths:
        raise ValueError("clip_paths and audio_paths must not be empty.")

    temp = settings.temp_dir
    normalized_clips: list[Path] = []
    # Fast preset for normalize (2–4x faster than default medium with minimal quality loss)
    x264_preset = "veryfast"
    width, height = settings.video_resolution.split("x")

    for i, clip in enumerate(clip_paths):
        norm_path = temp / f"episode_{episode_number:04d}_norm_{i:02d}.mp4"

        # Get the matching audio duration so we can loop the clip to fill it
        loop_args: list[str] = []
        trim_args: list[str] = []
        if i < len(audio_paths):
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_paths[i])],
                capture_output=True, text=True,
            )
            try:
                audio_dur = float(probe.stdout.strip())
            except (ValueError, TypeError):
                audio_dur = 0.0
            if audio_dur > 0:
                loop_args = ["-stream_loop", "-1"]
                trim_args = ["-t", str(audio_dur)]

        _run_ffmpeg([
            "ffmpeg", "-y", *loop_args, "-i", str(clip.resolve()),
            *trim_args,
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", settings.video_codec, "-preset", x264_preset, "-b:v", settings.video_bitrate,
            "-r", str(settings.video_fps), "-an",
            str(norm_path),
        ], f"normalize clip {i}")
        normalized_clips.append(norm_path)

    def _concat_line(p: Path) -> str:
        path_str = p.resolve().as_posix().replace("'", "''")
        return f"file '{path_str}'"

    video_manifest = temp / f"episode_{episode_number:04d}_video_concat.txt"
    video_manifest.write_text(
        "\n".join(_concat_line(p) for p in normalized_clips),
        encoding="utf-8",
    )
    merged_video = temp / f"episode_{episode_number:04d}_video_only.mp4"
    _run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(video_manifest.resolve()), "-c", "copy", str(merged_video),
    ], "concat video")

    audio_manifest = temp / f"episode_{episode_number:04d}_audio_concat.txt"
    audio_manifest.write_text(
        "\n".join(_concat_line(Path(p)) for p in audio_paths),
        encoding="utf-8",
    )
    merged_audio = temp / f"episode_{episode_number:04d}_audio_only.mp3"
    _run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(audio_manifest),
        "-c:a", "libmp3lame", "-b:a", settings.audio_bitrate,
        str(merged_audio),
    ], "concat audio")

    merged_subs: Path | None = None
    if subtitle_paths:
        merged_subs = temp / f"episode_{episode_number:04d}_subs.srt"
        _merge_srt_files(subtitle_paths, merged_subs)

    has_subs = merged_subs and merged_subs.exists() and merged_subs.stat().st_size > 0

    final_cmd = [
        "ffmpeg", "-y",
        "-i", str(merged_video.resolve()), "-i", str(merged_audio.resolve()),
    ]
    if has_subs:
        final_cmd.extend(["-i", str(merged_subs.resolve())])

    final_cmd.extend(["-map", "0:v", "-map", "1:a"])
    if has_subs:
        final_cmd.extend(["-map", "2:s"])

    # Stream-copy video (already normalized) — no re-encode needed
    final_cmd.extend(["-c:v", "copy"])
    final_cmd.extend(["-c:a", settings.audio_codec, "-b:a", settings.audio_bitrate])
    if has_subs:
        final_cmd.extend(["-c:s", "mov_text"])

    final_cmd.extend(["-shortest", str(output_path.resolve())])
    _run_ffmpeg(final_cmd, "final mux")

    cleanup_files = normalized_clips + [video_manifest, audio_manifest, merged_video, merged_audio]
    if merged_subs:
        cleanup_files.append(merged_subs)
    for f in cleanup_files:
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
