"""Dataset, Case, Candidate and immutable-version endpoints."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field

from analystbench.catalog.service import CatalogService
from analystbench.db.models import (
    Candidate,
    CandidateReport,
    CandidateVersion,
    CaseCategory,
    CaseRevision,
    Dataset,
    DatasetVersion,
)

router = APIRouter(tags=["catalog"])


class DatasetCreate(BaseModel):
    dataset_key: str | None = Field(default=None, min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""


class DatasetResponse(BaseModel):
    id: str
    dataset_key: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, item: Dataset) -> "DatasetResponse":
        return cls.model_validate(item, from_attributes=True)


class CaseRevisionCreate(BaseModel):
    case_key: str | None = Field(default=None, min_length=1, max_length=255)
    reference_answer: str = Field(min_length=1)
    category_key: str | None = None
    category_name: str | None = None


class CaseRevisionResponse(BaseModel):
    id: str
    case_id: str
    revision_number: int
    reference_answer_content_hash: str
    content_hash: str
    created_at: datetime

    @classmethod
    def from_model(cls, item: CaseRevision) -> "CaseRevisionResponse":
        return cls(
            id=item.id,
            case_id=item.case_id,
            revision_number=item.revision_number,
            reference_answer_content_hash=item.reference_answer_content_hash,
            content_hash=item.content_hash,
            created_at=item.created_at,
        )


class CaseResponse(BaseModel):
    id: str
    dataset_id: str
    case_key: str
    category_id: str | None
    source_filename: str | None
    revisions: list[CaseRevisionResponse]


class CaseCategoryCreate(BaseModel):
    category_key: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    description: str = ""


class CaseCategoryResponse(BaseModel):
    id: str
    dataset_id: str
    category_key: str
    name: str
    description: str

    @classmethod
    def from_model(cls, item: CaseCategory) -> "CaseCategoryResponse":
        return cls.model_validate(item, from_attributes=True)


class CaseRevisionContentResponse(BaseModel):
    case_id: str
    case_key: str
    revision_id: str
    revision_number: int
    reference_answer: str


class DatasetVersionCreate(BaseModel):
    case_revision_ids: list[str] = Field(min_length=1)


class DatasetVersionResponse(BaseModel):
    id: str
    dataset_id: str
    version_number: int
    content_hash: str
    frozen_at: datetime

    @classmethod
    def from_model(cls, item: DatasetVersion) -> "DatasetVersionResponse":
        return cls.model_validate(item, from_attributes=True)


class CandidateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""


class CandidateResponse(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, item: Candidate) -> "CandidateResponse":
        return cls.model_validate(item, from_attributes=True)


class CandidateVersionCreate(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateVersionResponse(BaseModel):
    id: str
    candidate_id: str
    version_number: int
    metadata: dict[str, Any]
    content_hash: str
    created_at: datetime

    @classmethod
    def from_model(cls, item: CandidateVersion) -> "CandidateVersionResponse":
        return cls(
            id=item.id,
            candidate_id=item.candidate_id,
            version_number=item.version_number,
            metadata=__import__("json").loads(item.metadata_json),
            content_hash=item.content_hash,
            created_at=item.created_at,
        )


class CandidateReportImport(BaseModel):
    case_revision_id: str
    report: str = Field(min_length=1)


class CandidateReportBatchImport(BaseModel):
    reports: list[CandidateReportImport] = Field(min_length=1)


class CandidateReportResponse(BaseModel):
    id: str
    candidate_version_id: str
    case_revision_id: str
    source: str
    report_content_hash: str
    content_hash: str
    created_at: datetime

    @classmethod
    def from_model(cls, item: CandidateReport) -> "CandidateReportResponse":
        return cls.model_validate(item, from_attributes=True)


def catalog(request: Request) -> CatalogService:
    return request.app.state.catalog_service


@router.post("/datasets", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
def create_dataset(payload: DatasetCreate, request: Request) -> DatasetResponse:
    return DatasetResponse.from_model(
        catalog(request).create_dataset(payload.name, payload.description, payload.dataset_key)
    )


@router.get("/datasets", response_model=list[DatasetResponse])
def list_datasets(request: Request) -> list[DatasetResponse]:
    return [DatasetResponse.from_model(item) for item in catalog(request).list_datasets()]


@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, request: Request) -> DatasetResponse:
    return DatasetResponse.from_model(catalog(request).get_dataset(dataset_id))


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: str, request: Request) -> None:
    catalog(request).archive_dataset(dataset_id)


@router.get("/datasets/{dataset_id}/cases", response_model=list[CaseResponse])
def list_cases(dataset_id: str, request: Request) -> list[CaseResponse]:
    service = catalog(request)
    return [
        CaseResponse(
            id=item.id,
            dataset_id=item.dataset_id,
            case_key=item.case_key,
            category_id=item.category_id,
            source_filename=item.source_filename,
            revisions=[
                CaseRevisionResponse.from_model(revision)
                for revision in service.get_case_revisions(item.id)
            ],
        )
        for item in service.list_cases(dataset_id)
    ]


@router.get("/datasets/{dataset_id}/categories", response_model=list[CaseCategoryResponse])
def list_categories(dataset_id: str, request: Request) -> list[CaseCategoryResponse]:
    return [
        CaseCategoryResponse.from_model(item)
        for item in catalog(request).list_categories(dataset_id)
    ]


@router.post(
    "/datasets/{dataset_id}/categories",
    response_model=CaseCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    dataset_id: str, payload: CaseCategoryCreate, request: Request
) -> CaseCategoryResponse:
    return CaseCategoryResponse.from_model(
        catalog(request).get_or_create_category(
            dataset_id, payload.category_key, payload.name, payload.description
        )
    )


@router.delete(
    "/datasets/{dataset_id}/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_category(dataset_id: str, category_id: str, request: Request) -> None:
    catalog(request).archive_category(dataset_id, category_id)


@router.post(
    "/datasets/{dataset_id}/cases",
    response_model=CaseRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_case(
    dataset_id: str, payload: CaseRevisionCreate, request: Request
) -> CaseRevisionResponse:
    service = catalog(request)
    category = (
        service.get_or_create_category(
            dataset_id, payload.category_key, payload.category_name or payload.category_key
        )
        if payload.category_key
        else None
    )
    if payload.case_key is None and category is None:
        from analystbench.errors import AnalystBenchError

        raise AnalystBenchError(
            "validation_failed", "category_key is required when case_key is generated"
        )
    revision = service.create_case_revision(
        dataset_id=dataset_id,
        case_key=payload.case_key,
        problem_statement="",
        reference_answer=payload.reference_answer,
        category_id=category.id if category else None,
    )
    return CaseRevisionResponse.from_model(revision)


@router.get("/cases/{case_id}", response_model=CaseResponse)
def get_case(case_id: str, request: Request) -> CaseResponse:
    service = catalog(request)
    case = service.get_case(case_id)
    return CaseResponse(
        id=case.id,
        dataset_id=case.dataset_id,
        case_key=case.case_key,
        category_id=case.category_id,
        source_filename=case.source_filename,
        revisions=[
            CaseRevisionResponse.from_model(item) for item in service.get_case_revisions(case_id)
        ],
    )


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(case_id: str, request: Request) -> None:
    catalog(request).archive_case(case_id)


@router.get(
    "/case-revisions/{case_revision_id}/content",
    response_model=CaseRevisionContentResponse,
)
def get_case_revision_content(
    case_revision_id: str, request: Request
) -> CaseRevisionContentResponse:
    content = catalog(request).get_case_revision_content(case_revision_id)
    return CaseRevisionContentResponse(
        case_id=content["case_id"],
        case_key=content["case_key"],
        revision_id=content["revision_id"],
        revision_number=content["revision_number"],
        reference_answer=content["reference_answer"],
    )


@router.post(
    "/cases/{case_id}/revisions",
    response_model=CaseRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_case_revision(
    case_id: str, payload: CaseRevisionCreate, request: Request
) -> CaseRevisionResponse:
    service = catalog(request)
    case = service.get_case(case_id)
    revision = service.create_case_revision(
        dataset_id=case.dataset_id,
        case_id=case_id,
        case_key=case.case_key,
        problem_statement="",
        reference_answer=payload.reference_answer,
    )
    return CaseRevisionResponse.from_model(revision)


@router.post(
    "/datasets/{dataset_id}/versions",
    response_model=DatasetVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def freeze_dataset_version(
    dataset_id: str, payload: DatasetVersionCreate, request: Request
) -> DatasetVersionResponse:
    return DatasetVersionResponse.from_model(
        catalog(request).freeze_dataset_version(dataset_id, payload.case_revision_ids)
    )


@router.post("/candidates", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
def create_candidate(payload: CandidateCreate, request: Request) -> CandidateResponse:
    return CandidateResponse.from_model(
        catalog(request).create_candidate(payload.name, payload.description)
    )


@router.post(
    "/candidates/{candidate_id}/versions",
    response_model=CandidateVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_candidate_version(
    candidate_id: str, payload: CandidateVersionCreate, request: Request
) -> CandidateVersionResponse:
    return CandidateVersionResponse.from_model(
        catalog(request).create_candidate_version(candidate_id, payload.metadata)
    )


@router.post(
    "/candidate-versions/{candidate_version_id}/reports:batch-import",
    response_model=list[CandidateReportResponse],
    status_code=status.HTTP_201_CREATED,
)
def import_candidate_reports(
    candidate_version_id: str, payload: CandidateReportBatchImport, request: Request
) -> list[CandidateReportResponse]:
    reports = catalog(request).import_candidate_reports(
        candidate_version_id, [item.model_dump() for item in payload.reports]
    )
    return [CandidateReportResponse.from_model(item) for item in reports]


@router.get("/candidate-versions/{candidate_version_id}/coverage")
def candidate_coverage(
    candidate_version_id: str,
    request: Request,
    dataset_version_id: str = Query(),
) -> dict[str, Any]:
    return catalog(request).candidate_coverage(candidate_version_id, dataset_version_id)
