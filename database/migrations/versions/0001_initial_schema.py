"""Initial schema for Spartacus automation pipeline.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-02-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


episode_status = sa.Enum(
    "pending", "scripting", "voiceover", "video_gen", "editing", "ready", "uploaded", "failed",
    name="episodestatus",
)
step_status = sa.Enum("started", "success", "failed", name="stepstatus")
scene_status = sa.Enum("pending", "voiceover_done", "video_done", "failed", name="scenestatus")
scene_type = sa.Enum("intro", "fight1", "fight2", "climax", "outro", "other", name="scenetype")
job_status = sa.Enum("pending", "running", "ready", "failed", name="jobstatus")


def upgrade() -> None:
    """Apply initial schema."""
    op.create_table(
        "series",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("theme", sa.String(length=255), nullable=False),
        sa.Column("style", sa.String(length=255), nullable=False),
        sa.Column("total_episodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "characters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("fighting_style", sa.String(length=255), nullable=False),
        sa.Column("personality", sa.String(length=255), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_alive", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_characters_is_alive", "characters", ["is_alive"])

    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("series_id", sa.Integer(), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", episode_status, nullable=False, server_default="pending"),
        sa.Column("youtube_video_id", sa.String(length=64), nullable=True),
        sa.Column("youtube_url", sa.String(length=500), nullable=True),
        sa.Column("scheduled_upload_at", sa.DateTime(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("series_id", "episode_number", name="uq_episode_series_number"),
    )
    op.create_index("ix_episodes_series_id", "episodes", ["series_id"])
    op.create_index("ix_episodes_status", "episodes", ["status"])

    op.create_table(
        "scenes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_order", sa.Integer(), nullable=False),
        sa.Column("scene_type", scene_type, nullable=False),
        sa.Column("narration_text", sa.Text(), nullable=False),
        sa.Column("audio_file_path", sa.String(length=500), nullable=True),
        sa.Column("video_clip_path", sa.String(length=500), nullable=True),
        sa.Column("status", scene_status, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("episode_id", "scene_order", name="uq_scene_episode_order"),
    )
    op.create_index("ix_scenes_episode_id", "scenes", ["episode_id"])
    op.create_index("ix_scenes_status", "scenes", ["status"])

    op.create_table(
        "character_stats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column("kills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notable_moment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("character_id", "episode_id", name="uq_character_episode_stat"),
    )
    op.create_index("ix_character_stats_character_id", "character_stats", ["character_id"])
    op.create_index("ix_character_stats_episode_id", "character_stats", ["episode_id"])

    op.create_table(
        "video_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", job_status, nullable=False, server_default="pending"),
        sa.Column("final_video_path", sa.String(length=500), nullable=True),
        sa.Column("thumbnail_path", sa.String(length=500), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_video_jobs_episode_id", "video_jobs", ["episode_id"])
    op.create_index("ix_video_jobs_status", "video_jobs", ["status"])

    op.create_table(
        "job_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("video_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("status", step_status, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("logged_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_job_logs_job_id", "job_logs", ["job_id"])
    op.create_index("ix_job_logs_logged_at", "job_logs", ["logged_at"])
    op.create_index("ix_job_logs_step", "job_logs", ["step"])


def downgrade() -> None:
    """Rollback initial schema."""
    op.drop_index("ix_job_logs_step", table_name="job_logs")
    op.drop_index("ix_job_logs_logged_at", table_name="job_logs")
    op.drop_index("ix_job_logs_job_id", table_name="job_logs")
    op.drop_table("job_logs")

    op.drop_index("ix_video_jobs_status", table_name="video_jobs")
    op.drop_index("ix_video_jobs_episode_id", table_name="video_jobs")
    op.drop_table("video_jobs")

    op.drop_index("ix_character_stats_episode_id", table_name="character_stats")
    op.drop_index("ix_character_stats_character_id", table_name="character_stats")
    op.drop_table("character_stats")

    op.drop_index("ix_scenes_status", table_name="scenes")
    op.drop_index("ix_scenes_episode_id", table_name="scenes")
    op.drop_table("scenes")

    op.drop_index("ix_episodes_status", table_name="episodes")
    op.drop_index("ix_episodes_series_id", table_name="episodes")
    op.drop_table("episodes")

    op.drop_index("ix_characters_is_alive", table_name="characters")
    op.drop_table("characters")
    op.drop_table("series")

    bind = op.get_bind()
    job_status.drop(bind, checkfirst=True)
    scene_type.drop(bind, checkfirst=True)
    scene_status.drop(bind, checkfirst=True)
    step_status.drop(bind, checkfirst=True)
    episode_status.drop(bind, checkfirst=True)
