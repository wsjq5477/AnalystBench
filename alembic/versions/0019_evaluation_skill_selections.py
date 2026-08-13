"""persist explicit Harness x Model x Skill schedule selections

Revision ID: 0019_evaluation_skill_selections
Revises: 0018_evaluation_submission_idempotency
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0019_evaluation_skill_selections"
down_revision: str | None = "0018_evaluation_submission_idempotency"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        item["name"]
        for item in inspect(op.get_bind()).get_columns("evaluation_schedules")
    }
    if "target_selections_json" not in columns:
        with op.batch_alter_table("evaluation_schedules") as batch:
            batch.add_column(
                sa.Column(
                    "target_selections_json",
                    sa.Text(),
                    nullable=False,
                    server_default="[]",
                )
            )


def downgrade() -> None:
    columns = {
        item["name"]
        for item in inspect(op.get_bind()).get_columns("evaluation_schedules")
    }
    if "target_selections_json" in columns:
        with op.batch_alter_table("evaluation_schedules") as batch:
            batch.drop_column("target_selections_json")
