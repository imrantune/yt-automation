"""Video generation providers (hybrid image + Ken Burns, AI video) and final assembly utilities."""

from __future__ import annotations

import logging
import random
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
from database.models import Episode, Scene, SceneStatus, SceneType, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()

_FONT_SEARCH_PATHS = [
    Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Impact.ttf",
    Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Impact.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/TTF/Impact.ttf"),
    Path("C:/Windows/Fonts/impact.ttf"),
]


def _find_font(size: int = 56):
    """Find a usable TrueType font across platforms, with bundled font priority."""
    from PIL import ImageFont
    for p in _FONT_SEARCH_PATHS:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()


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


# ─── Ken Burns pan/zoom effects applied to still images ──────────────────────

KEN_BURNS_EFFECTS = {
    "zoom_in": "zoompan=z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
    "zoom_out": "zoompan=z='if(eq(on,1),1.5,max(zoom-0.0015,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
    "pan_left": "zoompan=z='1.15':x='if(eq(on,1),0,min(x+2,iw-iw/zoom))':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
    "pan_right": "zoompan=z='1.15':x='if(eq(on,1),iw-iw/zoom,max(x-2,0))':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
    "pan_up": "zoompan=z='1.15':x='iw/2-(iw/zoom/2)':y='if(eq(on,1),ih-ih/zoom,max(y-1.5,0))':d={frames}:s={w}x{h}:fps={fps}",
    "ken_burns_tl": "zoompan=z='min(zoom+0.001,1.3)':x='if(eq(on,1),iw/4,max(x-1,0))':y='if(eq(on,1),ih/4,max(y-0.8,0))':d={frames}:s={w}x{h}:fps={fps}",
    "ken_burns_br": "zoompan=z='min(zoom+0.001,1.3)':x='if(eq(on,1),0,min(x+1,iw-iw/zoom))':y='if(eq(on,1),0,min(y+0.8,ih-ih/zoom))':d={frames}:s={w}x{h}:fps={fps}",
}

SCENE_TYPE_EFFECTS: dict[SceneType, list[str]] = {
    SceneType.INTRO: ["zoom_in", "pan_right"],
    SceneType.FIGHT1: ["pan_left", "ken_burns_tl", "zoom_in"],
    SceneType.FIGHT2: ["pan_right", "ken_burns_br", "zoom_out"],
    SceneType.CLIMAX: ["zoom_in", "ken_burns_tl", "ken_burns_br"],
    SceneType.OUTRO: ["zoom_out", "pan_up"],
    SceneType.OTHER: ["zoom_in", "pan_left"],
}


def apply_ken_burns(
    image_path: Path,
    output_path: Path,
    duration_seconds: float,
    effect: str | None = None,
    scene_type: SceneType = SceneType.OTHER,
) -> None:
    """Convert a still image into a video clip with Ken Burns pan/zoom motion."""
    w_str, h_str = settings.video_resolution.split("x")
    w, h = int(w_str), int(h_str)
    fps = settings.video_fps
    frames = int(duration_seconds * fps)

    if effect is None:
        candidates = SCENE_TYPE_EFFECTS.get(scene_type, ["zoom_in"])
        effect = random.choice(candidates)

    template = KEN_BURNS_EFFECTS.get(effect, KEN_BURNS_EFFECTS["zoom_in"])
    vf = template.format(frames=frames, w=w, h=h, fps=fps)

    _run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path.resolve()),
        "-vf", vf,
        "-c:v", settings.video_codec, "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-b:v", settings.video_bitrate,
        "-t", str(duration_seconds),
        str(output_path.resolve()),
    ], f"ken burns ({effect})")


