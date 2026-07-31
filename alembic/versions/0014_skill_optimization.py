"""skill optimization registry and experiment state

Revision ID: 0014_skill_optimization
Revises: 0013_p19_harness_model_targets
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision: str = "0014_skill_optimization"
down_revision: str | None = "0013_p19_harness_model_targets"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _timestamps() -> list[sa.Column[object]]:
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


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    # The historical 0002 migration calls current Base.metadata.create_all().
    # A database replayed from scratch can therefore already contain this
    # revision's tables. Deployed databases upgraded from an older release do
    # not, and continue through the explicit DDL below.
    if {"skills", "skill_package_versions"}.issubset(tables):
        submission_columns = {
            column["name"]
            for column in inspect(bind).get_columns("evaluation_submissions")
        }
        if "purpose" not in submission_columns:
            op.add_column(
                "evaluation_submissions",
                sa.Column(
                    "purpose",
                    sa.String(32),
                    nullable=False,
                    server_default="normal",
                ),
            )
            op.create_index(
                "ix_evaluation_submissions_purpose",
                "evaluation_submissions",
                ["purpose"],
            )
        if "optimization_context_json" not in submission_columns:
            op.add_column(
                "evaluation_submissions",
                sa.Column(
                    "optimization_context_json",
                    sa.Text(),
                    nullable=False,
                    server_default="{}",
                ),
            )
        return

    if "skills" not in tables:
        op.create_table(
            "skills",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("skill_key", sa.String(128), nullable=False, unique=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_path", sa.String(2048), nullable=False),
            sa.Column("invoke_as", sa.String(128), nullable=False),
            sa.Column("harness_key", sa.String(100), nullable=False),
            sa.Column("install_relative_path", sa.String(1024), nullable=False),
            sa.Column("publish_mode", sa.String(32), nullable=False, server_default="managed"),
            sa.Column("editable_paths_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("limits_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            *_timestamps(),
        )
        op.create_index("ix_skills_skill_key", "skills", ["skill_key"])
        op.create_index("ix_skills_harness_key", "skills", ["harness_key"])
        op.create_index("ix_skills_archived_at", "skills", ["archived_at"])

    op.create_table(
        "skill_package_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "parent_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("package_hash", sa.String(71), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=False),
        sa.Column("git_tree", sa.String(64), nullable=False),
        sa.Column("git_object_format", sa.String(16), nullable=False, server_default="sha1"),
        sa.Column("manifest_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column("created_by", sa.String(128), nullable=True),
        _created_at(),
        sa.UniqueConstraint(
            "skill_id", "version_number", name="uq_skill_versions_number"
        ),
        sa.UniqueConstraint("skill_id", "package_hash", name="uq_skill_versions_hash"),
        sa.UniqueConstraint(
            "skill_id", "git_commit", name="uq_skill_versions_git_commit"
        ),
    )
    op.create_index(
        "ix_skill_package_versions_skill_id", "skill_package_versions", ["skill_id"]
    )
    op.create_index(
        "ix_skill_package_versions_parent_version_id",
        "skill_package_versions",
        ["parent_version_id"],
    )
    op.create_index(
        "ix_skill_package_versions_package_hash",
        "skill_package_versions",
        ["package_hash"],
    )
    op.create_index(
        "ix_skill_package_versions_status", "skill_package_versions", ["status"]
    )

    op.create_table(
        "skill_target_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
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
            "active_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("active_level", sa.String(32), nullable=False, server_default="provisional"),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint(
            "skill_id", "evaluation_target_id", name="uq_skill_target_binding"
        ),
    )
    op.create_index(
        "ix_skill_target_bindings_skill_id", "skill_target_bindings", ["skill_id"]
    )
    op.create_index(
        "ix_skill_target_bindings_evaluation_target_id",
        "skill_target_bindings",
        ["evaluation_target_id"],
    )
    op.create_index(
        "ix_skill_target_bindings_active_version_id",
        "skill_target_bindings",
        ["active_version_id"],
    )

    op.create_table(
        "evaluation_variants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "evaluation_target_id",
            sa.String(36),
            sa.ForeignKey("evaluation_targets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "skill_package_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "materialized_method_id",
            sa.String(36),
            sa.ForeignKey("evaluation_methods.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("install_relative_path", sa.String(1024), nullable=False),
        sa.Column("invoke_as", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="frozen"),
        *_timestamps(),
        sa.UniqueConstraint(
            "evaluation_target_id",
            "skill_package_version_id",
            name="uq_evaluation_variant_target_skill",
        ),
    )
    op.create_index(
        "ix_evaluation_variants_evaluation_target_id",
        "evaluation_variants",
        ["evaluation_target_id"],
    )
    op.create_index(
        "ix_evaluation_variants_skill_package_version_id",
        "evaluation_variants",
        ["skill_package_version_id"],
    )
    op.create_index(
        "ix_evaluation_variants_materialized_method_id",
        "evaluation_variants",
        ["materialized_method_id"],
    )
    op.create_index(
        "ix_evaluation_variants_status", "evaluation_variants", ["status"]
    )

    op.create_table(
        "optimizer_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_key", sa.String(128), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "execution_profile_id",
            sa.String(36),
            sa.ForeignKey("execution_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("prompt_bundle_hash", sa.String(71), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(71), nullable=False, unique=True),
        _created_at(),
        sa.UniqueConstraint(
            "policy_key", "version_number", name="uq_optimizer_policy_version"
        ),
    )
    op.create_index(
        "ix_optimizer_policy_versions_policy_key",
        "optimizer_policy_versions",
        ["policy_key"],
    )
    op.create_index(
        "ix_optimizer_policy_versions_execution_profile_id",
        "optimizer_policy_versions",
        ["execution_profile_id"],
    )

    op.create_table(
        "verifier_bundle_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bundle_key", sa.String(128), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("static_policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("gate_policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("judge_config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(71), nullable=False, unique=True),
        _created_at(),
        sa.UniqueConstraint(
            "bundle_key", "version_number", name="uq_verifier_bundle_version"
        ),
    )
    op.create_index(
        "ix_verifier_bundle_versions_bundle_key",
        "verifier_bundle_versions",
        ["bundle_key"],
    )

    op.create_table(
        "optimization_data_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset_key", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("train_cases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("validation_cases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("hidden_test_cases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "prospective_holdout_cases_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("case_input_hashes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("eval_spec_hashes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(71), nullable=False, unique=True),
        _created_at(),
    )
    op.create_index(
        "ix_optimization_data_snapshots_dataset_key",
        "optimization_data_snapshots",
        ["dataset_key"],
    )
    op.create_index(
        "ix_optimization_data_snapshots_mode",
        "optimization_data_snapshots",
        ["mode"],
    )

    op.create_table(
        "optimization_experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "base_skill_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_target_id",
            sa.String(36),
            sa.ForeignKey("evaluation_targets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "data_snapshot_id",
            sa.String(36),
            sa.ForeignKey("optimization_data_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "optimizer_policy_version_id",
            sa.String(36),
            sa.ForeignKey("optimizer_policy_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "verifier_bundle_version_id",
            sa.String(36),
            sa.ForeignKey("verifier_bundle_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("current_epoch_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_epochs", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("stop_reason", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        *_timestamps(),
    )
    for column in (
        "skill_id",
        "base_skill_version_id",
        "evaluation_target_id",
        "data_snapshot_id",
        "optimizer_policy_version_id",
        "verifier_bundle_version_id",
        "status",
    ):
        op.create_index(
            f"ix_optimization_experiments_{column}",
            "optimization_experiments",
            [column],
        )

    op.create_table(
        "optimization_epochs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("optimization_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("epoch_number", sa.Integer(), nullable=False),
        sa.Column(
            "parent_skill_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("evidence_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "best_candidate_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "experiment_id", "epoch_number", name="uq_optimization_epoch"
        ),
    )
    op.create_index(
        "ix_optimization_epochs_experiment_id",
        "optimization_epochs",
        ["experiment_id"],
    )
    op.create_index(
        "ix_optimization_epochs_parent_skill_version_id",
        "optimization_epochs",
        ["parent_skill_version_id"],
    )
    op.create_index(
        "ix_optimization_epochs_status", "optimization_epochs", ["status"]
    )

    op.create_table(
        "candidate_mutations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "epoch_id",
            sa.String(36),
            sa.ForeignKey("optimization_epochs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_skill_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_skill_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("candidate_type", sa.String(32), nullable=False),
        sa.Column("structured_patch_json", sa.Text(), nullable=False),
        sa.Column("patch_hash", sa.String(71), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "intended_failure_clusters_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("rejection_code", sa.String(128), nullable=True),
        sa.Column("rejection_detail_json", sa.Text(), nullable=False, server_default="{}"),
        *_timestamps(),
    )
    for column in (
        "epoch_id",
        "parent_skill_version_id",
        "candidate_skill_version_id",
        "patch_hash",
        "status",
    ):
        op.create_index(
            f"ix_candidate_mutations_{column}", "candidate_mutations", [column]
        )

    op.create_table(
        "optimization_signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("optimization_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "epoch_id",
            sa.String(36),
            sa.ForeignKey("optimization_epochs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("case_path", sa.String(1024), nullable=False),
        sa.Column(
            "evaluation_method_run_id",
            sa.String(36),
            sa.ForeignKey("evaluation_submission_method_runs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("run_role", sa.String(32), nullable=False),
        sa.Column("case_family", sa.String(128), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("signal_json", sa.Text(), nullable=False),
        sa.Column("signal_hash", sa.String(71), nullable=False),
        _created_at(),
    )
    for column in (
        "experiment_id",
        "epoch_id",
        "case_path",
        "case_family",
        "signal_hash",
    ):
        op.create_index(
            f"ix_optimization_signals_{column}", "optimization_signals", [column]
        )

    op.create_table(
        "optimization_run_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("optimization_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "epoch_id",
            sa.String(36),
            sa.ForeignKey("optimization_epochs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "candidate_mutation_id",
            sa.String(36),
            sa.ForeignKey("candidate_mutations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("split_role", sa.String(32), nullable=False),
        sa.Column("arm", sa.String(32), nullable=False),
        sa.Column(
            "skill_package_version_id",
            sa.String(36),
            sa.ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("repeat_index", sa.Integer(), nullable=False),
        sa.Column(
            "evaluation_submission_id",
            sa.String(36),
            sa.ForeignKey("evaluation_submissions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("run_config_hash", sa.String(71), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "experiment_id",
            "epoch_id",
            "candidate_mutation_id",
            "split_role",
            "arm",
            "repeat_index",
            name="uq_optimization_run_group",
        ),
    )
    for column in (
        "experiment_id",
        "epoch_id",
        "candidate_mutation_id",
        "skill_package_version_id",
        "status",
    ):
        op.create_index(
            f"ix_optimization_run_groups_{column}",
            "optimization_run_groups",
            [column],
        )

    op.create_table(
        "candidate_comparisons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("optimization_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "epoch_id",
            sa.String(36),
            sa.ForeignKey("optimization_epochs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_mutation_id",
            sa.String(36),
            sa.ForeignKey("candidate_mutations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("comparison_type", sa.String(32), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("gate_result_json", sa.Text(), nullable=False, server_default="{}"),
        _created_at(),
    )
    for column in ("experiment_id", "epoch_id", "candidate_mutation_id"):
        op.create_index(
            f"ix_candidate_comparisons_{column}", "candidate_comparisons", [column]
        )

    op.create_table(
        "decision_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("optimization_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "epoch_id",
            sa.String(36),
            sa.ForeignKey("optimization_epochs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "candidate_mutation_id",
            sa.String(36),
            sa.ForeignKey("candidate_mutations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("diagnosis_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("revision_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("outcome_json", sa.Text(), nullable=False, server_default="{}"),
        _created_at(),
    )
    for column in ("experiment_id", "epoch_id", "candidate_mutation_id"):
        op.create_index(
            f"ix_decision_records_{column}", "decision_records", [column]
        )

    op.create_table(
        "optimization_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(36),
            sa.ForeignKey("optimization_experiments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "epoch_id",
            sa.String(36),
            sa.ForeignKey("optimization_epochs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "candidate_mutation_id",
            sa.String(36),
            sa.ForeignKey("candidate_mutations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        _created_at(),
    )
    for column in ("experiment_id", "epoch_id", "event_type", "created_at"):
        op.create_index(
            f"ix_optimization_events_{column}", "optimization_events", [column]
        )

    submission_columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_submissions")
    }
    if "purpose" not in submission_columns:
        op.add_column(
            "evaluation_submissions",
            sa.Column(
                "purpose", sa.String(32), nullable=False, server_default="normal"
            ),
        )
        op.create_index(
            "ix_evaluation_submissions_purpose",
            "evaluation_submissions",
            ["purpose"],
        )
    if "optimization_context_json" not in submission_columns:
        op.add_column(
            "evaluation_submissions",
            sa.Column(
                "optimization_context_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    submission_columns = {
        column["name"] for column in inspect(bind).get_columns("evaluation_submissions")
    }
    if "optimization_context_json" in submission_columns:
        op.drop_column("evaluation_submissions", "optimization_context_json")
    if "purpose" in submission_columns:
        op.drop_index(
            "ix_evaluation_submissions_purpose", table_name="evaluation_submissions"
        )
        op.drop_column("evaluation_submissions", "purpose")

    for table in (
        "optimization_events",
        "decision_records",
        "candidate_comparisons",
        "optimization_run_groups",
        "optimization_signals",
        "candidate_mutations",
        "optimization_epochs",
        "optimization_experiments",
        "optimization_data_snapshots",
        "verifier_bundle_versions",
        "optimizer_policy_versions",
        "evaluation_variants",
        "skill_target_bindings",
        "skill_package_versions",
        "skills",
    ):
        op.drop_table(table)
