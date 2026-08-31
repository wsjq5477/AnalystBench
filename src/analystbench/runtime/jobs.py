"""Database-backed queue with bounded leases for the Local Worker."""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, case, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from analystbench.db.models import (
    EvaluationMethod,
    EvaluationModel,
    EvaluationSubmission,
    EvaluationSubmissionMethodRun,
    EvaluationTarget,
    EvaluationVariant,
    Job,
)
from analystbench.db.transaction import transaction


class JobQueue:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def enqueue(self, session: Session, kind: str, payload: dict[str, object]) -> Job:
        payload_json = json.dumps(payload, sort_keys=True)
        if kind == "skill_optimization_advance":
            # An advance job may deliberately leave one durable successor while
            # it waits for evaluation submissions.  Keep at most one queued
            # successor; otherwise every quick poll adds another database write
            # job and a single experiment can flood the Worker queue.
            pending = next(
                (
                    item
                    for item in session.new
                    if isinstance(item, Job)
                    and item.kind == kind
                    and item.status in {None, "queued"}
                    and item.payload_json == payload_json
                ),
                None,
            )
            if pending is not None:
                return pending
            existing = session.scalar(
                select(Job)
                .where(
                    Job.kind == kind,
                    Job.status == "queued",
                    Job.payload_json == payload_json,
                )
                .order_by(Job.created_at, Job.id)
                .limit(1)
            )
            if existing is not None:
                return existing
        job = Job(id=str(uuid4()), kind=kind, payload_json=payload_json)
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
                # Reclaim an expired lease before a queued successor even when
                # SQLite gave both rows the same second-resolution timestamp.
                # Otherwise UUID ordering can execute the successor first and
                # later replay the crashed state transition as well.
                .order_by(
                    case(
                        (
                            and_(
                                Job.status == "running",
                                Job.lease_until.is_not(None),
                                Job.lease_until < now,
                            ),
                            0,
                        ),
                        else_=1,
                    ),
                    Job.created_at,
                    Job.id,
                )
            )
            if session.get_bind().dialect.name == "sqlite":
                # SQLite has no row-level SKIP LOCKED. Serialize the short claim
                # transaction so two Worker threads/processes cannot reserve the
                # same job or overrun a per-method concurrency limit.
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                statement = statement.with_for_update(skip_locked=True)

            active_resources = self._active_evaluation_resources(session, now)
            job = next(
                (
                    candidate
                    for candidate in session.scalars(statement)
                    if self._claim_allowed(
                        session,
                        candidate,
                        active_resources,
                        now,
                    )
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

    def _target_resources(
        self,
        session: Session,
        method_id: str,
    ) -> tuple[str | None, int | None] | None:
        row = session.execute(
            select(EvaluationTarget.model_id)
            .where(EvaluationTarget.materialized_method_id == method_id)
        ).one_or_none()
        if row is None:
            row = session.execute(
                select(EvaluationTarget.model_id)
                .join(
                    EvaluationVariant,
                    EvaluationVariant.evaluation_target_id == EvaluationTarget.id,
                )
                .where(EvaluationVariant.materialized_method_id == method_id)
            ).one_or_none()
        if row is None:
            return None
        model_id = row[0]
        if model_id is None:
            return None, None
        model_key = session.scalar(
            select(EvaluationModel.model_key).where(EvaluationModel.id == model_id)
        )
        if model_key is None:
            return None, None
        # Capacity is operational, not frozen evaluation identity. The newest
        # setting for a model key governs every Harness and historical version.
        concurrency_limit = session.scalar(
            select(EvaluationModel.concurrency_limit)
            .where(EvaluationModel.model_key == model_key)
            .order_by(EvaluationModel.version_number.desc())
            .limit(1)
        )
        return model_key, concurrency_limit

    def _active_evaluation_resources(
        self,
        session: Session,
        now: datetime,
    ) -> tuple[Counter[str], Counter[str]]:
        active_methods = Counter[str]()
        active_models = Counter[str]()
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
                active_methods[method_id] += 1
                resources = self._target_resources(session, method_id)
                if resources is not None and resources[0] is not None:
                    active_models[resources[0]] += 1
        return active_methods, active_models

    def _claim_allowed(
        self,
        session: Session,
        job: Job,
        active_resources: tuple[Counter[str], Counter[str]],
        now: datetime,
    ) -> bool:
        if job.kind == "skill_optimization_advance":
            return self._optimization_advance_allowed(session, job, now)
        method_id = self._method_id(session, job)
        if method_id is None:
            return True
        active_methods, active_models = active_resources
        target_resources = self._target_resources(session, method_id)
        if target_resources is not None and target_resources[0] is not None:
            model_key, model_limit = target_resources
            return model_limit is None or active_models[model_key] < model_limit
        concurrency_limit = session.scalar(
            select(EvaluationMethod.concurrency_limit).where(EvaluationMethod.id == method_id)
        )
        if concurrency_limit is None:
            return True
        return active_methods[method_id] < concurrency_limit

    @staticmethod
    def _optimization_experiment_id(job: Job) -> str | None:
        try:
            value = json.loads(job.payload_json).get("experiment_id")
        except (AttributeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, str) and value else None

    def _optimization_advance_allowed(
        self,
        session: Session,
        job: Job,
        now: datetime,
    ) -> bool:
        experiment_id = self._optimization_experiment_id(job)
        if experiment_id is None:
            return True

        # Only one live state-machine transition may run for an experiment.
        # The expired job itself remains reclaimable after a Worker crash.
        live_transition = session.scalar(
            select(Job.id)
            .where(
                Job.id != job.id,
                Job.kind == "skill_optimization_advance",
                Job.status == "running",
                Job.lease_until.is_not(None),
                Job.lease_until >= now,
                Job.payload_json == job.payload_json,
            )
            .limit(1)
        )
        if live_transition is not None:
            return False

        # A queued successor must not overtake a crashed transition whose
        # lease has expired.  The global query also prioritizes expired work,
        # while this guard preserves correctness if ordering changes later.
        if job.status == "queued":
            expired_predecessor = session.scalar(
                select(Job.id)
                .where(
                    Job.id != job.id,
                    Job.kind == "skill_optimization_advance",
                    Job.status == "running",
                    Job.lease_until.is_not(None),
                    Job.lease_until < now,
                    Job.payload_json == job.payload_json,
                )
                .limit(1)
            )
            if expired_predecessor is not None:
                return False

        # Advance is event-driven by the durable successor already in the
        # queue.  Do not execute that successor while one of this experiment's
        # evaluation submissions is still producing or scoring results.  The
        # Worker keeps polling the queue, so it becomes claimable as soon as all
        # matching submissions reach a terminal state.  Other experiments are
        # unaffected and may advance concurrently.
        terminal_submission_states = {
            "completed",
            "completed_with_errors",
            "failed",
            "cancelled",
        }
        pending_submissions = session.scalars(
            select(EvaluationSubmission).where(
                EvaluationSubmission.purpose == "skill_optimization",
                EvaluationSubmission.status.not_in(terminal_submission_states),
            )
        )
        for submission in pending_submissions:
            try:
                context = json.loads(submission.optimization_context_json or "{}")
            except (AttributeError, json.JSONDecodeError):
                continue
            if (
                isinstance(context, dict)
                and context.get("experiment_id") == experiment_id
            ):
                return False
        return True
