from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings


def migrated_settings(tmp_path: Path) -> Settings:
    database_path = tmp_path / "analystbench.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return Settings(database_url=database_url, content_store_path=tmp_path / "content")


def test_catalog_api_creates_immutable_versions_and_reports(tmp_path: Path) -> None:
    with TestClient(create_app(migrated_settings(tmp_path))) as client:
        dataset = client.post("/api/v1/datasets", json={"name": "kernel-cases"})
        assert dataset.status_code == 201
        dataset_id = dataset.json()["id"]

        case = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={
                "case_key": "1",
                "problem_statement": "The host panics after a driver reload.",
                "reference_answer": "A stale pointer in the driver causes the panic.",
            },
        )
        assert case.status_code == 201
        case_revision_id = case.json()["id"]
        case_id = case.json()["case_id"]
        assert case.json()["reference_answer_content_hash"].startswith("sha256:")

        cases = client.get(f"/api/v1/datasets/{dataset_id}/cases")
        assert cases.status_code == 200
        assert cases.json()[0]["id"] == case_id
        assert cases.json()[0]["revisions"][0]["id"] == case_revision_id

        case_detail = client.get(f"/api/v1/cases/{case_id}")
        assert case_detail.status_code == 200
        assert case_detail.json()["revisions"][0]["id"] == case_revision_id

        case_content = client.get(f"/api/v1/case-revisions/{case_revision_id}/content")
        assert case_content.status_code == 200
        assert case_content.json()["reference_answer"].startswith("A stale pointer")
        assert "problem_statement" not in case_content.json()

        dataset_version = client.post(
            f"/api/v1/datasets/{dataset_id}/versions",
            json={"case_revision_ids": [case_revision_id]},
        )
        assert dataset_version.status_code == 201
        dataset_version_id = dataset_version.json()["id"]

        candidate = client.post("/api/v1/candidates", json={"name": "claude"})
        assert candidate.status_code == 201
        candidate_version = client.post(
            f"/api/v1/candidates/{candidate.json()['id']}/versions",
            json={"metadata": {"runner": "claude", "model": "claude"}},
        )
        assert candidate_version.status_code == 201
        candidate_version_id = candidate_version.json()["id"]

        imported = client.post(
            f"/api/v1/candidate-versions/{candidate_version_id}/reports:batch-import",
            json={
                "reports": [
                    {
                        "case_revision_id": case_revision_id,
                        "report": "The reload retains a stale driver pointer.",
                    }
                ]
            },
        )
        assert imported.status_code == 201
        assert imported.json()[0]["source"] == "imported"

        coverage = client.get(
            f"/api/v1/candidate-versions/{candidate_version_id}/coverage",
            params={"dataset_version_id": dataset_version_id},
        )
        assert coverage.status_code == 200
        assert coverage.json() == {"total": 1, "available": 1, "missing_case_revision_ids": []}

        exported = client.app.state.catalog_service.export_dataset_version(dataset_version_id)
        assert exported["schema_version"] == "1.0"
        assert exported["cases"][0]["case_key"] == "1"
        assert exported["cases"][0]["reference_answer"].startswith("A stale pointer")

        exported["dataset"]["name"] = "kernel-cases-copy"
        exported["dataset"]["dataset_key"] = "kernel-cases-copy"
        imported_version = client.app.state.catalog_service.import_dataset_export(exported)
        assert imported_version.version_number == 1


def test_duplicate_candidate_report_is_rejected(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "dataset"}).json()["id"]
        revision_id = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"case_key": "case", "problem_statement": "problem", "reference_answer": "answer"},
        ).json()["id"]
        candidate_id = client.post("/api/v1/candidates", json={"name": "candidate"}).json()["id"]
        candidate_version_id = client.post(
            f"/api/v1/candidates/{candidate_id}/versions", json={}
        ).json()["id"]
        endpoint = f"/api/v1/candidate-versions/{candidate_version_id}/reports:batch-import"
        body = {"reports": [{"case_revision_id": revision_id, "report": "report"}]}
        assert client.post(endpoint, json=body).status_code == 201
        duplicate = client.post(endpoint, json=body)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "conflict"


def test_case_categories_and_generated_case_keys(tmp_path: Path) -> None:
    with TestClient(create_app(migrated_settings(tmp_path))) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "kernel"}).json()["id"]

        category = client.post(
            f"/api/v1/datasets/{dataset_id}/categories",
            json={"category_key": "HM_PANIC_SYSMGR", "name": "Panic / SYSMGR"},
        )
        assert category.status_code == 201
        category_id = category.json()["id"]
        assert client.get(f"/api/v1/datasets/{dataset_id}/categories").json() == [
            {
                "id": category_id,
                "dataset_id": dataset_id,
                "category_key": "HM_PANIC_SYSMGR",
                "name": "Panic / SYSMGR",
                "description": "",
            }
        ]

        payload = {
            "category_key": "HM_PANIC_SYSMGR",
            "problem_statement": "kernel panic",
            "reference_answer": "restart the service",
        }
        legacy_payload = {**payload, "case_key": "HM_PANIC_SYSMGR-case1"}
        response = client.post(f"/api/v1/datasets/{dataset_id}/cases", json=legacy_payload)
        assert response.status_code == 201
        assert client.post(f"/api/v1/datasets/{dataset_id}/cases", json=payload).status_code == 201

        second_category_payload = {
            "category_key": "HM_OOM",
            "problem_statement": "out of memory",
            "reference_answer": "reduce allocation",
        }
        assert client.post(
            f"/api/v1/datasets/{dataset_id}/cases", json=second_category_payload
        ).status_code == 201

        cases = client.get(f"/api/v1/datasets/{dataset_id}/cases").json()
        other_category_id = next(
            item["category_id"] for item in cases if item["category_id"] != category_id
        )
        assert sorted((item["category_id"], item["case_key"]) for item in cases) == sorted(
            [
                (category_id, "HM_PANIC_SYSMGR-case1"),
                (category_id, "2"),
                (other_category_id, "1"),
            ]
        )


def test_catalog_delete_endpoints_archive_hierarchy(tmp_path: Path) -> None:
    with TestClient(create_app(migrated_settings(tmp_path))) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "to-archive"}).json()["id"]
        category = client.post(
            f"/api/v1/datasets/{dataset_id}/categories",
            json={"category_key": "panic"},
        ).json()
        revision = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={
                "category_key": "panic",
                "problem_statement": "panic",
                "reference_answer": "answer",
            },
        ).json()
        case_id = revision["case_id"]

        assert client.delete(f"/api/v1/cases/{case_id}").status_code == 204
        assert client.get(f"/api/v1/datasets/{dataset_id}/cases").json() == []

        revision = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={
                "category_key": "panic",
                "problem_statement": "panic again",
                "reference_answer": "answer",
            },
        ).json()
        assert client.delete(
            f"/api/v1/datasets/{dataset_id}/categories/{category['id']}"
        ).status_code == 204
        assert client.get(f"/api/v1/datasets/{dataset_id}/categories").json() == []
        assert client.get(f"/api/v1/datasets/{dataset_id}/cases").json() == []

        assert client.delete(f"/api/v1/datasets/{dataset_id}").status_code == 204
        assert client.get("/api/v1/datasets").json() == []
