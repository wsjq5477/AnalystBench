"""p10 interactive evaluation sessions

Revision ID: 0005_p10_evaluation_sessions
Revises: 0004_p6_benchmark
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0005_p10_evaluation_sessions"
down_revision: str | None = "0004_p6_benchmark"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    if "evaluation_sessions" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "evaluation_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("case_draft_json", sa.Text(), nullable=False),
        sa.Column("report_drafts_json", sa.Text(), nullable=False),
        sa.Column("working_json", sa.Text(), nullable=False),
        sa.Column("questions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("answers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("resources_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_evaluation_sessions_status", "evaluation_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_sessions_status", table_name="evaluation_sessions")
    op.drop_table("evaluation_sessions")
