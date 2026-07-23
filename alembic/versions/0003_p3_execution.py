"""p3 durable agent execution

Revision ID: 0003_p3_execution
Revises: 0002_p2_catalog
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0003_p3_execution"
down_revision: str | None = "0002_p2_catalog"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "locked_by" not in job_columns:
        op.add_column("jobs", sa.Column("locked_by", sa.String(length=100), nullable=True))
    if "lease_until" not in job_columns:
        op.add_column("jobs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    if "last_error" not in job_columns:
        op.add_column("jobs", sa.Column("last_error", sa.Text(), nullable=True))
    job_indexes = {index["name"] for index in inspector.get_indexes("jobs")}
    if "ix_jobs_locked_by" not in job_indexes:
        op.create_index("ix_jobs_locked_by", "jobs", ["locked_by"], unique=False)

    profile_columns = {column["name"] for column in inspector.get_columns("execution_profiles")}
    if "status" not in profile_columns:
        op.add_column(
            "execution_profiles",
            sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        )
    profile_indexes = {index["name"] for index in inspector.get_indexes("execution_profiles")}
    if "ix_execution_profiles_status" not in profile_indexes:
        op.create_index(
            "ix_execution_profiles_status", "execution_profiles", ["status"], unique=False
        )


def downgrade() -> None:
    op.drop_index("ix_execution_profiles_status", table_name="execution_profiles")
    op.drop_column("execution_profiles", "status")
    op.drop_index("ix_jobs_locked_by", table_name="jobs")
    op.drop_column("jobs", "last_error")
    op.drop_column("jobs", "lease_until")
    op.drop_column("jobs", "locked_by")
