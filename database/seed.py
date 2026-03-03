"""Seed script for initial Spartacus series and character roster."""

from __future__ import annotations

import logging

from sqlalchemy import select

from database.connection import session_scope
from database.models import Character, Series


logger = logging.getLogger(__name__)


INITIAL_SERIES = {
    "name": "Spartacus Arena",
    "theme": "Ancient Roman gladiator conflicts with serialized story continuity.",
    "style": "Dark cinematic, dramatic narration, high stakes combat.",
}

INITIAL_CHARACTERS = [
    {
        "name": "Spartacus",
        "origin": "Thrace",
        "fighting_style": "Dual Sword",
        "personality": "Noble warrior",
        "voice_id": "pNInz6obpgDQGcFmaJgB",   # Adam — deep, authoritative
    },
    {
        "name": "Crixus",
        "origin": "Gaul",
        "fighting_style": "Shield & Gladius",
        "personality": "Aggressive",
        "voice_id": "VR6AewLTigWG4xSOukaG",   # Arnold — strong, gruff
    },
    {
        "name": "Gannicus",
        "origin": "Celt",
        "fighting_style": "Twin Blades",
        "personality": "Wild & fearless",
        "voice_id": "ErXwobaYiN019PkySvjV",   # Antoni — youthful, charismatic
    },
    {
        "name": "Oenomaus",
        "origin": "Africa",
        "fighting_style": "Heavy Weapons",
        "personality": "Disciplined",
        "voice_id": "onwK4e9ZLuTAKqWW03F9",   # Daniel — calm, deep British
    },
    {
        "name": "Agron",
        "origin": "Germania",
        "fighting_style": "Spear",
        "personality": "Loyal & fierce",
        "voice_id": "N2lVS1w4EtoT3dr4eOWO",   # Callum — energetic, intense
    },
]


def seed_initial_data() -> None:
    """Seed initial records with idempotent behavior."""
    with session_scope() as session:
        series = session.execute(
            select(Series).where(Series.name == INITIAL_SERIES["name"])
        ).scalar_one_or_none()
        if not series:
            series = Series(**INITIAL_SERIES)
            session.add(series)
            session.flush()
            logger.info("Created series '%s' (id=%s)", series.name, series.id)
        else:
            logger.info("Series '%s' already exists (id=%s)", series.name, series.id)

        for item in INITIAL_CHARACTERS:
            existing = session.execute(
                select(Character).where(Character.name == item["name"])
            ).scalar_one_or_none()
            if existing:
                logger.info("Character '%s' already exists", existing.name)
                continue
            session.add(Character(**item))
            logger.info("Created character '%s'", item["name"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    seed_initial_data()
