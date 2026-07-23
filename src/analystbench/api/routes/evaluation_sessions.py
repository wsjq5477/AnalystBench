"""Single-entry draft review and scoring session endpoints."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from analystbench.db.models import EvaluationSession
from analystbench.evaluation_session import EvaluationSessionService

router = APIRouter(tags=["evaluation-sessions"])


class EvaluationSessionCreate(BaseModel):
    case_draft: dict[str, Any]
    report_drafts: list[dict[str, Any]] = Field(min_length=1)


class EvaluationAnswer(BaseModel):
    question_id: str
    value: Any


class EvaluationAnswers(BaseModel):
    answers: list[EvaluationAnswer] = Field(min_length=1)


class EvaluationSessionResponse(BaseModel):
    id: str
    status: str
    questions: list[dict[str, Any]]
    required_questions: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    resources: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, item: EvaluationSession) -> "EvaluationSessionResponse":
        return cls.model_validate(EvaluationSessionService.view(item))


def service(request: Request) -> EvaluationSessionService:
    return request.app.state.evaluation_session_service


@router.post(
    "/evaluation-sessions",
    response_model=EvaluationSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_session(payload: EvaluationSessionCreate, request: Request) -> EvaluationSessionResponse:
    item = service(request).create_session(payload.case_draft, payload.report_drafts)
    return EvaluationSessionResponse.from_model(item)


@router.get("/evaluation-sessions/{session_id}", response_model=EvaluationSessionResponse)
def get_session(session_id: str, request: Request) -> EvaluationSessionResponse:
    return EvaluationSessionResponse.from_model(service(request).get_session(session_id))


@router.post("/evaluation-sessions/{session_id}/answers", response_model=EvaluationSessionResponse)
def submit_answers(
    session_id: str, payload: EvaluationAnswers, request: Request
) -> EvaluationSessionResponse:
    item = service(request).submit_answers(
        session_id, [answer.model_dump() for answer in payload.answers]
    )
    return EvaluationSessionResponse.from_model(item)


@router.get("/evaluation-sessions/{session_id}/result")
def get_result(session_id: str, request: Request) -> dict[str, Any]:
    return service(request).result(session_id)
