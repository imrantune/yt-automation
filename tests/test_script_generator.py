"""Test script generator with mocked OpenAI API."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from database.models import Episode, EpisodeStatus, Scene, SceneType, Series, VideoJob, JobStatus
from pipeline.script_generator import ScriptGenerator, GeneratedEpisodePayload


MOCK_GPT_RESPONSE = {
    "title": "Trial by Fire",
    "description": "Spartacus faces his greatest challenge yet.",
    "scenes": [
        {"scene_order": 1, "scene_type": "intro", "narration_text": "The arena stirs with anticipation."},
        {"scene_order": 2, "scene_type": "fight1", "narration_text": "Spartacus steps forward with purpose."},
        {"scene_order": 3, "scene_type": "fight2", "narration_text": "The crowd erupts as blades meet."},
        {"scene_order": 4, "scene_type": "climax", "narration_text": "A decisive moment changes everything."},
        {"scene_order": 5, "scene_type": "outro", "narration_text": "As dust settles, legends are born."},
    ],
    "character_results": [
        {"name": "Spartacus", "result": "win", "kills": 0, "notable_moment": "Showed true courage."},
    ],
}


def test_validate_payload():
    gen = ScriptGenerator.__new__(ScriptGenerator)
    payload = gen._validate_payload(MOCK_GPT_RESPONSE)
    assert isinstance(payload, GeneratedEpisodePayload)
    assert payload.title == "Trial by Fire"
    assert len(payload.scenes) == 5


def test_validate_payload_rejects_missing_title():
    gen = ScriptGenerator.__new__(ScriptGenerator)
    bad = {**MOCK_GPT_RESPONSE, "title": ""}
    try:
        gen._validate_payload(bad)
        assert False, "Should have raised"
    except ValueError:
        pass


def test_validate_payload_rejects_too_few_scenes():
    gen = ScriptGenerator.__new__(ScriptGenerator)
    bad = {**MOCK_GPT_RESPONSE, "scenes": [{"scene_order": 1, "scene_type": "intro", "narration_text": "x"}]}
    try:
        gen._validate_payload(bad)
        assert False, "Should have raised"
    except ValueError:
        pass


def test_normalize_scene_type():
    assert ScriptGenerator._normalize_scene_type("intro") == SceneType.INTRO
    assert ScriptGenerator._normalize_scene_type("FIGHT1") == SceneType.FIGHT1
    assert ScriptGenerator._normalize_scene_type("climax") == SceneType.CLIMAX
    assert ScriptGenerator._normalize_scene_type("unknown") == SceneType.OTHER


@patch("pipeline.script_generator.OpenAI")
@patch("pipeline.script_generator.ConsistencyManager")
def test_generate_and_persist(mock_cm_class, mock_openai_class, db_session):
    """Full script generation with mocked GPT call."""
    series = Series(name="Spartacus Arena", theme="gladiator", style="dark")
    db_session.add(series)
    db_session.flush()

    job = VideoJob(status=JobStatus.PENDING)
    db_session.add(job)
    db_session.flush()

    mock_cm = MagicMock()
    mock_cm_class.return_value = mock_cm
    mock_cm.get_or_create_series.return_value = series
    mock_cm.next_episode_number.return_value = 1
    mock_cm.build_context.return_value = {"alive_characters": [], "recent_summaries": []}
    mock_cm.title_exists.return_value = False

    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(MOCK_GPT_RESPONSE)
    mock_response.usage = MagicMock(prompt_tokens=500, completion_tokens=800)
    mock_client.chat.completions.create.return_value = mock_response

    gen = ScriptGenerator()
    gen.client = mock_client

    with patch("pipeline.script_generator.log_job_step"), \
         patch("pipeline.script_generator.log_job_step_isolated"), \
         patch("pipeline.script_generator.set_episode_status"), \
         patch("pipeline.cost_tracker.log_openai_chat"):

        episode, payload = gen.generate_and_persist(session=db_session, job_id=job.id)

    assert episode.title == "Trial by Fire"
    assert episode.episode_number == 1
    assert len(list(db_session.query(Scene).filter_by(episode_id=episode.id))) == 5