class HybridImageProvider(BaseVideoProvider):
    """Generate clips from DALL-E/Midjourney images + Ken Burns pan/zoom effects.

    This is the primary provider for the hybrid approach: consistent character
    images animated with cinematic camera movements.
    """

    name = "hybrid"

    def __init__(self) -> None:
        self._session: Session | None = None
        self._episode: Episode | None = None
        self._scene: Scene | None = None

    def set_context(self, session: Session, episode: Episode, scene: Scene) -> None:
        """Set the DB context needed for image generation prompts."""
        self._session = session
        self._episode = episode
        self._scene = scene

    def is_enabled(self) -> bool:
        if settings.image_provider == "midjourney":
            return settings.midjourney_enabled and bool(settings.midjourney_api_key)
        return bool(settings.openai_api_key)

    def generate_clip(self, prompt: str, output_path: Path, duration_seconds: int) -> None:
        try:
            if self._session and self._episode and self._scene:
                from pipeline.image_generator import generate_scene_image
                image_path = generate_scene_image(
                    self._session, self._episode, self._scene, quality="hd",
                )
            else:
                from pipeline.image_generator import IMAGES_DIR
                image_path = self._generate_image_from_prompt(prompt, output_path)

            scene_type = self._scene.scene_type if self._scene else SceneType.OTHER
            apply_ken_burns(
                image_path=image_path,
                output_path=output_path,
                duration_seconds=float(duration_seconds),
                scene_type=scene_type,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Hybrid image+kenburns failed: {exc}") from exc

    def _generate_image_from_prompt(self, prompt: str, output_path: Path) -> Path:
        """Fallback: generate image directly from prompt without DB context."""
        from io import BytesIO
        from openai import OpenAI
        from PIL import Image

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.images.generate(
            model="dall-e-3", prompt=prompt[:4000],
            size="1792x1024", quality="hd", n=1,
        )
        img_response = requests.get(response.data[0].url, timeout=60)
        img_response.raise_for_status()
        img = Image.open(BytesIO(img_response.content)).convert("RGB")
        w, h = (int(x) for x in settings.video_resolution.split("x"))
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        from pipeline.image_generator import IMAGES_DIR
        img_path = IMAGES_DIR / output_path.with_suffix(".png").name
        img.save(str(img_path), "PNG")
        return img_path


class Wan21Provider(BaseVideoProvider):
    """Local Wan 2.1 generation via diffusers on Apple Silicon."""

    name = "wan21"

    def __init__(self) -> None:
        self._pipe: Any | None = None

    def is_enabled(self) -> bool:
        if not settings.wan21_enabled:
            return False

        # Check if model hub directory exists and has significant data (avoid hanging on empty/partial download)
        try:
            import os
            hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
            model_dir = hf_home / "hub" / f"models--{settings.wan21_model_id.replace('/', '--')}"
            blobs_dir = model_dir / "blobs"

            if not blobs_dir.exists():
                logger.debug("Wan21 disabled: hub directory missing (%s)", blobs_dir)
                return False

            total_size = sum(f.stat().st_size for f in blobs_dir.iterdir() if f.is_file())
            # Wan 2.1 1.3B is ~22GB total. We require at least 15GB to consider it "mostly ready"
            min_bytes = 15 * (1024**3)
            if total_size < min_bytes:
                logger.warning("Wan21 disabled: incomplete model (found %.2f GB, need ~22GB)", total_size / (1024**3))
                return False

            return True
        except Exception as exc:
            logger.debug("Wan21 readiness check failed: %s", exc)
            return False

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
        from pipeline.retry import retry_api_call
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

            @retry_api_call(max_retries=2, base_delay=10.0)
            def _create():
                r = requests.post(f"{base}/v1/video_generation", headers=headers, json=payload, timeout=120)
                r.raise_for_status()
                return r

            create_res = _create()
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
        from pipeline.retry import retry_api_call
        try:
            create_endpoint = f"{settings.runway_api_base.rstrip('/')}/v1/generate/video"
            headers = {"Authorization": f"Bearer {settings.runway_api_key}", "Content-Type": "application/json"}
            payload = {
                "promptText": prompt,
                "seconds": duration_seconds,
                "resolution": settings.video_resolution,
            }

            @retry_api_call(max_retries=2, base_delay=10.0)
            def _create():
                r = requests.post(create_endpoint, headers=headers, json=payload, timeout=120)
                r.raise_for_status()
                return r

            create_res = _create()
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


class KlingProvider(BaseVideoProvider):
    """Kling AI (Kuaishou) video generation via their cloud API."""

    name = "kling"

    def is_enabled(self) -> bool:
        return settings.kling_enabled and bool(settings.kling_api_key)

    def generate_clip(self, prompt: str, output_path: Path, duration_seconds: int) -> None:
        from pipeline.retry import retry_api_call
        base = settings.kling_api_base.rstrip("/")
        headers = {
            "Authorization": f"Bearer {settings.kling_api_key}",
            "Content-Type": "application/json",
        }
        try:
            clip_dur = 5 if duration_seconds <= 6 else 10
            payload = {
                "model": settings.kling_model,
                "prompt": prompt[:2000],
                "duration": clip_dur,
                "aspect_ratio": "16:9",
                "mode": "standard",
                "negative_prompt": "blood, gore, graphic violence, text, watermark, blurry",
            }

            @retry_api_call(max_retries=2, base_delay=10.0)
            def _create():
                r = requests.post(
                    f"{base}/v1/videos/text2video",
                    headers=headers,
                    json=payload,
                    timeout=120,
                )
                r.raise_for_status()
                return r

            create_res = _create()
            resp_data = create_res.json()
            task_id = resp_data.get("task_id") or resp_data.get("id")
            if not task_id:
                raise ProviderError(f"Kling create returned no task_id: {resp_data}")
            logger.info("Kling task created: %s (model=%s, duration=%ds)", task_id, settings.kling_model, clip_dur)

            for attempt in range(60):
                time.sleep(5)
                poll_res = requests.get(
                    f"{base}/v1/videos/{task_id}",
                    headers=headers,
                    timeout=60,
                )
                poll_res.raise_for_status()
                data = poll_res.json()
                status = str(data.get("status", "")).lower()
                logger.debug("Kling task %s poll #%d: status=%s", task_id, attempt + 1, status)

                if status in ("completed", "succeeded", "success"):
                    video_url = (
                        data.get("video_url")
                        or data.get("output", {}).get("video_url")
                        or data.get("result", {}).get("video_url")
                    )
                    if isinstance(data.get("output"), list) and data["output"]:
                        video_url = video_url or data["output"][0]
                    if not video_url:
                        raise ProviderError(f"Kling task {task_id} completed but no video_url found: {data}")
                    media_res = requests.get(video_url, timeout=300)
                    media_res.raise_for_status()
                    output_path.write_bytes(media_res.content)
                    logger.info("Kling clip saved: %s (%.1f MB)", output_path, len(media_res.content) / (1024 * 1024))
                    return

                if status in ("failed", "cancelled", "error"):
                    error_msg = data.get("error") or data.get("message") or status
                    raise ProviderError(f"Kling task {task_id} failed: {error_msg}")

            raise ProviderError(f"Kling task {task_id} timed out after 5 minutes.")
        except ProviderError:
            raise
        except (requests.RequestException, KeyError, OSError) as exc:
            raise ProviderError(f"Kling generation failed: {exc}") from exc


class VideoGenerator:
    """Generate scene clips with provider failover (hybrid first, then AI video)."""

    def __init__(self) -> None:
        self._hybrid = HybridImageProvider()
        self.providers: dict[str, BaseVideoProvider] = {
            "hybrid": self._hybrid,
            "wan21": Wan21Provider(),
            "minimax": MinimaxProvider(),
            "kling": KlingProvider(),
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

    def _generate_with_fallback(
        self,
        prompt: str,
        output_path: Path,
        duration_seconds: int,
        session: Session | None = None,
        episode: Episode | None = None,
        scene: Scene | None = None,
    ) -> str:
        errors: list[str] = []
        for provider_key in settings.video_provider_order:
            provider = self.providers.get(provider_key)
            if not provider:
                errors.append(f"Unknown provider '{provider_key}'.")
                continue
            if not provider.is_enabled():
                errors.append(f"Provider '{provider.name}' disabled or missing credentials.")
                continue

            if isinstance(provider, HybridImageProvider) and session and episode and scene:
                provider.set_context(session, episode, scene)

            try:
                provider.generate_clip(prompt=prompt, output_path=output_path, duration_seconds=duration_seconds)
                return provider.name
            except ProviderError as exc:
                logger.warning("Provider '%s' failed: %s", provider.name, exc)
                errors.append(str(exc))
                continue
        raise ProviderError("All providers failed. " + " | ".join(errors))

    def _is_hybrid_primary(self) -> bool:
        """Return True if the first enabled provider in the order is hybrid."""
        for key in settings.video_provider_order:
            prov = self.providers.get(key)
            if prov and prov.is_enabled():
                return key == "hybrid"
        return False

    def _generate_hybrid_clips(
        self,
        session: Session,
        job_id: int,
        episode: Episode,
        scenes: list[Scene],
    ) -> list[Scene]:
        """Two-phase hybrid generation: DALL-E/Midjourney images first, then Ken Burns animation."""
        from pipeline.image_generator import generate_scene_image, get_active_image_provider
        from pipeline.cost_tracker import log_dalle, log_midjourney

        img_provider = get_active_image_provider()
        provider_label = "Midjourney" if img_provider == "midjourney" else "DALL-E 3"

        # Phase 1: Image generation (DALL-E or Midjourney)
        log_job_step(session, job_id, "image_gen", StepStatus.STARTED,
                     f"Generating {len(scenes)} scene images with {provider_label}.")
        session.commit()

        image_paths: list[Path] = []
        for scene in scenes:
            img_path = generate_scene_image(session, episode, scene)
            image_paths.append(img_path)
            if img_provider == "midjourney":
                log_midjourney(session, episode.id, job_id, f"scene_{scene.scene_order}_image")
            else:
                log_dalle(session, episode.id, job_id, f"scene_{scene.scene_order}_image",
                          size="1792x1024", quality="hd")
            log_job_step(session, job_id, "video_gen_scene", StepStatus.SUCCESS,
                         f"Scene {scene.scene_order} {provider_label} image generated.")
            session.commit()

        log_job_step(session, job_id, "image_gen", StepStatus.SUCCESS,
                     f"Generated {len(image_paths)} {provider_label} images.")
        session.commit()

        # Phase 2: Ken Burns animation
        log_job_step(session, job_id, "ken_burns", StepStatus.STARTED,
                     f"Applying Ken Burns effects to {len(scenes)} images.")
        session.commit()

        for i, (scene, img_path) in enumerate(zip(scenes, image_paths)):
            clip_path = settings.clips_dir / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.mp4"
            scene_type = scene.scene_type if scene.scene_type else SceneType.OTHER
            apply_ken_burns(
                image_path=img_path,
                output_path=clip_path,
                duration_seconds=float(settings.default_scene_duration_seconds),
                scene_type=scene_type,
            )
            scene.video_clip_path = str(clip_path)
            scene.status = SceneStatus.VIDEO_DONE
            session.add(scene)
            log_job_step(session, job_id, "video_gen_scene", StepStatus.SUCCESS,
                         f"Scene {scene.scene_order} Ken Burns clip created ({scene_type.value}).")
            session.commit()

        log_job_step(session, job_id, "ken_burns", StepStatus.SUCCESS,
                     f"Applied Ken Burns effects to {len(scenes)} clips.")
        session.commit()
        return scenes

    def generate_episode_clips(
        self,
        session: Session,
        job_id: int,
        episode: Episode,
        scenes_override: list[Scene] | None = None,
    ) -> list[Scene]:
        """Generate video clips for scenes using configured provider failover.

        When hybrid is the primary provider, runs DALL-E image generation and
        Ken Burns animation as separate tracked pipeline steps. Otherwise falls
        back to AI video generation providers.

        If *scenes_override* is provided only those scenes are processed;
        otherwise all episode scenes are generated.
        """
        try:
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

            if self._is_hybrid_primary():
                return self._generate_hybrid_clips(session, job_id, episode, scenes)

            log_job_step(session, job_id, "video_gen", StepStatus.STARTED, "Starting scene clip generation.")

            for scene in scenes:
                clip_path = settings.clips_dir / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.mp4"
                provider_name = self._generate_with_fallback(
                    prompt=self._scene_prompt(episode, scene),
                    output_path=clip_path,
                    duration_seconds=settings.default_scene_duration_seconds,
                    session=session,
                    episode=episode,
                    scene=scene,
                )
                scene.video_clip_path = str(clip_path)
                scene.status = SceneStatus.VIDEO_DONE
                session.add(scene)
                session.commit()

                from pipeline.cost_tracker import log_video_provider, log_dalle
                if provider_name == "hybrid":
                    log_dalle(session, episode.id, job_id, f"scene_{scene.scene_order}_image",
                              size="1792x1024", quality="hd")
                else:
                    dur = 6.0 if provider_name == "minimax" else (5.0 if provider_name == "kling" else settings.default_scene_duration_seconds)
                    log_video_provider(
                        session, episode.id, job_id, provider_name,
                        f"scene_{scene.scene_order}_clip",
                        duration_seconds=dur,
                        model="T2V-01" if provider_name == "minimax" else (settings.kling_model if provider_name == "kling" else ""),
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
            step_key = "image_gen" if self._is_hybrid_primary() else "video_gen"
            log_job_step_isolated(job_id, step_key, StepStatus.FAILED, f"Scene clip generation failed: {exc}")
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


def _probe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


def _render_card_image(
    text_lines: list[tuple[str, int, str]],
    bg_color: tuple[int, int, int] = (26, 10, 10),
) -> Path:
    """Render a branded title card image with Pillow and save as temporary PNG.

    text_lines: list of (text, font_size, hex_color) tuples.
    """
    from PIL import Image, ImageDraw, ImageFont

    w, h = (int(x) for x in settings.video_resolution.split("x"))
    img = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(img)

    y_cursor = h // 2 - 60 * len(text_lines) // 2
    for text, size, color in text_lines:
        font = _find_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = (w - tw) // 2
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                draw.text((x + dx, y_cursor + dy), text, font=font, fill="black")
        draw.text((x, y_cursor), text, font=font, fill=color)
        y_cursor += size + 20

    card_path = settings.temp_dir / f"_card_{id(text_lines)}.png"
    img.save(str(card_path), "PNG")
    return card_path


def generate_intro(output_path: Path, duration: float = 5.0) -> Path:
    """Generate a 5-second branded intro video from a Pillow-rendered title card."""
    card = _render_card_image([
        ("SPARTACUS ARENA", 72, "gold"),
        ("GLADIATORIAL COMBAT", 36, "white"),
    ])
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(card.resolve()),
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration}",
        "-t", str(duration),
        "-vf", f"fade=in:0:{settings.video_fps},fade=out:st={duration - 1}:d=1",
        "-c:v", settings.video_codec, "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-b:v", settings.video_bitrate,
        "-r", str(settings.video_fps),
        "-c:a", settings.audio_codec, "-b:a", settings.audio_bitrate,
        "-shortest",
        str(output_path.resolve()),
    ], "generate intro")
    card.unlink(missing_ok=True)
    return output_path


