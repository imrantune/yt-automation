"""SQLAlchemy ORM models for Spartacus automation pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Return timezone-naive UTC datetime for DB defaults."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class EpisodeStatus(str, Enum):
    """Lifecycle states for an episode."""

    PENDING = "pending"
    SCRIPTING = "scripting"
    VOICEOVER = "voiceover"
    VIDEO_GEN = "video_gen"
    EDITING = "editing"
    READY = "ready"
    UPLOADED = "uploaded"
    FAILED = "failed"


class StepStatus(str, Enum):
    """Status for job log entries."""

    STARTED = "started"
    SUCCESS = "success"
    FAILED = "failed"


class SceneStatus(str, Enum):
    """Status for each individual scene."""

    PENDING = "pending"
    VOICEOVER_DONE = "voiceover_done"
    VIDEO_DONE = "video_done"
    FAILED = "failed"


class SceneType(str, Enum):
    """Canonical scene segment types."""

    INTRO = "intro"
    FIGHT1 = "fight1"
    FIGHT2 = "fight2"
    CLIMAX = "climax"
    OUTRO = "outro"
    OTHER = "other"


class JobStatus(str, Enum):
    """Video job status lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class Series(Base):
    """Series metadata."""

    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    theme: Mapped[str] = mapped_column(String(255), nullable=False)
    style: Mapped[str] = mapped_column(String(255), nullable=False)
    total_episodes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    episodes: Mapped[list["Episode"]] = relationship("Episode", back_populates="series")


class Episode(Base):
    """Episode records and publishing metadata."""

    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("series_id", "episode_number", name="uq_episode_series_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), nullable=False, index=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[EpisodeStatus] = mapped_column(
        SAEnum(EpisodeStatus), nullable=False, default=EpisodeStatus.PENDING, index=True
    )
    youtube_video_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_upload_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    series: Mapped["Series"] = relationship("Series", back_populates="episodes")
    scenes: Mapped[list["Scene"]] = relationship("Scene", back_populates="episode", cascade="all, delete-orphan")
    character_stats: Mapped[list["CharacterStat"]] = relationship(
        "CharacterStat", back_populates="episode", cascade="all, delete-orphan"
    )
    video_jobs: Mapped[list["VideoJob"]] = relationship("VideoJob", back_populates="episode", cascade="all, delete-orphan")


class Scene(Base):
    """Narration and asset metadata for each scene."""

    __tablename__ = "scenes"
    __table_args__ = (UniqueConstraint("episode_id", "scene_order", name="uq_scene_episode_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), nullable=False, index=True)
    scene_order: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_type: Mapped[SceneType] = mapped_column(SAEnum(SceneType), nullable=False, default=SceneType.OTHER)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    video_clip_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SceneStatus] = mapped_column(
        SAEnum(SceneStatus), nullable=False, default=SceneStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    episode: Mapped["Episode"] = relationship("Episode", back_populates="scenes")


class Character(Base):
    """Gladiator roster and aggregate stats."""

    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    fighting_style: Mapped[str] = mapped_column(String(255), nullable=False)
    personality: Mapped[str] = mapped_column(String(255), nullable=False)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    stats: Mapped[list["CharacterStat"]] = relationship("CharacterStat", back_populates="character")


class CharacterStat(Base):
    """Per-episode character result stats."""

    __tablename__ = "character_stats"
    __table_args__ = (UniqueConstraint("character_id", "episode_id", name="uq_character_episode_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False, index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    kills: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notable_moment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    character: Mapped["Character"] = relationship("Character", back_populates="stats")
    episode: Mapped["Episode"] = relationship("Episode", back_populates="character_stats")


class VideoJob(Base):
    """End-to-end job tracking for episode production."""

    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), nullable=True, index=True)
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus), nullable=False, default=JobStatus.PENDING, index=True)
    final_video_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    episode: Mapped["Episode | None"] = relationship("Episode", back_populates="video_jobs")
    logs: Mapped[list["JobLog"]] = relationship("JobLog", back_populates="job", cascade="all, delete-orphan")


class JobLog(Base):
    """Structured logs for each pipeline step."""

    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("video_jobs.id"), nullable=False, index=True)
    step: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[StepStatus] = mapped_column(SAEnum(StepStatus), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)

    job: Mapped["VideoJob"] = relationship("VideoJob", back_populates="logs")
