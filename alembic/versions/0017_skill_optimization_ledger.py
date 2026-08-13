"""add immutable Skill optimization ledger fields

Revision ID: 0017_skill_optimization_ledger
Revises: 0016_harness_skill_base_dir
Create Date: 2026-08-12
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0017_skill_optimization_ledger"
down_revision: str | None = "0016_harness_skill_base_dir"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "skill_binding_history" not in tables:
        op.create_table(
            "skill_binding_history",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "binding_id",
                sa.String(36),
                sa.ForeignKey("skill_target_bindings.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "skill_id",
                sa.String(36),
                sa.ForeignKey("skills.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "evaluation_target_id",
                sa.String(36),
                sa.ForeignKey("evaluation_targets.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "previous_version_id",
                sa.String(36),
                sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column(
                "active_version_id",
                sa.String(36),
                sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("active_level", sa.String(32), nullable=False),
            sa.Column("lock_version", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "binding_id", "lock_version", name="uq_skill_binding_history_lock"
            ),
        )
        for column in (
            "binding_id",
            "skill_id",
            "evaluation_target_id",
            "previous_version_id",
            "active_version_id",
            "action",
            "created_at",
        ):
            op.create_index(
                f"ix_skill_binding_history_{column}",
                "skill_binding_history",
                [column],
            )
    if {"skill_target_bindings", "skill_binding_history"}.issubset(
        set(inspect(bind).get_table_names())
    ):
        existing = {
            (row["binding_id"], row["lock_version"])
            for row in bind.execute(
                sa.text("SELECT binding_id, lock_version FROM skill_binding_history")
            ).mappings()
        }
        bindings = bind.execute(
            sa.text(
                "SELECT id, skill_id, evaluation_target_id, active_version_id, "
                "active_level, lock_version FROM skill_target_bindings"
            )
        ).mappings()
        history = sa.table(
            "skill_binding_history",
            sa.column("id", sa.String),
            sa.column("binding_id", sa.String),
            sa.column("skill_id", sa.String),
            sa.column("evaluation_target_id", sa.String),
            sa.column("previous_version_id", sa.String),
            sa.column("active_version_id", sa.String),
            sa.column("active_level", sa.String),
            sa.column("lock_version", sa.Integer),
            sa.column("action", sa.String),
            sa.column("metadata_json", sa.Text),
        )
        for binding in bindings:
            key = (binding["id"], binding["lock_version"])
            if key in existing:
                continue
            bind.execute(
                history.insert().values(
                    id=str(uuid4()),
                    binding_id=binding["id"],
                    skill_id=binding["skill_id"],
                    evaluation_target_id=binding["evaluation_target_id"],
                    previous_version_id=None,
                    active_version_id=binding["active_version_id"],
                    active_level=binding["active_level"],
                    lock_version=binding["lock_version"],
                    action="migration_baseline",
                    metadata_json='{"migration":"0017_skill_optimization_ledger"}',
                )
            )
    if "optimization_epochs" in tables:
        columns = _columns("optimization_epochs")
        if "summary_json" not in columns:
            op.add_column(
                "optimization_epochs",
                sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
            )
    if "candidate_mutations" in tables:
        columns = _columns("candidate_mutations")
        if "intent_json" not in columns:
            op.add_column(
                "candidate_mutations",
                sa.Column("intent_json", sa.Text(), nullable=False, server_default="{}"),
            )
        if "change_stats_json" not in columns:
            op.add_column(
                "candidate_mutations",
                sa.Column(
                    "change_stats_json", sa.Text(), nullable=False, server_default="{}"
                ),
            )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "candidate_mutations" in tables:
        columns = _columns("candidate_mutations")
        if "change_stats_json" in columns:
            op.drop_column("candidate_mutations", "change_stats_json")
        if "intent_json" in columns:
            op.drop_column("candidate_mutations", "intent_json")
    if "optimization_epochs" in tables and "summary_json" in _columns(
        "optimization_epochs"
    ):
        op.drop_column("optimization_epochs", "summary_json")
    if "skill_binding_history" in tables:
        op.drop_table("skill_binding_history")
