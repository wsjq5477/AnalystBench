"""move provider-wide runtime limits from Harness to Model

Revision ID: 0020_model_runtime_limits
Revises: 0019_evaluation_skill_selections
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0020_model_runtime_limits"
down_revision: str | None = "0019_evaluation_skill_selections"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_models")
    }
    if "timeout_seconds" not in columns:
        op.add_column(
            "evaluation_models",
            sa.Column(
                "timeout_seconds",
                sa.Integer(),
                nullable=False,
                server_default="21600",
            ),
        )
    if "concurrency_limit" not in columns:
        op.add_column(
            "evaluation_models",
            sa.Column(
                "concurrency_limit",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )

    # A Model may already be referenced by several Harnesses. Preserve the
    # longest configured timeout while choosing the strictest concurrency cap,
    # so migration neither shortens valid work nor raises provider pressure.
    bind.execute(
        sa.text(
            """
            UPDATE evaluation_models
            SET timeout_seconds = COALESCE(
                    (
                        SELECT MAX(eh.timeout_seconds)
                        FROM evaluation_targets AS et
                        JOIN evaluation_harnesses AS eh ON eh.id = et.harness_id
                        WHERE et.model_id = evaluation_models.id
                    ),
                    timeout_seconds
                ),
                concurrency_limit = COALESCE(
                    (
                        SELECT MIN(eh.concurrency_limit)
                        FROM evaluation_targets AS et
                        JOIN evaluation_harnesses AS eh ON eh.id = et.harness_id
                        WHERE et.model_id = evaluation_models.id
                    ),
                    concurrency_limit
                )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_models")
    }
    if "concurrency_limit" in columns:
        op.drop_column("evaluation_models", "concurrency_limit")
    if "timeout_seconds" in columns:
        op.drop_column("evaluation_models", "timeout_seconds")
