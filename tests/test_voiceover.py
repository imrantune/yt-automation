"""Test voiceover generator with mocked ElevenLabs API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from database.models import Character, Episode, EpisodeStatus, Scene, SceneType, Series, VideoJob, JobStatus
from pipeline.voiceover import VoiceoverGenerator


@patch("pipeline.voiceover.requests.post")
def test_synthesize_writes_audio(mock_post, tmp_path):
    """Basic synthesis should write bytes to file."""
    mock_resp = MagicMock()
    mock_resp.content = b"\xff" * 2048
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    gen = VoiceoverGenerator()
    out = tmp_path / "test.mp3"
    gen._synthesize("Hello gladiator", out, voice_id="test-voice")

    assert out.exists()
    assert out.stat().st_size == 2048


@patch("pipeline.voiceover.requests.post")
def test_synthesize_rejects_tiny_audio(mock_post, tmp_path):
    """Audio smaller than 1KB should be rejected."""
    mock_resp = MagicMock()
    mock_resp.content = b"\xff" * 100
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    gen = VoiceoverGenerator()
    out = tmp_path / "test.mp3"
    try:
        gen._synthesize("Hello", out)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "suspiciously small" in str(e)


def test_pick_voice_for_scene_returns_character_voice(db_session):
    """Should return a character's voice when their name is in the narration."""
    char = Character(
        name="Spartacus", origin="Thrace", fighting_style="dual swords",
        personality="leader", voice_id="voice-spartacus",
    )
    db_session.add(char)
    db_session.flush()

    scene = MagicMock()
    scene.narration_text = "Spartacus raises his sword toward the sky."
    scene.scene_order = 1

    gen = VoiceoverGenerator()
    voice = gen._pick_voice_for_scene(db_session, scene)
    assert voice == "voice-spartacus"


def test_pick_voice_fallback_to_narrator(db_session):
    """Should return default narrator voice when no character matches."""
    scene = MagicMock()
    scene.narration_text = "The crowd watches in silence."
    scene.scene_order = 1

    gen = VoiceoverGenerator()
    voice = gen._pick_voice_for_scene(db_session, scene)
    assert voice is not None  # falls back to DEFAULT_NARRATOR_VOICE
