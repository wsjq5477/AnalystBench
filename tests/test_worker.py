from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from alembic.config import Config

from alembic import command
from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationMethod,
    EvaluationSubmission,
    EvaluationSubmissionCaseRun,
    EvaluationSubmissionMethodRun,
)
from analystbench.db.transaction import transaction
from analystbench.worker import LocalWorker


def test_worker_runs_dependency_check(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    try:
        assert worker.run_once() is False
    finally:
        worker.close()


def test_schedule_scan_failure_does_not_stop_worker(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    try:
        def fail_scan() -> int:
            raise RuntimeError("competing scheduler")

        worker.evaluation_schedules.enqueue_due = fail_scan
        assert worker.run_once() is False
    finally:
        worker.close()


def test_concurrent_job_claims_respect_evaluation_method_limit(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    method_id = str(uuid4())
    submission_id = str(uuid4())
    try:
        with transaction(worker.session_factory) as session:
            session.add(
                EvaluationMethod(
                    id=method_id,
                    method_key="parallel",
                    name="Parallel",
                    version_number=1,
                    command_template="python -c pass",
                    concurrency_limit=2,
                    status="frozen",
                    content_hash=f"sha256:{uuid4().hex}",
                )
            )
            session.add(
                EvaluationSubmission(
                    id=submission_id,
                    dataset_key="dataset",
                    run_timestamp="20260728000000",
                    manifest_json="{}",
                )
            )
            for index in range(4):
                case_run_id = str(uuid4())
                method_run_id = str(uuid4())
                session.add(
                    EvaluationSubmissionCaseRun(
                        id=case_run_id,
                        submission_id=submission_id,
                        case_path=f"dataset/category/case-{index}",
                        case_key=f"case-{index}",
                        run_directory=str(tmp_path / f"run-{index}"),
                    )
                )
                session.add(
                    EvaluationSubmissionMethodRun(
                        id=method_run_id,
                        case_run_id=case_run_id,
                        method_id=method_id,
                    )
                )
                worker.jobs.enqueue(
                    session,
                    "evaluation_method_run",
                    {"evaluation_method_run_id": method_run_id},
                )

        with ThreadPoolExecutor(max_workers=4) as executor:
            claimed = list(
                executor.map(
                    lambda index: worker.jobs.claim(f"worker-{index}", lease_seconds=30),
                    range(4),
                )
            )

        assert sum(job is not None for job in claimed) == 2
    finally:
        worker.close()


def test_job_lease_can_only_be_renewed_and_completed_by_owner(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    try:
        with transaction(worker.session_factory) as session:
            worker.jobs.enqueue(session, "unsupported", {})

        job = worker.jobs.claim("owner", lease_seconds=3)

        assert job is not None
        assert worker.jobs.renew(job.id, "other", lease_seconds=30) is False
        assert worker.jobs.renew(job.id, "owner", lease_seconds=30) is True
        assert worker.jobs.complete(job.id, worker_id="other") is False
        assert worker.jobs.complete(job.id, worker_id="owner") is True
    finally:
        worker.close()
