"""Application service for persistent claude/OpenCode candidate generation."""

import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from analystbench.config import Settings
from analystbench.db.models import (
    AgentCaseRun,
    CandidateGenerationRun,
    CandidateReport,
    CandidateVersion,
    CaseRevision,
    ContentBlob,
    DatasetVersion,
    ExecutionProfile,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError, ConflictError, NotFoundError
from analystbench.execution.runner import AgentRunnerError, ProbeResult, create_runner
from analystbench.runtime.jobs import JobQueue
from analystbench.storage.content import ContentRef, ContentStore, canonical_json


class AgentExecutionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        content_store: ContentStore,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.content_store = content_store
        self.settings = settings
        self.jobs = JobQueue(session_factory)

    def _store_ref(self, session: Session, ref: ContentRef) -> None:
        if session.get(ContentBlob, ref.content_hash) is None:
            session.add(ContentBlob(**asdict(ref)))

    @staticmethod
    def _validate_configuration(runner: str, configuration: dict[str, Any]) -> None:
        if runner not in {"claude", "opencode"}:
            raise AnalystBenchError("validation_failed", "runner must be claude or opencode")
        forbidden = {
            key
            for key in configuration
            if any(term in key.lower() for term in ("api_key", "token", "password", "secret"))
        }
        if forbidden:
            raise AnalystBenchError(
                "validation_failed",
                f"execution profile must not persist credential fields: {sorted(forbidden)}",
            )
        timeout = int(configuration.get("timeout_seconds", 21600))
        output_limit = int(configuration.get("max_output_bytes", 10 * 1024 * 1024))
        if not 1 <= timeout <= 21600 or not 1024 <= output_limit <= 100 * 1024 * 1024:
            raise AnalystBenchError(
                "validation_failed", "invalid execution timeout or output limit"
            )
        if configuration.get("environment_mode") not in {None, "local", "bare"}:
            raise AnalystBenchError("validation_failed", "environment_mode must be local or bare")

    def create_profile(
        self, name: str, runner: str, configuration: dict[str, Any]
    ) -> ExecutionProfile:
        self._validate_configuration(runner, configuration)
        with transaction(self.session_factory) as session:
            current = session.scalar(
                select(func.max(ExecutionProfile.version_number)).where(
                    ExecutionProfile.name == name
                )
            )
            version_number = int(current or 0) + 1
            manifest = {
                "name": name,
                "runner": runner,
                "version_number": version_number,
                "configuration": configuration,
            }
            ref = self.content_store.put_json(manifest)
            self._store_ref(session, ref)
            profile = ExecutionProfile(
                id=str(uuid4()),
                name=name,
                version_number=version_number,
                runner=runner,
                configuration_json=canonical_json(configuration),
                content_hash=ref.content_hash,
                status="draft",
            )
            session.add(profile)
            session.flush()
            session.expunge(profile)
            return profile

    def get_profile(self, profile_id: str) -> ExecutionProfile:
        with transaction(self.session_factory) as session:
            profile = session.get(ExecutionProfile, profile_id)
            if profile is None:
                raise NotFoundError("execution_profile", profile_id)
            session.expunge(profile)
            return profile

    def list_profiles(self) -> list[ExecutionProfile]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(ExecutionProfile).order_by(
                        ExecutionProfile.created_at.desc(),
                        ExecutionProfile.id,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def probe_profile(self, profile_id: str) -> ProbeResult:
        profile = self.get_profile(profile_id)
        configuration = json.loads(profile.configuration_json)
        runner = create_runner(profile.runner)
        return runner.probe(str(configuration.get("executable") or runner.default_executable))

    def freeze_profile(self, profile_id: str) -> ExecutionProfile:
        with transaction(self.session_factory) as session:
            profile = session.get(ExecutionProfile, profile_id)
            if profile is None:
                raise NotFoundError("execution_profile", profile_id)
            self._validate_configuration(profile.runner, json.loads(profile.configuration_json))
            profile.status = "frozen"
            session.flush()
            session.expunge(profile)
            return profile

    def create_generation_run(
        self, dataset_version_id: str, candidate_version_id: str, execution_profile_id: str
    ) -> CandidateGenerationRun:
        with transaction(self.session_factory) as session:
            dataset_version = session.get(DatasetVersion, dataset_version_id)
            if dataset_version is None:
                raise NotFoundError("dataset_version", dataset_version_id)
            if session.get(CandidateVersion, candidate_version_id) is None:
                raise NotFoundError("candidate_version", candidate_version_id)
            profile = session.get(ExecutionProfile, execution_profile_id)
            if profile is None:
                raise NotFoundError("execution_profile", execution_profile_id)
            if profile.status != "frozen":
                raise AnalystBenchError("validation_failed", "execution profile must be frozen")
            case_revision_ids = json.loads(dataset_version.case_revision_ids_json)
            manifest = {
                "dataset_version_id": dataset_version_id,
                "dataset_content_hash": dataset_version.content_hash,
                "candidate_version_id": candidate_version_id,
                "execution_profile_id": execution_profile_id,
                "execution_profile_hash": profile.content_hash,
                "case_revision_ids": case_revision_ids,
            }
            run = CandidateGenerationRun(
                id=str(uuid4()),
                dataset_version_id=dataset_version_id,
                candidate_version_id=candidate_version_id,
                execution_profile_id=execution_profile_id,
                status="queued",
                manifest_json=canonical_json(manifest),
            )
            session.add(run)
            # AgentCaseRun has an explicit FK but no ORM relationship to establish
            # dependency ordering automatically. Persist the parent first.
            session.flush()
            for case_revision_id in case_revision_ids:
                case_run = AgentCaseRun(
                    id=str(uuid4()),
                    generation_run_id=run.id,
                    case_revision_id=case_revision_id,
                    status="queued",
                )
                session.add(case_run)
                self.jobs.enqueue(session, "agent_case_run", {"agent_case_run_id": case_run.id})
            session.flush()
            session.expunge(run)
            return run

    def get_generation_run(self, run_id: str) -> CandidateGenerationRun:
        with transaction(self.session_factory) as session:
            run = session.get(CandidateGenerationRun, run_id)
            if run is None:
                raise NotFoundError("candidate_generation_run", run_id)
            session.expunge(run)
            return run

    def list_case_runs(self, generation_run_id: str) -> list[AgentCaseRun]:
        with transaction(self.session_factory) as session:
            if session.get(CandidateGenerationRun, generation_run_id) is None:
                raise NotFoundError("candidate_generation_run", generation_run_id)
            return list(
                session.scalars(
                    select(AgentCaseRun)
                    .where(AgentCaseRun.generation_run_id == generation_run_id)
                    .order_by(AgentCaseRun.id)
                )
            )

    def execute_agent_case_run(self, agent_case_run_id: str) -> None:
        """Run one agent outside a database transaction and persist immutable outputs."""
        with transaction(self.session_factory) as session:
            case_run = session.get(AgentCaseRun, agent_case_run_id)
            if case_run is None:
                raise NotFoundError("agent_case_run", agent_case_run_id)
            if case_run.status == "succeeded":
                return
            generation = session.get(CandidateGenerationRun, case_run.generation_run_id)
            assert generation is not None
            profile = session.get(ExecutionProfile, generation.execution_profile_id)
            candidate_version = session.get(CandidateVersion, generation.candidate_version_id)
            revision = session.get(CaseRevision, case_run.case_revision_id)
            assert profile is not None and candidate_version is not None and revision is not None
            profile_config = json.loads(profile.configuration_json)
            source = {
                "case_run_id": case_run.id,
                "candidate_version_id": candidate_version.id,
                "profile_runner": profile.runner,
                "profile_config": profile_config,
                "problem_statement": self.content_store.read_text(revision.problem_content_hash),
            }
            case_run.status = "running"
            case_run.attempt += 1

        stdout = ""
        stderr = ""
        try:
            with tempfile.TemporaryDirectory(
                dir=self.settings.workspace_root_path, prefix=f"agent-{agent_case_run_id[:8]}-"
            ) as directory:
                workspace = Path(directory)
                (workspace / "case.md").write_text(source["problem_statement"], encoding="utf-8")
                default_prompt = (
                    "Analyze the material in case.md. Return only a concise final analysis report."
                )
                prompt = str(
                    source["profile_config"].get(
                        "prompt_template",
                        default_prompt,
                    )
                )
                runner = create_runner(str(source["profile_runner"]))
                result = runner.execute(source["profile_config"], workspace, prompt)
                stdout, stderr = result.stdout, result.stderr
                self._persist_success(
                    agent_case_run_id, result.final_report, result.command, stdout, stderr
                )
        except AgentRunnerError as exc:
            stdout, stderr = exc.stdout, exc.stderr
            self._persist_failure(agent_case_run_id, exc.code, str(exc), stdout, stderr)
            raise
        except Exception as exc:
            self._persist_failure(
                agent_case_run_id, "agent_execution_failed", str(exc), stdout, stderr
            )
            raise

    def _persist_success(
        self,
        agent_case_run_id: str,
        final_report: str,
        command: list[str],
        stdout: str,
        stderr: str,
    ) -> None:
        with transaction(self.session_factory) as session:
            case_run = session.get(AgentCaseRun, agent_case_run_id)
            assert case_run is not None
            generation = session.get(CandidateGenerationRun, case_run.generation_run_id)
            assert generation is not None
            existing = session.scalar(
                select(CandidateReport).where(
                    CandidateReport.candidate_version_id == generation.candidate_version_id,
                    CandidateReport.case_revision_id == case_run.case_revision_id,
                )
            )
            if existing is not None:
                raise ConflictError("candidate report already exists for generated case")
            report_ref = self.content_store.put_text(final_report)
            stdout_ref = self.content_store.put_text(stdout, "application/json")
            stderr_ref = self.content_store.put_text(stderr)
            for ref in (report_ref, stdout_ref, stderr_ref):
                self._store_ref(session, ref)
            session.flush()
            artifact = {
                "command": command,
                "stdout_ref": stdout_ref.content_hash,
                "stderr_ref": stderr_ref.content_hash,
            }
            manifest_ref = self.content_store.put_json(
                {
                    "candidate_version_id": generation.candidate_version_id,
                    "case_revision_id": case_run.case_revision_id,
                    "source": "agent_run",
                    "agent_case_run_id": case_run.id,
                    "report_content_hash": report_ref.content_hash,
                }
            )
            self._store_ref(session, manifest_ref)
            # CandidateReport references this newly added immutable content blob.
            # Flush it before inserting the report because these models intentionally
            # do not use ORM relationships.
            session.flush()
            report = CandidateReport(
                id=str(uuid4()),
                candidate_version_id=generation.candidate_version_id,
                case_revision_id=case_run.case_revision_id,
                source="agent_run",
                report_content_hash=report_ref.content_hash,
                agent_case_run_id=case_run.id,
                content_hash=manifest_ref.content_hash,
            )
            case_run.status = "succeeded"
            case_run.error_code = None
            case_run.artifact_json = canonical_json(artifact)
            session.add(report)
            # The aggregate status is computed with a SQL query; make the
            # successful case visible to that query before calculating it.
            session.flush()
            self._update_generation_status(session, generation.id)

    def _persist_failure(
        self, agent_case_run_id: str, error_code: str, message: str, stdout: str, stderr: str
    ) -> None:
        with transaction(self.session_factory) as session:
            case_run = session.get(AgentCaseRun, agent_case_run_id)
            if case_run is None:
                return
            refs = []
            for text, media_type in (
                (stdout, "application/json"),
                (stderr, "text/plain; charset=utf-8"),
            ):
                ref = self.content_store.put_text(text, media_type)
                self._store_ref(session, ref)
                refs.append(ref.content_hash)
            case_run.status = "failed"
            case_run.error_code = error_code
            case_run.artifact_json = canonical_json(
                {"message": message, "stdout_ref": refs[0], "stderr_ref": refs[1]}
            )
            self._update_generation_status(session, case_run.generation_run_id)

    @staticmethod
    def _update_generation_status(session: Session, generation_run_id: str) -> None:
        generation = session.get(CandidateGenerationRun, generation_run_id)
        assert generation is not None
        statuses = list(
            session.scalars(
                select(AgentCaseRun.status).where(
                    AgentCaseRun.generation_run_id == generation_run_id
                )
            )
        )
        if statuses and all(status == "succeeded" for status in statuses):
            generation.status = "completed"
        elif any(status == "failed" for status in statuses) and all(
            status in {"succeeded", "failed", "cancelled"} for status in statuses
        ):
            generation.status = "completed_with_errors"
        else:
            generation.status = "running"
