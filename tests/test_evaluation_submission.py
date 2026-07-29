import json
import sys
import threading
import time
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings
from analystbench.evaluation_submission import EvaluationSubmissionService
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


def test_targets_share_their_harness_concurrency_limit(tmp_path: Path) -> None:
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
        harness = client.post(
            "/api/v1/evaluation-harnesses",
            json={
                "key": "codeagent-skill",
                "model_policy": "required",
                "tool_dir": str(tool_directory),
                "command_template": (
                    f"{sys.executable} {{tool_dir}}/report.py --model {{model}} {{input}}"
                ),
                "concurrency_limit": 1,
            },
        ).json()
        client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:probe")
        client.post(f"/api/v1/evaluation-harnesses/{harness['id']}:freeze")
        target_ids = []
        for key, argument in (("glm-5.1", "glm5.1"), ("deepseek-v4", "deepseek-v4")):
            model = client.post(
                "/api/v1/evaluation-models",
                json={"key": key, "argument": argument},
            ).json()
            target = client.post(
                "/api/v1/evaluation-targets",
                json={"harness_id": harness["id"], "model_id": model["id"]},
            ).json()
            client.post(f"/api/v1/evaluation-targets/{target['id']}:probe")
            client.post(f"/api/v1/evaluation-targets/{target['id']}:freeze")
            target_ids.append(target["id"])
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
            comparison = client.get(
                f"/api/v1/evaluation-submissions/{submission['id']}/target-comparison"
            ).json()
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
    assert comparison["by_harness"] == [
        {
            "key": "codeagent-skill@v1",
            "target_keys": ["codeagent-skill@deepseek-v4", "codeagent-skill@glm-5.1"],
        }
    ]


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
                "key": "codeagent-native",
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
        assert frozen_target["key"] == "codeagent-native@glm5.1"
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
    assert generation["targets"][0]["target_key"] == "codeagent-native@glm5.1"
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
        assert comparison.json()["targets"][0]["target"]["key"] == "codeagent-native@glm5.1"
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
                "key": "codeAgent(glm5.1)-native",
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
    assert response.json()["key"] == "codeAgent(glm5.1)-native"
    assert response.json()["name"] == "codeAgent(glm5.1)-native"
    assert trailing_parenthesis.status_code == 201
    assert trailing_parenthesis.json()["key"] == "agent(deepseek)"
    assert trailing_parenthesis.json()["name"] == "agent(deepseek)"
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
        / "anthropic.claude-code-2.1.220-linux-x64"
        / "resources"
        / "native-binary"
        / "claude"
    )
    extension_binary.parent.mkdir(parents=True)
    extension_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    extension_binary.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("analystbench.executable_resolver.shutil.which", lambda _: None)

    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        method = client.post(
            "/api/v1/evaluation-methods",
            json={
                "key": "claude",
                "name": "Claude Native",
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
        for key, name in (("script", "Script"), ("claude", "Claude")):
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