def generate_outro(output_path: Path, duration: float = 5.0) -> Path:
    """Generate a 5-second branded outro video from a Pillow-rendered card."""
    card = _render_card_image([
        ("SUBSCRIBE FOR MORE", 56, "red"),
        ("New Episode Every Day", 32, "white"),
    ])
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(card.resolve()),
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration}",
        "-t", str(duration),
        "-vf", f"fade=in:0:{settings.video_fps},fade=out:st={duration - 1}:d=1",
        "-c:v", settings.video_codec, "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-b:v", settings.video_bitrate,
        "-r", str(settings.video_fps),
        "-c:a", settings.audio_codec, "-b:a", settings.audio_bitrate,
        "-shortest",
        str(output_path.resolve()),
    ], "generate outro")
    card.unlink(missing_ok=True)
    return output_path


def _apply_color_grade(input_path: Path, output_path: Path) -> None:
    """Apply dark cinematic color grading via FFmpeg filters.

    Increases contrast, reduces brightness slightly, adds warm shadow tones.
    """
    vf = (
        "eq=contrast=1.15:brightness=-0.04:saturation=1.1,"
        "curves=master='0/0 0.25/0.20 0.5/0.48 0.75/0.78 1/1':"
        "red='0/0 0.5/0.52 1/1':blue='0/0 0.5/0.46 1/1',"
        "unsharp=5:5:0.5:5:5:0.0"
    )
    _run_ffmpeg([
        "ffmpeg", "-y", "-i", str(input_path.resolve()),
        "-vf", vf,
        "-c:v", settings.video_codec, "-preset", "veryfast", "-b:v", settings.video_bitrate,
        "-c:a", "copy",
        str(output_path.resolve()),
    ], "color grade")


