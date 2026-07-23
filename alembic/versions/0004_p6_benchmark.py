"""p6 durable benchmark execution

Revision ID: 0004_p6_benchmark
Revises: 0003_p3_execution
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0004_p6_benchmark"
down_revision: str | None = "0003_p3_execution"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "benchmark_runs" not in inspector.get_table_names():
        op.create_table(
            "benchmark_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "dataset_version_id",
                sa.String(length=36),
                sa.ForeignKey("dataset_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "candidate_version_id",
                sa.String(length=36),
                sa.ForeignKey("candidate_versions.id"),
                nullable=False,
            ),
            sa.Column(
                "scoring_policy_version_id",
                sa.String(length=36),
                sa.ForeignKey("scoring_policy_versions.id"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column(
                "cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("manifest_json", sa.Text(), nullable=False),
            sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
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
            "ix_benchmark_runs_dataset_version_id", "benchmark_runs", ["dataset_version_id"]
        )
        op.create_index(
            "ix_benchmark_runs_candidate_version_id", "benchmark_runs", ["candidate_version_id"]
        )
        op.create_index("ix_benchmark_runs_status", "benchmark_runs", ["status"])
    if "benchmark_case_runs" not in inspector.get_table_names():
        op.create_table(
            "benchmark_case_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "benchmark_run_id",
                sa.String(length=36),
                sa.ForeignKey("benchmark_runs.id"),
                nullable=False,
            ),
            sa.Column(
                "case_revision_id",
                sa.String(length=36),
                sa.ForeignKey("case_revisions.id"),
                nullable=False,
            ),
            sa.Column(
                "candidate_report_id",
                sa.String(length=36),
                sa.ForeignKey("candidate_reports.id"),
                nullable=False,
            ),
            sa.Column(
                "eval_spec_version_id",
                sa.String(length=36),
                sa.ForeignKey("eval_spec_versions.id"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("stage", sa.String(length=32), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column(
                "result_content_hash",
                sa.String(length=71),
                sa.ForeignKey("content_blobs.content_hash"),
                nullable=True,
            ),
            sa.Column("attempts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("error_code", sa.String(length=100), nullable=True),
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
            sa.UniqueConstraint("benchmark_run_id", "case_revision_id", name="uq_benchmark_case"),
        )
        op.create_index(
            "ix_benchmark_case_runs_benchmark_run_id", "benchmark_case_runs", ["benchmark_run_id"]
        )
        op.create_index(
            "ix_benchmark_case_runs_case_revision_id", "benchmark_case_runs", ["case_revision_id"]
        )
        op.create_index("ix_benchmark_case_runs_status", "benchmark_case_runs", ["status"])


def downgrade() -> None:
    op.drop_table("benchmark_case_runs")
    op.drop_table("benchmark_runs")
