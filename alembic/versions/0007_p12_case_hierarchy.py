"""p12 test set and case category hierarchy

Revision ID: 0007_p12_case_hierarchy
Revises: 0006_p11_case_library_batches
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0007_p12_case_hierarchy"
down_revision: str | None = "0006_p11_case_library_batches"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    dataset_columns = {item["name"] for item in inspector.get_columns("datasets")}
    if "dataset_key" not in dataset_columns:
        op.add_column("datasets", sa.Column("dataset_key", sa.String(length=255), nullable=True))
        op.execute(sa.text("UPDATE datasets SET dataset_key = name WHERE dataset_key IS NULL"))
        with op.batch_alter_table("datasets") as batch:
            batch.alter_column("dataset_key", existing_type=sa.String(length=255), nullable=False)
        op.create_index("ix_datasets_dataset_key", "datasets", ["dataset_key"], unique=True)

    tables = inspect(bind).get_table_names()
    if "case_categories" not in tables:
        op.create_table(
            "case_categories",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "dataset_id",
                sa.String(length=36),
                sa.ForeignKey("datasets.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("category_key", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "archived_at", sa.DateTime(timezone=True), nullable=True
            ),
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
            sa.UniqueConstraint(
                "dataset_id", "category_key", name="uq_case_categories_dataset_key"
            ),
        )
        op.create_index("ix_case_categories_dataset_id", "case_categories", ["dataset_id"])

    case_columns = {item["name"] for item in inspect(bind).get_columns("cases")}
    if "category_id" not in case_columns or "source_filename" not in case_columns:
        with op.batch_alter_table("cases") as batch:
            if "category_id" not in case_columns:
                batch.add_column(sa.Column("category_id", sa.String(length=36), nullable=True))
                batch.create_foreign_key(
                    "fk_cases_category_id_case_categories",
                    "case_categories",
                    ["category_id"],
                    ["id"],
                    ondelete="RESTRICT",
                )
                batch.create_index("ix_cases_category_id", ["category_id"])
            if "source_filename" not in case_columns:
                batch.add_column(sa.Column("source_filename", sa.String(length=512), nullable=True))

    if "case_traces" not in inspect(bind).get_table_names():
        op.create_table(
            "case_traces",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "case_revision_id",
                sa.String(length=36),
                sa.ForeignKey("case_revisions.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("trace_key", sa.String(length=255), nullable=False),
            sa.Column("filename", sa.String(length=512), nullable=False),
            sa.Column("media_type", sa.String(length=255), nullable=False),
            sa.Column(
                "content_hash",
                sa.String(length=71),
                sa.ForeignKey("content_blobs.content_hash"),
                nullable=False,
            ),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "case_revision_id", "trace_key", name="uq_case_traces_revision_key"
            ),
        )
        op.create_index("ix_case_traces_case_revision_id", "case_traces", ["case_revision_id"])

    draft_columns = {item["name"] for item in inspect(bind).get_columns("case_drafts")}
    for column in (
        sa.Column("source_filename", sa.String(length=512), nullable=True),
        sa.Column("dataset_key", sa.String(length=255), nullable=True),
        sa.Column("category_key", sa.String(length=255), nullable=True),
    ):
        if column.name not in draft_columns:
            op.add_column("case_drafts", column)
    draft_indexes = {item["name"] for item in inspect(bind).get_indexes("case_drafts")}
    if "ix_case_drafts_dataset_key" not in draft_indexes:
        op.create_index("ix_case_drafts_dataset_key", "case_drafts", ["dataset_key"])
    if "ix_case_drafts_category_key" not in draft_indexes:
        op.create_index("ix_case_drafts_category_key", "case_drafts", ["category_key"])


def downgrade() -> None:
    op.drop_index("ix_case_drafts_category_key", table_name="case_drafts")
    op.drop_index("ix_case_drafts_dataset_key", table_name="case_drafts")
    op.drop_column("case_drafts", "category_key")
    op.drop_column("case_drafts", "dataset_key")
    op.drop_column("case_drafts", "source_filename")
    op.drop_index("ix_case_traces_case_revision_id", table_name="case_traces")
    op.drop_table("case_traces")
    with op.batch_alter_table("cases") as batch:
        batch.drop_index("ix_cases_category_id")
        batch.drop_constraint("fk_cases_category_id_case_categories", type_="foreignkey")
        batch.drop_column("source_filename")
        batch.drop_column("category_id")
    op.drop_index("ix_case_categories_dataset_id", table_name="case_categories")
    op.drop_table("case_categories")
    op.drop_index("ix_datasets_dataset_key", table_name="datasets")
    op.drop_column("datasets", "dataset_key")