def merge_episode_assets(
    episode_number: int,
    clip_paths: list[Path],
    audio_paths: list[Path],
    output_path: Path,
    subtitle_paths: list[Path] | None = None,
    color_grade: bool = True,
    add_intro: bool = True,
    add_outro: bool = True,
) -> float:
    """Normalize clips, apply color grading, add intro/outro, concat, mix audio, embed subtitles.

    Uses fast encoding preset and stream-copy where possible to minimize merge time.
    """
    if not clip_paths or not audio_paths:
        raise ValueError("clip_paths and audio_paths must not be empty.")

    temp = settings.temp_dir
    normalized_clips: list[Path] = []
    x264_preset = "veryfast"
    width, height = settings.video_resolution.split("x")

    for i, clip in enumerate(clip_paths):
        norm_path = temp / f"episode_{episode_number:04d}_norm_{i:02d}.mp4"

        loop_args: list[str] = []
        trim_args: list[str] = []
        if i < len(audio_paths):
            audio_dur = _probe_duration(audio_paths[i])
            if audio_dur > 0:
                loop_args = ["-stream_loop", "-1"]
                trim_args = ["-t", str(audio_dur)]

        vf_filters = [f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"]
        if color_grade:
            vf_filters.append(
                "eq=contrast=1.15:brightness=-0.04:saturation=1.1,"
                "curves=master='0/0 0.25/0.20 0.5/0.48 0.75/0.78 1/1':"
                "red='0/0 0.5/0.52 1/1':blue='0/0 0.5/0.46 1/1'"
            )

        _run_ffmpeg([
            "ffmpeg", "-y", *loop_args, "-i", str(clip.resolve()),
            *trim_args,
            "-vf", ",".join(vf_filters),
            "-c:v", settings.video_codec, "-preset", x264_preset, "-b:v", settings.video_bitrate,
            "-r", str(settings.video_fps), "-an",
            str(norm_path),
        ], f"normalize clip {i}")
        normalized_clips.append(norm_path)

    all_video_parts: list[Path] = []

    if add_intro:
        intro_path = temp / f"episode_{episode_number:04d}_intro.mp4"
        generate_intro(intro_path)
        all_video_parts.append(intro_path)

    all_video_parts.extend(normalized_clips)

    if add_outro:
        outro_path = temp / f"episode_{episode_number:04d}_outro.mp4"
        generate_outro(outro_path)
        all_video_parts.append(outro_path)

    def _concat_line(p: Path) -> str:
        path_str = p.resolve().as_posix().replace("'", "''")
        return f"file '{path_str}'"

    video_manifest = temp / f"episode_{episode_number:04d}_video_concat.txt"
    video_manifest.write_text(
        "\n".join(_concat_line(p) for p in all_video_parts),
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

    final_cmd.extend(["-c:v", "copy"])
    final_cmd.extend(["-c:a", settings.audio_codec, "-b:a", settings.audio_bitrate])
    if has_subs:
        final_cmd.extend(["-c:s", "mov_text"])

    final_cmd.extend(["-shortest", str(output_path.resolve())])
    _run_ffmpeg(final_cmd, "final mux")

    cleanup_files = list(all_video_parts) + [video_manifest, audio_manifest, merged_video, merged_audio]
    if merged_subs:
        cleanup_files.append(merged_subs)
    for f in cleanup_files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    return _probe_duration(output_path)
