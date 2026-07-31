"""Execution Profile and Candidate Generation Run endpoints."""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from analystbench.agent_execution import AgentExecutionService
from analystbench.db.models import AgentCaseRun, CandidateGenerationRun, ExecutionProfile

router = APIRouter(tags=["agent-execution"])


class ExecutionProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    runner: str
    configuration: dict[str, Any] = Field(default_factory=dict)


class ExecutionProfileResponse(BaseModel):
    id: str
    name: str
    version_number: int
    runner: str
    configuration: dict[str, Any]
    status: str
    content_hash: str
    frozen_at: datetime

    @classmethod
    def from_model(cls, item: ExecutionProfile) -> "ExecutionProfileResponse":
        return cls(
            id=item.id,
            name=item.name,
            version_number=item.version_number,
            runner=item.runner,
            configuration=json.loads(item.configuration_json),
            status=item.status,
            content_hash=item.content_hash,
            frozen_at=item.frozen_at,
        )


class GenerationRunCreate(BaseModel):
    dataset_version_id: str
    candidate_version_id: str
    execution_profile_id: str


class GenerationRunResponse(BaseModel):
    id: str
    dataset_version_id: str
    candidate_version_id: str
    execution_profile_id: str
    status: str
    manifest: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, item: CandidateGenerationRun) -> "GenerationRunResponse":
        return cls(
            id=item.id,
            dataset_version_id=item.dataset_version_id,
            candidate_version_id=item.candidate_version_id,
            execution_profile_id=item.execution_profile_id,
            status=item.status,
            manifest=json.loads(item.manifest_json),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class AgentCaseRunResponse(BaseModel):
    id: str
    generation_run_id: str
    case_revision_id: str
    status: str
    attempt: int
    artifact: dict[str, Any]
    error_code: str | None

    @classmethod
    def from_model(cls, item: AgentCaseRun) -> "AgentCaseRunResponse":
        return cls(
            id=item.id,
            generation_run_id=item.generation_run_id,
            case_revision_id=item.case_revision_id,
            status=item.status,
            attempt=item.attempt,
            artifact=json.loads(item.artifact_json),
            error_code=item.error_code,
        )


def execution(request: Request) -> AgentExecutionService:
    return request.app.state.agent_execution_service


@router.post(
    "/execution-profiles",
    response_model=ExecutionProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_profile(payload: ExecutionProfileCreate, request: Request) -> ExecutionProfileResponse:
    return ExecutionProfileResponse.from_model(
        execution(request).create_profile(payload.name, payload.runner, payload.configuration)
    )


@router.get("/execution-profiles", response_model=list[ExecutionProfileResponse])
def list_profiles(request: Request) -> list[ExecutionProfileResponse]:
    return [
        ExecutionProfileResponse.from_model(item)
        for item in execution(request).list_profiles()
    ]


@router.post("/execution-profiles/{profile_id}:validate")
def validate_profile(profile_id: str, request: Request) -> dict[str, Any]:
    result = execution(request).probe_profile(profile_id)
    return {
        "available": result.available,
        "version_output": result.version_output,
        "error": result.error,
    }


@router.post("/execution-profiles/{profile_id}:freeze", response_model=ExecutionProfileResponse)
def freeze_profile(profile_id: str, request: Request) -> ExecutionProfileResponse:
    return ExecutionProfileResponse.from_model(execution(request).freeze_profile(profile_id))


@router.post(
    "/candidate-generation-runs",
    response_model=GenerationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_generation_run(payload: GenerationRunCreate, request: Request) -> GenerationRunResponse:
    return GenerationRunResponse.from_model(
        execution(request).create_generation_run(
            payload.dataset_version_id, payload.candidate_version_id, payload.execution_profile_id
        )
    )


@router.get("/candidate-generation-runs/{run_id}", response_model=GenerationRunResponse)
def get_generation_run(run_id: str, request: Request) -> GenerationRunResponse:
    return GenerationRunResponse.from_model(execution(request).get_generation_run(run_id))


@router.get(
    "/candidate-generation-runs/{run_id}/case-runs", response_model=list[AgentCaseRunResponse]
)
def list_case_runs(run_id: str, request: Request) -> list[AgentCaseRunResponse]:
    return [
        AgentCaseRunResponse.from_model(item) for item in execution(request).list_case_runs(run_id)
    ]
