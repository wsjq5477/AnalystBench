"""Local Worker process for database-backed background jobs."""

import logging
import os
import time
from uuid import uuid4

from sqlalchemy import text

from analystbench.agent_execution import AgentExecutionService
from analystbench.agent_runner import AgentRunnerError
from analystbench.benchmark import BenchmarkService
from analystbench.case_library import CaseLibraryService
from analystbench.config import Settings, get_settings
from analystbench.content_store import ContentStore
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.evaluation_submission import (
    EvaluationCommandError,
    EvaluationSubmissionService,
)
from analystbench.jobs import JobQueue
from analystbench.logging import configure_logging

logger = logging.getLogger(__name__)


class LocalWorker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        configure_logging(self.settings.log_level)
        self.engine = create_database_engine(self.settings)
        self.session_factory = create_session_factory(self.engine)
        self.content_store = ContentStore(self.settings.content_store_path)
        self.execution = AgentExecutionService(
            self.session_factory, self.content_store, self.settings
        )
        self.benchmarks = BenchmarkService(self.session_factory, self.content_store, self.settings)
        self.case_library = CaseLibraryService(
            self.session_factory, self.content_store, self.settings
        )
        self.evaluation_submissions = EvaluationSubmissionService(
            self.session_factory, self.settings
        )
        self.jobs = JobQueue(self.session_factory)
        self.worker_id = f"{os.getpid()}-{uuid4()}"

    def run_once(self) -> bool:
        """Claim and execute one durable job; return false when the queue is idle."""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        job = self.jobs.claim(self.worker_id)
        if job is None:
            logger.info("worker_idle")
            return False
        try:
            payload = self.jobs.payload(job)
            if job.kind == "agent_case_run":
                self.execution.execute_agent_case_run(str(payload["agent_case_run_id"]))
            elif job.kind == "benchmark_case_run":
                self.benchmarks.execute_case_run(str(payload["benchmark_case_run_id"]))
            elif job.kind == "case_draft_generate":
                self.case_library.execute_generation(str(payload["case_draft_id"]))
            elif job.kind == "evaluation_method_run":
                self.evaluation_submissions.execute_method_run(
                    str(payload["evaluation_method_run_id"])
                )
            elif job.kind == "evaluation_case_score":
                self.evaluation_submissions.execute_case_scoring(
                    str(payload["evaluation_case_run_id"])
                )
            else:
                raise RuntimeError(f"unsupported job kind '{job.kind}'")
        except AgentRunnerError as exc:
            self.jobs.fail(job.id, f"{exc.code}: {exc}", retryable=False)
            logger.warning("job_failed", extra={"job_id": job.id, "attempt": job.attempts})
        except EvaluationCommandError as exc:
            self.jobs.fail(job.id, f"{exc.code}: {exc}", retryable=False)
            logger.warning("job_failed", extra={"job_id": job.id, "attempt": job.attempts})
        except Exception as exc:
            self.jobs.fail(job.id, str(exc), retryable=True)
            logger.exception("job_failed", extra={"job_id": job.id, "attempt": job.attempts})
        else:
            self.jobs.complete(job.id)
            logger.info("job_succeeded", extra={"job_id": job.id, "attempt": job.attempts})
        return True

    def serve(self) -> None:
        try:
            while True:
                processed = self.run_once()
                if not processed:
                    time.sleep(self.settings.worker_poll_interval_seconds)
        finally:
            self.engine.dispose()

    def close(self) -> None:
        self.engine.dispose()
