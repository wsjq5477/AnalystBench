"""Database-backed queue with bounded leases for the Local Worker."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from analystbench.db.models import Job
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
            job = session.scalar(
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
                .limit(1)
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

    def complete(self, job_id: str) -> None:
        with transaction(self.session_factory) as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = "succeeded"
                job.locked_by = None
                job.lease_until = None

    def fail(self, job_id: str, error: str, retryable: bool) -> None:
        with transaction(self.session_factory) as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.status = "queued" if retryable and job.attempts < 2 else "failed"
                job.locked_by = None
                job.lease_until = None
                job.last_error = error[:4000]

    @staticmethod
    def payload(job: Job) -> dict[str, object]:
        return json.loads(job.payload_json)
