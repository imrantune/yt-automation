"""DALL-E 3 thumbnail generation with Pillow text overlay."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import requests
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated
from database.models import Episode, StepStatus, VideoJob


logger = logging.getLogger(__name__)
settings = get_settings()

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720


class ThumbnailGenerator:
    """Generate YouTube thumbnails using DALL-E 3 + text overlay."""

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.thumbs_dir = settings.output_root / "thumbnails"
        self.thumbs_dir.mkdir(parents=True, exist_ok=True)

    def _generate_image(self, episode: Episode) -> Image.Image:
        """Generate base image via DALL-E 3."""
        from pipeline.retry import retry_api_call

        prompt = (
            "Provide a safe, theatrical, PG-rated historical drama scene. "
            f"Dramatic cinematic thumbnail for a historical arena episode titled '{episode.title}'. "
            "Ancient Roman colosseum, dark moody lighting, epic theatrical atmosphere, "
            "warm golden shadows, sand and dust arena, hyper-detailed, "
            "Artistic historical reenactment, no modern elements, 16:9 aspect ratio."
        )

        from pipeline.image_generator import sanitize_for_dalle
        safe_prompt = sanitize_for_dalle(prompt)

        @retry_api_call(max_retries=3, base_delay=5.0)
        def _call_dalle():
            resp = self.client.images.generate(
                model="dall-e-3", prompt=safe_prompt,
                size="1792x1024", quality="hd", n=1,
            )
            url = resp.data[0].url
            img_resp = requests.get(url, timeout=60)
            img_resp.raise_for_status()
            return img_resp.content

        img_bytes = _call_dalle()
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        return img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.Resampling.LANCZOS)

    def _add_text_overlay(self, img: Image.Image, episode: Episode) -> Image.Image:
        """Add episode title and number as text overlay with outline."""
        draw = ImageDraw.Draw(img)

        from pipeline.video_generator import _find_font
        title_font = _find_font(56)
        ep_font = _find_font(36)

        title_text = episode.title[:50]
        ep_text = f"EPISODE {episode.episode_number}"
        outline_color = "black"
        text_color = "white"

        title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_w = title_bbox[2] - title_bbox[0]
        title_x = (THUMBNAIL_WIDTH - title_w) // 2
        title_y = THUMBNAIL_HEIGHT - 140

        for dx in range(-3, 4):
            for dy in range(-3, 4):
                draw.text((title_x + dx, title_y + dy), title_text, font=title_font, fill=outline_color)
        draw.text((title_x, title_y), title_text, font=title_font, fill=text_color)

        ep_bbox = draw.textbbox((0, 0), ep_text, font=ep_font)
        ep_w = ep_bbox[2] - ep_bbox[0]
        ep_x = (THUMBNAIL_WIDTH - ep_w) // 2
        ep_y = THUMBNAIL_HEIGHT - 75

        for dx in range(-2, 3):
            for dy in range(-2, 3):
                draw.text((ep_x + dx, ep_y + dy), ep_text, font=ep_font, fill=outline_color)
        draw.text((ep_x, ep_y), ep_text, font=ep_font, fill="gold")

        return img

    def generate_thumbnail(self, session: Session, job_id: int, episode: Episode, job: VideoJob) -> Path:
        """Generate thumbnail and update job record."""
        try:
            log_job_step(session, job_id, "thumbnail", StepStatus.STARTED, "Generating DALL-E 3 thumbnail.")
            img = self._generate_image(episode)
            img = self._add_text_overlay(img, episode)

            thumb_path = self.thumbs_dir / f"episode_{episode.episode_number:04d}.png"
            img.save(str(thumb_path), "PNG", optimize=True)

            job.thumbnail_path = str(thumb_path)
            session.add(job)
            session.flush()

            from pipeline.cost_tracker import log_dalle
            log_dalle(session, episode.id, job_id, "thumbnail_generation", size="1792x1024", quality="hd")

            log_job_step(
                session, job_id, "thumbnail", StepStatus.SUCCESS,
                f"Thumbnail saved: {thumb_path}",
            )
            return thumb_path
        except Exception as exc:
            log_job_step_isolated(job_id, "thumbnail", StepStatus.FAILED, f"Thumbnail generation failed: {exc}")
            logger.exception("Thumbnail generation failed for episode_id=%s", episode.id)
            raise
