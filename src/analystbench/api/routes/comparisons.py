"""A/B Benchmark Run comparison endpoint."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["comparisons"])


class ComparisonCreate(BaseModel):
    baseline_run_id: str
    candidate_run_id: str


@router.post("/comparisons")
def create_comparison(payload: ComparisonCreate, request: Request) -> dict[str, Any]:
    return request.app.state.comparison_service.compare(
        payload.baseline_run_id, payload.candidate_run_id
    )
