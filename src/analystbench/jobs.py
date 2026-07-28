"""Database-backed queue with bounded leases for the Local Worker."""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from analystbench.db.models import EvaluationMethod, EvaluationSubmissionMethodRun, Job
from analystbench.services import transaction


class JobQueue:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def enqueue(self, session: Session, kind: str, payload: dict[str, object]) -> Job:
        job = Job(id=str(uuid4()), kind=kind, payload_json=json.dumps(payload, sort_keys=True))
        session.add(job)
        return job

    def claim(self, worker_id: str, lease_seconds: int = 120) -> Job | None:
        now = datetime.now(UTC)
        with transaction(self.session_factory) as session:
            statement = (
                select(Job)
                .where(
                    or_(
                        Job.status == "queued",
                        and_(
                            Job.status == "running",
                            Job.lease_until.is_not(None),
                            Job.lease_until < now,
                        ),
                    )
                )
                .order_by(Job.created_at, Job.id)
            )
            if session.get_bind().dialect.name == "sqlite":
                # SQLite has no row-level SKIP LOCKED. Serialize the short claim
                # transaction so two Worker threads/processes cannot reserve the
                # same job or overrun a per-method concurrency limit.
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                statement = statement.with_for_update(skip_locked=True)

            active_methods = self._active_evaluation_methods(session, now)
            job = next(
                (
                    candidate
                    for candidate in session.scalars(statement)
                    if self._claim_allowed(session, candidate, active_methods)
                ),
                None,
            )
            if job is None:
                return None
            job.status = "running"
            job.locked_by = worker_id
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.attempts += 1
            session.flush()
            session.expunge(job)
            return job

    def renew(
        self,
        job_id: str,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> bool:
        now = datetime.now(UTC)
        with transaction(self.session_factory) as session:
            result = session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == "running",
                    Job.locked_by == worker_id,
                )
                .values(lease_until=now + timedelta(seconds=lease_seconds))
            )
            return bool(result.rowcount)

    def complete(self, job_id: str, worker_id: str | None = None) -> bool:
        with transaction(self.session_factory) as session:
            statement = update(Job).where(Job.id == job_id)
            if worker_id is not None:
                statement = statement.where(
                    Job.status == "running",
                    Job.locked_by == worker_id,
                )
            result = session.execute(
                statement.values(status="succeeded", locked_by=None, lease_until=None)
            )
            return bool(result.rowcount)

    def fail(
        self,
        job_id: str,
        error: str,
        retryable: bool,
        worker_id: str | None = None,
    ) -> bool:
        with transaction(self.session_factory) as session:
            job = session.get(Job, job_id)
            if job is None or (
                worker_id is not None
                and (job.status != "running" or job.locked_by != worker_id)
            ):
                return False
            job.status = "queued" if retryable and job.attempts < 2 else "failed"
            job.locked_by = None
            job.lease_until = None
            job.last_error = error[:4000]
            return True

    @staticmethod
    def payload(job: Job) -> dict[str, object]:
        return json.loads(job.payload_json)

    @staticmethod
    def _method_run_id(job: Job) -> str | None:
        if job.kind != "evaluation_method_run":
            return None
        try:
            value = json.loads(job.payload_json).get("evaluation_method_run_id")
        except (AttributeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, str) else None

    def _method_id(self, session: Session, job: Job) -> str | None:
        method_run_id = self._method_run_id(job)
        if method_run_id is None:
            return None
        return session.scalar(
            select(EvaluationSubmissionMethodRun.method_id).where(
                EvaluationSubmissionMethodRun.id == method_run_id
            )
        )

    def _active_evaluation_methods(
        self,
        session: Session,
        now: datetime,
    ) -> Counter[str]:
        active = Counter[str]()
        jobs = session.scalars(
            select(Job).where(
                Job.kind == "evaluation_method_run",
                Job.status == "running",
                Job.lease_until.is_not(None),
                Job.lease_until >= now,
            )
        )
        for job in jobs:
            method_id = self._method_id(session, job)
            if method_id is not None:
                active[method_id] += 1
        return active

    def _claim_allowed(
        self,
        session: Session,
        job: Job,
        active_methods: Counter[str],
    ) -> bool:
        method_id = self._method_id(session, job)
        if method_id is None:
            return True
        concurrency_limit = session.scalar(
            select(EvaluationMethod.concurrency_limit).where(EvaluationMethod.id == method_id)
        )
        if concurrency_limit is None:
            return True
        return active_methods[method_id] < concurrency_limit
