import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings
from analystbench.db.models import EvaluationSchedule, EvaluationScheduleRun
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.db.transaction import transaction
from analystbench.evaluation.schedule import (
    EvaluationScheduleService,
    latest_due_run,
    next_daily_run,
)
from analystbench.worker import LocalWorker


def migrated_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return Settings(
        database_url=database_url,
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )


def create_case(settings: Settings, *, with_logs: bool = True) -> Path:
    directory = (
        settings.results_formal_path / "kdiag" / "SYSTEM_DEADLOCK" / "chmod_hung"
    )
    directory.mkdir(parents=True)
    payload = {
        "case": {
            "case_key": "chmod_hung",
            "test_set": "kdiag",
            "category": "SYSTEM_DEADLOCK",
            "problem_statement": "分析日志。",
            "reference_answer": (
                "问题分类：SYSTEM_DEADLOCK\n"
                "问题根因：chmod 进程发生系统死锁\n"
                "证据1：chmod hung\n"
                "结论1：chmod 进程长期阻塞"
            ),
        },
        "eval_spec_draft": {
            "claims": [
                {
                    "id": "root",
                    "type": "root_cause",
                    "statement": "chmod 进程发生系统死锁",
                    "importance": "critical",
                    "weight": 100,
                    "quote": "chmod 进程发生系统死锁",
                },
                {
                    "id": "category",
                    "type": "classification",
                    "statement": "SYSTEM_DEADLOCK",
                    "importance": "high",
                    "weight": 20,
                    "quote": "问题分类：SYSTEM_DEADLOCK",
                },
                {
                    "id": "chain-1",
                    "type": "analysis_chain",
                    "statement": "chmod 进程长期阻塞",
                    "importance": "normal",
                    "weight": 60,
                    "evidence_keyword": "chmod hung",
                    "conclusion": "chmod 进程长期阻塞",
                    "quote": "证据1：chmod hung\n结论1：chmod 进程长期阻塞",
                },
            ],
            "scoring_strategy": {
                "mode": "root_category_chain",
                "root_cause_score": 100,
                "category_score": 20,
                "chain_total_score": 60,
            },
            "causal_edges": [],
            "forbidden_claims": [],
            "unresolved_items": [],
        },
    }
    (directory / "case.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    if with_logs:
        logs = directory / "logs"
        logs.mkdir()
        (logs / "log.txt").write_text("chmod hung", encoding="utf-8")
    return directory


def create_frozen_method(client: TestClient) -> dict:
    method = client.post(
        "/api/v1/evaluation-methods",
        json={
            "key": "nightly-script",
            "command_template": (
                f"{sys.executable} -c "
                "\"print('问题分类：SYSTEM_DEADLOCK\\n"
                "问题根因：chmod 进程发生系统死锁\\n"
                "证据1：chmod hung\\n结论1：chmod 进程长期阻塞')\""
            ),
        },
    ).json()
    assert client.post(f"/api/v1/evaluation-methods/{method['id']}:probe").json()[
        "probe"
    ]["available"]
    return client.post(f"/api/v1/evaluation-methods/{method['id']}:freeze").json()


def create_frozen_script_harness(client: TestClient) -> dict:
    harness = client.post(
        "/api/v1/evaluation-harnesses",
        json={
            "key": "script-only",
            "name": "Script Only",
            "command_template": (
                f"{sys.executable} -c "
                "\"print('问题分类：SYSTEM_DEADLOCK\\n"
                "问题根因：chmod 进程发生系统死锁\\n"
                "证据1：chmod hung\\n结论1：chmod 进程长期阻塞')\""
            ),
        },
    )
    assert harness.status_code == 201
    harness_id = harness.json()["id"]
    assert client.post(f"/api/v1/evaluation-harnesses/{harness_id}:probe").json()[
        "probe"
    ]["available"]
    frozen = client.post(f"/api/v1/evaluation-harnesses/{harness_id}:freeze")
    assert frozen.json()["status"] == "frozen"
    return frozen.json()


def test_daily_time_calculation_uses_schedule_timezone() -> None:
    before = datetime(2026, 7, 28, 14, 59, tzinfo=UTC)
    after = datetime(2026, 7, 28, 15, 1, tzinfo=UTC)

    assert next_daily_run("23:00", "Asia/Shanghai", after=before) == datetime(
        2026, 7, 28, 15, 0, tzinfo=UTC
    )
    assert next_daily_run("23:00", "Asia/Shanghai", after=after) == datetime(
        2026, 7, 29, 15, 0, tzinfo=UTC
    )
    assert latest_due_run("23:00", "Asia/Shanghai", now=after) == datetime(
        2026, 7, 28, 15, 0, tzinfo=UTC
    )


def test_run_now_creates_normal_submission_and_preserves_next_run(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case(settings)
    with TestClient(create_app(settings)) as client:
        method = create_frozen_method(client)
        created = client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "夜间回归",
                "dataset_key": "kdiag",
                "case_mode": "all_ready",
                "case_paths": [],
                "method_ids": [method["id"]],
                "judge_runner": "lexical",
                "timezone": "Asia/Shanghai",
                "local_time": "23:00",
                "enabled": True,
            },
        )
        assert created.status_code == 201
        schedule = created.json()
        next_run = schedule["next_run_at"]
        queued = client.post(
            f"/api/v1/evaluation-schedules/{schedule['id']}:run-now"
        )
        assert queued.status_code == 202
        assert queued.json()["status"] == "queued"
        assert (
            client.post(
                f"/api/v1/evaluation-schedules/{schedule['id']}:run-now"
            ).status_code
            == 409
        )

    worker = LocalWorker(settings)
    try:
        assert worker.run_once() is True
        assert worker.run_once() is True
        assert worker.run_once() is True
        assert worker.run_once() is False
    finally:
        worker.close()

    with TestClient(create_app(settings)) as client:
        current = client.get(
            f"/api/v1/evaluation-schedules/{schedule['id']}"
        ).json()
        runs = client.get(
            f"/api/v1/evaluation-schedules/{schedule['id']}/runs"
        ).json()
        assert current["next_run_at"] == next_run
        assert runs[0]["status"] == "completed"
        assert runs[0]["submission_id"]
        assert (
            client.delete(
                f"/api/v1/evaluation-schedules/{schedule['id']}"
            ).status_code
            == 409
        )
    timestamp = runs[0]["submission_timestamp"]
    result_path = case_directory / "runs" / timestamp / "result.json"
    assert result_path.is_file()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["generation"]["methods"][0]["duration_ms"] >= 0
    with TestClient(create_app(settings)) as client:
        deleted = client.delete(
            f"/api/v1/evaluation-methods/{method['id']}"
        )
        assert deleted.status_code == 200
        assert deleted.json()["submissions_deleted"] == 1
        assert deleted.json()["schedule_runs_deleted"] == 1
        assert deleted.json()["schedules_deleted"] == 1
        assert (
            client.get(
                f"/api/v1/evaluation-schedules/{schedule['id']}"
            ).status_code
            == 404
        )
    assert not (case_directory / "runs" / timestamp).exists()


