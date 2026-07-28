"""p16 built-in daily evaluation schedules

Revision ID: 0011_p16_evaluation_schedules
Revises: 0010_p15_evaluation_submissions
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0011_p16_evaluation_schedules"
down_revision: str | None = "0010_p15_evaluation_submissions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()
    if "evaluation_schedules" not in tables:
        op.create_table(
            "evaluation_schedules",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("dataset_key", sa.String(255), nullable=False),
            sa.Column("case_mode", sa.String(32), nullable=False),
            sa.Column("case_paths_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("method_ids_json", sa.Text(), nullable=False),
            sa.Column("judge_runner", sa.String(32), nullable=False),
            sa.Column("timezone", sa.String(100), nullable=False),
            sa.Column("local_time", sa.String(5), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
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
        op.create_index(
            "ix_evaluation_schedules_dataset_key",
            "evaluation_schedules",
            ["dataset_key"],
        )
        op.create_index(
            "ix_evaluation_schedules_enabled",
            "evaluation_schedules",
            ["enabled"],
        )
        op.create_index(
            "ix_evaluation_schedules_next_run_at",
            "evaluation_schedules",
            ["next_run_at"],
        )

    if "evaluation_schedule_runs" not in tables:
        op.create_table(
            "evaluation_schedule_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "schedule_id",
                sa.String(36),
                sa.ForeignKey("evaluation_schedules.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("trigger_key", sa.String(255), nullable=False, unique=True),
            sa.Column("trigger_type", sa.String(32), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("config_snapshot_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
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
        op.create_index(
            "ix_evaluation_schedule_runs_schedule_id",
            "evaluation_schedule_runs",
            ["schedule_id"],
        )
        op.create_index(
            "ix_evaluation_schedule_runs_scheduled_for",
            "evaluation_schedule_runs",
            ["scheduled_for"],
        )
        op.create_index(
            "ix_evaluation_schedule_runs_status",
            "evaluation_schedule_runs",
            ["status"],
        )

    inspector = inspect(bind)
    submission_columns = {
        column["name"] for column in inspector.get_columns("evaluation_submissions")
    }
    if "schedule_run_id" not in submission_columns:
        op.add_column(
            "evaluation_submissions",
            sa.Column(
                "schedule_run_id",
                sa.String(36),
                nullable=True,
            ),
        )
        if bind.dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_evaluation_submissions_schedule_run_id",
                "evaluation_submissions",
                "evaluation_schedule_runs",
                ["schedule_run_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    submission_indexes = {
        index["name"] for index in inspect(bind).get_indexes("evaluation_submissions")
    }
    if "ix_evaluation_submissions_schedule_run_id" not in submission_indexes:
        op.create_index(
            "ix_evaluation_submissions_schedule_run_id",
            "evaluation_submissions",
            ["schedule_run_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index(
        "ix_evaluation_submissions_schedule_run_id",
        table_name="evaluation_submissions",
    )
    if bind.dialect.name != "sqlite":
        op.drop_constraint(
            "fk_evaluation_submissions_schedule_run_id",
            "evaluation_submissions",
            type_="foreignkey",
        )
    op.drop_column("evaluation_submissions", "schedule_run_id")
    op.drop_table("evaluation_schedule_runs")
    op.drop_table("evaluation_schedules")
