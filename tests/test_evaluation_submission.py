import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings
from analystbench.db.models import EvaluationMethod
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.db.transaction import transaction
from analystbench.evaluation.submission import (
    EvaluationSubmissionService,
    _candidate_name,
    _scoring_error_payload,
)
from analystbench.evaluation.target import (
    EvaluationHarnessService,
    EvaluationModelService,
    EvaluationTargetService,
)
from analystbench.execution.runner import AgentRunnerError
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


def test_skill_variant_candidate_name_never_exposes_internal_key() -> None:
    assert _candidate_name("sv-1234567890abcdef", "codeagent-native@glm-5.2") == (
        "codeagent-native@glm-5.2"
    )
    assert _candidate_name("script", "Script") == "script"


def case_payload() -> dict:
    reference = (
        "问题分类：SYSTEM_DEADLOCK\n"
        "问题根因：chmod 进程发生系统死锁\n"
        "证据1：chmod hung\n"
        "结论1：chmod 进程长期阻塞"
    )
    return {
        "case": {
            "case_key": "chmod_hung",
            "test_set": "kdiag",
            "category": "SYSTEM_DEADLOCK",
            "problem_statement": "分析日志。",
            "reference_answer": reference,
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


def create_case_directory(settings: Settings) -> Path:
    case_directory = settings.results_formal_path / "kdiag" / "SYSTEM_DEADLOCK" / "chmod_hung"
    case_directory.mkdir(parents=True)
    (case_directory / "case.json").write_text(
        json.dumps(case_payload(), ensure_ascii=False), encoding="utf-8"
    )
    return case_directory


def test_model_key_accepts_safe_square_brackets(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    engine = create_database_engine(settings)
    try:
        model = EvaluationModelService(create_session_factory(engine)).create(
            model_key="GLM-5.2[1m]",
            name="GLM-5.2[1m]",
            argument="GLM-5.2[1m]",
        )
    finally:
        engine.dispose()

    assert model.model_key == "GLM-5.2[1m]"
    assert model.name == "GLM-5.2[1m]"
    assert model.argument == "GLM-5.2[1m]"


def test_method_and_model_allow_six_hour_timeout(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        default_method = client.post(
            "/api/v1/evaluation-methods",
            json={"key": "default-timeout", "command_template": sys.executable},
        )
        max_method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "six-hour-method",
                "command_template": sys.executable,
                "timeout_seconds": 21600,
            },
        )
        invalid_method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "too-long-method",
                "command_template": sys.executable,
                "timeout_seconds": 21601,
            },
        )
        default_model = client.post(
            "/api/v1/evaluation-models",
            json={"key": "default-model"},
        )
        max_model = client.post(
            "/api/v1/evaluation-models",
            json={
                "key": "six-hour-model",
                "timeout_seconds": 21600,
            },
        )
        invalid_model = client.post(
            "/api/v1/evaluation-models",
            json={
                "key": "too-long-model",
                "timeout_seconds": 21601,
            },
        )

    assert default_method.status_code == 201
    assert default_method.json()["timeout_seconds"] == 21600
    assert max_method.status_code == 201
    assert invalid_method.status_code == 422
    assert default_model.status_code == 201
    assert default_model.json()["timeout_seconds"] == 21600
    assert default_model.json()["concurrency_limit"] == 1
    assert max_model.status_code == 201
    assert invalid_model.status_code == 422


