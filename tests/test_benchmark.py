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


def test_benchmark_run_is_durable_and_exports_explainable_result(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "bench-dataset"}).json()["id"]
        revision_id = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={
                "case_key": "case",
                "problem_statement": "Why?",
                "reference_answer": "Stale pointer caused failure.",
            },
        ).json()["id"]
        dataset_version_id = client.post(
            f"/api/v1/datasets/{dataset_id}/versions", json={"case_revision_ids": [revision_id]}
        ).json()["id"]
        candidate_id = client.post("/api/v1/candidates", json={"name": "candidate"}).json()["id"]
        candidate_version_id = client.post(
            f"/api/v1/candidates/{candidate_id}/versions", json={"metadata": {}}
        ).json()["id"]
        client.post(
            f"/api/v1/candidate-versions/{candidate_version_id}/reports:batch-import",
            json={
                "reports": [
                    {"case_revision_id": revision_id, "report": "Stale pointer caused failure."}
                ]
            },
        )
        policy_id = client.post("/api/v1/scoring-policies", json={"name": "policy"}).json()["id"]
        draft = client.post(
            "/api/v1/eval-spec-drafts:generate",
            json={"case_revision_id": revision_id, "scoring_policy_version_id": policy_id},
        ).json()
        payload = draft["payload"]
        payload["claims"][0]["review_required"] = False
        payload["review"] = {"status": "approved", "unresolved_items": []}
        edited = client.post(
            "/api/v1/eval-spec-drafts", json={"case_revision_id": revision_id, "payload": payload}
        ).json()
        assert client.post(f"/api/v1/eval-spec-drafts/{edited['id']}:freeze").status_code == 200
        run = client.post(
            "/api/v1/benchmark-runs",
            json={
                "dataset_version_id": dataset_version_id,
                "candidate_version_id": candidate_version_id,
                "scoring_policy_version_id": policy_id,
            },
        )
        assert run.status_code == 202
        run_id = run.json()["id"]
        worker = LocalWorker(settings)
        try:
            assert worker.run_once() is True
        finally:
            worker.close()
        completed = client.get(f"/api/v1/benchmark-runs/{run_id}").json()
        assert completed["status"] == "completed"
        assert completed["summary"]["succeeded"] == 1
        case_run = client.get(f"/api/v1/benchmark-runs/{run_id}/case-runs").json()[0]
        result = client.get(f"/api/v1/benchmark-case-runs/{case_run['id']}/result").json()
        assert result["total_score"] == "100.00"
        assert client.get(f"/api/v1/benchmark-runs/{run_id}/export").json()["case_runs"][0][
            "result"
        ]
