"""Local Worker process for database-backed background jobs."""

import logging
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from uuid import uuid4

from sqlalchemy import text

from analystbench.catalog.case_library import CaseLibraryService
from analystbench.config import Settings, get_settings
from analystbench.db.models import Job
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.errors import AnalystBenchError
from analystbench.evaluation.benchmark import BenchmarkService
from analystbench.evaluation.schedule import EvaluationScheduleService
from analystbench.evaluation.submission import (
    EvaluationCommandError,
    EvaluationSubmissionService,
)
from analystbench.execution.runner import AgentRunnerError
from analystbench.execution.service import AgentExecutionService
from analystbench.runtime.jobs import JobQueue
from analystbench.runtime.logging import configure_logging
from analystbench.skill_optimization import (
    SkillRegistryService,
    SkillWorkspacePreparer,
)
from analystbench.skill_optimization.experiment import OptimizationExperimentService
from analystbench.storage.content import ContentStore

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
        self.skill_registry = SkillRegistryService(
            self.session_factory, self.settings
        )
        workspace_preparer = (
            SkillWorkspacePreparer(self.session_factory, self.skill_registry)
            if self.settings.skill_optimization_enabled
            else None
        )
        self.evaluation_submissions = EvaluationSubmissionService(
            self.session_factory,
            self.settings,
            workspace_preparer=workspace_preparer,
        )
        self.skill_optimization = OptimizationExperimentService(
            self.session_factory,
            self.settings,
            self.skill_registry,
            self.evaluation_submissions,
        )
        self.evaluation_schedules = EvaluationScheduleService(
            self.session_factory,
            self.settings,
            self.evaluation_submissions,
        )
        self.jobs = JobQueue(self.session_factory)
        self.worker_id = f"{os.getpid()}-{uuid4()}"

    def run_once(self) -> bool:
        """Claim and execute one durable job; return false when the queue is idle."""
        self._check_database()
        self._scan_schedules()
        job = self.jobs.claim(
            self.worker_id,
            lease_seconds=self.settings.worker_job_lease_seconds,
        )
        if job is None:
            logger.info("worker_idle")
            return False
        self._execute_job(job)
        return True

    def _check_database(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def _scan_schedules(self) -> None:
        try:
            self.evaluation_schedules.enqueue_due()
        except Exception:
            # A competing worker may win the unique schedule trigger or SQLite
            # may briefly be locked. Scheduling must not take the worker down or
            # prevent it from draining jobs that are already durable.
            logger.exception("evaluation_schedule_scan_failed")

    def _execute_job(self, job: Job) -> None:
        lease_stop = threading.Event()
        lease_thread = threading.Thread(
            target=self._renew_lease,
            args=(job.id, lease_stop),
            name=f"job-lease-{job.id}",
            daemon=True,
        )
        lease_thread.start()
        payload: dict[str, object] = {}
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
            elif job.kind == "evaluation_schedule_trigger":
                self.evaluation_schedules.execute_trigger(
                    str(payload["evaluation_schedule_run_id"])
                )
            elif job.kind == "skill_optimization_advance":
                self.skill_optimization.advance(str(payload["experiment_id"]))
            else:
                raise RuntimeError(f"unsupported job kind '{job.kind}'")
        except AgentRunnerError as exc:
            self.jobs.fail(
                job.id,
                f"{exc.code}: {exc}",
                retryable=False,
                worker_id=self.worker_id,
            )
            logger.warning("job_failed", extra={"job_id": job.id, "attempt": job.attempts})
        except EvaluationCommandError as exc:
            self.jobs.fail(
                job.id,
                f"{exc.code}: {exc}",
                retryable=False,
                worker_id=self.worker_id,
            )
            logger.warning("job_failed", extra={"job_id": job.id, "attempt": job.attempts})
        except Exception as exc:
            feature_disabled = (
                job.kind == "skill_optimization_advance"
                and isinstance(exc, AnalystBenchError)
                and exc.code == "skill_optimization_disabled"
            )
            job_failed = self.jobs.fail(
                job.id,
                str(exc),
                retryable=not feature_disabled,
                worker_id=self.worker_id,
            )
            if (
                job.kind == "skill_optimization_advance"
                and job.attempts >= 2
                and job_failed
                and not feature_disabled
            ):
                try:
                    self.skill_optimization.fail(
                        str(payload["experiment_id"]), exc
                    )
                except Exception:
                    logger.exception(
                        "skill_optimization_failure_persist_failed",
                        extra={"job_id": job.id},
                    )
            logger.exception("job_failed", extra={"job_id": job.id, "attempt": job.attempts})
        else:
            self.jobs.complete(job.id, worker_id=self.worker_id)
            logger.info("job_succeeded", extra={"job_id": job.id, "attempt": job.attempts})
        finally:
            lease_stop.set()
            lease_thread.join()

    def _renew_lease(self, job_id: str, stop: threading.Event) -> None:
        lease_seconds = self.settings.worker_job_lease_seconds
        interval = max(1.0, lease_seconds / 3)
        while not stop.wait(interval):
            if not self.jobs.renew(job_id, self.worker_id, lease_seconds):
                logger.warning("job_lease_lost", extra={"job_id": job_id})
                return

    def serve(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        futures: set[Future[None]] = set()
        try:
            with ThreadPoolExecutor(
                max_workers=self.settings.worker_concurrency_limit,
                thread_name_prefix="analystbench-job",
            ) as executor:
                while not stop.is_set():
                    self._check_database()
                    self._scan_schedules()
                    futures = {future for future in futures if not future.done()}
                    while len(futures) < self.settings.worker_concurrency_limit:
                        job = self.jobs.claim(
                            self.worker_id,
                            lease_seconds=self.settings.worker_job_lease_seconds,
                        )
                        if job is None:
                            break
                        futures.add(executor.submit(self._execute_job, job))
                    if futures:
                        wait(
                            futures,
                            timeout=self.settings.worker_poll_interval_seconds,
                            return_when=FIRST_COMPLETED,
                        )
                    else:
                        time.sleep(self.settings.worker_poll_interval_seconds)
        finally:
            self.engine.dispose()

    def close(self) -> None:
        self.engine.dispose()
