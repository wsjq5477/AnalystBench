import sys
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings
from analystbench.worker import LocalWorker


def migrated_settings(tmp_path: Path) -> Settings:
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return Settings(
        database_url=database_url,
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
    )


def test_fake_claude_runner_generates_a_frozen_candidate_report(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_agent.py"
    fake_cli.write_text(
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('fake-agent 1.0')\n"
        "else:\n"
        "    print(json.dumps({'result': 'The driver retains a stale pointer after reload.'}))\n",
        encoding="utf-8",
    )
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "execution-dataset"}).json()[
            "id"
        ]
        revision_id = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={
                "case_key": "case",
                "problem_statement": "driver panic",
                "reference_answer": "stale pointer",
            },
        ).json()["id"]
        dataset_version_id = client.post(
            f"/api/v1/datasets/{dataset_id}/versions",
            json={"case_revision_ids": [revision_id]},
        ).json()["id"]
        candidate_id = client.post("/api/v1/candidates", json={"name": "fake-claude"}).json()["id"]
        candidate_version_id = client.post(
            f"/api/v1/candidates/{candidate_id}/versions",
            json={"metadata": {"runner": "claude-code"}},
        ).json()["id"]
        profile = client.post(
            "/api/v1/execution-profiles",
            json={
                "name": "fake-profile",
                "runner": "claude-code",
                "configuration": {
                    "executable": sys.executable,
                    "extra_args": [str(fake_cli)],
                    "timeout_seconds": 30,
                    "max_output_bytes": 1024 * 1024,
                    "environment_mode": "local",
                },
            },
        )
        assert profile.status_code == 201
        profile_id = profile.json()["id"]
        assert client.post(f"/api/v1/execution-profiles/{profile_id}:validate").json()["available"]
        assert (
            client.post(f"/api/v1/execution-profiles/{profile_id}:freeze").json()["status"]
            == "frozen"
        )
        run = client.post(
            "/api/v1/candidate-generation-runs",
            json={
                "dataset_version_id": dataset_version_id,
                "candidate_version_id": candidate_version_id,
                "execution_profile_id": profile_id,
            },
        )
        assert run.status_code == 202
        run_id = run.json()["id"]

        worker = LocalWorker(settings)
        try:
            assert worker.run_once() is True
        finally:
            worker.close()

        assert (
            client.get(f"/api/v1/candidate-generation-runs/{run_id}").json()["status"]
            == "completed"
        )
        case_runs = client.get(f"/api/v1/candidate-generation-runs/{run_id}/case-runs").json()
        assert case_runs[0]["status"] == "succeeded"
        coverage = client.get(
            f"/api/v1/candidate-versions/{candidate_version_id}/coverage",
            params={"dataset_version_id": dataset_version_id},
        ).json()
        assert coverage["available"] == 1
        assert not list(settings.workspace_root_path.iterdir())
