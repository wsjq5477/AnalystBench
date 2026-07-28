"""p15 evaluation submissions and command methods

Revision ID: 0010_p15_evaluation_submissions
Revises: 0009_p14_remove_domain_tags
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0010_p15_evaluation_submissions"
down_revision: str | None = "0009_p14_remove_domain_tags"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "evaluation_methods" not in tables:
        op.create_table(
            "evaluation_methods",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("method_key", sa.String(100), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("tool_dir", sa.String(1024), nullable=True),
            sa.Column("command_template", sa.Text(), nullable=False),
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="1800"),
            sa.Column(
                "max_output_bytes",
                sa.Integer(),
                nullable=False,
                server_default=str(10 * 1024 * 1024),
            ),
            sa.Column("concurrency_limit", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("content_hash", sa.String(71), nullable=False, unique=True),
            sa.Column("last_probe_json", sa.Text(), nullable=False, server_default="{}"),
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
            sa.UniqueConstraint(
                "method_key", "version_number", name="uq_evaluation_methods_key_version"
            ),
        )
        op.create_index("ix_evaluation_methods_method_key", "evaluation_methods", ["method_key"])
        op.create_index("ix_evaluation_methods_status", "evaluation_methods", ["status"])

    if "evaluation_submissions" not in tables:
        op.create_table(
            "evaluation_submissions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("dataset_key", sa.String(255), nullable=False),
            sa.Column("run_timestamp", sa.String(14), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("manifest_json", sa.Text(), nullable=False),
            sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
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
            "ix_evaluation_submissions_dataset_key", "evaluation_submissions", ["dataset_key"]
        )
        op.create_index(
            "ix_evaluation_submissions_run_timestamp",
            "evaluation_submissions",
            ["run_timestamp"],
        )
        op.create_index("ix_evaluation_submissions_status", "evaluation_submissions", ["status"])

    if "evaluation_submission_case_runs" not in tables:
        op.create_table(
            "evaluation_submission_case_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "submission_id",
                sa.String(36),
                sa.ForeignKey("evaluation_submissions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("case_path", sa.String(1024), nullable=False),
            sa.Column("case_key", sa.String(255), nullable=False),
            sa.Column("run_directory", sa.String(2048), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("scoring_status", sa.String(32), nullable=False, server_default="pending"),
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
            sa.UniqueConstraint(
                "submission_id", "case_path", name="uq_evaluation_submission_case_path"
            ),
        )
        op.create_index(
            "ix_evaluation_submission_case_runs_submission_id",
            "evaluation_submission_case_runs",
            ["submission_id"],
        )
        op.create_index(
            "ix_evaluation_submission_case_runs_status",
            "evaluation_submission_case_runs",
            ["status"],
        )
        op.create_index(
            "ix_evaluation_submission_case_runs_scoring_status",
            "evaluation_submission_case_runs",
            ["scoring_status"],
        )

    if "evaluation_submission_method_runs" not in tables:
        op.create_table(
            "evaluation_submission_method_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "case_run_id",
                sa.String(36),
                sa.ForeignKey("evaluation_submission_case_runs.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "method_id",
                sa.String(36),
                sa.ForeignKey("evaluation_methods.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("artifact_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("error_code", sa.String(100), nullable=True),
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
            sa.UniqueConstraint("case_run_id", "method_id", name="uq_evaluation_submission_method"),
        )
        op.create_index(
            "ix_evaluation_submission_method_runs_case_run_id",
            "evaluation_submission_method_runs",
            ["case_run_id"],
        )
        op.create_index(
            "ix_evaluation_submission_method_runs_method_id",
            "evaluation_submission_method_runs",
            ["method_id"],
        )
        op.create_index(
            "ix_evaluation_submission_method_runs_status",
            "evaluation_submission_method_runs",
            ["status"],
        )


def downgrade() -> None:
    op.drop_table("evaluation_submission_method_runs")
    op.drop_table("evaluation_submission_case_runs")
    op.drop_table("evaluation_submissions")
    op.drop_table("evaluation_methods")
