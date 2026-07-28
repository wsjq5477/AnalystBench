"""Frontend-ready Case Library and multi-report evaluation endpoints."""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field, model_validator

from analystbench.case_library import (
    CaseLibraryService,
    EvaluationBatchService,
    ReportDraftService,
    report_payload_from_text,
)
from analystbench.config import Settings

router = APIRouter(tags=["case-library"])


class CaseDraftCreate(BaseModel):
    payload: dict[str, Any]
    case_key: str | None = None
    source_filename: str | None = None
    test_set: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=255)


class CaseDraftGenerate(BaseModel):
    reference_answer: str = Field(min_length=1)
    problem_statement: str = ""
    case_key: str | None = None
    runner: str = "claude-code"
    runner_configuration: dict[str, Any] = Field(default_factory=dict)
    source_filename: str | None = None
    test_set: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=255)


class Answer(BaseModel):
    question_id: str
    value: Any


class Answers(BaseModel):
    answers: list[Answer] = Field(min_length=1)


class CaseDraftResponse(BaseModel):
    id: str
    case_key: str | None
    source_filename: str | None
    test_set: str | None
    category: str | None
    status: str
    questions: list[dict[str, Any]]
    summary: dict[str, Any]
    resources: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ReportDraftCreate(BaseModel):
    payload: dict[str, Any]


class ReportDraftConvert(BaseModel):
    candidate_name: str = Field(min_length=1)
    candidate_report: str = Field(min_length=1)
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportDraftResponse(BaseModel):
    id: str
    candidate_name: str | None
    status: str
    issues: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class RawEvaluationReport(BaseModel):
    filename: str = Field(min_length=1)
    content: str = Field(min_length=1)
    candidate_name: str | None = None
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationBatchCreate(BaseModel):
    case_key: str = Field(min_length=1)
    reports: list[dict[str, Any]] = Field(default_factory=list)
    raw_reports: list[RawEvaluationReport] = Field(default_factory=list)
    report_draft_ids: list[str] = Field(default_factory=list)
    judge_runner: str = "claude-code"
    judge_configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_reports(self) -> "EvaluationBatchCreate":
        if not self.reports and not self.raw_reports and not self.report_draft_ids:
            raise ValueError("at least one raw_report, report, or report_draft_id is required")
        return self


class CaseOrganize(BaseModel):
    source_filename: str = Field(min_length=1)
    case_key: str | None = Field(default=None, min_length=1, max_length=255)
    test_set: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=255)


class EvaluationBatchResponse(BaseModel):
    id: str
    case_key: str | None
    status: str
    report_count: int
    resources: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def case_service(request: Request) -> CaseLibraryService:
    return request.app.state.case_library_service


def report_service(request: Request) -> ReportDraftService:
    return request.app.state.report_draft_service


def batch_service(request: Request) -> EvaluationBatchService:
    return request.app.state.evaluation_batch_service


@router.post("/case-drafts", response_model=CaseDraftResponse, status_code=status.HTTP_201_CREATED)
def create_case_draft(payload: CaseDraftCreate, request: Request) -> dict[str, Any]:
    item = case_service(request).create_draft(
        payload.payload,
        case_key=payload.case_key,
        source_filename=payload.source_filename,
        test_set=payload.test_set,
        category=payload.category,
    )
    return CaseLibraryService.view(item)


