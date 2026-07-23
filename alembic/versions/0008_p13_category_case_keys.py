"""allow category-scoped generated Case keys

Revision ID: 0008_p13_category_case_keys
Revises: 0007_p12_case_hierarchy
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect


revision: str = "0008_p13_category_case_keys"
down_revision: str | None = "0007_p12_case_hierarchy"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    constraints = {item["name"] for item in inspect(op.get_bind()).get_unique_constraints("cases")}
    with op.batch_alter_table("cases") as batch:
        if "uq_cases_dataset_key" in constraints:
            batch.drop_constraint("uq_cases_dataset_key", type_="unique")
        if "uq_cases_dataset_category_key" not in constraints:
            batch.create_unique_constraint(
                "uq_cases_dataset_category_key", ["dataset_id", "category_id", "case_key"]
            )


def downgrade() -> None:
    constraints = {item["name"] for item in inspect(op.get_bind()).get_unique_constraints("cases")}
    with op.batch_alter_table("cases") as batch:
        if "uq_cases_dataset_category_key" in constraints:
            batch.drop_constraint("uq_cases_dataset_category_key", type_="unique")
        if "uq_cases_dataset_key" not in constraints:
            batch.create_unique_constraint("uq_cases_dataset_key", ["dataset_id", "case_key"])
