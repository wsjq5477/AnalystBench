"""Eval Spec draft, validation and immutable freezing endpoints."""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from analystbench.db.models import EvalSpecDraft, EvalSpecVersion, ScoringPolicyVersion
from analystbench.eval_spec import EvalSpecService

router = APIRouter(tags=["eval-specs"])


class ScoringPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    policy: dict[str, Any] | None = None


class ScoringPolicyResponse(BaseModel):
    id: str
    name: str
    version_number: int
    policy: dict[str, Any]
    content_hash: str

    @classmethod
    def from_model(cls, item: ScoringPolicyVersion) -> "ScoringPolicyResponse":
        return cls(
            id=item.id,
            name=item.name,
            version_number=item.version_number,
            policy=json.loads(item.policy_json),
            content_hash=item.content_hash,
        )


class EvalSpecGenerate(BaseModel):
    case_revision_id: str
    scoring_policy_version_id: str


class EvalSpecDraftCreate(BaseModel):
    case_revision_id: str
    payload: dict[str, Any]


class EvalSpecDraftResponse(BaseModel):
    id: str
    case_revision_id: str
    payload: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, item: EvalSpecDraft) -> "EvalSpecDraftResponse":
        return cls(
            id=item.id,
            case_revision_id=item.case_revision_id,
            payload=json.loads(item.payload_json),
            status=item.status,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class EvalSpecVersionResponse(BaseModel):
    id: str
    case_revision_id: str
    version_number: int
    payload: dict[str, Any]
    content_hash: str
    frozen_at: datetime

    @classmethod
    def from_model(cls, item: EvalSpecVersion) -> "EvalSpecVersionResponse":
        return cls(
            id=item.id,
            case_revision_id=item.case_revision_id,
            version_number=item.version_number,
            payload=json.loads(item.payload_json),
            content_hash=item.content_hash,
            frozen_at=item.frozen_at,
        )


def eval_specs(request: Request) -> EvalSpecService:
    return request.app.state.eval_spec_service


@router.post(
    "/scoring-policies", response_model=ScoringPolicyResponse, status_code=status.HTTP_201_CREATED
)
def create_scoring_policy(payload: ScoringPolicyCreate, request: Request) -> ScoringPolicyResponse:
    return ScoringPolicyResponse.from_model(
        eval_specs(request).create_scoring_policy(payload.name, payload.policy)
    )


@router.post(
    "/eval-spec-drafts:generate",
    response_model=EvalSpecDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_draft(payload: EvalSpecGenerate, request: Request) -> EvalSpecDraftResponse:
    return EvalSpecDraftResponse.from_model(
        eval_specs(request).generate_draft(
            payload.case_revision_id, payload.scoring_policy_version_id
        )
    )


@router.post(
    "/eval-spec-drafts", response_model=EvalSpecDraftResponse, status_code=status.HTTP_201_CREATED
)
def create_draft(payload: EvalSpecDraftCreate, request: Request) -> EvalSpecDraftResponse:
    return EvalSpecDraftResponse.from_model(
        eval_specs(request).create_draft(payload.case_revision_id, payload.payload)
    )


@router.get("/eval-spec-drafts/{draft_id}", response_model=EvalSpecDraftResponse)
def get_draft(draft_id: str, request: Request) -> EvalSpecDraftResponse:
    return EvalSpecDraftResponse.from_model(eval_specs(request).get_draft(draft_id))


@router.post("/eval-spec-drafts/{draft_id}:validate")
def validate_draft(draft_id: str, request: Request) -> dict[str, Any]:
    errors = eval_specs(request).validate_draft(draft_id)
    return {"valid": not errors, "errors": errors}


@router.post("/eval-spec-drafts/{draft_id}:freeze", response_model=EvalSpecVersionResponse)
def freeze_draft(draft_id: str, request: Request) -> EvalSpecVersionResponse:
    return EvalSpecVersionResponse.from_model(eval_specs(request).freeze_draft(draft_id))
