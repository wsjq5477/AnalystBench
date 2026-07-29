"""p19 harness, model, and target catalog

Revision ID: 0013_p19_harness_model_targets
Revises: 0012_p18_method_run_timing
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0013_p19_harness_model_targets"
down_revision: str | None = "0012_p18_method_run_timing"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _timestamp_columns() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "evaluation_harnesses" not in tables:
        op.create_table(
            "evaluation_harnesses",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("harness_key", sa.String(100), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("family", sa.String(100), nullable=True),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("model_policy", sa.String(16), nullable=False),
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
            *_timestamp_columns(),
            sa.UniqueConstraint(
                "harness_key", "version_number", name="uq_evaluation_harnesses_key_version"
            ),
        )
        op.create_index("ix_evaluation_harnesses_harness_key", "evaluation_harnesses", ["harness_key"])
        op.create_index("ix_evaluation_harnesses_family", "evaluation_harnesses", ["family"])
        op.create_index("ix_evaluation_harnesses_status", "evaluation_harnesses", ["status"])

    if "evaluation_models" not in tables:
        op.create_table(
            "evaluation_models",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("model_key", sa.String(100), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("argument", sa.String(255), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="frozen"),
            sa.Column("content_hash", sa.String(71), nullable=False, unique=True),
            *_timestamp_columns(),
            sa.UniqueConstraint(
                "model_key", "version_number", name="uq_evaluation_models_key_version"
            ),
        )
        op.create_index("ix_evaluation_models_model_key", "evaluation_models", ["model_key"])
        op.create_index("ix_evaluation_models_status", "evaluation_models", ["status"])

    if "evaluation_targets" not in tables:
        op.create_table(
            "evaluation_targets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("target_key", sa.String(255), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column(
                "harness_id",
                sa.String(36),
                sa.ForeignKey("evaluation_harnesses.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "model_id",
                sa.String(36),
                sa.ForeignKey("evaluation_models.id", ondelete="RESTRICT"),
                nullable=True,
            ),
            sa.Column("model_argument", sa.String(255), nullable=True),
            sa.Column("concurrency_limit", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("content_hash", sa.String(71), nullable=False, unique=True),
            sa.Column("last_probe_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column(
                "materialized_method_id",
                sa.String(36),
                sa.ForeignKey("evaluation_methods.id", ondelete="RESTRICT"),
                nullable=True,
                unique=True,
            ),
            *_timestamp_columns(),
            sa.UniqueConstraint(
                "target_key", "version_number", name="uq_evaluation_targets_key_version"
            ),
        )
        op.create_index("ix_evaluation_targets_target_key", "evaluation_targets", ["target_key"])
        op.create_index("ix_evaluation_targets_harness_id", "evaluation_targets", ["harness_id"])
        op.create_index("ix_evaluation_targets_model_id", "evaluation_targets", ["model_id"])
        op.create_index("ix_evaluation_targets_status", "evaluation_targets", ["status"])
        op.create_index(
            "ix_evaluation_targets_materialized_method_id",
            "evaluation_targets",
            ["materialized_method_id"],
        )

    schedule_columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_schedules")
    }
    if "target_ids_json" not in schedule_columns:
        op.add_column(
            "evaluation_schedules",
            sa.Column("target_ids_json", sa.Text(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    schedule_columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_schedules")
    }
    if "target_ids_json" in schedule_columns:
        op.drop_column("evaluation_schedules", "target_ids_json")
    op.drop_table("evaluation_targets")
    op.drop_table("evaluation_models")
    op.drop_table("evaluation_harnesses")
