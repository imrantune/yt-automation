"""Generate trending video topics for Youtube Shorts."""

from __future__ import annotations

import json
import logging

from openai import OpenAI

from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class TrendingSuggestor:
    """Uses GPT-4o to suggest trending topics based on a niche."""

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def suggest_topics(self, niche: str, count: int = 5) -> list[str]:
        """Fetch trending topics for a niche."""
        prompt = (
            f"You are a YouTube Shorts strategist. Suggest exactly {count} highly engaging, "
            f"viral-worthy video concepts for the niche: '{niche}'.\n\n"
            "Respond ONLY with a JSON object containing a 'topics' key which is a list of strings.\n"
            "Example: {\"topics\": [\"10 hidden iOS features\", \"Why AI is replacing coders\"]}"
        )

        try:
            response = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.7,
            )
            content = response.choices[0].message.content or "{}"
            data = json.loads(content)
            return data.get("topics", [])
        except Exception as exc:
            logger.exception("Failed to fetch trending topics: %s", exc)
            return [f"Concept {i+1} for {niche}" for i in range(count)]
