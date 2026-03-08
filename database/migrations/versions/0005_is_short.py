"""Add is_short to episodes table.

Revision ID: 0005_is_short
Revises: 0004_api_cost_logs
Create Date: 2026-03-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_is_short"
down_revision: Union[str, Sequence[str], None] = "0004_api_cost_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("episodes", sa.Column("is_short", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("episodes", "is_short")
