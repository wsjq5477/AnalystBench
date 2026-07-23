"""p2 versioned catalog

Revision ID: 0002_p2_catalog
Revises: 0001_p1_baseline
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_p2_catalog"
down_revision: str | None = "0001_p1_baseline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


P2_TABLES = [
    "eval_spec_versions",
    "eval_spec_drafts",
    "scoring_policy_versions",
    "model_profiles",
    "prompt_versions",
    "candidate_reports",
    "agent_case_runs",
    "candidate_generation_runs",
    "execution_profiles",
    "candidate_versions",
    "candidates",
    "dataset_versions",
    "case_revisions",
    "cases",
    "datasets",
    "content_blobs",
]


def upgrade() -> None:
    # These models are intentionally the source of truth for P2's first schema.
    # P1's jobs table already exists, so create_all only adds the versioned catalog.
    import analystbench.db.models  # noqa: F401
    from analystbench.db.base import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    for table_name in P2_TABLES:
        op.drop_table(table_name)
