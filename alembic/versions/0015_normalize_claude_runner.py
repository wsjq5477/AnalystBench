"""normalize persisted runner names to claude

Revision ID: 0015_normalize_claude_runner
Revises: 0014_skill_optimization
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0015_normalize_claude_runner"
down_revision: str | None = "0014_skill_optimization"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _legacy_runner_names() -> tuple[str, ...]:
    return (
        "-".join(("claude", "code")),
        " ".join(("claude", "code")),
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    runner_columns = (
        ("execution_profiles", "runner"),
        ("evaluation_schedules", "judge_runner"),
    )
    for table_name, column_name in runner_columns:
        if table_name not in tables:
            continue
        columns = {
            column["name"] for column in inspect(bind).get_columns(table_name)
        }
        if column_name not in columns:
            continue
        statement = sa.text(
            f'UPDATE "{table_name}" '
            f'SET "{column_name}" = :canonical '
            f'WHERE lower("{column_name}") = :legacy'
        )
        for legacy_name in _legacy_runner_names():
            bind.execute(
                statement,
                {"canonical": "claude", "legacy": legacy_name},
            )


def downgrade() -> None:
    # Canonical runner names are intentionally not converted back.
    pass
