"""Application services for versioned datasets and candidate reports."""

import json
import re
from datetime import datetime, timezone
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from analystbench.content_store import ContentRef, ContentStore, canonical_json
from analystbench.db.models import (
    Candidate,
    CandidateReport,
    CandidateVersion,
    Case,
    CaseCategory,
    CaseRevision,
    CaseTrace,
    ContentBlob,
    Dataset,
    DatasetVersion,
)
from analystbench.errors import AnalystBenchError


class NotFoundError(AnalystBenchError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            code="not_found", message=f"{resource} '{resource_id}' was not found", status_code=404
        )


class ConflictError(AnalystBenchError):
    def __init__(self, message: str) -> None:
        super().__init__(code="conflict", message=message, status_code=409)


@contextmanager
def transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class CatalogService:
    def __init__(self, session_factory: sessionmaker[Session], content_store: ContentStore) -> None:
        self.session_factory = session_factory
        self.content_store = content_store

    def _store_ref(self, session: Session, ref: ContentRef) -> None:
        if session.get(ContentBlob, ref.content_hash) is None:
            session.add(ContentBlob(**asdict(ref)))

    @staticmethod
    def _next_version(session: Session, model: type[Any], field: Any, criterion: Any) -> int:
        current = session.scalar(select(func.max(field)).where(criterion))
        return int(current or 0) + 1

    def create_dataset(
        self, name: str, description: str = "", dataset_key: str | None = None
    ) -> Dataset:
        dataset_key = (dataset_key or name).strip()
        dataset = Dataset(
            id=str(uuid4()), dataset_key=dataset_key, name=name, description=description
        )
        try:
            with transaction(self.session_factory) as session:
                session.add(dataset)
                session.flush()
                session.expunge(dataset)
            return dataset
        except IntegrityError as exc:
            raise ConflictError(
                f"dataset name '{name}' or dataset_key '{dataset_key}' already exists"
            ) from exc

    def get_or_create_dataset(
        self, dataset_key: str, name: str | None = None, description: str = ""
    ) -> Dataset:
        dataset_key = dataset_key.strip()
        if not dataset_key:
            raise AnalystBenchError("validation_failed", "dataset_key cannot be empty")
        with transaction(self.session_factory) as session:
            existing = session.scalar(
                select(Dataset).where(
                    Dataset.dataset_key == dataset_key, Dataset.archived_at.is_(None)
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
        return self.create_dataset(name or dataset_key, description, dataset_key)

    def get_or_create_category(
        self,
        dataset_id: str,
        category_key: str,
        name: str | None = None,
        description: str = "",
    ) -> CaseCategory:
        category_key = category_key.strip()
        if not category_key:
            raise AnalystBenchError("validation_failed", "category_key cannot be empty")
        with transaction(self.session_factory) as session:
            if session.get(Dataset, dataset_id) is None:
                raise NotFoundError("dataset", dataset_id)
            existing = session.scalar(
                select(CaseCategory).where(
                    CaseCategory.dataset_id == dataset_id,
                    CaseCategory.category_key == category_key,
                    CaseCategory.archived_at.is_(None),
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
            category = CaseCategory(
                id=str(uuid4()),
                dataset_id=dataset_id,
                category_key=category_key,
                name=name or category_key,
                description=description,
            )
            session.add(category)
            session.flush()
            session.expunge(category)
            return category

    def list_datasets(self) -> list[Dataset]:
        with transaction(self.session_factory) as session:
            return list(
                session.scalars(
                    select(Dataset).where(Dataset.archived_at.is_(None)).order_by(Dataset.name)
                )
            )

    def list_categories(self, dataset_id: str) -> list[CaseCategory]:
        with transaction(self.session_factory) as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None or dataset.archived_at is not None:
                raise NotFoundError("dataset", dataset_id)
            categories = list(
                session.scalars(
                    select(CaseCategory)
                    .where(
                        CaseCategory.dataset_id == dataset_id,
                        CaseCategory.archived_at.is_(None),
                    )
                    .order_by(CaseCategory.category_key)
                )
            )
            for item in categories:
                session.expunge(item)
            return categories

    def get_dataset(self, dataset_id: str) -> Dataset:
        with transaction(self.session_factory) as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None or dataset.archived_at is not None:
                raise NotFoundError("dataset", dataset_id)
            session.expunge(dataset)
            return dataset

    def list_cases(self, dataset_id: str) -> list[Case]:
        """List active Cases belonging to a Dataset in stable key order."""
        with transaction(self.session_factory) as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None or dataset.archived_at is not None:
                raise NotFoundError("dataset", dataset_id)
            cases = list(
                session.scalars(
                    select(Case)
                    .where(Case.dataset_id == dataset_id, Case.archived_at.is_(None))
                    .order_by(Case.case_key)
                )
            )
            for item in cases:
                session.expunge(item)
            return cases

    def archive_case(self, case_id: str) -> None:
        with transaction(self.session_factory) as session:
            case = session.get(Case, case_id)
            if case is None or case.archived_at is not None:
                raise NotFoundError("case", case_id)
            case.archived_at = datetime.now(timezone.utc)

    def archive_category(self, dataset_id: str, category_id: str) -> None:
        with transaction(self.session_factory) as session:
            dataset = session.get(Dataset, dataset_id)
            category = session.get(CaseCategory, category_id)
            if dataset is None or dataset.archived_at is not None:
                raise NotFoundError("dataset", dataset_id)
            if (
                category is None
                or category.dataset_id != dataset_id
                or category.archived_at is not None
            ):
                raise NotFoundError("case_category", category_id)
            archived_at = datetime.now(timezone.utc)
            category.archived_at = archived_at
            for case in session.scalars(
                select(Case).where(Case.category_id == category_id, Case.archived_at.is_(None))
            ):
                case.archived_at = archived_at

    def archive_dataset(self, dataset_id: str) -> None:
        with transaction(self.session_factory) as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None or dataset.archived_at is not None:
                raise NotFoundError("dataset", dataset_id)
            archived_at = datetime.now(timezone.utc)
            dataset.archived_at = archived_at
            for category in session.scalars(
                select(CaseCategory).where(
                    CaseCategory.dataset_id == dataset_id,
                    CaseCategory.archived_at.is_(None),
                )
            ):
                category.archived_at = archived_at
            for case in session.scalars(
                select(Case).where(Case.dataset_id == dataset_id, Case.archived_at.is_(None))
            ):
                case.archived_at = archived_at

    def create_case_revision(
        self,
        dataset_id: str,
        case_key: str | None,
        problem_statement: str,
        reference_answer: str,
        case_id: str | None = None,
        category_id: str | None = None,
        source_filename: str | None = None,
        traces: list[dict[str, Any]] | None = None,
    ) -> CaseRevision:
        traces = traces or []
        with transaction(self.session_factory) as session:
            if session.get(Dataset, dataset_id) is None:
                raise NotFoundError("dataset", dataset_id)
            if category_id is not None:
                category = session.get(CaseCategory, category_id)
                if category is None or category.dataset_id != dataset_id:
                    raise NotFoundError("case_category", category_id)
            normalized_key = (case_key or "").strip()
            if case_id is None and not normalized_key:
                if category_id is None:
                    raise AnalystBenchError(
                        "validation_failed", "category_key is required when case_key is generated"
                    )
                existing_keys = session.scalars(
                    select(Case.case_key).where(
                        Case.dataset_id == dataset_id,
                        Case.category_id == category_id,
                        Case.archived_at.is_(None),
                    )
                )
                sequence = [
                    int(match.group(1))
                    for value in existing_keys
                    if (match := re.search(r"(\d+)$", value))
                ]
                normalized_key = str(max(sequence, default=0) + 1)
            if case_id is None:
                existing_case = session.scalar(
                    select(Case).where(
                        Case.dataset_id == dataset_id,
                        Case.category_id == category_id,
                        Case.case_key == normalized_key,
                    )
                )
                if existing_case is not None:
                    raise ConflictError(f"case key '{normalized_key}' already exists in category")
                case = Case(
                    id=str(uuid4()),
                    dataset_id=dataset_id,
                    category_id=category_id,
                    case_key=normalized_key,
                    source_filename=source_filename,
                )
                session.add(case)
                session.flush()
            else:
                case = session.get(Case, case_id)
                if case is None or case.dataset_id != dataset_id:
                    raise NotFoundError("case", case_id)
                if category_id is not None and case.category_id != category_id:
                    raise AnalystBenchError(
                        "validation_failed", "case category does not match the requested category"
                    )
            problem_ref = self.content_store.put_text(problem_statement)
            answer_ref = self.content_store.put_text(reference_answer)
            self._store_ref(session, problem_ref)
            self._store_ref(session, answer_ref)
            trace_manifests: list[dict[str, Any]] = []
            trace_records: list[dict[str, Any]] = []
            for index, trace in enumerate(traces):
                if not isinstance(trace, dict) or not isinstance(trace.get("content"), str):
                    raise AnalystBenchError(
                        "validation_failed",
                        "each case trace must be an object containing string field 'content'",
                    )
                trace_key = str(trace.get("trace_key") or f"trace{index + 1}")
                filename = str(trace.get("filename") or f"{trace_key}.txt")
                media_type = str(trace.get("media_type") or "text/plain")
                metadata = trace.get("metadata", {})
                trace_ref = self.content_store.put_text(trace["content"], media_type=media_type)
                self._store_ref(session, trace_ref)
                trace_manifest = {
                    "trace_key": trace_key,
                    "filename": filename,
                    "media_type": media_type,
                    "content_hash": trace_ref.content_hash,
                    "metadata": metadata,
                }
                trace_manifests.append(trace_manifest)
                trace_records.append(trace_manifest)
            session.flush()
            revision_number = self._next_version(
                session, CaseRevision, CaseRevision.revision_number, CaseRevision.case_id == case.id
            )
            manifest = {
                "case_id": case.id,
                "revision_number": revision_number,
                "problem_content_hash": problem_ref.content_hash,
                "reference_answer_content_hash": answer_ref.content_hash,
                "attachments": trace_manifests,
                "category_id": category_id,
                "source_filename": source_filename,
            }
            manifest_ref = self.content_store.put_json(manifest)
            self._store_ref(session, manifest_ref)
            session.flush()
            revision = CaseRevision(
                id=str(uuid4()),
                case_id=case.id,
                revision_number=revision_number,
                problem_content_hash=problem_ref.content_hash,
                reference_answer_content_hash=answer_ref.content_hash,
                attachments_json=canonical_json(trace_manifests),
                content_hash=manifest_ref.content_hash,
            )
            session.add(revision)
            session.flush()
            for trace in trace_records:
                session.add(
                    CaseTrace(
                        id=str(uuid4()),
                        case_revision_id=revision.id,
                        trace_key=trace["trace_key"],
                        filename=trace["filename"],
                        media_type=trace["media_type"],
                        content_hash=trace["content_hash"],
                        metadata_json=canonical_json(trace["metadata"]),
                    )
                )
            session.flush()
            session.expunge(revision)
            return revision

    def latest_case_revision_ids(self, dataset_id: str) -> list[str]:
        """Return one latest active revision for every Case in a test set."""
        with transaction(self.session_factory) as session:
            cases = list(
                session.scalars(
                    select(Case)
                    .where(Case.dataset_id == dataset_id, Case.archived_at.is_(None))
                    .order_by(Case.case_key)
                )
            )
            revision_ids: list[str] = []
            for case in cases:
                revision_id = session.scalar(
                    select(CaseRevision.id)
                    .where(CaseRevision.case_id == case.id)
                    .order_by(CaseRevision.revision_number.desc())
                    .limit(1)
                )
                if revision_id is not None:
                    revision_ids.append(revision_id)
            return revision_ids

    def get_case_revisions(self, case_id: str) -> list[CaseRevision]:
        with transaction(self.session_factory) as session:
            if session.get(Case, case_id) is None:
                raise NotFoundError("case", case_id)
            return list(
                session.scalars(
                    select(CaseRevision)
                    .where(CaseRevision.case_id == case_id)
                    .order_by(CaseRevision.revision_number)
                )
            )

    def get_case_revision_content(self, case_revision_id: str) -> dict[str, Any]:
        """Return the user-editable payload for one immutable Case revision."""
        with transaction(self.session_factory) as session:
            revision = session.get(CaseRevision, case_revision_id)
            if revision is None:
                raise NotFoundError("case_revision", case_revision_id)
            case = session.get(Case, revision.case_id)
            if case is None or case.archived_at is not None:
                raise NotFoundError("case", revision.case_id)
            return {
                "case_id": case.id,
                "case_key": case.case_key,
                "revision_id": revision.id,
                "revision_number": revision.revision_number,
                "reference_answer": self.content_store.read_text(
                    revision.reference_answer_content_hash
                ),
            }

    def get_case(self, case_id: str) -> Case:
        with transaction(self.session_factory) as session:
            case = session.get(Case, case_id)
            if case is None or case.archived_at is not None:
                raise NotFoundError("case", case_id)
            session.expunge(case)
            return case

    def freeze_dataset_version(
        self, dataset_id: str, case_revision_ids: list[str]
    ) -> DatasetVersion:
        if not case_revision_ids:
            raise AnalystBenchError(
                "validation_failed", "dataset version requires at least one case revision"
            )
        if len(case_revision_ids) != len(set(case_revision_ids)):
            raise AnalystBenchError("validation_failed", "case revision ids must be unique")
        with transaction(self.session_factory) as session:
            if session.get(Dataset, dataset_id) is None:
                raise NotFoundError("dataset", dataset_id)
            revisions = list(
                session.scalars(select(CaseRevision).where(CaseRevision.id.in_(case_revision_ids)))
            )
            if len(revisions) != len(case_revision_ids):
                raise AnalystBenchError(
                    "validation_failed", "one or more case revisions do not exist"
                )
            revision_case_ids = {revision.case_id for revision in revisions}
            if len(revision_case_ids) != len(case_revision_ids):
                raise AnalystBenchError(
                    "validation_failed", "a dataset version may contain only one revision per case"
                )
            cases = list(session.scalars(select(Case).where(Case.id.in_(revision_case_ids))))
            if any(case.dataset_id != dataset_id for case in cases):
                raise AnalystBenchError(
                    "validation_failed", "case revision does not belong to dataset"
                )
            version_number = self._next_version(
                session,
                DatasetVersion,
                DatasetVersion.version_number,
                DatasetVersion.dataset_id == dataset_id,
            )
            manifest = {"dataset_id": dataset_id, "case_revision_ids": case_revision_ids}
            manifest_ref = self.content_store.put_json(manifest)
            self._store_ref(session, manifest_ref)
            version = DatasetVersion(
                id=str(uuid4()),
                dataset_id=dataset_id,
                version_number=version_number,
                case_revision_ids_json=canonical_json(case_revision_ids),
                content_hash=manifest_ref.content_hash,
            )
            session.add(version)
            session.flush()
            session.expunge(version)
            return version

    def create_candidate(self, name: str, description: str = "") -> Candidate:
        candidate = Candidate(id=str(uuid4()), name=name, description=description)
        try:
            with transaction(self.session_factory) as session:
                session.add(candidate)
                session.flush()
                session.expunge(candidate)
            return candidate
        except IntegrityError as exc:
            raise ConflictError(f"candidate name '{name}' already exists") from exc

    def create_candidate_version(
        self, candidate_id: str, metadata: dict[str, Any] | None = None
    ) -> CandidateVersion:
        metadata = metadata or {}
        with transaction(self.session_factory) as session:
            if session.get(Candidate, candidate_id) is None:
                raise NotFoundError("candidate", candidate_id)
            version_number = self._next_version(
                session,
                CandidateVersion,
                CandidateVersion.version_number,
                CandidateVersion.candidate_id == candidate_id,
            )
            manifest_ref = self.content_store.put_json(
                {
                    "candidate_id": candidate_id,
                    "version_number": version_number,
                    "metadata": metadata,
                }
            )
            self._store_ref(session, manifest_ref)
            version = CandidateVersion(
                id=str(uuid4()),
                candidate_id=candidate_id,
                version_number=version_number,
                metadata_json=canonical_json(metadata),
                content_hash=manifest_ref.content_hash,
            )
            session.add(version)
            session.flush()
            session.expunge(version)
            return version

    def import_candidate_reports(
        self, candidate_version_id: str, reports: list[dict[str, str]]
    ) -> list[CandidateReport]:
        imported: list[CandidateReport] = []
        with transaction(self.session_factory) as session:
            if session.get(CandidateVersion, candidate_version_id) is None:
                raise NotFoundError("candidate_version", candidate_version_id)
            for item in reports:
                case_revision_id = item["case_revision_id"]
                if session.get(CaseRevision, case_revision_id) is None:
                    raise NotFoundError("case_revision", case_revision_id)
                if session.scalar(
                    select(CandidateReport).where(
                        CandidateReport.candidate_version_id == candidate_version_id,
                        CandidateReport.case_revision_id == case_revision_id,
                    )
                ):
                    raise ConflictError(
                        f"report already exists for case revision '{case_revision_id}'"
                    )
                report_ref = self.content_store.put_text(item["report"])
                self._store_ref(session, report_ref)
                session.flush()
                manifest_ref = self.content_store.put_json(
                    {
                        "candidate_version_id": candidate_version_id,
                        "case_revision_id": case_revision_id,
                        "source": "imported",
                        "report_content_hash": report_ref.content_hash,
                    }
                )
                self._store_ref(session, manifest_ref)
                report = CandidateReport(
                    id=str(uuid4()),
                    candidate_version_id=candidate_version_id,
                    case_revision_id=case_revision_id,
                    source="imported",
                    report_content_hash=report_ref.content_hash,
                    content_hash=manifest_ref.content_hash,
                )
                session.add(report)
                imported.append(report)
            session.flush()
            for report in imported:
                session.expunge(report)
        return imported

    def candidate_coverage(
        self, candidate_version_id: str, dataset_version_id: str
    ) -> dict[str, Any]:
        with transaction(self.session_factory) as session:
            dataset_version = session.get(DatasetVersion, dataset_version_id)
            if dataset_version is None:
                raise NotFoundError("dataset_version", dataset_version_id)
            if session.get(CandidateVersion, candidate_version_id) is None:
                raise NotFoundError("candidate_version", candidate_version_id)
            expected = json.loads(dataset_version.case_revision_ids_json)
            found = set(
                session.scalars(
                    select(CandidateReport.case_revision_id).where(
                        CandidateReport.candidate_version_id == candidate_version_id,
                        CandidateReport.case_revision_id.in_(expected),
                    )
                )
            )
            missing = [revision_id for revision_id in expected if revision_id not in found]
            return {
                "total": len(expected),
                "available": len(found),
                "missing_case_revision_ids": missing,
            }

    def export_dataset_version(self, dataset_version_id: str) -> dict[str, Any]:
        """Export a frozen dataset snapshot with UTF-8 source texts."""
        with transaction(self.session_factory) as session:
            version = session.get(DatasetVersion, dataset_version_id)
            if version is None:
                raise NotFoundError("dataset_version", dataset_version_id)
            dataset = session.get(Dataset, version.dataset_id)
            assert dataset is not None
            revision_ids = json.loads(version.case_revision_ids_json)
            revisions = list(
                session.scalars(select(CaseRevision).where(CaseRevision.id.in_(revision_ids)))
            )
            cases = {
                case.id: case
                for case in session.scalars(
                    select(Case).where(Case.id.in_([revision.case_id for revision in revisions]))
                )
            }
            revision_by_id = {revision.id: revision for revision in revisions}
            exported_cases = []
            for revision_id in revision_ids:
                revision = revision_by_id[revision_id]
                case = cases[revision.case_id]
                exported_cases.append(
                    {
                        "case_key": case.case_key,
                        "reference_answer": self.content_store.read_text(
                            revision.reference_answer_content_hash
                        ),
                    }
                )
            return {
                "schema_version": "1.0",
                "dataset": {
                    "dataset_key": dataset.dataset_key,
                    "name": dataset.name,
                    "description": dataset.description,
                },
                "cases": exported_cases,
            }

    def import_dataset_export(self, payload: dict[str, Any]) -> DatasetVersion:
        """Import the public JSON export format into a new dataset and frozen snapshot."""
        if payload.get("schema_version") != "1.0":
            raise AnalystBenchError(
                "validation_failed", "unsupported dataset export schema version"
            )
        dataset_payload = payload.get("dataset")
        cases_payload = payload.get("cases")
        if (
            not isinstance(dataset_payload, dict)
            or not isinstance(cases_payload, list)
            or not cases_payload
        ):
            raise AnalystBenchError(
                "validation_failed", "dataset export requires dataset and non-empty cases"
            )
        dataset = self.create_dataset(
            str(dataset_payload.get("name", "")),
            str(dataset_payload.get("description", "")),
            str(dataset_payload.get("dataset_key") or dataset_payload.get("name", "")),
        )
        revision_ids: list[str] = []
        for case_payload in cases_payload:
            if not isinstance(case_payload, dict):
                raise AnalystBenchError("validation_failed", "case export entries must be objects")
            revision = self.create_case_revision(
                dataset_id=dataset.id,
                case_key=str(case_payload.get("case_key", "")),
                problem_statement="",
                reference_answer=str(case_payload.get("reference_answer", "")),
            )
            revision_ids.append(revision.id)
        return self.freeze_dataset_version(dataset.id, revision_ids)
