"""Add api_cost_logs table for per-episode API cost tracking.

Revision ID: 0004_api_cost_logs
Revises: 0003_character_voice_id
Create Date: 2026-03-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_api_cost_logs"
down_revision: Union[str, Sequence[str], None] = "0003_character_voice_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_cost_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("video_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("input_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_type", sa.String(length=32), nullable=False, server_default="tokens"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_api_cost_logs_episode_id", "api_cost_logs", ["episode_id"])
    op.create_index("ix_api_cost_logs_service", "api_cost_logs", ["service"])
    op.create_index("ix_api_cost_logs_job_id", "api_cost_logs", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_api_cost_logs_job_id", table_name="api_cost_logs")
    op.drop_index("ix_api_cost_logs_service", table_name="api_cost_logs")
    op.drop_index("ix_api_cost_logs_episode_id", table_name="api_cost_logs")
    op.drop_table("api_cost_logs")
