"""P19 Harness, Model and Evaluation Target catalog endpoints."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from analystbench.evaluation.target import (
    EvaluationHarnessService,
    EvaluationModelService,
    EvaluationTargetService,
)

router = APIRouter(tags=["evaluation-targets"])


class HarnessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    family: str | None = Field(default=None, max_length=100)
    model_policy: str | None = None
    command_template: str = Field(min_length=1)
    tool_dir: str | None = None
    skill_base_dir: str | None = None
    max_output_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)


class HarnessRevise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    family: str | None = Field(default=None, max_length=100)
    model_policy: str | None = None
    command_template: str | None = Field(default=None, min_length=1)
    tool_dir: str | None = None
    skill_base_dir: str | None = None
    max_output_bytes: int | None = Field(default=None, ge=1024)


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    argument: str | None = Field(default=None, min_length=1, max_length=255)
    timeout_seconds: int = Field(default=21600, ge=1, le=21600)
    concurrency_limit: int = Field(default=1, ge=1, le=32)


class ModelRevise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    argument: str | None = Field(default=None, min_length=1, max_length=255)
    timeout_seconds: int | None = Field(default=None, ge=1, le=21600)
    concurrency_limit: int | None = Field(default=None, ge=1, le=32)


class TargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness_id: str
    model_id: str | None = None
    model_argument: str | None = Field(default=None, min_length=1, max_length=255)


class HarnessResponse(BaseModel):
    id: str
    key: str
    name: str
    family: str | None
    version: int
    model_policy: str
    tool_dir: str | None
    skill_base_dir: str | None
    command_template: str
    max_output_bytes: int
    status: str
    content_hash: str
    probe: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ModelResponse(BaseModel):
    id: str
    key: str
    name: str
    version: int
    argument: str
    timeout_seconds: int
    concurrency_limit: int
    status: str
    content_hash: str
    created_at: datetime
    updated_at: datetime


class TargetResponse(BaseModel):
    id: str
    key: str
    version: int
    display_name: str
    harness: dict[str, Any]
    model: dict[str, Any] | None
    model_argument: str | None
    status: str
    content_hash: str
    probe: dict[str, Any]
    materialized_method_id: str | None
    created_at: datetime
    updated_at: datetime


def harnesses(request: Request) -> EvaluationHarnessService:
    return request.app.state.evaluation_harness_service


def models(request: Request) -> EvaluationModelService:
    return request.app.state.evaluation_model_service


def targets(request: Request) -> EvaluationTargetService:
    return request.app.state.evaluation_target_service


@router.post("/evaluation-harnesses", response_model=HarnessResponse, status_code=status.HTTP_201_CREATED)
def create_harness(payload: HarnessCreate, request: Request) -> dict[str, Any]:
    item = harnesses(request).create(
        harness_key=payload.key,
        model_policy=payload.model_policy
        or ("required" if "{model}" in payload.command_template else "none"),
        **payload.model_dump(exclude={"key", "model_policy"}),
    )
    return EvaluationHarnessService.view(item)


@router.get("/evaluation-harnesses", response_model=list[HarnessResponse])
def list_harnesses(request: Request) -> list[dict[str, Any]]:
    return [EvaluationHarnessService.view(item) for item in harnesses(request).list()]


@router.get("/evaluation-harnesses/{harness_id}", response_model=HarnessResponse)
def get_harness(harness_id: str, request: Request) -> dict[str, Any]:
    return EvaluationHarnessService.view(harnesses(request).get(harness_id))


@router.post("/evaluation-harnesses/{harness_id}:probe", response_model=HarnessResponse)
def probe_harness(harness_id: str, request: Request) -> dict[str, Any]:
    return EvaluationHarnessService.view(harnesses(request).probe(harness_id))


@router.post("/evaluation-harnesses/{harness_id}:freeze", response_model=HarnessResponse)
def freeze_harness(harness_id: str, request: Request) -> dict[str, Any]:
    return EvaluationHarnessService.view(harnesses(request).freeze(harness_id))


@router.post("/evaluation-harnesses/{harness_id}:revise", response_model=HarnessResponse)
def revise_harness(
    harness_id: str, payload: HarnessRevise, request: Request
) -> dict[str, Any]:
    return EvaluationHarnessService.view(
        harnesses(request).revise(harness_id, **payload.model_dump(exclude_unset=True))
    )


@router.post("/evaluation-harnesses/{harness_id}:archive", response_model=HarnessResponse)
def archive_harness(harness_id: str, request: Request) -> dict[str, Any]:
    return EvaluationHarnessService.view(harnesses(request).archive(harness_id))


@router.post("/evaluation-models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelCreate, request: Request) -> dict[str, Any]:
    item = models(request).create(
        model_key=payload.key,
        name=payload.name or payload.key,
        argument=payload.argument or payload.key,
        timeout_seconds=payload.timeout_seconds,
        concurrency_limit=payload.concurrency_limit,
    )
    return EvaluationModelService.view(item)


@router.get("/evaluation-models", response_model=list[ModelResponse])
def list_models(request: Request) -> list[dict[str, Any]]:
    return [EvaluationModelService.view(item) for item in models(request).list()]


@router.post("/evaluation-models/{model_id}:revise", response_model=ModelResponse)
def revise_model(model_id: str, payload: ModelRevise, request: Request) -> dict[str, Any]:
    return EvaluationModelService.view(
        models(request).revise(model_id, **payload.model_dump(exclude_unset=True))
    )


@router.post("/evaluation-models/{model_id}:archive", response_model=ModelResponse)
def archive_model(model_id: str, request: Request) -> dict[str, Any]:
    return EvaluationModelService.view(models(request).archive(model_id))


@router.post("/evaluation-targets", response_model=TargetResponse, status_code=status.HTTP_201_CREATED)
def create_target(payload: TargetCreate, request: Request) -> dict[str, Any]:
    item = targets(request).create(**payload.model_dump())
    return targets(request).target_view(item.id)


@router.get("/evaluation-targets", response_model=list[TargetResponse])
def list_targets(request: Request) -> list[dict[str, Any]]:
    return targets(request).list_views()


@router.get("/evaluation-targets/{target_id}", response_model=TargetResponse)
def get_target(target_id: str, request: Request) -> dict[str, Any]:
    return targets(request).target_view(target_id)


@router.post("/evaluation-targets/{target_id}:probe", response_model=TargetResponse)
def probe_target(target_id: str, request: Request) -> dict[str, Any]:
    targets(request).probe(target_id)
    return targets(request).target_view(target_id)


@router.post("/evaluation-targets/{target_id}:freeze", response_model=TargetResponse)
def freeze_target(target_id: str, request: Request) -> dict[str, Any]:
    targets(request).freeze(target_id)
    return targets(request).target_view(target_id)


@router.post("/evaluation-targets/{target_id}:archive", response_model=TargetResponse)
def archive_target(target_id: str, request: Request) -> dict[str, Any]:
    targets(request).archive(target_id)
    return targets(request).target_view(target_id)