@router.post(
    "/case-drafts-generate",
    response_model=CaseDraftResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/case-drafts:generate",
    response_model=CaseDraftResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
def generate_case_draft(payload: CaseDraftGenerate, request: Request) -> dict[str, Any]:
    service = case_service(request)
    item = service.create_generation(
        reference_answer=payload.reference_answer,
        problem_statement=payload.problem_statement,
        case_key=payload.case_key,
        runner_id=payload.runner,
        runner_configuration=payload.runner_configuration,
        source_filename=payload.source_filename,
        test_set=payload.test_set,
        category=payload.category,
    )
    return CaseLibraryService.view(item)


@router.get("/case-drafts/{draft_id}", response_model=CaseDraftResponse)
def get_case_draft(draft_id: str, request: Request) -> dict[str, Any]:
    return CaseLibraryService.view(case_service(request).get_draft(draft_id))


@router.post("/case-drafts/{draft_id}/answers", response_model=CaseDraftResponse)
def answer_case_draft(draft_id: str, payload: Answers, request: Request) -> dict[str, Any]:
    item = case_service(request).submit_answers(
        draft_id, [answer.model_dump() for answer in payload.answers]
    )
    return CaseLibraryService.view(item)


@router.post("/case-drafts/{draft_id}/publish", response_model=CaseDraftResponse)
@router.post(
    "/case-drafts/{draft_id}:publish",
    response_model=CaseDraftResponse,
    include_in_schema=False,
)
def publish_case_draft(draft_id: str, request: Request) -> dict[str, Any]:
    service = case_service(request)
    published = service.publish(draft_id)
    view = CaseLibraryService.view(published)

    # Sync published Case JSON to the formal results directory so the
    # frontend local-cases/tree picks it up automatically.
    settings: Settings = request.app.state.settings
    resources = view.get("resources") or {}
    ts_key = (resources.get("test_set") or {}).get("key", "default")
    cat_key = (resources.get("category") or {}).get("key", "uncategorized")
    case_key = view.get("case_key", "unknown")
    formal_case_dir = settings.results_formal_path / ts_key / cat_key / case_key
    formal_case_dir.mkdir(parents=True, exist_ok=True)
    formal_case_file = formal_case_dir / "case.json"
    published_draft = service.get_draft(published.id)
    formal_case_file.write_text(
        json.dumps(
            json.loads(published_draft.working_json),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return view


@router.get("/benchmark-cases")
def list_benchmark_cases(request: Request) -> list[dict[str, Any]]:
    return [CaseLibraryService.view(item) for item in case_service(request).list_published()]


@router.post("/benchmark-cases/{case_key}:organize", response_model=CaseDraftResponse)
def organize_benchmark_case(
    case_key: str, payload: CaseOrganize, request: Request
) -> dict[str, Any]:
    item = case_service(request).organize_published(
        case_key,
        payload.source_filename,
        payload.test_set,
        payload.category,
        payload.case_key,
    )
    return CaseLibraryService.view(item)


@router.post(
    "/report-drafts", response_model=ReportDraftResponse, status_code=status.HTTP_201_CREATED
)
def create_report_draft(payload: ReportDraftCreate, request: Request) -> dict[str, Any]:
    return ReportDraftService.view(report_service(request).create_draft(payload.payload))


@router.post(
    "/report-drafts:convert",
    response_model=ReportDraftResponse,
    status_code=status.HTTP_201_CREATED,
)
def convert_report_draft(payload: ReportDraftConvert, request: Request) -> dict[str, Any]:
    item = report_service(request).create_from_text(
        payload.candidate_name,
        payload.candidate_report,
        payload.description,
        payload.metadata,
    )
    return ReportDraftService.view(item)


@router.get("/report-drafts/{draft_id}", response_model=ReportDraftResponse)
def get_report_draft(draft_id: str, request: Request) -> dict[str, Any]:
    return ReportDraftService.view(report_service(request).get_draft(draft_id))


@router.post(
    "/evaluation-batches",
    response_model=EvaluationBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_evaluation_batch(payload: EvaluationBatchCreate, request: Request) -> dict[str, Any]:
    raw_payloads = [
        report_payload_from_text(
            report.filename,
            report.content,
            report.candidate_name,
            report.description,
            report.metadata,
        )
        for report in payload.raw_reports
    ]
    item = batch_service(request).create_batch(
        payload.case_key,
        [*payload.reports, *raw_payloads],
        payload.report_draft_ids,
        payload.judge_runner,
        payload.judge_configuration,
    )
    return EvaluationBatchService.view(item)


@router.get("/evaluation-batches/{batch_id}", response_model=EvaluationBatchResponse)
def get_evaluation_batch(batch_id: str, request: Request) -> dict[str, Any]:
    return EvaluationBatchService.view(batch_service(request).get_batch(batch_id))


@router.get("/evaluation-batches/{batch_id}/result")
def get_evaluation_batch_result(batch_id: str, request: Request) -> dict[str, Any]:
    return batch_service(request).result(batch_id)
