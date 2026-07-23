"""remove domain and tags from case revisions

Revision ID: 0009_p14_remove_domain_tags
Revises: 0008_p13_category_case_keys
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from sqlalchemy import inspect

from alembic import op

revision: str = "0009_p14_remove_domain_tags"
down_revision: str | None = "0008_p13_category_case_keys"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("case_revisions")
    }
    with op.batch_alter_table("case_revisions") as batch:
        if "domain" in columns:
            batch.drop_column("domain")
        if "tags_json" in columns:
            batch.drop_column("tags_json")


def downgrade() -> None:
    with op.batch_alter_table("case_revisions") as batch:
        batch.add_column(op.Column("domain", op.String(100), server_default="generic-analysis"))
        batch.add_column(op.Column("tags_json", op.Text, server_default="[]"))
