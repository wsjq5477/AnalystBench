"""Persistence models for local, versioned benchmark artifacts."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from analystbench.db.base import Base


class TimestampedModel:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Job(TimestampedModel, Base):
    """Persistent job envelope used by background execution stages."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ContentBlob(Base):
    __tablename__ = "content_blobs"

    content_hash: Mapped[str] = mapped_column(String(71), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(512), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dataset(TimestampedModel, Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseCategory(TimestampedModel, Base):
    """A stable problem category inside one test set."""

    __tablename__ = "case_categories"
    __table_args__ = (
        UniqueConstraint("dataset_id", "category_key", name="uq_case_categories_dataset_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), index=True
    )
    category_key: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Case(TimestampedModel, Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "category_id", "case_key", name="uq_cases_dataset_category_key"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), index=True
    )
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("case_categories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    case_key: Mapped[str] = mapped_column(String(255))
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseRevision(Base):
    __tablename__ = "case_revisions"
    __table_args__ = (
        UniqueConstraint("case_id", "revision_number", name="uq_case_revisions_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="RESTRICT"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    problem_content_hash: Mapped[str] = mapped_column(ForeignKey("content_blobs.content_hash"))
    reference_answer_content_hash: Mapped[str] = mapped_column(
        ForeignKey("content_blobs.content_hash")
    )
    attachments_json: Mapped[str] = mapped_column(Text, default="[]")
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CaseTrace(Base):
    """One source log, snapshot, stack, or other trace attached to a Case revision."""

    __tablename__ = "case_traces"
    __table_args__ = (
        UniqueConstraint("case_revision_id", "trace_key", name="uq_case_traces_revision_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_revision_id: Mapped[str] = mapped_column(
        ForeignKey("case_revisions.id", ondelete="RESTRICT"), index=True
    )
    trace_key: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(255), default="text/plain")
    content_hash: Mapped[str] = mapped_column(ForeignKey("content_blobs.content_hash"))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dataset_versions_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    case_revision_ids_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Candidate(TimestampedModel, Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CandidateVersion(Base):
    __tablename__ = "candidate_versions"
    __table_args__ = (
        UniqueConstraint("candidate_id", "version_number", name="uq_candidate_versions_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExecutionProfile(Base):
    __tablename__ = "execution_profiles"
    __table_args__ = (
        UniqueConstraint("name", "version_number", name="uq_execution_profiles_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    runner: Mapped[str] = mapped_column(String(32))
    configuration_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CandidateGenerationRun(TimestampedModel, Base):
    __tablename__ = "candidate_generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )
    candidate_version_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_versions.id", ondelete="RESTRICT")
    )
    execution_profile_id: Mapped[str] = mapped_column(
        ForeignKey("execution_profiles.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    manifest_json: Mapped[str] = mapped_column(Text)


class AgentCaseRun(TimestampedModel, Base):
    __tablename__ = "agent_case_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_run_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_generation_runs.id", ondelete="RESTRICT"), index=True
    )
    case_revision_id: Mapped[str] = mapped_column(
        ForeignKey("case_revisions.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    artifact_json: Mapped[str] = mapped_column(Text, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class CandidateReport(Base):
    __tablename__ = "candidate_reports"
    __table_args__ = (
        UniqueConstraint(
            "candidate_version_id", "case_revision_id", name="uq_candidate_reports_version_case"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_version_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_versions.id", ondelete="RESTRICT"), index=True
    )
    case_revision_id: Mapped[str] = mapped_column(
        ForeignKey("case_revisions.id", ondelete="RESTRICT"), index=True
    )
    source: Mapped[str] = mapped_column(String(32))
    report_content_hash: Mapped[str] = mapped_column(ForeignKey("content_blobs.content_hash"))
    agent_case_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_case_runs.id", ondelete="RESTRICT"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("purpose", "version_number", name="uq_prompt_versions_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(100))
    version_number: Mapped[int] = mapped_column(Integer)
    template_content_hash: Mapped[str] = mapped_column(ForeignKey("content_blobs.content_hash"))
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelProfile(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (UniqueConstraint("name", "version_number", name="uq_model_profiles_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version_number: Mapped[int] = mapped_column(Integer)
    adapter_type: Mapped[str] = mapped_column(String(100))
    configuration_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScoringPolicyVersion(Base):
    __tablename__ = "scoring_policy_versions"
    __table_args__ = (
        UniqueConstraint("name", "version_number", name="uq_scoring_policy_versions_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version_number: Mapped[int] = mapped_column(Integer)
    policy_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalSpecDraft(TimestampedModel, Base):
    __tablename__ = "eval_spec_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_revision_id: Mapped[str] = mapped_column(
        ForeignKey("case_revisions.id", ondelete="RESTRICT"), index=True
    )
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")


class EvalSpecVersion(Base):
    __tablename__ = "eval_spec_versions"
    __table_args__ = (
        UniqueConstraint("case_revision_id", "version_number", name="uq_eval_spec_versions_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_revision_id: Mapped[str] = mapped_column(
        ForeignKey("case_revisions.id", ondelete="RESTRICT"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BenchmarkRun(TimestampedModel, Base):
    """Immutable benchmark manifest with a cooperative cancellation flag."""

    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    candidate_version_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_versions.id"), index=True
    )
    scoring_policy_version_id: Mapped[str] = mapped_column(ForeignKey("scoring_policy_versions.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    cancellation_requested: Mapped[bool] = mapped_column(default=False)
    manifest_json: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")


class BenchmarkCaseRun(TimestampedModel, Base):
    """One immutable-input evaluation attempt chain per BenchmarkRun and case."""

    __tablename__ = "benchmark_case_runs"
    __table_args__ = (
        UniqueConstraint("benchmark_run_id", "case_revision_id", name="uq_benchmark_case"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    benchmark_run_id: Mapped[str] = mapped_column(ForeignKey("benchmark_runs.id"), index=True)
    case_revision_id: Mapped[str] = mapped_column(ForeignKey("case_revisions.id"), index=True)
    candidate_report_id: Mapped[str] = mapped_column(ForeignKey("candidate_reports.id"))
    eval_spec_version_id: Mapped[str] = mapped_column(ForeignKey("eval_spec_versions.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    result_content_hash: Mapped[str | None] = mapped_column(
        ForeignKey("content_blobs.content_hash"), nullable=True
    )
    attempts_json: Mapped[str] = mapped_column(Text, default="[]")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class EvaluationSession(TimestampedModel, Base):
    """User-facing orchestration state for draft review and scoring."""

    __tablename__ = "evaluation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="needs_confirmation", index=True)
    case_draft_json: Mapped[str] = mapped_column(Text)
    report_drafts_json: Mapped[str] = mapped_column(Text)
    working_json: Mapped[str] = mapped_column(Text)
    questions_json: Mapped[str] = mapped_column(Text, default="[]")
    answers_json: Mapped[str] = mapped_column(Text, default="[]")
    resources_json: Mapped[str] = mapped_column(Text, default="{}")
    error_json: Mapped[str] = mapped_column(Text, default="{}")


class CaseDraft(TimestampedModel, Base):
    """Review state for publishing one reusable benchmark Case."""

    __tablename__ = "case_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dataset_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    category_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="needs_confirmation", index=True)
    original_json: Mapped[str] = mapped_column(Text)
    working_json: Mapped[str] = mapped_column(Text)
    questions_json: Mapped[str] = mapped_column(Text, default="[]")
    answers_json: Mapped[str] = mapped_column(Text, default="[]")
    resources_json: Mapped[str] = mapped_column(Text, default="{}")
    error_json: Mapped[str] = mapped_column(Text, default="{}")


class ReportDraft(TimestampedModel, Base):
    """One normalized AI report ready for repeated benchmark use."""

    __tablename__ = "report_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")


class EvaluationBatch(TimestampedModel, Base):
    """A published Case evaluated against one or more Report Drafts."""

    __tablename__ = "evaluation_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_draft_id: Mapped[str] = mapped_column(
        ForeignKey("case_drafts.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    report_draft_ids_json: Mapped[str] = mapped_column(Text)
    resources_json: Mapped[str] = mapped_column(Text, default="{}")
    comparison_json: Mapped[str] = mapped_column(Text, default="[]")
    error_json: Mapped[str] = mapped_column(Text, default="{}")


class EvaluationMethod(TimestampedModel, Base):
    """One versioned command that turns Case logs into a text report."""

    __tablename__ = "evaluation_methods"
    __table_args__ = (
        UniqueConstraint("method_key", "version_number", name="uq_evaluation_methods_key_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    method_key: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    version_number: Mapped[int] = mapped_column(Integer)
    tool_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    command_template: Mapped[str] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    max_output_bytes: Mapped[int] = mapped_column(Integer, default=10 * 1024 * 1024)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    last_probe_json: Mapped[str] = mapped_column(Text, default="{}")


class EvaluationHarness(TimestampedModel, Base):
    """One immutable report-generation harness version."""

    __tablename__ = "evaluation_harnesses"
    __table_args__ = (
        UniqueConstraint(
            "harness_key",
            "version_number",
            name="uq_evaluation_harnesses_key_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    harness_key: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    family: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    model_policy: Mapped[str] = mapped_column(String(16))
    tool_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    skill_base_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    command_template: Mapped[str] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    max_output_bytes: Mapped[int] = mapped_column(Integer, default=10 * 1024 * 1024)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    last_probe_json: Mapped[str] = mapped_column(Text, default="{}")


class EvaluationModel(TimestampedModel, Base):
    """A local harness-selectable model name, not a provider configuration."""

    __tablename__ = "evaluation_models"
    __table_args__ = (
        UniqueConstraint("model_key", "version_number", name="uq_evaluation_models_key_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(255))
    version_number: Mapped[int] = mapped_column(Integer)
    argument: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="frozen", index=True)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)


class EvaluationTarget(TimestampedModel, Base):
    """A frozen compatible Harness x Model execution target."""

    __tablename__ = "evaluation_targets"
    __table_args__ = (
        UniqueConstraint(
            "target_key", "version_number", name="uq_evaluation_targets_key_version"
        ),
        UniqueConstraint(
            "materialized_method_id", name="uq_evaluation_targets_materialized_method"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_key: Mapped[str] = mapped_column(String(255), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    harness_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_harnesses.id", ondelete="RESTRICT"), index=True
    )
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_models.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    model_argument: Mapped[str | None] = mapped_column(String(255), nullable=True)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    last_probe_json: Mapped[str] = mapped_column(Text, default="{}")
    materialized_method_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_methods.id", ondelete="RESTRICT"), nullable=True, index=True
    )


class Skill(TimestampedModel, Base):
    """One optimizable local Skill and its installation contract."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    source_path: Mapped[str] = mapped_column(String(2048))
    invoke_as: Mapped[str] = mapped_column(String(128))
    harness_key: Mapped[str] = mapped_column(String(100), index=True)
    install_relative_path: Mapped[str] = mapped_column(String(1024))
    publish_mode: Mapped[str] = mapped_column(String(32), default="managed")
    editable_paths_json: Mapped[str] = mapped_column(Text, default="[]")
    limits_json: Mapped[str] = mapped_column(Text, default="{}")
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class SkillPackageVersion(Base):
    """One immutable Skill package stored in an AnalystBench-owned Git repository."""

    __tablename__ = "skill_package_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version_number", name="uq_skill_versions_number"),
        UniqueConstraint("skill_id", "package_hash", name="uq_skill_versions_hash"),
        UniqueConstraint("skill_id", "git_commit", name="uq_skill_versions_git_commit"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    package_hash: Mapped[str] = mapped_column(String(71), index=True)
    git_commit: Mapped[str] = mapped_column(String(64))
    git_tree: Mapped[str] = mapped_column(String(64))
    git_object_format: Mapped[str] = mapped_column(String(16), default="sha1")
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    source_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SkillTargetBinding(TimestampedModel, Base):
    """The active Skill version for one EvaluationTarget."""

    __tablename__ = "skill_target_bindings"
    __table_args__ = (
        UniqueConstraint("skill_id", "evaluation_target_id", name="uq_skill_target_binding"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), index=True
    )
    evaluation_target_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_targets.id", ondelete="RESTRICT"), index=True
    )
    active_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), index=True
    )
    active_level: Mapped[str] = mapped_column(String(32), default="provisional")
    lock_version: Mapped[int] = mapped_column(Integer, default=0)


class SkillBindingHistory(Base):
    """Append-only audit record for every active Skill binding transition."""

    __tablename__ = "skill_binding_history"
    __table_args__ = (
        UniqueConstraint(
            "binding_id", "lock_version", name="uq_skill_binding_history_lock"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("skill_target_bindings.id", ondelete="RESTRICT"), index=True
    )
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), index=True
    )
    evaluation_target_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_targets.id", ondelete="RESTRICT"), index=True
    )
    previous_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    active_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), index=True
    )
    active_level: Mapped[str] = mapped_column(String(32))
    lock_version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32), index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EvaluationVariant(TimestampedModel, Base):
    """A frozen EvaluationTarget x SkillPackageVersion executable variant."""

    __tablename__ = "evaluation_variants"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_target_id",
            "skill_package_version_id",
            name="uq_evaluation_variant_target_skill",
        ),
        UniqueConstraint(
            "materialized_method_id", name="uq_evaluation_variant_materialized_method"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evaluation_target_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_targets.id", ondelete="RESTRICT"), index=True
    )
    skill_package_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), index=True
    )
    materialized_method_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_methods.id", ondelete="RESTRICT"), index=True
    )
    install_relative_path: Mapped[str] = mapped_column(String(1024))
    invoke_as: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="frozen", index=True)


class OptimizerPolicyVersion(Base):
    __tablename__ = "optimizer_policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_key", "version_number", name="uq_optimizer_policy_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_key: Mapped[str] = mapped_column(String(128), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    execution_profile_id: Mapped[str] = mapped_column(
        ForeignKey("execution_profiles.id", ondelete="RESTRICT"), index=True
    )
    prompt_bundle_hash: Mapped[str] = mapped_column(String(71))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class VerifierBundleVersion(Base):
    __tablename__ = "verifier_bundle_versions"
    __table_args__ = (
        UniqueConstraint("bundle_key", "version_number", name="uq_verifier_bundle_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bundle_key: Mapped[str] = mapped_column(String(128), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    static_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    gate_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    judge_config_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OptimizationDataSnapshot(Base):
    __tablename__ = "optimization_data_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(255), index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    train_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    validation_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    hidden_test_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    prospective_holdout_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    case_input_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    eval_spec_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OptimizationExperiment(TimestampedModel, Base):
    __tablename__ = "optimization_experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), index=True
    )
    base_skill_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), index=True
    )
    evaluation_target_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_targets.id", ondelete="RESTRICT"), index=True
    )
    data_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_data_snapshots.id", ondelete="RESTRICT"), index=True
    )
    optimizer_policy_version_id: Mapped[str] = mapped_column(
        ForeignKey("optimizer_policy_versions.id", ondelete="RESTRICT"), index=True
    )
    verifier_bundle_version_id: Mapped[str] = mapped_column(
        ForeignKey("verifier_bundle_versions.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    current_epoch_number: Mapped[int] = mapped_column(Integer, default=0)
    max_epochs: Mapped[int] = mapped_column(Integer, default=5)
    stop_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    error_json: Mapped[str] = mapped_column(Text, default="{}")


class OptimizationEpoch(TimestampedModel, Base):
    __tablename__ = "optimization_epochs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "epoch_number", name="uq_optimization_epoch"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_experiments.id", ondelete="RESTRICT"), index=True
    )
    epoch_number: Mapped[int] = mapped_column(Integer)
    parent_skill_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    evidence_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    best_candidate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), nullable=True
    )
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CandidateMutation(TimestampedModel, Base):
    __tablename__ = "candidate_mutations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    epoch_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_epochs.id", ondelete="RESTRICT"), index=True
    )
    parent_skill_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), index=True
    )
    candidate_skill_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    candidate_type: Mapped[str] = mapped_column(String(32))
    structured_patch_json: Mapped[str] = mapped_column(Text)
    patch_hash: Mapped[str] = mapped_column(String(71), index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    intended_failure_clusters_json: Mapped[str] = mapped_column(Text, default="[]")
    intent_json: Mapped[str] = mapped_column(Text, default="{}")
    change_stats_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)
    rejection_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejection_detail_json: Mapped[str] = mapped_column(Text, default="{}")


class OptimizationSignal(Base):
    __tablename__ = "optimization_signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_experiments.id", ondelete="RESTRICT"), index=True
    )
    epoch_id: Mapped[str | None] = mapped_column(
        ForeignKey("optimization_epochs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    case_path: Mapped[str] = mapped_column(String(1024), index=True)
    evaluation_method_run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_submission_method_runs.id", ondelete="RESTRICT"),
        unique=True,
    )
    run_role: Mapped[str] = mapped_column(String(32))
    case_family: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_json: Mapped[str] = mapped_column(Text)
    signal_hash: Mapped[str] = mapped_column(String(71), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OptimizationRunGroup(TimestampedModel, Base):
    __tablename__ = "optimization_run_groups"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "epoch_id",
            "candidate_mutation_id",
            "split_role",
            "arm",
            "repeat_index",
            name="uq_optimization_run_group",
        ),
        UniqueConstraint(
            "experiment_id",
            "run_config_hash",
            name="uq_optimization_run_group_config",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_experiments.id", ondelete="RESTRICT"), index=True
    )
    epoch_id: Mapped[str | None] = mapped_column(
        ForeignKey("optimization_epochs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    candidate_mutation_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_mutations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    split_role: Mapped[str] = mapped_column(String(32))
    arm: Mapped[str] = mapped_column(String(32))
    skill_package_version_id: Mapped[str] = mapped_column(
        ForeignKey("skill_package_versions.id", ondelete="RESTRICT"), index=True
    )
    repeat_index: Mapped[int] = mapped_column(Integer)
    evaluation_submission_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_submissions.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    run_config_hash: Mapped[str] = mapped_column(String(71))


class CandidateComparison(Base):
    __tablename__ = "candidate_comparisons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_experiments.id", ondelete="RESTRICT"), index=True
    )
    epoch_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_epochs.id", ondelete="RESTRICT"), index=True
    )
    candidate_mutation_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_mutations.id", ondelete="RESTRICT"), index=True
    )
    comparison_type: Mapped[str] = mapped_column(String(32))
    metrics_json: Mapped[str] = mapped_column(Text)
    gate_result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DecisionRecord(Base):
    __tablename__ = "decision_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_experiments.id", ondelete="RESTRICT"), index=True
    )
    epoch_id: Mapped[str | None] = mapped_column(
        ForeignKey("optimization_epochs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    candidate_mutation_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_mutations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    diagnosis_json: Mapped[str] = mapped_column(Text, default="{}")
    revision_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    outcome_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OptimizationEvent(Base):
    __tablename__ = "optimization_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_experiments.id", ondelete="RESTRICT"), index=True
    )
    epoch_id: Mapped[str | None] = mapped_column(
        ForeignKey("optimization_epochs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    candidate_mutation_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_mutations.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EvaluationSchedule(TimestampedModel, Base):
    """One persistent daily schedule that creates evaluation submissions."""

    __tablename__ = "evaluation_schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    dataset_key: Mapped[str] = mapped_column(String(255), index=True)
    case_mode: Mapped[str] = mapped_column(String(32))
    case_paths_json: Mapped[str] = mapped_column(Text, default="[]")
    method_ids_json: Mapped[str] = mapped_column(Text)
    target_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    target_selections_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
    judge_runner: Mapped[str] = mapped_column(String(32))
    timezone: Mapped[str] = mapped_column(String(100))
    local_time: Mapped[str] = mapped_column(String(5))
    enabled: Mapped[bool] = mapped_column(default=True, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EvaluationScheduleRun(TimestampedModel, Base):
    """One durable trigger occurrence for an EvaluationSchedule."""

    __tablename__ = "evaluation_schedule_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_schedules.id", ondelete="RESTRICT"), index=True
    )
    trigger_key: Mapped[str] = mapped_column(String(255), unique=True)
    trigger_type: Mapped[str] = mapped_column(String(32))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    config_snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    error_json: Mapped[str] = mapped_column(Text, default="{}")


class EvaluationSubmission(TimestampedModel, Base):
    """A durable request to run several methods over one local test set."""

    __tablename__ = "evaluation_submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    dataset_key: Mapped[str] = mapped_column(String(255), index=True)
    run_timestamp: Mapped[str] = mapped_column(String(14), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    purpose: Mapped[str] = mapped_column(
        String(32), default="normal", server_default="normal", index=True
    )
    optimization_context_json: Mapped[str] = mapped_column(
        Text, default="{}", server_default="{}"
    )
    schedule_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_schedule_runs.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    manifest_json: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    error_json: Mapped[str] = mapped_column(Text, default="{}")


class EvaluationSubmissionCaseRun(TimestampedModel, Base):
    """One Case inside an EvaluationSubmission."""

    __tablename__ = "evaluation_submission_case_runs"
    __table_args__ = (
        UniqueConstraint("submission_id", "case_path", name="uq_evaluation_submission_case_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_submissions.id", ondelete="RESTRICT"), index=True
    )
    case_path: Mapped[str] = mapped_column(String(1024))
    case_key: Mapped[str] = mapped_column(String(255))
    run_directory: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    scoring_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error_json: Mapped[str] = mapped_column(Text, default="{}")


class EvaluationSubmissionMethodRun(TimestampedModel, Base):
    """One method execution for one submitted Case."""

    __tablename__ = "evaluation_submission_method_runs"
    __table_args__ = (
        UniqueConstraint("case_run_id", "method_id", name="uq_evaluation_submission_method"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_run_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_submission_case_runs.id", ondelete="RESTRICT"), index=True
    )
    method_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_methods.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_json: Mapped[str] = mapped_column(Text, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
