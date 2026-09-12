"""Schedule versions carry their analytics and lifecycle details.

``schedule_versions.analytics`` stores the executive KPIs, capacity table,
bottlenecks and data-quality summary computed together with the version;
``schedule_versions.details`` stores lifecycle facts (trigger, previous
version, writeback receipt, replanning decision).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12 06:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("schedule_versions") as batch:
        batch.add_column(sa.Column("analytics", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("details", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("schedule_versions") as batch:
        batch.drop_column("details")
        batch.drop_column("analytics")
