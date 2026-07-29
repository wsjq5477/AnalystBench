"""p18 evaluation method run timing

Revision ID: 0012_p18_method_run_timing
Revises: 0011_p16_evaluation_schedules
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0012_p18_method_run_timing"
down_revision: str | None = "0011_p16_evaluation_schedules"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in inspect(bind).get_columns(
            "evaluation_submission_method_runs"
        )
    }
    if "started_at" not in columns:
        op.add_column(
            "evaluation_submission_method_runs",
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "finished_at" not in columns:
        op.add_column(
            "evaluation_submission_method_runs",
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "duration_ms" not in columns:
        op.add_column(
            "evaluation_submission_method_runs",
            sa.Column("duration_ms", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(
            "evaluation_submission_method_runs"
        )
    }
    if "duration_ms" in columns:
        op.drop_column("evaluation_submission_method_runs", "duration_ms")
    if "finished_at" in columns:
        op.drop_column("evaluation_submission_method_runs", "finished_at")
    if "started_at" in columns:
        op.drop_column("evaluation_submission_method_runs", "started_at")
