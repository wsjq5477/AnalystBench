"""Benchmark Run APIs backed by durable Local Worker jobs."""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from analystbench.db.models import BenchmarkCaseRun, BenchmarkRun
from analystbench.evaluation.benchmark import BenchmarkService

router = APIRouter(tags=["benchmarks"])


class BenchmarkRunCreate(BaseModel):
    dataset_version_id: str
    candidate_version_id: str
    scoring_policy_version_id: str


class BenchmarkRunResponse(BaseModel):
    id: str
    dataset_version_id: str
    candidate_version_id: str
    scoring_policy_version_id: str
    status: str
    cancellation_requested: bool
    manifest: dict[str, Any]
    summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, item: BenchmarkRun) -> "BenchmarkRunResponse":
        return cls(
            id=item.id,
            dataset_version_id=item.dataset_version_id,
            candidate_version_id=item.candidate_version_id,
            scoring_policy_version_id=item.scoring_policy_version_id,
            status=item.status,
            cancellation_requested=item.cancellation_requested,
            manifest=json.loads(item.manifest_json),
            summary=json.loads(item.summary_json),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class BenchmarkCaseRunResponse(BaseModel):
    id: str
    benchmark_run_id: str
    case_revision_id: str
    status: str
    stage: str
    attempt: int
    result_content_hash: str | None
    attempts: list[dict[str, Any]]
    error_code: str | None

    @classmethod
    def from_model(cls, item: BenchmarkCaseRun) -> "BenchmarkCaseRunResponse":
        return cls(
            id=item.id,
            benchmark_run_id=item.benchmark_run_id,
            case_revision_id=item.case_revision_id,
            status=item.status,
            stage=item.stage,
            attempt=item.attempt,
            result_content_hash=item.result_content_hash,
            attempts=json.loads(item.attempts_json),
            error_code=item.error_code,
        )


def benchmarks(request: Request) -> BenchmarkService:
    return request.app.state.benchmark_service


@router.get("/benchmark-runs", response_model=list[BenchmarkRunResponse])
def list_benchmark_runs(request: Request) -> list[BenchmarkRunResponse]:
    """List all benchmark runs."""
    service: BenchmarkService = request.app.state.benchmark_service
    runs = service.list_runs()
    return [BenchmarkRunResponse.from_model(r) for r in runs]


@router.post(
    "/benchmark-runs", response_model=BenchmarkRunResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_benchmark_run(payload: BenchmarkRunCreate, request: Request) -> BenchmarkRunResponse:
    return BenchmarkRunResponse.from_model(
        benchmarks(request).create_run(
            payload.dataset_version_id,
            payload.candidate_version_id,
            payload.scoring_policy_version_id,
        )
    )


@router.get("/benchmark-runs/{run_id}", response_model=BenchmarkRunResponse)
def get_benchmark_run(run_id: str, request: Request) -> BenchmarkRunResponse:
    return BenchmarkRunResponse.from_model(benchmarks(request).get_run(run_id))


@router.post("/benchmark-runs/{run_id}:cancel", response_model=BenchmarkRunResponse)
def cancel_benchmark_run(run_id: str, request: Request) -> BenchmarkRunResponse:
    return BenchmarkRunResponse.from_model(benchmarks(request).cancel_run(run_id))


@router.post("/benchmark-runs/{run_id}:retry-failed")
def retry_failed_benchmark_cases(run_id: str, request: Request) -> dict[str, int]:
    return {"retried": benchmarks(request).retry_failed(run_id)}


@router.get("/benchmark-runs/{run_id}/case-runs", response_model=list[BenchmarkCaseRunResponse])
def list_benchmark_case_runs(run_id: str, request: Request) -> list[BenchmarkCaseRunResponse]:
    return [
        BenchmarkCaseRunResponse.from_model(item)
        for item in benchmarks(request).list_case_runs(run_id)
    ]


@router.get("/benchmark-case-runs/{case_run_id}/result")
def get_benchmark_case_result(case_run_id: str, request: Request) -> dict[str, Any]:
    return benchmarks(request).get_case_result(case_run_id)


@router.get("/benchmark-runs/{run_id}/export")
def export_benchmark_run(run_id: str, request: Request) -> dict[str, Any]:
    return benchmarks(request).export_run(run_id)
