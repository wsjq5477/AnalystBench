from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings
from analystbench.storage.content import content_hash


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


def test_generated_eval_spec_requires_review_and_freezes_after_valid_edit(tmp_path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "spec-dataset"}).json()["id"]
        revision_id = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={
                "case_key": "case",
                "problem_statement": "Why did the service fail?",
                "reference_answer": "The stale cache pointer caused the service failure.",
            },
        ).json()["id"]
        policy_id = client.post("/api/v1/scoring-policies", json={"name": "v1"}).json()["id"]
        generated = client.post(
            "/api/v1/eval-spec-drafts:generate",
            json={"case_revision_id": revision_id, "scoring_policy_version_id": policy_id},
        )
        assert generated.status_code == 201
        draft = generated.json()
        assert client.post(f"/api/v1/eval-spec-drafts/{draft['id']}:validate").json()["valid"]
        assert client.post(f"/api/v1/eval-spec-drafts/{draft['id']}:freeze").status_code == 400

        payload = draft["payload"]
        payload["claims"][0]["review_required"] = False
        payload["claims"][0]["notes"] = "Reviewed."
        payload["review"] = {"status": "approved", "unresolved_items": []}
        edited = client.post(
            "/api/v1/eval-spec-drafts", json={"case_revision_id": revision_id, "payload": payload}
        ).json()
        frozen = client.post(f"/api/v1/eval-spec-drafts/{edited['id']}:freeze")
        assert frozen.status_code == 200
        assert frozen.json()["payload"]["claims"][0]["source_ref"]["quote"] in (
            "The stale cache pointer caused the service failure.",
        )


def test_eval_spec_rejects_forged_quote_at_freeze(tmp_path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "bad-spec-dataset"}).json()["id"]
        revision_id = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"case_key": "case", "problem_statement": "p", "reference_answer": "actual"},
        ).json()["id"]
        policy_id = client.post("/api/v1/scoring-policies", json={"name": "v1"}).json()["id"]
        draft = client.post(
            "/api/v1/eval-spec-drafts:generate",
            json={"case_revision_id": revision_id, "scoring_policy_version_id": policy_id},
        ).json()
        payload = draft["payload"]
        payload["claims"][0]["review_required"] = False
        payload["claims"][0]["source_ref"]["quote"] = "forged"
        payload["review"] = {"status": "approved", "unresolved_items": []}
        edited_id = client.post(
            "/api/v1/eval-spec-drafts", json={"case_revision_id": revision_id, "payload": payload}
        ).json()["id"]
        response = client.post(f"/api/v1/eval-spec-drafts/{edited_id}:freeze")
        assert response.status_code == 400
        assert "quote" in response.json()["error"]["details"]["errors"][0]


def test_eval_spec_validation_errors_include_forbidden_claim_field_path(tmp_path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "bad-forbidden"}).json()[
            "id"
        ]
        revision_id = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"case_key": "case", "problem_statement": "p", "reference_answer": "actual"},
        ).json()["id"]
        policy_id = client.post("/api/v1/scoring-policies", json={"name": "v1"}).json()["id"]
        generated = client.post(
            "/api/v1/eval-spec-drafts:generate",
            json={"case_revision_id": revision_id, "scoring_policy_version_id": policy_id},
        ).json()
        payload = generated["payload"]
        payload["forbidden_claims"] = [
            {"id": "forbidden-1", "statement": "wrong", "type": "root_cause"}
        ]
        draft_id = client.post(
            "/api/v1/eval-spec-drafts",
            json={"case_revision_id": revision_id, "payload": payload},
        ).json()["id"]

        errors = client.post(f"/api/v1/eval-spec-drafts/{draft_id}:validate").json()["errors"]
        assert "forbidden_claims[0].severity: Field required" in errors
        assert "forbidden_claims[0].penalty: Field required" in errors
        assert any(error.startswith("forbidden_claims[0].type:") for error in errors)


def test_root_category_chain_spec_freezes_with_mutually_exclusive_weights(tmp_path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        dataset_id = client.post("/api/v1/datasets", json={"name": "root-chain"}).json()["id"]
        revision_id = client.post(
            f"/api/v1/datasets/{dataset_id}/cases",
            json={"case_key": "case", "problem_statement": "p", "reference_answer": "root chain"},
        ).json()["id"]
        policy_id = client.post("/api/v1/scoring-policies", json={"name": "v1"}).json()["id"]
        source_ref = {
            "content_hash": content_hash(b"root chain"),
            "start": 0,
            "end": 10,
            "quote": "root chain",
        }
        payload = {
            "schema_version": "1.0",
            "case_revision_id": revision_id,
            "suite": {"id": "kdiag", "version": "1.0.0"},
            "claims": [
                {
                    "id": "root",
                    "type": "root_cause",
                    "statement": "root",
                    "importance": "critical",
                    "weight": 100,
                    "source_ref": source_ref,
                    "review_required": False,
                },
                {
                    "id": "category",
                    "type": "classification",
                    "statement": "HM_PANIC_SYSMGR",
                    "importance": "high",
                    "weight": 20,
                    "source_ref": source_ref,
                    "review_required": False,
                },
                {
                    "id": "chain-1",
                    "type": "analysis_chain",
                    "statement": "chain",
                    "importance": "normal",
                    "weight": 60,
                    "source_ref": source_ref,
                    "review_required": False,
                    "evidence_keyword": "chain",
                    "conclusion": "chain",
                },
            ],
            "causal_edges": [],
            "forbidden_claims": [],
            "scoring_policy_version_id": policy_id,
            "scoring_strategy": {
                "mode": "root_category_chain",
                "root_cause_score": 100,
                "category_score": 20,
                "chain_total_score": 60,
            },
            "review": {"status": "approved", "unresolved_items": []},
        }
        draft_id = client.post(
            "/api/v1/eval-spec-drafts",
            json={"case_revision_id": revision_id, "payload": payload},
        ).json()["id"]
        assert client.post(f"/api/v1/eval-spec-drafts/{draft_id}:freeze").status_code == 200


def test_identical_scoring_policies_are_reused_by_content(tmp_path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        first = client.post("/api/v1/scoring-policies", json={"name": "case-one"})
        second = client.post("/api/v1/scoring-policies", json={"name": "case-two"})

        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["content_hash"] == first.json()["content_hash"]
