"""Phase 2: subtitles, SEO, shorts tables.

Revision ID: 0002_phase2_columns
Revises: 0001_initial_schema
Create Date: 2026-02-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_phase2_columns"
down_revision: Union[str, Sequence[str], None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Phase 2 columns and tables."""
    op.add_column("scenes", sa.Column("subtitle_file_path", sa.String(length=500), nullable=True))

    op.create_table(
        "episode_seo",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title_seo", sa.String(length=255), nullable=False),
        sa.Column("description_seo", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("hashtags", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_episode_seo_episode_id", "episode_seo", ["episode_id"], unique=True)

    op.create_table(
        "shorts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("youtube_video_id", sa.String(length=64), nullable=True),
        sa.Column("youtube_url", sa.String(length=500), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_shorts_episode_id", "shorts", ["episode_id"])


def downgrade() -> None:
    """Remove Phase 2 columns and tables."""
    op.drop_index("ix_shorts_episode_id", table_name="shorts")
    op.drop_table("shorts")

    op.drop_index("ix_episode_seo_episode_id", table_name="episode_seo")
    op.drop_table("episode_seo")

    op.drop_column("scenes", "subtitle_file_path")
