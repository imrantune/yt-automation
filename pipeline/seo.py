"""GPT-powered SEO optimization for YouTube metadata."""

from __future__ import annotations

import json
import logging

from openai import OpenAI
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated
from database.models import Episode, EpisodeSEO, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()


class SEOGenerator:
    """Generate YouTube-optimized metadata using GPT."""

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def generate_seo(self, session: Session, job_id: int, episode: Episode) -> EpisodeSEO:
        """Generate SEO metadata and persist to DB."""
        try:
            log_job_step(session, job_id, "seo", StepStatus.STARTED, "Generating YouTube SEO metadata.")

            prompt = (
                "Generate YouTube SEO metadata for a Spartacus gladiator arena episode.\n"
                f"Episode number: {episode.episode_number}\n"
                f"Original title: {episode.title}\n"
                f"Description: {episode.description}\n\n"
                "IMPORTANT RULES:\n"
                "- Title must NOT be misleading clickbait or imply real violence.\n"
                "- Description MUST include this AI disclosure line near the top:\n"
                '  "This video was created using AI-generated narration and visuals. '
                'All characters and events are fictional."\n'
                "- Do NOT use words like 'real', 'actual footage', or 'caught on camera'.\n"
                "- Keep language advertiser-friendly (no gore, slurs, or shock terms).\n"
                "- Include a subscribe CTA and episode summary.\n\n"
                "Return strict JSON:\n"
                "{\n"
                '  "title_seo": "Engaging YouTube title under 70 chars, dramatic but not misleading",\n'
                '  "description_seo": "Full YouTube description starting with AI disclosure, then episode '
                'summary, timestamps placeholder, keywords, subscribe CTA, 200-400 words",\n'
                '  "tags": ["tag1", "tag2", ...15-20 relevant YouTube tags],\n'
                '  "hashtags": "#Spartacus #Gladiator #Arena #AIAnimation ...5-8 hashtags"\n'
                "}"
            )

            response = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
            )
            if not response.choices:
                raise ValueError("OpenAI returned empty choices for SEO generation.")

            if response.usage:
                from pipeline.cost_tracker import log_openai_chat
                log_openai_chat(
                    session, episode.id, job_id, "seo_generation",
                    settings.openai_model,
                    response.usage.prompt_tokens, response.usage.completion_tokens,
                )

            content = response.choices[0].message.content or "{}"
            data = json.loads(content)

            seo = EpisodeSEO(
                episode_id=episode.id,
                title_seo=str(data.get("title_seo", episode.title))[:255],
                description_seo=str(data.get("description_seo", episode.description)),
                tags=data.get("tags", []) if isinstance(data.get("tags"), list) else [],
                hashtags=str(data.get("hashtags", "")),
            )
            session.add(seo)
            session.flush()

            log_job_step(
                session, job_id, "seo", StepStatus.SUCCESS,
                f"SEO generated: '{seo.title_seo}' with {len(seo.tags)} tags.",
            )
            return seo
        except Exception as exc:
            log_job_step_isolated(job_id, "seo", StepStatus.FAILED, f"SEO generation failed: {exc}")
            logger.exception("SEO generation failed for episode_id=%s", episode.id)
            raise
