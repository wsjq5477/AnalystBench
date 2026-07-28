"""P15 evaluation method and submission endpoints."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from analystbench.evaluation_submission import (
    EvaluationMethodService,
    EvaluationSubmissionService,
)

router = APIRouter(tags=["evaluation-submissions"])


class EvaluationMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    command_template: str = Field(min_length=1)
    tool_dir: str | None = None
    timeout_seconds: int = Field(default=1800, ge=1, le=7200)
    max_output_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    concurrency_limit: int = Field(default=1, ge=1, le=32)


class EvaluationMethodRevise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    command_template: str | None = Field(default=None, min_length=1)
    tool_dir: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=7200)
    max_output_bytes: int | None = Field(default=None, ge=1024)
    concurrency_limit: int | None = Field(default=None, ge=1, le=32)


class EvaluationMethodResponse(BaseModel):
    id: str
    key: str
    name: str
    version: int
    tool_dir: str | None
    command_template: str
    timeout_seconds: int
    max_output_bytes: int
    concurrency_limit: int
    status: str
    content_hash: str
    probe: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EvaluationSubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_key: str = Field(min_length=1, max_length=255)
    case_paths: list[str] | None = None
    method_ids: list[str] = Field(min_length=1)
    judge_runner: str = "claude-code"


class EvaluationSubmissionResponse(BaseModel):
    id: str
    dataset_key: str
    timestamp: str
    status: str
    schedule_run_id: str | None
    method_ids: list[str]
    methods: list[dict[str, Any]]
    case_count: int
    summary: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def methods(request: Request) -> EvaluationMethodService:
    return request.app.state.evaluation_method_service


def submissions(request: Request) -> EvaluationSubmissionService:
    return request.app.state.evaluation_submission_service


@router.post(
    "/evaluation-methods",
    response_model=EvaluationMethodResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_method(payload: EvaluationMethodCreate, request: Request) -> dict[str, Any]:
    item = methods(request).create(
        method_key=payload.key,
        name=payload.name or payload.key,
        command_template=payload.command_template,
        tool_dir=payload.tool_dir,
        timeout_seconds=payload.timeout_seconds,
        max_output_bytes=payload.max_output_bytes,
        concurrency_limit=payload.concurrency_limit,
    )
    return EvaluationMethodService.view(item)


@router.get("/evaluation-methods", response_model=list[EvaluationMethodResponse])
def list_methods(request: Request) -> list[dict[str, Any]]:
    return [EvaluationMethodService.view(item) for item in methods(request).list()]


@router.get("/evaluation-methods/{method_id}", response_model=EvaluationMethodResponse)
def get_method(method_id: str, request: Request) -> dict[str, Any]:
    return EvaluationMethodService.view(methods(request).get(method_id))


@router.post("/evaluation-methods/{method_id}:probe", response_model=EvaluationMethodResponse)
def probe_method(method_id: str, request: Request) -> dict[str, Any]:
    return EvaluationMethodService.view(methods(request).probe(method_id))


@router.post("/evaluation-methods/{method_id}:freeze", response_model=EvaluationMethodResponse)
def freeze_method(method_id: str, request: Request) -> dict[str, Any]:
    return EvaluationMethodService.view(methods(request).freeze(method_id))


@router.post("/evaluation-methods/{method_id}:archive", response_model=EvaluationMethodResponse)
def archive_method(method_id: str, request: Request) -> dict[str, Any]:
    return EvaluationMethodService.view(methods(request).archive(method_id))


@router.delete("/evaluation-methods/{method_id}")
def delete_method(method_id: str, request: Request) -> dict[str, int]:
    return methods(request).delete(method_id)


@router.post("/evaluation-methods/{method_id}:revise", response_model=EvaluationMethodResponse)
def revise_method(
    method_id: str,
    payload: EvaluationMethodRevise,
    request: Request,
) -> dict[str, Any]:
    item = methods(request).revise(
        method_id,
        **payload.model_dump(exclude_unset=True),
    )
    return EvaluationMethodService.view(item)


@router.post(
    "/evaluation-submissions",
    response_model=EvaluationSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_submission(payload: EvaluationSubmissionCreate, request: Request) -> dict[str, Any]:
    item = submissions(request).create_submission(
        payload.dataset_key,
        payload.method_ids,
        payload.judge_runner,
        case_paths=payload.case_paths,
    )
    return EvaluationSubmissionService.submission_view(item)


@router.get("/evaluation-submissions", response_model=list[EvaluationSubmissionResponse])
def list_submissions(request: Request) -> list[dict[str, Any]]:
    return [
        EvaluationSubmissionService.submission_view(item)
        for item in submissions(request).list_submissions()
    ]


@router.get(
    "/evaluation-submissions/{submission_id}",
    response_model=EvaluationSubmissionResponse,
)
def get_submission(submission_id: str, request: Request) -> dict[str, Any]:
    return EvaluationSubmissionService.submission_view(
        submissions(request).get_submission(submission_id)
    )


@router.get("/evaluation-submissions/{submission_id}/case-runs")
def get_submission_case_runs(submission_id: str, request: Request) -> list[dict[str, Any]]:
    service = submissions(request)
    return [service.case_run_view(item) for item in service.list_case_runs(submission_id)]


@router.post(
    "/evaluation-submissions/{submission_id}:cancel",
    response_model=EvaluationSubmissionResponse,
)
def cancel_submission(submission_id: str, request: Request) -> dict[str, Any]:
    item = submissions(request).cancel_submission(submission_id)
    return EvaluationSubmissionService.submission_view(item)


@router.post("/evaluation-case-runs/{case_run_id}:retry-failed")
def retry_case_run(case_run_id: str, request: Request) -> dict[str, Any]:
    service = submissions(request)
    return service.case_run_view(service.retry_case(case_run_id))


@router.get("/evaluation-method-runs/{method_run_id}/artifacts")
def get_method_run_artifacts(method_run_id: str, request: Request) -> dict[str, Any]:
    return submissions(request).method_artifacts(method_run_id)
