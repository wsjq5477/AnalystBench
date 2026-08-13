from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from analystbench.api.app import create_app
from analystbench.api.routes import skill_optimization as optimization_routes
from analystbench.api.routes import skills as skill_routes


def experiment(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "experiment-1",
        "name": "demo",
        "skill_id": "skill-1",
        "base_skill_version_id": "version-1",
        "evaluation_target_id": "target-1",
        "data_snapshot_id": "snapshot-1",
        "optimizer_policy_version_id": "policy-1",
        "verifier_bundle_version_id": "verifier-1",
        "status": "completed",
        "current_epoch_number": 3,
        "max_epochs": 3,
        "stop_reason": "MAX_EPOCHS",
        "config_snapshot_json": "{}",
        "error_json": "{}",
        "started_at": datetime(2026, 8, 12, 1, tzinfo=UTC),
        "finished_at": datetime(2026, 8, 12, 2, tzinfo=UTC),
        "created_at": datetime(2026, 8, 12, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 12, 2, tzinfo=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DetailService:
    def __init__(self) -> None:
        self.detail_calls: list[tuple[str, dict[str, object]]] = []

    def detail(self, experiment_id: str, **kwargs: object) -> dict[str, object]:
        self.detail_calls.append((experiment_id, kwargs))
        return {
            "experiment": experiment(),
            "epoch_total": 41,
            "version_metadata": {
                "version-1": {
                    "id": "version-1",
                    "version_number": 1,
                    "created_at": datetime(2026, 8, 12, tzinfo=UTC),
                }
            },
            "epochs": [
                {
                    "id": "epoch-3",
                    "number": 3,
                    "status": "completed",
                    "parent_skill_version_id": "version-1",
                    "best_candidate_version_id": None,
                    "decision": "retain",
                    "candidates": [],
                }
            ],
        }

    def summary(self, experiment_id: str) -> dict[str, object]:
        assert experiment_id == "experiment-1"
        return {
            "initial_score": 70.0,
            "final_score": 70.0,
            "active_path_score": 70.0,
            "score_semantics": "initial_score_plus_promoted_epoch_deltas",
            "cumulative_delta": 0.0,
            "promoted_epochs": 0,
            "retained_epochs": 3,
            "stop_reason": "MAX_EPOCHS",
        }


def optimization_request(service: object) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(skill_optimization_service=service))
    )


def test_detail_surface_keeps_global_summary_and_explicit_pagination() -> None:
    service = DetailService()

    result = optimization_routes.experiment_detail(
        "experiment-1",
        optimization_request(service),  # type: ignore[arg-type]
        epoch_offset=20,
        epoch_limit=10,
    )

    assert service.detail_calls == [
        (
            "experiment-1",
            {"epoch_offset": 20, "epoch_limit": 10, "newest_first": True},
        )
    ]
    assert result["summary"]["retained_epochs"] == 3
    assert result["pagination"] == {
        "offset": 20,
        "limit": 10,
        "total": 41,
        "has_more": True,
        "newest_first": True,
    }
    assert result["version_metadata"]["version-1"]["version_number"] == 1
    encoded = jsonable_encoder(result)
    assert encoded["version_metadata"]["version-1"]["created_at"] == ("2026-08-12T00:00:00+00:00")


@pytest.mark.parametrize(
    ("format_name", "content_type", "suffix"),
    [
        ("json", "application/json", "json"),
        ("markdown", "text/markdown; charset=utf-8", "md"),
        ("csv", "text/csv; charset=utf-8", "csv"),
    ],
)
def test_ledger_exports_have_download_and_private_cache_headers(
    format_name: str,
    content_type: str,
    suffix: str,
) -> None:
    service = DetailService()

    response = optimization_routes.export_experiment_ledger(
        "experiment-1",
        optimization_request(service),  # type: ignore[arg-type]
        format=format_name,
    )

    assert response.headers["content-type"] == content_type
    assert response.headers["content-disposition"] == (
        f'attachment; filename="skill-optimization-experiment-1.{suffix}"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert int(response.headers["content-length"]) == len(response.body)
    if format_name == "json":
        payload = json.loads(response.body)
        assert payload["schema_version"] == "1"
        assert payload["experiment"]["id"] == "experiment-1"


def test_preflight_surface_forwards_every_optional_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, object, dict[str, object]]] = []

    class Preflight:
        def __init__(self, session_factory: object, settings: object) -> None:
            self.session_factory = session_factory
            self.settings = settings

        def run(self, **kwargs: object) -> dict[str, object]:
            calls.append((self.session_factory, self.settings, kwargs))
            return {"status": "PASS", "checks": []}

    monkeypatch.setattr(
        optimization_routes,
        "SkillOptimizationPreflightService",
        Preflight,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_factory="sessions",
                settings="settings",
            )
        )
    )
    payload = optimization_routes.PreflightRequest(
        skill_key="demo",
        evaluation_target_id="target-1",
        execution_profile_id="profile-1",
        optimizer_policy_version_id="policy-1",
        verifier_bundle_version_id="verifier-1",
        case_paths=["dataset/family/case-1"],
        data_snapshot_id="snapshot-1",
    )

    result = optimization_routes.run_preflight(payload, request)  # type: ignore[arg-type]

    assert result == {"status": "PASS", "checks": []}
    assert calls == [
        (
            "sessions",
            "settings",
            {
                "skill_key": "demo",
                "evaluation_target_id": "target-1",
                    "execution_profile_id": "profile-1",
                    "optimizer_policy_version_id": "policy-1",
                    "verifier_bundle_version_id": "verifier-1",
                    "case_paths": ["dataset/family/case-1"],
                "data_snapshot_id": "snapshot-1",
            },
        )
    ]
    with pytest.raises(ValidationError):
        optimization_routes.PreflightRequest(skill_key="")


