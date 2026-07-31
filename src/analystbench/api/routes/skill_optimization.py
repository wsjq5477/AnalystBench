"""Skill optimization policy, snapshot and experiment endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from analystbench.skill_optimization.experiment import OptimizationExperimentService

router = APIRouter(tags=["skill-optimization"])


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    execution_profile_id: str
    prompt_bundle: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class VerifierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    static_policy: dict[str, Any] = Field(default_factory=dict)
    gate_policy: dict[str, Any] = Field(default_factory=dict)
    judge_config: dict[str, Any] = Field(default_factory=dict)


class SnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_key: str
    validation_case_paths: list[str] = Field(min_length=1)
    mode: str = "development_regression"
    train_case_paths: list[str] = Field(default_factory=list)
    hidden_test_case_paths: list[str] = Field(default_factory=list)
    prospective_holdout_case_paths: list[str] = Field(default_factory=list)


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    skill_id: str
    base_skill_version_id: str
    evaluation_target_id: str
    data_snapshot_id: str
    optimizer_policy_version_id: str
    verifier_bundle_version_id: str
    max_epochs: int | None = Field(default=None, ge=1)
    created_by: str | None = Field(default=None, max_length=128)


def service(request: Request) -> OptimizationExperimentService:
    return request.app.state.skill_optimization_service


@router.post(
    "/skill-optimization/policies", status_code=status.HTTP_201_CREATED
)
def create_policy(payload: PolicyCreate, request: Request) -> dict[str, Any]:
    item = service(request).create_policy(
        policy_key=payload.key,
        **payload.model_dump(exclude={"key"}),
    )
    return {
        "id": item.id,
        "key": item.policy_key,
        "version": item.version_number,
        "execution_profile_id": item.execution_profile_id,
        "prompt_bundle_hash": item.prompt_bundle_hash,
        "config": json.loads(item.config_json),
        "content_hash": item.content_hash,
        "created_at": item.created_at,
    }


@router.get("/skill-optimization/policies")
def list_policies(request: Request) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "key": item.policy_key,
            "version": item.version_number,
            "execution_profile_id": item.execution_profile_id,
            "config": json.loads(item.config_json or "{}"),
            "content_hash": item.content_hash,
            "created_at": item.created_at,
        }
        for item in service(request).list_policies()
    ]


@router.post(
    "/skill-optimization/verifiers", status_code=status.HTTP_201_CREATED
)
def create_verifier(payload: VerifierCreate, request: Request) -> dict[str, Any]:
    item = service(request).create_verifier(
        bundle_key=payload.key,
        **payload.model_dump(exclude={"key"}),
    )
    return {
        "id": item.id,
        "key": item.bundle_key,
        "version": item.version_number,
        "static_policy": json.loads(item.static_policy_json),
        "gate_policy": json.loads(item.gate_policy_json),
        "judge_config": json.loads(item.judge_config_json),
        "content_hash": item.content_hash,
        "created_at": item.created_at,
    }


@router.get("/skill-optimization/verifiers")
def list_verifiers(request: Request) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "key": item.bundle_key,
            "version": item.version_number,
            "static_policy": json.loads(item.static_policy_json or "{}"),
            "gate_policy": json.loads(item.gate_policy_json or "{}"),
            "judge_config": json.loads(item.judge_config_json or "{}"),
            "content_hash": item.content_hash,
            "created_at": item.created_at,
        }
        for item in service(request).list_verifiers()
    ]


@router.post(
    "/skill-optimization/data-snapshots", status_code=status.HTTP_201_CREATED
)
def create_snapshot(payload: SnapshotCreate, request: Request) -> dict[str, Any]:
    item = service(request).create_snapshot(**payload.model_dump())
    return {
        "id": item.id,
        "dataset_key": item.dataset_key,
        "mode": item.mode,
        "train_cases": json.loads(item.train_cases_json),
        "validation_cases": json.loads(item.validation_cases_json),
        "hidden_test_cases": json.loads(item.hidden_test_cases_json),
        "prospective_holdout_cases": json.loads(
            item.prospective_holdout_cases_json
        ),
        "content_hash": item.content_hash,
        "created_at": item.created_at,
    }


@router.get("/skill-optimization/data-snapshots")
def list_snapshots(request: Request) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "dataset_key": item.dataset_key,
            "mode": item.mode,
            "train_cases": json.loads(item.train_cases_json or "[]"),
            "validation_cases": json.loads(item.validation_cases_json or "[]"),
            "hidden_test_cases": json.loads(item.hidden_test_cases_json or "[]"),
            "prospective_holdout_cases": json.loads(
                item.prospective_holdout_cases_json or "[]"
            ),
            "content_hash": item.content_hash,
            "created_at": item.created_at,
        }
        for item in service(request).list_snapshots()
    ]


@router.post(
    "/skill-optimization/experiments", status_code=status.HTTP_201_CREATED
)
def create_experiment(
    payload: ExperimentCreate, request: Request
) -> dict[str, Any]:
    item = service(request).create_experiment(**payload.model_dump(exclude_none=True))
    return experiment_view(item)


@router.get("/skill-optimization/experiments")
def list_experiments(request: Request) -> list[dict[str, Any]]:
    return [
        experiment_view(item)
        for item in service(request).list_experiments()
    ]


@router.get("/skill-optimization/experiments/{experiment_id}")
def get_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).get(experiment_id))


@router.post("/skill-optimization/experiments/{experiment_id}:start")
def start_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).start(experiment_id))


@router.post("/skill-optimization/experiments/{experiment_id}:resume")
def resume_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).resume(experiment_id))


@router.post("/skill-optimization/experiments/{experiment_id}:cancel")
def cancel_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).cancel(experiment_id))


@router.get("/skill-optimization/experiments/{experiment_id}/events")
def list_events(experiment_id: str, request: Request) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "epoch_id": item.epoch_id,
            "candidate_mutation_id": item.candidate_mutation_id,
            "type": item.event_type,
            "payload": json.loads(item.payload_json or "{}"),
            "created_at": item.created_at,
        }
        for item in service(request).events(experiment_id)
    ]


@router.get("/skill-optimization/experiments/{experiment_id}/detail")
def experiment_detail(experiment_id: str, request: Request) -> dict[str, Any]:
    detail = service(request).detail(experiment_id)
    return {
        "experiment": experiment_view(detail["experiment"]),
        "epochs": detail["epochs"],
    }


@router.get("/skill-optimization/candidates/{candidate_id}")
def candidate_detail(candidate_id: str, request: Request) -> dict[str, Any]:
    return service(request).candidate_detail(candidate_id)


def experiment_view(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "skill_id": item.skill_id,
        "base_skill_version_id": item.base_skill_version_id,
        "evaluation_target_id": item.evaluation_target_id,
        "data_snapshot_id": item.data_snapshot_id,
        "optimizer_policy_version_id": item.optimizer_policy_version_id,
        "verifier_bundle_version_id": item.verifier_bundle_version_id,
        "status": item.status,
        "current_epoch_number": item.current_epoch_number,
        "max_epochs": item.max_epochs,
        "stop_reason": item.stop_reason,
        "config": json.loads(item.config_snapshot_json or "{}"),
        "error": json.loads(item.error_json or "{}"),
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
