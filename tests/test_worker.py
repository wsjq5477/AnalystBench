import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from alembic.config import Config

from alembic import command
from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationHarness,
    EvaluationMethod,
    EvaluationModel,
    EvaluationSubmission,
    EvaluationSubmissionCaseRun,
    EvaluationSubmissionMethodRun,
    EvaluationTarget,
    Job,
)
from analystbench.db.transaction import transaction
from analystbench.evaluation.submission import EvaluationSubmissionService
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


def test_concurrent_job_claims_share_latest_model_limit_across_harnesses(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    submission_id = str(uuid4())
    model_v1_id = str(uuid4())
    try:
        with transaction(worker.session_factory) as session:
            session.add_all(
                [
                    EvaluationModel(
                        id=model_v1_id,
                        model_key="shared-model",
                        name="Shared Model",
                        version_number=1,
                        argument="shared-model",
                        timeout_seconds=300,
                        concurrency_limit=2,
                        status="frozen",
                        content_hash=f"sha256:{uuid4().hex}",
                    ),
                    EvaluationModel(
                        id=str(uuid4()),
                        model_key="shared-model",
                        name="Shared Model",
                        version_number=2,
                        argument="shared-model",
                        timeout_seconds=123,
                        concurrency_limit=1,
                        status="frozen",
                        content_hash=f"sha256:{uuid4().hex}",
                    ),
                    EvaluationSubmission(
                        id=submission_id,
                        dataset_key="dataset",
                        run_timestamp="20260831000000",
                        manifest_json="{}",
                    ),
                ]
            )
            method_ids: list[str] = []
            for harness_key in ("harness-a", "harness-b"):
                harness_id = str(uuid4())
                method_id = str(uuid4())
                method_ids.append(method_id)
                session.add(
                    EvaluationHarness(
                        id=harness_id,
                        harness_key=harness_key,
                        name=harness_key,
                        version_number=1,
                        model_policy="required",
                        command_template="tool --model shared-model",
                        status="frozen",
                        content_hash=f"sha256:{uuid4().hex}",
                    )
                )
                session.add(
                    EvaluationMethod(
                        id=method_id,
                        method_key=f"{harness_key}@shared-model",
                        name=f"{harness_key}@shared-model",
                        version_number=1,
                        command_template="tool --model shared-model",
                        concurrency_limit=32,
                        status="frozen",
                        content_hash=f"sha256:{uuid4().hex}",
                    )
                )
                session.add(
                    EvaluationTarget(
                        id=str(uuid4()),
                        target_key=f"{harness_key}@shared-model",
                        version_number=1,
                        harness_id=harness_id,
                        model_id=model_v1_id,
                        model_argument="shared-model",
                        status="frozen",
                        content_hash=f"sha256:{uuid4().hex}",
                        materialized_method_id=method_id,
                    )
                )
                for case_index in range(2):
                    case_run_id = str(uuid4())
                    method_run_id = str(uuid4())
                    session.add(
                        EvaluationSubmissionCaseRun(
                            id=case_run_id,
                            submission_id=submission_id,
                            case_path=f"dataset/category/{harness_key}-{case_index}",
                            case_key=f"{harness_key}-{case_index}",
                            run_directory=str(tmp_path / f"{harness_key}-{case_index}"),
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

        with transaction(worker.session_factory) as session:
            assert EvaluationSubmissionService._effective_model_timeout(
                session, method_ids[0], 21600
            ) == 123

        with ThreadPoolExecutor(max_workers=4) as executor:
            claimed = list(
                executor.map(
                    lambda index: worker.jobs.claim(
                        f"model-worker-{index}", lease_seconds=30
                    ),
                    range(4),
                )
            )

        assert sum(job is not None for job in claimed) == 1
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


def test_skill_optimization_advance_is_single_flight_and_waits_for_submission(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    experiment_id = str(uuid4())
    other_experiment_id = str(uuid4())
    try:
        with transaction(worker.session_factory) as session:
            first = worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
            duplicate = worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
            assert duplicate.id == first.id

        running = worker.jobs.claim("optimizer-owner", lease_seconds=30)
        assert running is not None and running.id == first.id

        with transaction(worker.session_factory) as session:
            successor = worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
            duplicate_successor = worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
            assert duplicate_successor.id == successor.id
            worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": other_experiment_id},
            )
            session.add(
                EvaluationSubmission(
                    id=str(uuid4()),
                    dataset_key="kernel",
                    run_timestamp="20260812000000",
                    status="scoring",
                    purpose="skill_optimization",
                    optimization_context_json=json.dumps(
                        {"experiment_id": experiment_id}
                    ),
                    manifest_json="{}",
                )
            )

        other = worker.jobs.claim("other-owner", lease_seconds=30)
        assert other is not None
        assert worker.jobs.payload(other)["experiment_id"] == other_experiment_id
        assert worker.jobs.claim("third-owner", lease_seconds=30) is None

        assert worker.jobs.complete(running.id, "optimizer-owner") is True
        assert worker.jobs.complete(other.id, "other-owner") is True
        assert worker.jobs.claim("after-transition", lease_seconds=30) is None

        with transaction(worker.session_factory) as session:
            submission = session.query(EvaluationSubmission).one()
            submission.status = "completed"
            queued = session.query(Job).filter(Job.status == "queued").all()
            assert [job.id for job in queued] == [successor.id]

        resumed = worker.jobs.claim("resumed-owner", lease_seconds=30)
        assert resumed is not None and resumed.id == successor.id
    finally:
        worker.close()


def test_expired_skill_optimization_advance_is_reclaimed_before_successor(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    experiment_id = str(uuid4())
    try:
        with transaction(worker.session_factory) as session:
            first = worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
        running = worker.jobs.claim("crashed-owner", lease_seconds=30)
        assert running is not None and running.id == first.id
        with transaction(worker.session_factory) as session:
            successor = worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
            stored = session.get(Job, first.id)
            assert stored is not None
            stored.lease_until = datetime.now(UTC) - timedelta(seconds=1)

        recovered = worker.jobs.claim("recovery-owner", lease_seconds=30)
        assert recovered is not None and recovered.id == first.id
        assert worker.jobs.claim("competing-owner", lease_seconds=30) is None
        assert worker.jobs.complete(recovered.id, "recovery-owner") is True
        resumed = worker.jobs.claim("successor-owner", lease_seconds=30)
        assert resumed is not None and resumed.id == successor.id
    finally:
        worker.close()


def test_skill_optimization_transient_failure_retries_before_failing_experiment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    experiment_id = str(uuid4())
    calls = 0
    failed_experiments: list[str] = []

    def flaky_advance(received_id: str) -> None:
        nonlocal calls
        assert received_id == experiment_id
        calls += 1
        if calls == 1:
            raise RuntimeError("transient database contention")

    monkeypatch.setattr(worker.skill_optimization, "advance", flaky_advance)
    monkeypatch.setattr(
        worker.skill_optimization,
        "fail",
        lambda received_id, _exc: failed_experiments.append(received_id),
    )
    try:
        with transaction(worker.session_factory) as session:
            queued = worker.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
        assert worker.run_once() is True
        with transaction(worker.session_factory) as session:
            stored = session.get(Job, queued.id)
            assert stored is not None
            assert stored.status == "queued"
            assert stored.attempts == 1
        assert failed_experiments == []

        assert worker.run_once() is True
        with transaction(worker.session_factory) as session:
            stored = session.get(Job, queued.id)
            assert stored is not None
            assert stored.status == "succeeded"
            assert stored.attempts == 2
        assert calls == 2
        assert failed_experiments == []
    finally:
        worker.close()
