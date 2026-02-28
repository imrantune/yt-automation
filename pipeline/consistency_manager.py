"""Continuity and story context manager for script generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from database.models import Character, CharacterStat, Episode, Series


logger = logging.getLogger(__name__)

MIN_ALIVE_CHARACTERS = 3


@dataclass(frozen=True)
class StoryRules:
    """Rule switches that influence script generation prompt."""

    should_kill_character: bool
    should_add_new_character: bool
    is_grand_tournament: bool


class ConsistencyManager:
    """Reads DB state and prepares continuity context for GPT."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create_series(self, name: str, theme: str, style: str) -> Series:
        """Get existing series or create a new one."""
        series = self.session.execute(
            select(Series).where(Series.name == name)
        ).scalar_one_or_none()
        if series:
            return series
        series = Series(name=name, theme=theme, style=style)
        self.session.add(series)
        self.session.flush()
        return series

    def next_episode_number(self, series_id: int) -> int:
        """Compute next episode number for a series."""
        current_max = self.session.execute(
            select(func.max(Episode.episode_number)).where(Episode.series_id == series_id)
        ).scalar_one()
        return (current_max or 0) + 1

    def title_exists(self, title: str) -> bool:
        """Check if episode title already exists."""
        existing = self.session.execute(select(Episode.id).where(Episode.title == title)).first()
        return existing is not None

    def get_alive_characters(self) -> list[Character]:
        """Fetch all alive characters."""
        return list(
            self.session.execute(
                select(Character).where(Character.is_alive.is_(True)).order_by(Character.name.asc())
            ).scalars()
        )

    def get_recent_episode_summaries(self, series_id: int, limit: int = 3) -> list[str]:
        """Return most recent episode descriptions for continuity."""
        episodes = list(
            self.session.execute(
                select(Episode)
                .where(Episode.series_id == series_id)
                .order_by(desc(Episode.episode_number))
                .limit(limit)
            ).scalars()
        )
        return [f"Episode {ep.episode_number}: {ep.description}" for ep in episodes]

    def get_story_rules(self, episode_number: int) -> StoryRules:
        """Return rule flags based on episode cadence and current roster size."""
        alive_count = self.session.execute(
            select(func.count()).select_from(Character).where(Character.is_alive.is_(True))
        ).scalar_one()
        return StoryRules(
            should_kill_character=(episode_number % 5 == 0 and alive_count > MIN_ALIVE_CHARACTERS),
            should_add_new_character=(episode_number % 10 == 0),
            is_grand_tournament=(episode_number % 20 == 0),
        )

    def build_context(self, series: Series, episode_number: int) -> dict:
        """Build context payload for script generation."""
        characters = self.get_alive_characters()
        if not characters:
            raise ValueError("No alive characters found. Seed the database before generating episodes.")
        summaries = self.get_recent_episode_summaries(series_id=series.id, limit=3)
        rules = self.get_story_rules(episode_number=episode_number)
        return {
            "series": {
                "id": series.id,
                "name": series.name,
                "theme": series.theme,
                "style": series.style,
            },
            "episode_number": episode_number,
            "alive_characters": [
                {
                    "name": c.name,
                    "origin": c.origin,
                    "fighting_style": c.fighting_style,
                    "personality": c.personality,
                    "wins": c.wins,
                    "losses": c.losses,
                }
                for c in characters
            ],
            "recent_summaries": summaries,
            "rules": {
                "should_kill_character": rules.should_kill_character,
                "should_add_new_character": rules.should_add_new_character,
                "is_grand_tournament": rules.is_grand_tournament,
            },
        }

    def apply_character_results(self, session: Session, episode: Episode, results: list[dict]) -> None:
        """Persist character_results from GPT script: update stats, wins/losses, alive status."""
        for entry in results:
            name = str(entry.get("name", "")).strip()
            result = str(entry.get("result", "")).strip().lower()
            kills = int(entry.get("kills", 0))
            notable = str(entry.get("notable_moment", ""))
            if not name or not result:
                continue

            character = session.execute(
                select(Character).where(Character.name == name)
            ).scalar_one_or_none()
            if not character:
                logger.warning("Character '%s' from results not found in DB, skipping.", name)
                continue

            stat = CharacterStat(
                character_id=character.id,
                episode_id=episode.id,
                result=result,
                kills=kills,
                notable_moment=notable,
            )
            session.add(stat)

            if result == "win":
                character.wins += 1
            elif result == "loss":
                character.losses += 1
            elif result == "death":
                character.losses += 1
                character.is_alive = False
                logger.info("Character '%s' has died in episode %s.", name, episode.episode_number)

            session.add(character)

        session.flush()