def test_version_export_and_rollback_surface_contracts() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    calls: list[dict[str, object]] = []

    class Registry:
        def export_version_archive(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"filename": "demo-v2.zip", "content": b"PK-safe"}

        def rollback(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                id="binding-1",
                skill_id="skill-1",
                evaluation_target_id="target-1",
                active_version_id="version-1",
                active_level="validated",
                lock_version=4,
                created_at=now,
                updated_at=now,
            )

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(skill_registry_service=Registry()))
    )
    exported = skill_routes.export_skill_version(
        "skill-1",
        "version-2",
        request,  # type: ignore[arg-type]
    )
    rollback = skill_routes.rollback_skill_binding(
        "skill-1",
        "target-1",
        skill_routes.SkillRollback(
            version_id="version-1",
            expected_lock_version=3,
        ),
        request,  # type: ignore[arg-type]
    )

    assert exported.body == b"PK-safe"
    assert exported.headers["content-type"] == "application/zip"
    assert exported.headers["content-disposition"] == ('attachment; filename="demo-v2.zip"')
    assert exported.headers["cache-control"] == "no-store"
    assert exported.headers["x-content-type-options"] == "nosniff"
    assert rollback["lock_version"] == 4
    assert calls == [
        {"skill_id": "skill-1", "version_id": "version-2"},
        {
            "skill_id": "skill-1",
            "evaluation_target_id": "target-1",
            "version_id": "version-1",
            "expected_lock_version": 3,
            "reason": "manual_api_rollback",
        },
    ]
    with pytest.raises(ValidationError):
        skill_routes.SkillRollback(
            version_id="version-1",
            expected_lock_version=0,
        )
    with pytest.raises(ValidationError):
        skill_routes.SkillRollback(
            version_id="version-1",
            expected_lock_version=1,
            reason="   ",
        )
    assert (
        skill_routes.SkillRollback(
            version_id="version-1",
            expected_lock_version=1,
            reason="  operator rollback  ",
        ).reason
        == "operator rollback"
    )


def test_openapi_describes_pagination_download_types_and_rollback_lock() -> None:
    schema = create_app().openapi()
    detail = schema["paths"]["/api/v1/skill-optimization/experiments/{experiment_id}/detail"]["get"]
    parameters = {item["name"]: item["schema"] for item in detail["parameters"]}
    assert parameters["epoch_offset"]["minimum"] == 0
    assert parameters["epoch_limit"] == {
        "type": "integer",
        "maximum": 100,
        "minimum": 1,
        "default": 20,
        "title": "Epoch Limit",
    }
    experiment_list = schema["paths"]["/api/v1/skill-optimization/experiments"]["get"]
    experiment_parameters = {
        item["name"]: item["schema"] for item in experiment_list["parameters"]
    }
    assert experiment_parameters["limit"]["maximum"] == 500
    assert experiment_parameters["offset"]["minimum"] == 0
    event_list = schema["paths"][
        "/api/v1/skill-optimization/experiments/{experiment_id}/events"
    ]["get"]
    event_parameters = {item["name"]: item["schema"] for item in event_list["parameters"]}
    assert event_parameters["limit"]["default"] == 500

    ledger_operation = schema["paths"][
        "/api/v1/skill-optimization/experiments/{experiment_id}/export"
    ]["get"]
    export_parameters = {item["name"]: item["schema"] for item in ledger_operation["parameters"]}
    assert export_parameters["format"]["enum"] == ["json", "markdown", "csv"]
    ledger_download = ledger_operation["responses"]["200"]
    assert set(ledger_download["content"]) == {
        "application/json",
        "text/markdown",
        "text/csv",
    }
    assert "Content-Disposition" in ledger_download["headers"]

    version_download = schema["paths"]["/api/v1/skills/{skill_id}/versions/{version_id}/export"][
        "get"
    ]["responses"]["200"]
    assert version_download["content"] == {
        "application/zip": {"schema": {"type": "string", "format": "binary"}}
    }

    rollback_operation = schema["paths"][
        "/api/v1/skills/{skill_id}/bindings/{evaluation_target_id}/rollback"
    ]["post"]
    rollback_ref = rollback_operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    rollback_schema = schema["components"]["schemas"][rollback_ref.rsplit("/", 1)[-1]]
    assert set(rollback_schema["required"]) == {
        "version_id",
        "expected_lock_version",
    }
    assert rollback_schema["properties"]["expected_lock_version"]["minimum"] == 1
