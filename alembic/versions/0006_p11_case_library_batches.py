"""p11 case library and evaluation batches

Revision ID: 0006_p11_case_library_batches
Revises: 0005_p10_evaluation_sessions
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0006_p11_case_library_batches"
down_revision: str | None = "0005_p10_evaluation_sessions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    tables = inspect(op.get_bind()).get_table_names()
    if "case_drafts" not in tables:
        op.create_table(
            "case_drafts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("case_key", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("original_json", sa.Text(), nullable=False),
            sa.Column("working_json", sa.Text(), nullable=False),
            sa.Column("questions_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("answers_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("resources_json", sa.Text(), nullable=False, server_default="{}"),
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
        op.create_index("ix_case_drafts_case_key", "case_drafts", ["case_key"])
        op.create_index("ix_case_drafts_status", "case_drafts", ["status"])
    if "report_drafts" not in tables:
        op.create_table(
            "report_drafts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("candidate_name", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("issues_json", sa.Text(), nullable=False, server_default="[]"),
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
        op.create_index("ix_report_drafts_candidate_name", "report_drafts", ["candidate_name"])
        op.create_index("ix_report_drafts_status", "report_drafts", ["status"])
    if "evaluation_batches" not in tables:
        op.create_table(
            "evaluation_batches",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "case_draft_id",
                sa.String(length=36),
                sa.ForeignKey("case_drafts.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("report_draft_ids_json", sa.Text(), nullable=False),
            sa.Column("resources_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("comparison_json", sa.Text(), nullable=False, server_default="[]"),
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
            "ix_evaluation_batches_case_draft_id",
            "evaluation_batches",
            ["case_draft_id"],
        )
        op.create_index("ix_evaluation_batches_status", "evaluation_batches", ["status"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_batches_status", table_name="evaluation_batches")
    op.drop_index("ix_evaluation_batches_case_draft_id", table_name="evaluation_batches")
    op.drop_table("evaluation_batches")
    op.drop_index("ix_report_drafts_status", table_name="report_drafts")
    op.drop_index("ix_report_drafts_candidate_name", table_name="report_drafts")
    op.drop_table("report_drafts")
    op.drop_index("ix_case_drafts_status", table_name="case_drafts")
    op.drop_index("ix_case_drafts_case_key", table_name="case_drafts")
    op.drop_table("case_drafts")
