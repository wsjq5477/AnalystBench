"""P16 built-in daily evaluation schedule endpoints."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from analystbench.evaluation_schedule import EvaluationScheduleService

router = APIRouter(tags=["evaluation-schedules"])


class EvaluationScheduleWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    dataset_key: str = Field(min_length=1, max_length=255)
    case_mode: str = "all_ready"
    case_paths: list[str] = Field(default_factory=list)
    method_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    judge_runner: str = "claude-code"
    timezone: str = "Asia/Shanghai"
    local_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True


class EvaluationScheduleRunResponse(BaseModel):
    id: str
    schedule_id: str
    trigger_type: str
    scheduled_for: datetime
    status: str
    submission_id: str | None
    submission_timestamp: str | None
    config: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EvaluationScheduleResponse(BaseModel):
    id: str
    name: str
    dataset_key: str
    case_mode: str
    case_paths: list[str]
    method_ids: list[str]
    methods: list[dict[str, Any]]
    target_ids: list[str] = Field(default_factory=list)
    targets: list[dict[str, Any]] = Field(default_factory=list)
    judge_runner: str
    timezone: str
    local_time: str
    enabled: bool
    next_run_at: datetime | None
    last_triggered_at: datetime | None
    latest_run: EvaluationScheduleRunResponse | None
    created_at: datetime
    updated_at: datetime


def schedules(request: Request) -> EvaluationScheduleService:
    return request.app.state.evaluation_schedule_service


@router.get("/evaluation-schedules", response_model=list[EvaluationScheduleResponse])
def list_schedules(request: Request) -> list[dict[str, Any]]:
    service = schedules(request)
    return [service.view(item) for item in service.list()]


@router.post(
    "/evaluation-schedules",
    response_model=EvaluationScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    payload: EvaluationScheduleWrite,
    request: Request,
) -> dict[str, Any]:
    service = schedules(request)
    item = service.create(**payload.model_dump())
    return service.view(item)


@router.get(
    "/evaluation-schedules/{schedule_id}",
    response_model=EvaluationScheduleResponse,
)
def get_schedule(schedule_id: str, request: Request) -> dict[str, Any]:
    service = schedules(request)
    return service.view(service.get(schedule_id))


@router.put(
    "/evaluation-schedules/{schedule_id}",
    response_model=EvaluationScheduleResponse,
)
def update_schedule(
    schedule_id: str,
    payload: EvaluationScheduleWrite,
    request: Request,
) -> dict[str, Any]:
    service = schedules(request)
    return service.view(service.update(schedule_id, **payload.model_dump()))


@router.delete(
    "/evaluation-schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_schedule(schedule_id: str, request: Request) -> None:
    schedules(request).delete(schedule_id)


@router.post(
    "/evaluation-schedules/{schedule_id}:enable",
    response_model=EvaluationScheduleResponse,
)
def enable_schedule(schedule_id: str, request: Request) -> dict[str, Any]:
    service = schedules(request)
    return service.view(service.set_enabled(schedule_id, True))


@router.post(
    "/evaluation-schedules/{schedule_id}:disable",
    response_model=EvaluationScheduleResponse,
)
def disable_schedule(schedule_id: str, request: Request) -> dict[str, Any]:
    service = schedules(request)
    return service.view(service.set_enabled(schedule_id, False))


@router.post(
    "/evaluation-schedules/{schedule_id}:run-now",
    response_model=EvaluationScheduleRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_schedule_now(schedule_id: str, request: Request) -> dict[str, Any]:
    service = schedules(request)
    return service.run_view(service.run_now(schedule_id))


@router.get(
    "/evaluation-schedules/{schedule_id}/runs",
    response_model=list[EvaluationScheduleRunResponse],
)
def list_schedule_runs(schedule_id: str, request: Request) -> list[dict[str, Any]]:
    service = schedules(request)
    return [service.run_view(item) for item in service.list_runs(schedule_id)]


@router.get(
    "/evaluation-schedule-runs/{run_id}",
    response_model=EvaluationScheduleRunResponse,
)
def get_schedule_run(run_id: str, request: Request) -> dict[str, Any]:
    service = schedules(request)
    return service.run_view(service.get_run(run_id))