def test_list_targets_handles_multiple_targets_for_same_harness(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        harness = EvaluationHarnessService(session_factory, settings).create(
            harness_key="shared-harness",
            name="Shared Harness",
            family=None,
            model_policy="required",
            command_template=f'{sys.executable} -c "print(1)" {{model}} {{input}}',
        )
        models = EvaluationModelService(session_factory)
        targets = EvaluationTargetService(session_factory, settings)
        for model_key in ("model-a", "model-b"):
            model = models.create(
                model_key=model_key,
                name=model_key,
                argument=model_key,
            )
            targets.create(harness_id=harness.id, model_id=model.id)
        views = targets.list_views()
    finally:
        engine.dispose()

    assert {item["key"] for item in views} == {
        "shared-harness@model-a",
        "shared-harness@model-b",
    }


def test_delete_local_case_removes_inputs_and_preserves_history(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir()
    (logs_directory / "main.log").write_text("chmod hung", encoding="utf-8")
    run_directory = case_directory / "runs" / "20260820120000"
    run_directory.mkdir(parents=True)
    (run_directory / "result.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )

    with TestClient(create_app(settings)) as client:
        deleted = client.delete(
            "/api/v1/local-cases/kdiag/SYSTEM_DEADLOCK/chmod_hung"
        )

        assert deleted.status_code == 200
        assert deleted.json() == {
            "case_files_deleted": 1,
            "log_files_deleted": 1,
            "historical_results_preserved": 1,
        }
        assert client.get("/api/v1/local-cases/tree").json() == []
        assert (
            client.get(
                "/api/v1/local-cases/kdiag/SYSTEM_DEADLOCK/chmod_hung"
            ).status_code
            == 404
        )

    assert not (case_directory / "case.json").exists()
    assert not logs_directory.exists()
    assert (run_directory / "result.json").is_file()


def test_delete_local_case_rejects_active_submission(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir()
    (logs_directory / "main.log").write_text("chmod hung", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "case-delete-script",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        method_id = method["id"]
        assert client.post(f"/api/v1/evaluation-methods/{method_id}:probe").status_code == 200
        assert client.post(f"/api/v1/evaluation-methods/{method_id}:freeze").status_code == 200
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        )
        assert submission.status_code == 202

        blocked = client.delete(
            "/api/v1/local-cases/kdiag/SYSTEM_DEADLOCK/chmod_hung"
        )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "local_case_delete_running"
    assert (case_directory / "case.json").is_file()
    assert (logs_directory / "main.log").is_file()


def test_scoring_error_payload_keeps_bounded_runner_output() -> None:
    stdout = "discarded-stdout-" + "x" * 2000
    stderr = b"discarded-stderr-" + b"y" * 2000
    payload = _scoring_error_payload(
        AgentRunnerError("agent_exit_nonzero", "judge failed", stdout, stderr)
    )

    assert payload == {
        "code": "scoring_failed",
        "message": "judge failed",
        "cause_code": "agent_exit_nonzero",
        "stdout_tail": "x" * 2000,
        "stderr_tail": "y" * 2000,
    }


def test_scoring_failure_persists_runner_output_tails(
    tmp_path: Path, monkeypatch
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir()
    (logs_directory / "log.txt").write_text("chmod hung", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "scoring-error-script",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        client.post(f"/api/v1/evaluation-methods/{method['id']}:probe")
        client.post(f"/api/v1/evaluation-methods/{method['id']}:freeze")
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method["id"]],
                "judge_runner": "lexical",
            },
        ).json()

    def fail_scoring(*_args: object, **_kwargs: object) -> None:
        raise AgentRunnerError(
            "agent_exit_nonzero",
            "judge failed",
            "discarded-stdout-" + "x" * 2000,
            "discarded-stderr-" + "y" * 2000,
        )

    worker = LocalWorker(settings)
    try:
        assert worker.run_once() is True
        monkeypatch.setattr(
            "analystbench.evaluation.submission.evaluate_direct", fail_scoring
        )
        assert worker.run_once() is True
    finally:
        worker.close()

    with TestClient(create_app(settings)) as client:
        case_run = client.get(
            f"/api/v1/evaluation-submissions/{submission['id']}/case-runs"
        ).json()[0]

    assert case_run["scoring_status"] == "failed"
    assert case_run["error"] == {
        "code": "scoring_failed",
        "message": "judge failed",
        "cause_code": "agent_exit_nonzero",
        "stdout_tail": "x" * 2000,
        "stderr_tail": "y" * 2000,
    }


def test_worker_executes_method_runs_in_parallel_up_to_method_limit(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    settings.worker_concurrency_limit = 2
    settings.worker_poll_interval_seconds = 0.02
    first_case = create_case_directory(settings)
    second_case = settings.results_formal_path / "kdiag" / "SYSTEM_DEADLOCK" / "chmod_hung_2"
    second_case.mkdir(parents=True)
    second_payload = case_payload()
    second_payload["case"]["case_key"] = "chmod_hung_2"
    (second_case / "case.json").write_text(
        json.dumps(second_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    for case_directory in (first_case, second_case):
        logs = case_directory / "logs"
        logs.mkdir()
        (logs / "log.txt").write_text("chmod hung", encoding="utf-8")

    tool_directory = tmp_path / "tools"
    tool_directory.mkdir()
    timing_path = tool_directory / "timings.log"
    (tool_directory / "report.py").write_text(
        "from pathlib import Path\n"
        "import time\n"
        "start = time.monotonic()\n"
        "time.sleep(0.5)\n"
        "end = time.monotonic()\n"
        "timing = Path(__file__).parent / 'timings.log'\n"
        "with timing.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(f'{start},{end}\\n')\n"
        "print('问题分类：SYSTEM_DEADLOCK')\n"
        "print('问题根因：chmod 进程发生系统死锁')\n"
        "print('证据：chmod hung')\n"
        "print('结论：chmod 进程长期阻塞')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "parallel-script",
                "tool_dir": str(tool_directory),
                "command_template": f"{sys.executable} {{tool_dir}}/report.py {{input}}",
                "concurrency_limit": 2,
            },
        ).json()
        client.post(f"/api/v1/evaluation-methods/{method['id']}:probe")
        client.post(f"/api/v1/evaluation-methods/{method['id']}:freeze")
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method["id"]],
                "judge_runner": "lexical",
            },
        ).json()

    worker = LocalWorker(settings)
    stop = threading.Event()
    worker_thread = threading.Thread(target=worker.serve, args=(stop,))
    worker_thread.start()
    try:
        deadline = time.monotonic() + 10
        status = "queued"
        with TestClient(create_app(settings)) as client:
            while time.monotonic() < deadline:
                status = client.get(
                    f"/api/v1/evaluation-submissions/{submission['id']}"
                ).json()["status"]
                if status == "completed":
                    break
                time.sleep(0.05)
        assert status == "completed"
    finally:
        stop.set()
        worker_thread.join(timeout=5)
        worker.close()

    intervals = [
        tuple(float(value) for value in line.split(","))
        for line in timing_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(intervals) == 2
    assert max(start for start, _ in intervals) < min(end for _, end in intervals)


def test_model_concurrency_is_global_across_harnesses_and_versions(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    settings.worker_concurrency_limit = 2
    settings.worker_poll_interval_seconds = 0.02
    first_case = create_case_directory(settings)
    second_case = settings.results_formal_path / "kdiag" / "SYSTEM_DEADLOCK" / "chmod_hung_2"
    second_case.mkdir(parents=True)
    second_payload = case_payload()
    second_payload["case"]["case_key"] = "chmod_hung_2"
    (second_case / "case.json").write_text(
        json.dumps(second_payload, ensure_ascii=False), encoding="utf-8"
    )
    for case_directory in (first_case, second_case):
        logs = case_directory / "logs"
        logs.mkdir()
        (logs / "log.txt").write_text("chmod hung", encoding="utf-8")

    tool_directory = tmp_path / "target-tools"
    tool_directory.mkdir()
    timing_path = tool_directory / "timings.log"
    (tool_directory / "report.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "import time\n"
        "start = time.monotonic()\n"
        "time.sleep(0.3)\n"
        "end = time.monotonic()\n"
        "with (Path(__file__).parent / 'timings.log').open('a', encoding='utf-8') as stream:\n"
        "    stream.write(f'{sys.argv[2]},{start},{end}\\n')\n"
        "print('问题分类：SYSTEM_DEADLOCK')\n"
        "print('问题根因：chmod 进程发生系统死锁')\n"
        "print('证据：chmod hung')\n"
        "print('结论：chmod 进程长期阻塞')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        model = client.post(
            "/api/v1/evaluation-models",
            json={"key": "glm-5.1", "argument": "glm5.1", "concurrency_limit": 2},
        ).json()
        target_ids = []
        method_ids = []
        for harness_key in ("claude-skill", "opencode-skill"):
            harness = client.post(
                "/api/v1/evaluation-harnesses",
                json={
                    "key": harness_key,
                    "model_policy": "required",
                    "tool_dir": str(tool_directory),
                    "command_template": (
                        f"{sys.executable} {{tool_dir}}/report.py --model {{model}} {{input}}"
                    ),
                },
            ).json()
            client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:probe")
            client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:freeze")
            target = client.post(
                "/api/v1/evaluation-targets",
                json={"harness_id": harness["id"], "model_id": model["id"]},
            ).json()
            client.post(f"/api/v1/evaluation-targets/{target['id']}:probe")
            frozen_target = client.post(
                f"/api/v1/evaluation-targets/{target['id']}:freeze"
            ).json()
            target_ids.append(target["id"])
            method_ids.append(frozen_target["materialized_method_id"])
        revised_model = client.post(
            f"/api/v1/evaluation-models/{model['id']}:revise",
            json={"timeout_seconds": 123, "concurrency_limit": 1},
        ).json()
        assert revised_model["version"] == 2
        engine = create_database_engine(settings)
        try:
            with transaction(create_session_factory(engine)) as session:
                assert EvaluationSubmissionService._effective_model_timeout(
                    session, method_ids[0], 21600
                ) == 123
        finally:
            engine.dispose()
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={"dataset_key": "kdiag", "target_ids": target_ids, "judge_runner": "lexical"},
        ).json()

    worker = LocalWorker(settings)
    stop = threading.Event()
    worker_thread = threading.Thread(target=worker.serve, args=(stop,))
    worker_thread.start()
    try:
        deadline = time.monotonic() + 15
        status = "queued"
        with TestClient(create_app(settings)) as client:
            while time.monotonic() < deadline:
                status = client.get(
                    f"/api/v1/evaluation-submissions/{submission['id']}"
                ).json()["status"]
                if status == "completed":
                    break
                time.sleep(0.05)
        assert status == "completed"
    finally:
        stop.set()
        worker_thread.join(timeout=5)
        worker.close()

    intervals = sorted(
        tuple(float(value) for value in line.split(",")[1:])
        for line in timing_path.read_text(encoding="utf-8").splitlines()
    )
    assert len(intervals) == 4
    assert all(
        next_start >= previous_end
        for (_, previous_end), (next_start, _) in zip(
            intervals, intervals[1:], strict=False
        )
    )


def test_submission_requires_logs_and_runs_isolated_command_then_scores(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    tool_directory = tmp_path / "tools"
    tool_directory.mkdir()
    (tool_directory / "report.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "assert not (Path.cwd() / 'case.json').exists()\n"
        "assert not (Path.cwd() / 'runs').exists()\n"
        "source = Path(sys.argv[1])\n"
        "assert source.parent.name == 'logs'\n"
        "print('问题分类：SYSTEM_DEADLOCK')\n"
        "print('问题根因：chmod 进程发生系统死锁')\n"
        "print(source.read_text(encoding='utf-8'))\n"
        "print('结论：chmod 进程长期阻塞')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "script",
                "name": "Python Script",
                "tool_dir": str(tool_directory),
                "command_template": (f"{sys.executable} {{tool_dir}}/report.py {{input}}"),
            },
        )
        assert method.status_code == 201
        method_id = method.json()["id"]
        revised = client.post(
            f"/api/v1/evaluation-methods/{method_id}:revise",
            json={"name": "Python Script Revised"},
        )
        assert revised.status_code == 200
        assert revised.json()["version"] == 2
        assert revised.json()["name"] == "Python Script Revised"
        method_id = revised.json()["id"]
        assert client.post(f"/api/v1/evaluation-methods/{method_id}:probe").json()["probe"][
            "available"
        ]
        assert (
            client.post(f"/api/v1/evaluation-methods/{method_id}:freeze").json()["status"]
            == "frozen"
        )

        blocked = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        )
        assert blocked.status_code == 400
        assert blocked.json()["error"]["code"] == "case_logs_missing"

        uploaded = client.post(
            "/api/v1/local-cases/kdiag/SYSTEM_DEADLOCK/chmod_hung/logs",
            files={"files": ("log.txt", b"chmod hung", "text/plain")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["primary_log"] == "log.txt"
        assert uploaded.json()["submission_ready"] is True

        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        )
        assert submission.status_code == 202
        submission_id = submission.json()["id"]
        timestamp = submission.json()["timestamp"]
        assert len(timestamp) == 14

    worker = LocalWorker(settings)
    try:
        assert worker.run_once() is True
        assert worker.run_once() is True
        assert worker.run_once() is False
    finally:
        worker.close()

    run_directory = case_directory / "runs" / timestamp
    assert (run_directory / "inputs" / "log.txt").read_text() == "chmod hung"
    assert "系统死锁" in (run_directory / "script.md").read_text(encoding="utf-8")
    result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert result["submission_id"] == submission_id
    assert result["reports"][0]["candidate_name"] == "script"
    generation = result["generation"]["methods"]
    assert len(generation) == 1
    assert generation[0]["key"] == "script"
    assert generation[0]["started_at"].endswith("Z")
    assert generation[0]["finished_at"].endswith("Z")
    assert generation[0]["duration_ms"] >= 0
    run_state = json.loads((run_directory / "run.json").read_text(encoding="utf-8"))
    assert run_state["methods"][0]["duration_ms"] == generation[0]["duration_ms"]
    assert "生成耗时" in (run_directory / "result.md").read_text(encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        current = client.get(f"/api/v1/evaluation-submissions/{submission_id}").json()
        assert current["status"] == "completed"
        direct_results = client.get("/api/v1/direct-results").json()
        generated = next(item for item in direct_results if item["timestamp"] == timestamp)
        assert generated["id"] == (f"kdiag/SYSTEM_DEADLOCK/chmod_hung/runs/{timestamp}")
        completed_cases = client.get(
            f"/api/v1/evaluation-submissions/{submission_id}/case-runs"
        ).json()
        completed_method = completed_cases[0]["methods"][0]
        assert completed_method["started_at"].endswith("Z")
        assert completed_method["finished_at"].endswith("Z")
        assert completed_method["duration_ms"] == generation[0]["duration_ms"]
        artifacts = client.get(
            f"/api/v1/evaluation-method-runs/{completed_method['id']}/artifacts"
        )
        assert artifacts.status_code == 200
        assert "系统死锁" in artifacts.json()["stdout"]
        assert artifacts.json()["stderr"] == ""
        assert artifacts.json()["duration_ms"] == generation[0]["duration_ms"]
        queued = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        ).json()
        blocked_delete = client.delete(
            f"/api/v1/evaluation-submissions/{queued['id']}"
        )
        assert blocked_delete.status_code == 409
        assert (
            blocked_delete.json()["error"]["code"]
            == "evaluation_submission_delete_running"
        )
        cancelled = client.post(f"/api/v1/evaluation-submissions/{queued['id']}:cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        cancelled_cases = client.get(
            f"/api/v1/evaluation-submissions/{queued['id']}/case-runs"
        ).json()
        assert cancelled_cases[0]["status"] == "cancelled"
        assert cancelled_cases[0]["methods"][0]["status"] == "cancelled"
        cancelled_run = case_directory / "runs" / queued["timestamp"]
        assert not (cancelled_run / "script.md").exists()
        deleted = client.delete(f"/api/v1/evaluation-methods/{method_id}")
        assert deleted.status_code == 200
        assert deleted.json()["submissions_deleted"] == 2
        assert deleted.json()["local_directories_deleted"] == 2
        assert client.get(f"/api/v1/evaluation-methods/{method_id}").status_code == 404
        assert (
            client.get(f"/api/v1/evaluation-submissions/{submission_id}").status_code
            == 404
        )
        assert (
            client.get(f"/api/v1/evaluation-submissions/{queued['id']}").status_code
            == 404
        )
        assert not run_directory.exists()
        assert not cancelled_run.exists()
        assert all(
            item["timestamp"] not in {timestamp, queued["timestamp"]}
            for item in client.get("/api/v1/direct-results").json()
        )


def test_frozen_method_can_be_revised_as_new_draft(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "script",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        client.post(f"/api/v1/evaluation-methods/{method['id']}:probe")
        frozen = client.post(
            f"/api/v1/evaluation-methods/{method['id']}:freeze"
        ).json()
        revised = client.post(
            f"/api/v1/evaluation-methods/{frozen['id']}:revise",
            json={
                "command_template": f'{sys.executable} -c "print(2)"',
                "concurrency_limit": 3,
            },
        )

    assert revised.status_code == 200
    assert revised.json()["version"] == 2
    assert revised.json()["status"] == "draft"
    assert revised.json()["concurrency_limit"] == 3
    assert "print(2)" in revised.json()["command_template"]


def test_revising_harness_infers_model_policy_from_changed_command(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        harness = client.post(
            "/api/v1/evaluation-harnesses",
            json={
                "key": "claude",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:probe")
        frozen = client.post(
            f"/api/v1/evaluation-harnesses/{harness['id']}:freeze"
        ).json()

        revised = client.post(
            f"/api/v1/evaluation-harnesses/{frozen['id']}:revise",
            json={
                "command_template": (
                    f'{sys.executable} -c "print(1)" --model {{model}}'
                )
            },
        )

    assert revised.status_code == 200
    assert revised.json()["version"] == 2
    assert revised.json()["status"] == "draft"
    assert revised.json()["model_policy"] == "required"


def test_harness_versions_skill_base_directory_and_checks_it_during_probe(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    skill_base_dir = tmp_path / ".claude"
    skill_base_dir.mkdir()
    with TestClient(create_app(settings)) as client:
        harness = client.post(
            "/api/v1/evaluation-harnesses",
            json={
                "key": "claude",
                "skill_base_dir": str(skill_base_dir),
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        probed = client.post(
            f"/api/v1/evaluation-harnesses/{harness['id']}:probe"
        ).json()
        frozen = client.post(
            f"/api/v1/evaluation-harnesses/{harness['id']}:freeze"
        ).json()
        revised = client.post(
            f"/api/v1/evaluation-harnesses/{frozen['id']}:revise",
            json={"command_template": f'{sys.executable} -c "print(2)"'},
        ).json()

    assert harness["skill_base_dir"] == str(skill_base_dir.resolve())
    assert probed["probe"]["skill_base_dir_ok"] is True
    assert probed["probe"]["available"] is True
    assert revised["version"] == 2
    assert revised["skill_base_dir"] == str(skill_base_dir.resolve())
    assert revised["content_hash"] != frozen["content_hash"]


def test_target_freeze_reuses_and_refreshes_existing_materialized_method(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    stale_hash = f"sha256:{'a' * 64}"
    changed_hash = f"sha256:{'b' * 64}"
    existing_method_id = str(uuid4())
    try:
        with TestClient(create_app(settings)) as client:
            harness = client.post(
                "/api/v1/evaluation-harnesses",
                json={
                    "key": "script-only",
                    "command_template": f'{sys.executable} -c "print(1)"',
                },
            ).json()
            client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:probe")
            frozen_harness = client.post(
                f"/api/v1/evaluation-harnesses/{harness['id']}:freeze"
            ).json()
            target = client.post(
                "/api/v1/evaluation-targets",
                json={"harness_id": frozen_harness["id"]},
            ).json()
            client.post(f"/api/v1/evaluation-targets/{target['id']}:probe")

            with transaction(session_factory) as session:
                session.add(
                    EvaluationMethod(
                        id=existing_method_id,
                        method_key=target["key"],
                        name="stale",
                        version_number=target["version"],
                        tool_dir=None,
                        command_template="stale",
                        timeout_seconds=1,
                        max_output_bytes=1024,
                        concurrency_limit=1,
                        status="draft",
                        content_hash=stale_hash,
                        last_probe_json="{}",
                    )
                )

            first = client.post(
                f"/api/v1/evaluation-targets/{target['id']}:freeze"
            )
            assert first.status_code == 200
            assert first.json()["materialized_method_id"] == existing_method_id

            with transaction(session_factory) as session:
                method = session.get(EvaluationMethod, existing_method_id)
                assert method is not None
                assert method.command_template == frozen_harness["command_template"]
                assert method.content_hash != stale_hash
                method.command_template = "changed-after-freeze"
                method.content_hash = changed_hash

            second = client.post(
                f"/api/v1/evaluation-targets/{target['id']}:freeze"
            )
            assert second.status_code == 200
            assert second.json()["materialized_method_id"] == existing_method_id

        with transaction(session_factory) as session:
            method = session.get(EvaluationMethod, existing_method_id)
            assert method is not None
            assert method.command_template == frozen_harness["command_template"]
            assert method.content_hash not in {stale_hash, changed_hash}
            count = session.scalar(
                select(func.count(EvaluationMethod.id)).where(
                    EvaluationMethod.method_key == target["key"],
                    EvaluationMethod.version_number == target["version"],
                )
            )
            assert count == 1
    finally:
        engine.dispose()


def test_concurrent_target_freeze_materializes_one_method(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        harness = client.post(
            "/api/v1/evaluation-harnesses",
            json={
                "key": "script-only",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:probe")
        client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:freeze")
        target = client.post(
            "/api/v1/evaluation-targets",
            json={"harness_id": harness["id"]},
        ).json()
        client.post(f"/api/v1/evaluation-targets/{target['id']}:probe")

    barrier = threading.Barrier(2)

    def freeze() -> tuple[int, str | None]:
        with TestClient(create_app(settings)) as client:
            barrier.wait()
            response = client.post(
                f"/api/v1/evaluation-targets/{target['id']}:freeze"
            )
            return response.status_code, response.json().get("materialized_method_id")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: freeze(), range(2)))

    assert [status for status, _ in results] == [200, 200]
    assert results[0][1] == results[1][1]

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        with transaction(session_factory) as session:
            count = session.scalar(
                select(func.count(EvaluationMethod.id)).where(
                    EvaluationMethod.method_key == target["key"],
                    EvaluationMethod.version_number == target["version"],
                )
            )
        assert count == 1
    finally:
        engine.dispose()


def test_target_uses_harness_model_argument_and_exposes_comparison(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir()
    (logs_directory / "log.txt").write_text("chmod hung", encoding="utf-8")
    tool_directory = tmp_path / "target-tool"
    tool_directory.mkdir()
    report_script = tool_directory / "report.py"
    report_script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:3] == ['--model', 'glm5.1']\n"
        "print(Path(sys.argv[3]).read_text(encoding='utf-8'))\n"
        "print('问题分类：SYSTEM_DEADLOCK')\n"
        "print('问题根因：chmod 进程发生系统死锁')\n"
        "print('结论：chmod 进程长期阻塞')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        harness = client.post(
            "/api/v1/evaluation-harnesses",
            json={
                "key": "claude-native",
                "command_template": (
                    f"{sys.executable} {report_script} --model {{model}} {{input}}"
                ),
                "concurrency_limit": 1,
            },
        )
        assert harness.status_code == 201
        harness_id = harness.json()["id"]
        assert client.post(f"/api/v1/evaluation-harnesses/{harness_id}:probe").json()[
            "probe"
        ]["available"]
        assert client.post(f"/api/v1/evaluation-harnesses/{harness_id}:freeze").json()[
            "status"
        ] == "frozen"
        model = client.post(
            "/api/v1/evaluation-models",
            json={"key": "glm5.1"},
        )
        assert model.status_code == 201
        assert client.get("/api/v1/evaluation-methods").json() == []

        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "target_selections": [
                    {"harness_id": harness_id, "model_id": model.json()["id"]}
                ],
                "judge_runner": "lexical",
            },
        )
        assert submission.status_code == 202
        submission_id = submission.json()["id"]
        assert len(submission.json()["target_ids"]) == 1
        frozen_target = submission.json()["targets"][0]
        assert frozen_target["key"] == "claude-native@glm5.1"
        assert frozen_target["materialized_method_id"]
        generated_targets = client.get("/api/v1/evaluation-targets").json()
        assert generated_targets[0]["id"] == frozen_target["id"]
        assert generated_targets[0]["status"] == "frozen"

    worker = LocalWorker(settings)
    try:
        while worker.run_once():
            pass
    finally:
        worker.close()

    timestamp = submission.json()["timestamp"]
    result = json.loads(
        (case_directory / "runs" / timestamp / "result.json").read_text(encoding="utf-8")
    )
    generation = result["generation"]
    assert generation["targets"][0]["target_key"] == "claude-native@glm5.1"
    assert generation["targets"][0]["model"]["argument"] == "glm5.1"

    with TestClient(create_app(settings)) as client:
        case_run = client.get(
            f"/api/v1/evaluation-submissions/{submission_id}/case-runs"
        ).json()[0]
        artifact = client.get(
            f"/api/v1/evaluation-method-runs/{case_run['methods'][0]['id']}/artifacts"
        ).json()
        assert artifact["status"] == "succeeded"
        assert "SYSTEM_DEADLOCK" in artifact["stdout"]
        comparison = client.get(
            f"/api/v1/evaluation-submissions/{submission_id}/target-comparison"
        )
        assert comparison.status_code == 200
        assert comparison.json()["targets"][0]["target"]["key"] == "claude-native@glm5.1"
        assert comparison.json()["targets"][0]["generation_success_rate"] == 1.0
        assert comparison.json()["by_harness"] == []
        assert comparison.json()["by_model"] == []
        protected = client.delete(
            f"/api/v1/evaluation-methods/{frozen_target['materialized_method_id']}"
        )
        assert protected.status_code == 409
        assert protected.json()["error"]["code"] == "evaluation_method_managed_by_target"


def test_completed_submission_delete_removes_runs_but_keeps_method_and_case(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir()
    (logs_directory / "log.txt").write_text("chmod hung", encoding="utf-8")
    tool_directory = tmp_path / "tools"
    tool_directory.mkdir()
    (tool_directory / "report.py").write_text(
        "print('问题根因：chmod 进程发生系统死锁')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "delete-script",
                "tool_dir": str(tool_directory),
                "command_template": f"{sys.executable} {{tool_dir}}/report.py",
            },
        ).json()
        method_id = method["id"]
        client.post(f"/api/v1/evaluation-methods/{method_id}:probe")
        client.post(f"/api/v1/evaluation-methods/{method_id}:freeze")
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        ).json()

    worker = LocalWorker(settings)
    try:
        assert worker.run_once() is True
        assert worker.run_once() is True
    finally:
        worker.close()

    run_directory = case_directory / "runs" / submission["timestamp"]
    assert (run_directory / "result.json").is_file()
    with TestClient(create_app(settings)) as client:
        deleted = client.delete(
            f"/api/v1/evaluation-submissions/{submission['id']}"
        )
        assert deleted.status_code == 200
        assert deleted.json() == {
            "submissions_deleted": 1,
            "case_runs_deleted": 1,
            "method_runs_deleted": 1,
            "local_directories_deleted": 1,
        }
        assert (
            client.get(f"/api/v1/evaluation-submissions/{submission['id']}").status_code
            == 404
        )
        assert client.get(f"/api/v1/evaluation-methods/{method_id}").status_code == 200
    assert not run_directory.exists()
    assert (case_directory / "case.json").is_file()
    assert (logs_directory / "log.txt").is_file()


def test_timed_out_method_keeps_terminal_timing_in_api_and_result(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir()
    (logs_directory / "log.txt").write_text("chmod hung", encoding="utf-8")
    tool_directory = tmp_path / "tools"
    tool_directory.mkdir()
    (tool_directory / "slow.py").write_text(
        "import time\n"
        "time.sleep(2)\n"
        "print('too late')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "slow-script",
                "tool_dir": str(tool_directory),
                "command_template": f"{sys.executable} {{tool_dir}}/slow.py",
                "timeout_seconds": 1,
            },
        ).json()
        client.post(f"/api/v1/evaluation-methods/{method['id']}:probe")
        client.post(f"/api/v1/evaluation-methods/{method['id']}:freeze")
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method["id"]],
                "judge_runner": "lexical",
            },
        ).json()

    worker = LocalWorker(settings)
    try:
        assert worker.run_once() is True
    finally:
        worker.close()

    with TestClient(create_app(settings)) as client:
        current = client.get(
            f"/api/v1/evaluation-submissions/{submission['id']}"
        ).json()
        case_runs = client.get(
            f"/api/v1/evaluation-submissions/{submission['id']}/case-runs"
        ).json()
    assert current["status"] == "failed"
    method_run = case_runs[0]["methods"][0]
    assert method_run["status"] == "timeout"
    assert method_run["started_at"].endswith("Z")
    assert method_run["finished_at"].endswith("Z")
    assert method_run["duration_ms"] >= 900
    result = json.loads(
        (
            case_directory
            / "runs"
            / submission["timestamp"]
            / "result.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        result["generation"]["methods"][0]["duration_ms"]
        == method_run["duration_ms"]
    )


def test_partial_method_failure_skips_scoring_and_names_failed_method(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir()
    (logs_directory / "log.txt").write_text("chmod hung", encoding="utf-8")
    tool_directory = tmp_path / "tools"
    tool_directory.mkdir()
    (tool_directory / "fast.py").write_text("print('done')\n", encoding="utf-8")
    (tool_directory / "slow.py").write_text(
        "import time\ntime.sleep(2)\nprint('too late')\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        method_ids: list[str] = []
        for key, script, timeout in (
            ("fast-method", "fast.py", 10),
            ("sv-demo", "slow.py", 1),
        ):
            method = client.post(
                "/api/v1/evaluation-methods",
                json={
                    "key": key,
                    "tool_dir": str(tool_directory),
                    "command_template": f"{sys.executable} {{tool_dir}}/{script}",
                    "timeout_seconds": timeout,
                },
            ).json()
            client.post(f"/api/v1/evaluation-methods/{method['id']}:probe")
            client.post(f"/api/v1/evaluation-methods/{method['id']}:freeze")
            method_ids.append(method["id"])
        with transaction(client.app.state.session_factory) as session:
            variant = session.get(EvaluationMethod, method_ids[1])
            assert variant is not None
            variant.name = "codeagent-native@glm-5.2"
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": method_ids,
                "judge_runner": "lexical",
            },
        ).json()

    worker = LocalWorker(settings)
    try:
        while worker.run_once():
            pass
    finally:
        worker.close()

    with TestClient(create_app(settings)) as client:
        current = client.get(
            f"/api/v1/evaluation-submissions/{submission['id']}"
        ).json()
        case_run = client.get(
            f"/api/v1/evaluation-submissions/{submission['id']}/case-runs"
        ).json()[0]

    assert current["status"] == "failed"
    assert case_run["status"] == "failed"
    assert case_run["scoring_status"] == "skipped"
    assert case_run["error"] == {
        "code": "partial_methods_failed",
        "message": "部分测评方式失败，已跳过评分。",
        "failed_methods": ["codeagent-native@glm-5.2（timeout）"],
    }
    variant_run = next(item for item in case_run["methods"] if item["key"] == "sv-demo")
    assert variant_run["name"] == "codeagent-native@glm-5.2"
    result = json.loads(
        (Path(case_run["run_directory"]) / "result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["status"] == "failed"
    assert result["reports"] == []
    variant_summary = next(
        item
        for item in result["summary"]["reports"]
        if item["candidate_name"] == "codeagent-native@glm-5.2"
    )
    assert variant_summary["status"] == "timeout"
    variant_generation = next(
        item for item in result["generation"]["methods"] if item["key"] == "sv-demo"
    )
    assert variant_generation["name"] == "codeagent-native@glm-5.2"
    assert result["error"] == case_run["error"]


def test_deleting_one_method_removes_shared_submission_but_keeps_other_method(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    case_directory = create_case_directory(settings)
    logs = case_directory / "logs"
    logs.mkdir()
    (logs / "log.txt").write_text("chmod hung", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        methods = []
        for key in ("one", "two"):
            method = client.post(
                "/api/v1/evaluation-methods",
                json={
                    "key": key,
                    "command_template": f'{sys.executable} -c "print(1)"',
                },
            ).json()
            client.post(f"/api/v1/evaluation-methods/{method['id']}:probe")
            methods.append(
                client.post(
                    f"/api/v1/evaluation-methods/{method['id']}:freeze"
                ).json()
            )
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [item["id"] for item in methods],
                "judge_runner": "lexical",
            },
        ).json()
        run_directory = case_directory / "runs" / submission["timestamp"]

        deleted = client.delete(
            f"/api/v1/evaluation-methods/{methods[0]['id']}"
        )

        assert deleted.status_code == 200
        assert deleted.json()["submissions_deleted"] == 1
        assert not run_directory.exists()
        assert (
            client.get(
                f"/api/v1/evaluation-submissions/{submission['id']}"
            ).status_code
            == 404
        )
        assert (
            client.get(f"/api/v1/evaluation-methods/{methods[1]['id']}").status_code
            == 200
        )


def test_multiple_logs_require_primary_selection(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    create_case_directory(settings)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/local-cases/kdiag/SYSTEM_DEADLOCK/chmod_hung/logs",
            files=[
                ("files", ("one.log", b"one", "text/plain")),
                ("files", ("two.log", b"two", "text/plain")),
            ],
        )
        assert response.status_code == 200
        assert response.json()["submission_ready"] is False
        selected = client.put(
            "/api/v1/local-cases/kdiag/SYSTEM_DEADLOCK/chmod_hung/logs/primary",
            json={"filename": "two.log"},
        )
        assert selected.status_code == 200
        assert selected.json()["primary_log"] == "two.log"
        assert selected.json()["submission_ready"] is True


def test_submission_supports_relative_results_path(
    tmp_path: Path, monkeypatch
) -> None:
    settings = migrated_settings(tmp_path)
    monkeypatch.chdir(tmp_path)
    settings = settings.model_copy(
        update={
            "results_formal_path": Path("results"),
            "results_tmp_path": Path("results/tmp"),
        }
    )
    settings.ensure_local_directories()
    create_case_directory(settings)

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "script",
                "name": "Script",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        method_id = method["id"]
        assert client.post(f"/api/v1/evaluation-methods/{method_id}:probe").json()[
            "probe"
        ]["available"]
        client.post(f"/api/v1/evaluation-methods/{method_id}:freeze")
        response = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "case_logs_missing"


def test_method_key_is_display_name_and_accepts_safe_filename_characters(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "claude(glm5.1)-native",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        )
        trailing_parenthesis = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "agent(deepseek)",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        )
        trailing_square_bracket = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "agent[deepseek]",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        )
        unsafe = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "../escape",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        )
        deleted = client.delete(f"/api/v1/evaluation-methods/{response.json()['id']}")
        remaining = client.get("/api/v1/evaluation-methods").json()

    assert response.status_code == 201
    assert response.json()["key"] == "claude(glm5.1)-native"
    assert response.json()["name"] == "claude(glm5.1)-native"
    assert trailing_parenthesis.status_code == 201
    assert trailing_parenthesis.json()["key"] == "agent(deepseek)"
    assert trailing_parenthesis.json()["name"] == "agent(deepseek)"
    assert trailing_square_bracket.status_code == 201
    assert trailing_square_bracket.json()["key"] == "agent[deepseek]"
    assert trailing_square_bracket.json()["name"] == "agent[deepseek]"
    assert deleted.status_code == 200
    assert deleted.json()["submissions_deleted"] == 0
    assert all(item["id"] != response.json()["id"] for item in remaining)
    assert unsafe.status_code == 400
    assert unsafe.json()["error"]["code"] == "evaluation_method_invalid"


def test_claude_command_resolves_vscode_extension_binary(
    tmp_path: Path, monkeypatch
) -> None:
    extension_binary = (
        tmp_path
        / ".vscode-server"
        / "extensions"
        / "anthropic.claude-2.1.220-linux-x64"
        / "resources"
        / "native-binary"
        / "claude"
    )
    extension_binary.parent.mkdir(parents=True)
    extension_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    extension_binary.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("analystbench.execution.resolver.shutil.which", lambda _: None)

    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "claude",
                "name": "claude Native",
                "command_template": 'claude -p "分析日志 {input}"',
            },
        ).json()
        probed = client.post(f"/api/v1/evaluation-methods/{method['id']}:probe").json()

    assert probed["probe"]["available"] is True
    assert probed["probe"]["executable"] == str(extension_binary.resolve())
    command = EvaluationSubmissionService._build_command(
        {"command_template": "claude {input}", "tool_dir": None},
        tmp_path / "workspace",
        tmp_path / "logs" / "log.txt",
        tmp_path / "logs",
    )
    assert command[0] == str(extension_binary.resolve())


def test_submission_skips_unready_cases_and_runs_ready_cases(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    ready = create_case_directory(settings)
    skipped = settings.results_formal_path / "kdiag" / "SYSTEM_DEADLOCK" / "without_logs"
    skipped.mkdir(parents=True)
    skipped_payload = case_payload()
    skipped_payload["case"]["case_key"] = "without_logs"
    (skipped / "case.json").write_text(
        json.dumps(skipped_payload, ensure_ascii=False), encoding="utf-8"
    )
    logs = ready / "logs"
    logs.mkdir()
    (logs / "log.txt").write_text("chmod hung", encoding="utf-8")
    (logs / "manifest.json").write_text('{"primary":"log.txt"}', encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "script",
                "name": "Script",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        method_id = method["id"]
        client.post(f"/api/v1/evaluation-methods/{method_id}:probe")
        client.post(f"/api/v1/evaluation-methods/{method_id}:freeze")
        response = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        )
        submission = response.json()
        stored = client.app.state.evaluation_submission_service.get_submission(
            submission["id"]
        )

    assert response.status_code == 202
    assert submission["case_count"] == 1
    manifest = json.loads(stored.manifest_json)
    assert manifest["selected_case_paths"] == [
        "kdiag/SYSTEM_DEADLOCK/chmod_hung"
    ]
    assert manifest["skipped_cases"] == [
        {
            "case_path": "kdiag/SYSTEM_DEADLOCK/without_logs",
            "issues": [
                {
                    "code": "case_logs_missing",
                    "message": "Case 没有原始日志",
                }
            ],
            "reason": "case_logs_missing",
        }
    ]


def test_submission_runs_only_explicitly_selected_cases(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    first = create_case_directory(settings)
    second = settings.results_formal_path / "kdiag" / "SYSTEM_DEADLOCK" / "chmod_hung_2"
    second.mkdir(parents=True)
    second_payload = case_payload()
    second_payload["case"]["case_key"] = "chmod_hung_2"
    (second / "case.json").write_text(
        json.dumps(second_payload, ensure_ascii=False), encoding="utf-8"
    )
    for directory in (first, second):
        logs = directory / "logs"
        logs.mkdir()
        (logs / "log.txt").write_text("chmod hung", encoding="utf-8")
        (logs / "manifest.json").write_text('{"primary":"log.txt"}', encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "script",
                "name": "Script",
                "command_template": f'{sys.executable} -c "print(1)"',
            },
        ).json()
        method_id = method["id"]
        client.post(f"/api/v1/evaluation-methods/{method_id}:probe")
        client.post(f"/api/v1/evaluation-methods/{method_id}:freeze")
        response = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "case_paths": ["kdiag/SYSTEM_DEADLOCK/chmod_hung_2"],
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        )
        submission = response.json()
        case_runs = client.get(
            f"/api/v1/evaluation-submissions/{submission['id']}/case-runs"
        ).json()

    assert response.status_code == 202
    assert submission["case_count"] == 1
    assert [item["case_path"] for item in case_runs] == [
        "kdiag/SYSTEM_DEADLOCK/chmod_hung_2"
    ]
    assert not (first / "runs" / submission["timestamp"]).exists()
    assert (second / "runs" / submission["timestamp"]).is_dir()


def test_two_cases_and_two_methods_are_scored_in_one_submission(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    first = create_case_directory(settings)
    second = settings.results_formal_path / "kdiag" / "SYSTEM_DEADLOCK" / "chmod_hung_2"
    second.mkdir(parents=True)
    second_payload = case_payload()
    second_payload["case"]["case_key"] = "chmod_hung_2"
    (second / "case.json").write_text(
        json.dumps(second_payload, ensure_ascii=False), encoding="utf-8"
    )
    for directory in (first, second):
        logs = directory / "logs"
        logs.mkdir()
        (logs / "log.txt").write_text("chmod hung", encoding="utf-8")
        (logs / "manifest.json").write_text('{"primary":"log.txt"}', encoding="utf-8")

    tool_directory = tmp_path / "tools"
    tool_directory.mkdir()
    (tool_directory / "report.py").write_text(
        "print('问题分类：SYSTEM_DEADLOCK')\n"
        "print('问题根因：chmod 进程发生系统死锁')\n"
        "print('chmod hung')\n"
        "print('结论：chmod 进程长期阻塞')\n",
        encoding="utf-8",
    )
    command_template = f"{sys.executable} {{tool_dir}}/report.py {{input}}"

    with TestClient(create_app(settings)) as client:
        method_ids = []
        for key, name in (("script", "Script"), ("sv-demo", "harness@model")):
            created = client.post(
                "/api/v1/evaluation-methods",
                json={
                    "key": key,
                    "name": name,
                    "tool_dir": str(tool_directory),
                    "command_template": command_template,
                },
            ).json()
            method_id = created["id"]
            assert client.post(f"/api/v1/evaluation-methods/{method_id}:probe").json()["probe"][
                "available"
            ]
            client.post(f"/api/v1/evaluation-methods/{method_id}:freeze")
            method_ids.append(method_id)
        submission = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": method_ids,
                "judge_runner": "lexical",
            },
        ).json()

    worker = LocalWorker(settings)
    try:
        processed = 0
        while worker.run_once():
            processed += 1
        assert processed == 6
    finally:
        worker.close()

    with TestClient(create_app(settings)) as client:
        completed = client.get(f"/api/v1/evaluation-submissions/{submission['id']}").json()
        assert completed["status"] == "completed"
        case_runs = client.get(
            f"/api/v1/evaluation-submissions/{submission['id']}/case-runs"
        ).json()
        assert len(case_runs) == 2
        assert all(len(item["methods"]) == 2 for item in case_runs)
        assert all(item["status"] == "completed" for item in case_runs)

    for directory in (first, second):
        result = json.loads(
            (directory / "runs" / submission["timestamp"] / "result.json").read_text(
                encoding="utf-8"
            )
        )
        assert {item["candidate_name"] for item in result["reports"]} == {
            "script",
            "harness@model",
        }
