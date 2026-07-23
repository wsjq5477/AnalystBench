from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings
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
    )


def test_evaluation_session_prompts_then_queues_and_returns_result(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    reference = "日志：suspend timeout。根因：未 REPICK 导致线程运行错误 CPU 核。"
    case_draft = {
        "case": {
            "case_key": "1",
            "problem_statement": "分析 suspend timeout 的根因。",
            "reference_answer": reference,
        },
        "eval_spec_draft": {
            "claims": [
                {
                    "id": "claim-1",
                    "type": "symptom",
                    "statement": "发生 suspend timeout",
                    "importance": "supporting",
                    "weight": 30,
                    "quote": "suspend timeout",
                    "review_required": True,
                },
                {
                    "id": "root",
                    "type": "root_cause",
                    "statement": "未 REPICK 导致线程运行错误 CPU 核",
                    "importance": "critical",
                    "weight": 70,
                    "quote": "未 REPICK 导致线程运行错误 CPU 核",
                    "review_required": True,
                },
            ],
            "causal_edges": [],
            "forbidden_claims": [],
            "unresolved_items": ["具体触发代码路径尚未确认"],
        },
    }
    report_draft = {
        "candidate": {"name": "candidate-a", "metadata": {"model": "test"}},
        "candidate_report": "系统发生 suspend timeout，但根因是服务心跳异常。",
        "claim_hints": [
            {
                "id": "candidate-1",
                "type": "root_cause",
                "statement": "服务心跳异常",
                "quote": "并不存在的引用",
            }
        ],
        "unresolved_items": [],
    }

    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/evaluation-sessions",
            json={"case_draft": case_draft, "report_drafts": [report_draft]},
        )
        assert created.status_code == 201
        session = created.json()
        assert session["status"] == "needs_confirmation"
        assert any(
            item["field_path"] == "eval_spec_draft.claims[0].importance"
            for item in session["required_questions"]
        )
        assert any(
            item["field_path"] == "report_drafts[0].claim_hints[0].quote"
            for item in session["warnings"]
        )

        answers = [
            {"question_id": item["id"], "value": item["suggested_value"]}
            for item in session["required_questions"]
        ]
        answered = client.post(
            f"/api/v1/evaluation-sessions/{session['id']}/answers",
            json={"answers": answers},
        )
        assert answered.status_code == 200
        assert answered.json()["status"] == "queued"
        assert len(answered.json()["resources"]["runs"]) == 1

        worker = LocalWorker(settings)
        try:
            assert worker.run_once() is True
        finally:
            worker.close()

        result = client.get(f"/api/v1/evaluation-sessions/{session['id']}/result").json()
        assert result["status"] == "completed"
        assert result["runs"][0]["status"] == "completed"
        assert result["runs"][0]["result"]["case_runs"][0]["result"] is not None
