"""threads.graph_version: release and state schema that started each thread (T26.2)

Every existing thread was started by 2.0.0 with resolution state schema 1, so the backfill is exact.
Additive and safe before the code: the previous release does not read the column, and the server default
covers its inserts during a rolling deploy.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("graph_version", sa.String(length=40), nullable=False, server_default="2.0.0+s1"),
    )
    with op.get_context().autocommit_block():  # no write lock on a live threads table during a rolling deploy
        op.create_index(
            "ix_threads_graph_version", "threads", ["graph", "graph_version"], postgresql_concurrently=True
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_threads_graph_version", table_name="threads", postgresql_concurrently=True)
    op.drop_column("threads", "graph_version")
