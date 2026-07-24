"""Persistent Benchmark Run orchestration over frozen reports and Eval Specs."""

import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analystbench.config import Settings
from analystbench.content_store import ContentRef, ContentStore, canonical_json
from analystbench.db.models import (
    BenchmarkCaseRun,
    BenchmarkRun,
    CandidateReport,
    CandidateVersion,
    ContentBlob,
    DatasetVersion,
    EvalSpecVersion,
    ScoringPolicyVersion,
)
from analystbench.errors import AnalystBenchError
from analystbench.jobs import JobQueue
from analystbench.scoring import evaluate
from analystbench.semantic_judge import SemanticJudge
from analystbench.services import ConflictError, NotFoundError, transaction


class BenchmarkService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        content_store: ContentStore,
        settings: Settings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.content_store = content_store
        self.settings = settings
        self.jobs = JobQueue(session_factory)

    def list_runs(self) -> list[BenchmarkRun]:
        """List all benchmark runs ordered by creation time descending."""
        with transaction(self.session_factory) as session:
            return list(
                session.scalars(
                    select(BenchmarkRun).order_by(BenchmarkRun.created_at.desc())
                )
            )

    @staticmethod
    def _store_ref(session: Session, ref: ContentRef) -> None:
        if session.get(ContentBlob, ref.content_hash) is None:
            session.add(ContentBlob(**asdict(ref)))

    def create_run(
        self, dataset_version_id: str, candidate_version_id: str, scoring_policy_version_id: str
    ) -> BenchmarkRun:
        with transaction(self.session_factory) as session:
            dataset = session.get(DatasetVersion, dataset_version_id)
            if dataset is None:
                raise NotFoundError("dataset_version", dataset_version_id)
            candidate = session.get(CandidateVersion, candidate_version_id)
            if candidate is None:
                raise NotFoundError("candidate_version", candidate_version_id)
            policy = session.get(ScoringPolicyVersion, scoring_policy_version_id)
            if policy is None:
                raise NotFoundError("scoring_policy_version", scoring_policy_version_id)
            revision_ids = json.loads(dataset.case_revision_ids_json)
            reports = {
                report.case_revision_id: report
                for report in session.scalars(
                    select(CandidateReport).where(
                        CandidateReport.candidate_version_id == candidate_version_id,
                        CandidateReport.case_revision_id.in_(revision_ids),
                    )
                )
            }
            missing_reports = [
                revision_id for revision_id in revision_ids if revision_id not in reports
            ]
            if missing_reports:
                raise AnalystBenchError(
                    "coverage_incomplete",
                    "candidate version is missing reports for this dataset version",
                    {"missing_case_revision_ids": missing_reports},
                )
            specs: dict[str, EvalSpecVersion] = {}
            for revision_id in revision_ids:
                spec = session.scalar(
                    select(EvalSpecVersion)
                    .where(EvalSpecVersion.case_revision_id == revision_id)
                    .order_by(EvalSpecVersion.version_number.desc())
                    .limit(1)
                )
                if spec is None:
                    raise AnalystBenchError(
                        "validation_failed",
                        f"case revision '{revision_id}' has no frozen eval spec",
                    )
                payload = json.loads(spec.payload_json)
                if payload.get("scoring_policy_version_id") != scoring_policy_version_id:
                    raise AnalystBenchError(
                        "validation_failed",
                        "every frozen Eval Spec must reference the requested scoring policy",
                        {"case_revision_id": revision_id, "eval_spec_version_id": spec.id},
                    )
                specs[revision_id] = spec
            manifest = {
                "dataset_version_id": dataset.id,
                "dataset_version_hash": dataset.content_hash,
                "candidate_version_id": candidate.id,
                "candidate_version_hash": candidate.content_hash,
                "scoring_policy_version_id": policy.id,
                "scoring_policy_hash": policy.content_hash,
                "cases": [
                    {
                        "case_revision_id": revision_id,
                        "candidate_report_id": reports[revision_id].id,
                        "candidate_report_hash": reports[revision_id].content_hash,
                        "eval_spec_version_id": specs[revision_id].id,
                        "eval_spec_hash": specs[revision_id].content_hash,
                    }
                    for revision_id in revision_ids
                ],
            }
            run = BenchmarkRun(
                id=str(uuid4()),
                dataset_version_id=dataset.id,
                candidate_version_id=candidate.id,
                scoring_policy_version_id=policy.id,
                status="queued",
                manifest_json=canonical_json(manifest),
            )
            session.add(run)
            session.flush()
            for revision_id in revision_ids:
                case_run = BenchmarkCaseRun(
                    id=str(uuid4()),
                    benchmark_run_id=run.id,
                    case_revision_id=revision_id,
                    candidate_report_id=reports[revision_id].id,
                    eval_spec_version_id=specs[revision_id].id,
                    status="pending",
                    stage="pending",
                )
                session.add(case_run)
                self.jobs.enqueue(
                    session, "benchmark_case_run", {"benchmark_case_run_id": case_run.id}
                )
            session.flush()
            session.expunge(run)
            return run

    def get_run(self, run_id: str) -> BenchmarkRun:
        with transaction(self.session_factory) as session:
            run = session.get(BenchmarkRun, run_id)
            if run is None:
                raise NotFoundError("benchmark_run", run_id)
            session.expunge(run)
            return run

    def list_case_runs(self, run_id: str) -> list[BenchmarkCaseRun]:
        with transaction(self.session_factory) as session:
            if session.get(BenchmarkRun, run_id) is None:
                raise NotFoundError("benchmark_run", run_id)
            return list(
                session.scalars(
                    select(BenchmarkCaseRun)
                    .where(BenchmarkCaseRun.benchmark_run_id == run_id)
                    .order_by(BenchmarkCaseRun.case_revision_id)
                )
            )

    def cancel_run(self, run_id: str) -> BenchmarkRun:
        with transaction(self.session_factory) as session:
            run = session.get(BenchmarkRun, run_id)
            if run is None:
                raise NotFoundError("benchmark_run", run_id)
            run.cancellation_requested = True
            for case_run in session.scalars(
                select(BenchmarkCaseRun).where(
                    BenchmarkCaseRun.benchmark_run_id == run_id,
                    BenchmarkCaseRun.status == "pending",
                )
            ):
                case_run.status, case_run.stage = "skipped", "cancelled"
            session.flush()
            self._update_run_summary(session, run.id)
            session.flush()
            session.expunge(run)
            return run

    def retry_failed(self, run_id: str) -> int:
        with transaction(self.session_factory) as session:
            run = session.get(BenchmarkRun, run_id)
            if run is None:
                raise NotFoundError("benchmark_run", run_id)
            if run.cancellation_requested:
                raise ConflictError("cancelled benchmark run cannot be retried")
            retried = 0
            for case_run in session.scalars(
                select(BenchmarkCaseRun).where(
                    BenchmarkCaseRun.benchmark_run_id == run_id,
                    BenchmarkCaseRun.status == "failed",
                )
            ):
                case_run.status, case_run.stage, case_run.error_code = "pending", "pending", None
                self.jobs.enqueue(
                    session, "benchmark_case_run", {"benchmark_case_run_id": case_run.id}
                )
                retried += 1
            if retried:
                run.status = "queued"
            return retried

    def execute_case_run(self, case_run_id: str) -> None:
        with transaction(self.session_factory) as session:
            case_run = session.get(BenchmarkCaseRun, case_run_id)
            if case_run is None:
                raise NotFoundError("benchmark_case_run", case_run_id)
            run = session.get(BenchmarkRun, case_run.benchmark_run_id)
            assert run is not None
            if case_run.status == "succeeded":
                return
            if run.cancellation_requested:
                case_run.status, case_run.stage = "skipped", "cancelled"
                self._update_run_summary(session, run.id)
                return
            case_run.status, case_run.stage = "running", "extracting"
            case_run.attempt += 1
            case_run_id = case_run.id
            report = session.get(CandidateReport, case_run.candidate_report_id)
            candidate_version = session.get(CandidateVersion, run.candidate_version_id)
            spec = session.get(EvalSpecVersion, case_run.eval_spec_version_id)
            assert report is not None and spec is not None and candidate_version is not None
            report_text = self.content_store.read_text(report.report_content_hash)
            spec_payload = json.loads(spec.payload_json)
            report_hash = report.report_content_hash
            candidate_metadata = json.loads(candidate_version.metadata_json)
            claim_hints = candidate_metadata.get("analystbench_claim_hints")
            judge_configuration = candidate_metadata.get(
                "analystbench_judge", {"runner": "lexical", "configuration": {}}
            )

        try:
            judge_runner = str(judge_configuration.get("runner", "lexical"))
            alignment_judge = None
            judge_audit: dict[str, Any] = {"kind": "lexical_debug", "runner": "lexical"}
            if judge_runner != "lexical":
                if self.settings is None:
                    raise AnalystBenchError(
                        "configuration_error", "semantic judge requires application settings"
                    )
                semantic_judge = SemanticJudge(
                    self.settings,
                    judge_runner,
                    dict(judge_configuration.get("configuration", {})),
                )
                alignment_judge = semantic_judge.align
            result = evaluate(
                spec_payload,
                report_text,
                report_hash,
                claim_hints,
                alignment_judge,
            )
            result["judge"] = semantic_judge.audit if alignment_judge else judge_audit
        except Exception as exc:
            self._record_failure(case_run_id, "scoring_failed", str(exc))
            raise
        with transaction(self.session_factory) as session:
            case_run = session.get(BenchmarkCaseRun, case_run_id)
            assert case_run is not None
            run = session.get(BenchmarkRun, case_run.benchmark_run_id)
            assert run is not None
            if run.cancellation_requested:
                case_run.status, case_run.stage = "skipped", "cancelled"
                self._update_run_summary(session, run.id)
                return
            ref = self.content_store.put_json(result)
            self._store_ref(session, ref)
            session.flush()
            history = json.loads(case_run.attempts_json)
            history.append({"attempt": case_run.attempt, "result_content_hash": ref.content_hash})
            case_run.status, case_run.stage = "succeeded", "succeeded"
            case_run.result_content_hash = ref.content_hash
            case_run.attempts_json = canonical_json(history)
            case_run.error_code = None
            session.flush()
            self._update_run_summary(session, run.id)

    def _record_failure(self, case_run_id: str, error_code: str, message: str) -> None:
        with transaction(self.session_factory) as session:
            case_run = session.get(BenchmarkCaseRun, case_run_id)
            if case_run is None:
                return
            history = json.loads(case_run.attempts_json)
            history.append(
                {"attempt": case_run.attempt, "error_code": error_code, "message": message[:1000]}
            )
            case_run.status, case_run.stage = "failed", "failed"
            case_run.error_code = error_code
            case_run.attempts_json = canonical_json(history)
            self._update_run_summary(session, case_run.benchmark_run_id)

    def get_case_result(self, case_run_id: str) -> dict[str, Any]:
        with transaction(self.session_factory) as session:
            case_run = session.get(BenchmarkCaseRun, case_run_id)
            if case_run is None:
                raise NotFoundError("benchmark_case_run", case_run_id)
            if case_run.result_content_hash is None:
                raise ConflictError("case run has no successful result")
            return json.loads(self.content_store.read_text(case_run.result_content_hash))

    def export_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        return {
            "manifest": json.loads(run.manifest_json),
            "summary": json.loads(run.summary_json),
            "case_runs": [
                {
                    "id": case_run.id,
                    "case_revision_id": case_run.case_revision_id,
                    "status": case_run.status,
                    "attempt": case_run.attempt,
                    "result": self.get_case_result(case_run.id)
                    if case_run.status == "succeeded"
                    else None,
                }
                for case_run in self.list_case_runs(run_id)
            ],
        }

    def _update_run_summary(self, session: Session, run_id: str) -> None:
        run = session.get(BenchmarkRun, run_id)
        assert run is not None
        case_runs = list(
            session.scalars(
                select(BenchmarkCaseRun).where(BenchmarkCaseRun.benchmark_run_id == run_id)
            )
        )
        counts = {
            status: sum(item.status == status for item in case_runs)
            for status in ("pending", "running", "succeeded", "failed", "skipped")
        }
        scores: list[Decimal] = []
        passed = 0
        for item in case_runs:
            if item.status == "succeeded" and item.result_content_hash:
                result = json.loads(self.content_store.read_text(item.result_content_hash))
                scores.append(Decimal(result["total_score"]))
                passed += bool(result["passed"])
        total = len(case_runs)
        summary = {
            "total": total,
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "running": counts["running"],
            "coverage_rate": float(Decimal(counts["succeeded"]) / Decimal(total)) if total else 0.0,
            "average_total_score": float(sum(scores) / len(scores)) if scores else None,
            "pass_rate": float(Decimal(passed) / Decimal(len(scores))) if scores else None,
        }
        run.summary_json = canonical_json(summary)
        if counts["pending"] or counts["running"]:
            run.status = "running"
        elif counts["succeeded"] == total:
            run.status = "completed"
        elif counts["succeeded"]:
            run.status = "completed_with_errors"
        elif run.cancellation_requested:
            run.status = "cancelled"
        else:
            run.status = "failed"
