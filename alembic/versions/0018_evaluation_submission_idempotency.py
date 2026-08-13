"""add durable EvaluationSubmission idempotency key

Revision ID: 0018_evaluation_submission_idempotency
Revises: 0017_skill_optimization_ledger
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0018_evaluation_submission_idempotency"
down_revision: str | None = "0017_skill_optimization_ledger"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        item["name"]
        for item in inspect(op.get_bind()).get_columns("evaluation_submissions")
    }
    if "idempotency_key" not in columns:
        with op.batch_alter_table("evaluation_submissions") as batch:
            batch.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
            batch.create_unique_constraint(
                "uq_evaluation_submissions_idempotency_key", ["idempotency_key"]
            )
    run_group_uniques = {
        item["name"]
        for item in inspect(op.get_bind()).get_unique_constraints(
            "optimization_run_groups"
        )
    }
    if "uq_optimization_run_group_config" not in run_group_uniques:
        with op.batch_alter_table("optimization_run_groups") as batch:
            batch.create_unique_constraint(
                "uq_optimization_run_group_config",
                ["experiment_id", "run_config_hash"],
            )


def downgrade() -> None:
    run_group_uniques = {
        item["name"]
        for item in inspect(op.get_bind()).get_unique_constraints(
            "optimization_run_groups"
        )
    }
    if "uq_optimization_run_group_config" in run_group_uniques:
        with op.batch_alter_table("optimization_run_groups") as batch:
            batch.drop_constraint(
                "uq_optimization_run_group_config", type_="unique"
            )
    columns = {
        item["name"]
        for item in inspect(op.get_bind()).get_columns("evaluation_submissions")
    }
    if "idempotency_key" in columns:
        with op.batch_alter_table("evaluation_submissions") as batch:
            if op.get_bind().dialect.name != "sqlite":
                batch.drop_constraint(
                    "uq_evaluation_submissions_idempotency_key", type_="unique"
                )
            batch.drop_column("idempotency_key")
