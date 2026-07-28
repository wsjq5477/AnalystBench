import json
import sys
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

    with TestClient(create_app(settings)) as client:
        current = client.get(f"/api/v1/evaluation-submissions/{submission_id}").json()
        assert current["status"] == "completed"
        direct_results = client.get("/api/v1/direct-results").json()
        generated = next(item for item in direct_results if item["timestamp"] == timestamp)
        assert generated["id"] == (f"kdiag/SYSTEM_DEADLOCK/chmod_hung/runs/{timestamp}")
        completed_cases = client.get(
            f"/api/v1/evaluation-submissions/{submission_id}/case-runs"
        ).json()
        artifacts = client.get(
            f"/api/v1/evaluation-method-runs/{completed_cases[0]['methods'][0]['id']}/artifacts"
        )
        assert artifacts.status_code == 200
        assert "系统死锁" in artifacts.json()["stdout"]
        assert artifacts.json()["stderr"] == ""
        protected = client.delete(f"/api/v1/evaluation-methods/{method_id}")
        assert protected.status_code == 409
        assert protected.json()["error"]["code"] == "evaluation_method_in_use"

        queued = client.post(
            "/api/v1/evaluation-submissions",
            json={
                "dataset_key": "kdiag",
                "method_ids": [method_id],
                "judge_runner": "lexical",
            },
        ).json()
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
    assert deleted.status_code == 204
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
