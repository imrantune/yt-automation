"""Add voice_id column to characters for per-character voice mapping.

Revision ID: 0003_character_voice_id
Revises: 0002_phase2_columns
Create Date: 2026-03-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_character_voice_id"
down_revision: Union[str, Sequence[str], None] = "0002_phase2_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("voice_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "voice_id")
