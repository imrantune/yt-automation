"""Test database models and relationships."""

from __future__ import annotations

from database.models import (
    Character,
    Episode,
    EpisodeStatus,
    JobStatus,
    Scene,
    SceneType,
    Series,
    VideoJob,
)


def test_create_series(db_session):
    series = Series(name="Test Arena", theme="gladiator", style="dark")
    db_session.add(series)
    db_session.flush()
    assert series.id is not None
    assert series.name == "Test Arena"


def test_create_episode_with_scenes(db_session):
    series = Series(name="Test", theme="t", style="s")
    db_session.add(series)
    db_session.flush()

    episode = Episode(
        series_id=series.id,
        episode_number=1,
        title="Blood and Sand",
        description="First episode",
        status=EpisodeStatus.PENDING,
    )
    db_session.add(episode)
    db_session.flush()

    for i, st in enumerate([SceneType.INTRO, SceneType.FIGHT1, SceneType.FIGHT2, SceneType.CLIMAX, SceneType.OUTRO], 1):
        scene = Scene(
            episode_id=episode.id,
            scene_order=i,
            scene_type=st,
            narration_text=f"Narration for scene {i}.",
        )
        db_session.add(scene)
    db_session.flush()

    assert len(episode.scenes) == 5
    assert episode.scenes[0].scene_type == SceneType.INTRO
    assert episode.scenes[4].scene_type == SceneType.OUTRO


def test_create_video_job(db_session):
    series = Series(name="S", theme="t", style="s")
    db_session.add(series)
    db_session.flush()

    episode = Episode(series_id=series.id, episode_number=1, title="E1", description="d", status=EpisodeStatus.PENDING)
    db_session.add(episode)
    db_session.flush()

    job = VideoJob(episode_id=episode.id, status=JobStatus.PENDING)
    db_session.add(job)
    db_session.flush()

    assert job.id is not None
    assert job.status == JobStatus.PENDING
    assert job.episode.title == "E1"


def test_character_with_voice(db_session):
    char = Character(
        name="Spartacus",
        origin="Thrace",
        fighting_style="dual swords",
        personality="determined leader",
        voice_id="test-voice-123",
    )
    db_session.add(char)
    db_session.flush()

    assert char.id is not None
    assert char.voice_id == "test-voice-123"
    assert char.is_alive is True
    assert char.wins == 0


def test_episode_status_transitions(db_session):
    series = Series(name="S", theme="t", style="s")
    db_session.add(series)
    db_session.flush()

    ep = Episode(series_id=series.id, episode_number=1, title="T", description="d", status=EpisodeStatus.PENDING)
    db_session.add(ep)
    db_session.flush()

    for status in [EpisodeStatus.SCRIPTING, EpisodeStatus.VOICEOVER, EpisodeStatus.VIDEO_GEN,
                   EpisodeStatus.EDITING, EpisodeStatus.READY, EpisodeStatus.UPLOADED]:
        ep.status = status
        db_session.flush()
        assert ep.status == status
