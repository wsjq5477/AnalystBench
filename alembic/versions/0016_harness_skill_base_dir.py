"""add Harness-local Skill configuration directory

Revision ID: 0016_harness_skill_base_dir
Revises: 0015_normalize_claude_runner
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0016_harness_skill_base_dir"
down_revision: str | None = "0015_normalize_claude_runner"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "evaluation_harnesses" not in inspect(bind).get_table_names():
        return
    columns = {
        column["name"]
        for column in inspect(bind).get_columns("evaluation_harnesses")
    }
    if "skill_base_dir" not in columns:
        op.add_column(
            "evaluation_harnesses",
            sa.Column("skill_base_dir", sa.String(1024), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "evaluation_harnesses" not in inspect(bind).get_table_names():
        return
    columns = {
        column["name"]
        for column in inspect(bind).get_columns("evaluation_harnesses")
    }
    if "skill_base_dir" in columns:
        op.drop_column("evaluation_harnesses", "skill_base_dir")
