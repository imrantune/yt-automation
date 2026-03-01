"""GPT-4o powered script generation with DB persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated, set_episode_status
from database.models import Episode, EpisodeStatus, Scene, SceneType, StepStatus
from pipeline.consistency_manager import ConsistencyManager


logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(frozen=True)
class GeneratedEpisodePayload:
    """Validated script payload from GPT response."""

    title: str
    description: str
    scenes: list[dict]
    character_results: list[dict]


class ScriptGenerator:
    """Generate scripted episode JSON and persist scene records."""

    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _prompt(self, context: dict) -> str:
        return (
            "You are a cinematic Spartacus arena writer.\n"
            "Generate one episode script with strict JSON output.\n"
            "Requirements:\n"
            "- 1800 to 2000 words total narration.\n"
            "- Scene order: intro, fight1, fight2, climax, outro.\n"
            "- Use only alive_characters from context.\n"
            "- Keep continuity using recent_summaries.\n"
            "- If should_kill_character is true, exactly one character dies.\n"
            "- If should_add_new_character is true, introduce one new character.\n"
            "- If is_grand_tournament is true, set special event tone.\n"
            "- No duplicate or generic titles.\n"
            "\n"
            "YOUTUBE CONTENT SAFETY (MANDATORY):\n"
            "- Focus on dramatic storytelling, honor, strategy, and character arcs.\n"
            "- Describe combat as athletic and tactical, like a sports broadcast.\n"
            "- NEVER include graphic gore, blood descriptions, dismemberment, or torture.\n"
            "- Deaths should be implied or described with restraint (e.g. 'fell in battle').\n"
            "- Avoid hate speech, slurs, or dehumanizing language.\n"
            "- Keep language PG-13: no excessive profanity.\n"
            "- Frame the arena as a test of skill and courage, not gratuitous violence.\n"
            "Return JSON shape:\n"
            "{\n"
            '  "title": "string",\n'
            '  "description": "string",\n'
            '  "scenes": [\n'
            '    {"scene_order":1,"scene_type":"intro","narration_text":"..."},\n'
            '    {"scene_order":2,"scene_type":"fight1","narration_text":"..."},\n'
            '    {"scene_order":3,"scene_type":"fight2","narration_text":"..."},\n'
            '    {"scene_order":4,"scene_type":"climax","narration_text":"..."},\n'
            '    {"scene_order":5,"scene_type":"outro","narration_text":"..."}\n'
            "  ],\n"
            '  "character_results":[\n'
            '    {"name":"Spartacus","result":"win|loss|death","kills":0,"notable_moment":"..."}\n'
            "  ]\n"
            "}\n"
            f"Context JSON:\n{json.dumps(context)}"
        )

    @staticmethod
    def _normalize_scene_type(raw_type: str) -> SceneType:
        mapping = {
            "intro": SceneType.INTRO,
            "fight1": SceneType.FIGHT1,
            "fight2": SceneType.FIGHT2,
            "climax": SceneType.CLIMAX,
            "outro": SceneType.OUTRO,
        }
        return mapping.get(raw_type.strip().lower(), SceneType.OTHER)

    def _validate_payload(self, raw: dict) -> GeneratedEpisodePayload:
        title = str(raw.get("title", "")).strip()
        description = str(raw.get("description", "")).strip()
        scenes = raw.get("scenes", [])
        character_results = raw.get("character_results", [])
        if not title or not description:
            raise ValueError("Script response missing title or description.")
        if not isinstance(scenes, list) or len(scenes) < 5:
            raise ValueError("Script response must contain at least 5 scenes.")
        if not isinstance(character_results, list):
            raise ValueError("character_results must be a list.")
        return GeneratedEpisodePayload(
            title=title,
            description=description,
            scenes=scenes,
            character_results=character_results,
        )

    def _ensure_unique_title(self, manager: ConsistencyManager, title: str, episode_number: int) -> str:
        """Generate a unique title, retrying with suffixes if needed."""
        candidate = title
        for attempt in range(5):
            if not manager.title_exists(candidate):
                return candidate
            candidate = f"{title} - Episode {episode_number}" if attempt == 0 else f"{title} ({attempt + 1})"
        raise ValueError(f"Could not generate unique title after 5 attempts. Base: '{title}'")

    def generate_and_persist(
        self,
        session: Session,
        job_id: int,
        series_name: str = "Spartacus Arena",
    ) -> tuple[Episode, GeneratedEpisodePayload]:
        """Generate script using GPT and save episode+scenes into DB."""
        manager = ConsistencyManager(session)
        try:
            series = manager.get_or_create_series(
                name=series_name,
                theme="Ancient Roman gladiator conflicts with serialized story continuity.",
                style="Dark cinematic, dramatic narration, high stakes combat.",
            )
            episode_number = manager.next_episode_number(series_id=series.id)
            context = manager.build_context(series=series, episode_number=episode_number)
            prompt = self._prompt(context=context)

            log_job_step(session, job_id, "scripting", StepStatus.STARTED, "Starting GPT-4o script generation.")

            response = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.9,
            )
            if not response.choices:
                raise ValueError("OpenAI returned empty choices list.")
            content = response.choices[0].message.content or "{}"
            payload = self._validate_payload(json.loads(content))
            _script_usage = response.usage

            unique_title = self._ensure_unique_title(manager, payload.title, episode_number)
            if unique_title != payload.title:
                payload = GeneratedEpisodePayload(
                    title=unique_title,
                    description=payload.description,
                    scenes=payload.scenes,
                    character_results=payload.character_results,
                )

            episode = Episode(
                series_id=series.id,
                episode_number=episode_number,
                title=payload.title,
                description=payload.description,
                status=EpisodeStatus.SCRIPTING,
            )
            session.add(episode)
            session.flush()

            for scene_raw in payload.scenes:
                narration = str(scene_raw.get("narration_text", "")).strip()
                if not narration:
                    continue
                scene = Scene(
                    episode_id=episode.id,
                    scene_order=int(scene_raw["scene_order"]),
                    scene_type=self._normalize_scene_type(str(scene_raw.get("scene_type", "other"))),
                    narration_text=narration,
                )
                session.add(scene)
            session.flush()

            manager.apply_character_results(session, episode, payload.character_results)

            if _script_usage:
                from pipeline.cost_tracker import log_openai_chat
                log_openai_chat(
                    session, episode.id, job_id, "script_generation",
                    settings.openai_model,
                    _script_usage.prompt_tokens, _script_usage.completion_tokens,
                )

            set_episode_status(session, episode, EpisodeStatus.VOICEOVER)
            log_job_step(
                session,
                job_id,
                "scripting",
                StepStatus.SUCCESS,
                f"Script generated for episode {episode.episode_number} with {len(payload.scenes)} scenes.",
            )
            return episode, payload
        except Exception as exc:
            log_job_step_isolated(job_id, "scripting", StepStatus.FAILED, f"Script generation failed: {exc}")
            logger.exception("Script generation failed.")
            raise