def test_run_now_accepts_script_only_target(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case(settings)
    with TestClient(create_app(settings)) as client:
        harness = create_frozen_script_harness(client)
        created = client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "脚本基线",
                "dataset_key": "kdiag",
                "case_mode": "all_ready",
                "target_selections": [
                    {"harness_id": harness["id"], "model_id": None}
                ],
                "judge_runner": "lexical",
                "timezone": "Asia/Shanghai",
                "local_time": "23:00",
                "enabled": False,
            },
        )
        assert created.status_code == 201
        schedule = created.json()
        assert len(schedule["target_ids"]) == 1
        target_id = schedule["target_ids"][0]
        assert schedule["method_ids"] == []
        assert schedule["targets"][0]["model"] is None
        assert schedule["target_selections"] == [
            {"harness_id": harness["id"], "model_id": None}
        ]
        assert (
            client.post(f"/api/v1/evaluation-schedules/{schedule['id']}:run-now").status_code
            == 202
        )

    worker = LocalWorker(settings)
    try:
        while worker.run_once():
            pass
    finally:
        worker.close()

    with TestClient(create_app(settings)) as client:
        run = client.get(f"/api/v1/evaluation-schedules/{schedule['id']}/runs").json()[0]
        assert run["status"] == "completed"
        submission = client.get(f"/api/v1/evaluation-submissions/{run['submission_id']}").json()
        assert submission["target_ids"] == [target_id]
    result = json.loads(
        (case_directory / "runs" / run["submission_timestamp"] / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["generation"]["targets"][0]["target_key"] == "script-only"


def test_due_scan_backfills_only_latest_occurrence(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    create_case(settings)
    with TestClient(create_app(settings)) as client:
        method = create_frozen_method(client)
        schedule = client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "补跑测试",
                "dataset_key": "kdiag",
                "case_mode": "all_ready",
                "method_ids": [method["id"]],
                "judge_runner": "lexical",
                "timezone": "Asia/Shanghai",
                "local_time": "23:00",
            },
        ).json()

    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    now = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
    with transaction(factory) as session:
        stored = session.get(EvaluationSchedule, schedule["id"])
        assert stored is not None
        stored.next_run_at = now - timedelta(days=3)
    service = EvaluationScheduleService(factory, settings)
    assert service.enqueue_due(now=now) == 1
    assert service.enqueue_due(now=now) == 0
    with transaction(factory) as session:
        runs = list(
            session.scalars(
                select(EvaluationScheduleRun).where(
                    EvaluationScheduleRun.schedule_id == schedule["id"]
                )
            )
        )
        stored = session.get(EvaluationSchedule, schedule["id"])
        assert len(runs) == 1
        assert runs[0].trigger_type == "catch_up"
        assert runs[0].scheduled_for == datetime(2026, 8, 1, 15, 0)
        assert stored is not None
        assert stored.next_run_at == datetime(2026, 8, 2, 15, 0)
    engine.dispose()


def test_concurrent_due_scans_create_one_trigger(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    create_case(settings)
    with TestClient(create_app(settings)) as client:
        method = create_frozen_method(client)
        schedule = client.post(
            "/api/v1/evaluation-schedules",
            json={
                "name": "并发扫描",
                "dataset_key": "kdiag",
                "case_mode": "all_ready",
                "method_ids": [method["id"]],
                "judge_runner": "lexical",
                "timezone": "Asia/Shanghai",
                "local_time": "23:00",
            },
        ).json()

    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    now = datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
    with transaction(factory) as session:
        stored = session.get(EvaluationSchedule, schedule["id"])
        assert stored is not None
        stored.next_run_at = now - timedelta(days=1)

    barrier = Barrier(2)

    def scan() -> int:
        barrier.wait()
        return EvaluationScheduleService(factory, settings).enqueue_due(now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: scan(), range(2)))

    assert sorted(results) == [0, 1]
    with transaction(factory) as session:
        runs = list(
            session.scalars(
                select(EvaluationScheduleRun).where(
                    EvaluationScheduleRun.schedule_id == schedule["id"]
                )
            )
        )
        assert len(runs) == 1
    engine.dispose()
