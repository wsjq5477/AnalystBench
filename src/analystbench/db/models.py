"""Persistence models for local, versioned benchmark artifacts."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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


class EvaluationSubmission(TimestampedModel, Base):
    """A durable request to run several methods over one local test set."""

    __tablename__ = "evaluation_submissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_key: Mapped[str] = mapped_column(String(255), index=True)
    run_timestamp: Mapped[str] = mapped_column(String(14), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
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
    artifact_json: Mapped[str] = mapped_column(Text, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
