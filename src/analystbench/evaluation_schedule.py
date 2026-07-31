"""Persistent daily schedules that trigger ordinary P15 evaluation submissions."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from analystbench.config import Settings
from analystbench.content_store import canonical_json
from analystbench.db.models import (
    EvaluationMethod,
    EvaluationSchedule,
    EvaluationScheduleRun,
    EvaluationSubmission,
)
from analystbench.errors import AnalystBenchError
from analystbench.evaluation_submission import EvaluationSubmissionService
from analystbench.evaluation_target import EvaluationTargetService
from analystbench.jobs import JobQueue
from analystbench.services import transaction

logger = logging.getLogger(__name__)

LOCAL_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
CASE_MODES = {"all_ready", "selected"}
JUDGE_RUNNERS = {"claude", "opencode", "lexical"}
SUBMISSION_TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled"}
RUN_TERMINAL = SUBMISSION_TERMINAL | {
    "skipped_no_cases",
    "skipped_overlap",
    "failed_preflight",
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AnalystBenchError(
            "evaluation_schedule_invalid",
            f"不支持的时区：{value}",
        ) from exc


def _parse_local_time(value: str) -> time:
    if not LOCAL_TIME_RE.fullmatch(value):
        raise AnalystBenchError(
            "evaluation_schedule_invalid",
            "每日时间必须使用 HH:mm 格式。",
        )
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour=hour, minute=minute)


def next_daily_run(
    local_time: str,
    timezone: str,
    *,
    after: datetime | None = None,
) -> datetime:
    """Return the first daily occurrence strictly after ``after`` in UTC."""
    current = _as_utc(after or datetime.now(UTC))
    zone = _parse_timezone(timezone)
    configured_time = _parse_local_time(local_time)
    local_current = current.astimezone(zone)
    candidate = datetime.combine(local_current.date(), configured_time, tzinfo=zone)
    if candidate <= local_current:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def latest_due_run(local_time: str, timezone: str, *, now: datetime) -> datetime:
    """Return the most recent configured occurrence at or before ``now``."""
    current = _as_utc(now)
    zone = _parse_timezone(timezone)
    configured_time = _parse_local_time(local_time)
    local_current = current.astimezone(zone)
    candidate = datetime.combine(local_current.date(), configured_time, tzinfo=zone)
    if candidate > local_current:
        candidate -= timedelta(days=1)
    return candidate.astimezone(UTC)


class EvaluationScheduleService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        submissions: EvaluationSubmissionService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.jobs = JobQueue(session_factory)
        self.submissions = submissions or EvaluationSubmissionService(session_factory, settings)

    def _validate_config(
        self,
        *,
        name: str,
        dataset_key: str,
        case_mode: str,
        case_paths: list[str],
        method_ids: list[str],
        target_ids: list[str],
        target_selections: list[dict[str, str | None]],
        judge_runner: str,
        timezone: str,
        local_time: str,
    ) -> dict[str, Any]:
        normalized_name = name.strip()
        normalized_dataset = dataset_key.strip()
        if not normalized_name:
            raise AnalystBenchError("evaluation_schedule_invalid", "计划名称不能为空。")
        dataset_path = Path(normalized_dataset)
        if (
            not normalized_dataset
            or dataset_path.is_absolute()
            or len(dataset_path.parts) != 1
            or dataset_path.name != normalized_dataset
        ):
            raise AnalystBenchError("evaluation_schedule_invalid", "测试集路径无效。")
        dataset_directory = (self.settings.results_formal_path / normalized_dataset).resolve()
        results_root = self.settings.results_formal_path.resolve()
        if results_root not in dataset_directory.parents or not dataset_directory.is_dir():
            raise AnalystBenchError(
                "test_set_not_found",
                f"找不到测试集：{normalized_dataset}",
                status_code=404,
            )
        if case_mode not in CASE_MODES:
            raise AnalystBenchError("evaluation_schedule_invalid", "不支持的 Case 选择模式。")
        normalized_cases: list[str] = []
        if case_mode == "selected":
            for value in dict.fromkeys(case_paths):
                candidate = Path(value)
                if (
                    candidate.is_absolute()
                    or ".." in candidate.parts
                    or len(candidate.parts) != 3
                    or candidate.parts[0] != normalized_dataset
                ):
                    raise AnalystBenchError(
                        "evaluation_schedule_invalid",
                        f"Case 路径无效：{value}",
                    )
                normalized = candidate.as_posix()
                if not (results_root / normalized / "case.json").is_file():
                    raise AnalystBenchError(
                        "evaluation_schedule_invalid",
                        f"找不到 Case：{normalized}",
                    )
                normalized_cases.append(normalized)
            if not normalized_cases:
                raise AnalystBenchError(
                    "evaluation_schedule_invalid",
                    "固定选择模式至少需要一个 Case。",
                )
        selection_modes = sum(
            bool(value) for value in (method_ids, target_ids, target_selections)
        )
        if selection_modes != 1:
            raise AnalystBenchError(
                "evaluation_schedule_invalid",
                "请选择旧测评方式或 Harness/模型组合中的一种，且不能混用。",
            )
        if target_selections:
            targets, target_snapshots = EvaluationTargetService(
                self.session_factory, self.settings
            ).resolve_selections(target_selections)
            target_ids = [item.id for item in targets]
            return {
                "name": normalized_name,
                "dataset_key": normalized_dataset,
                "case_mode": case_mode,
                "case_paths": normalized_cases,
                "method_ids": [str(item.materialized_method_id) for item in targets],
                "target_ids": target_ids,
                "targets": target_snapshots,
                "methods": [],
                "judge_runner": self._validate_judge(judge_runner),
                "timezone": self._validate_timezone(timezone),
                "local_time": self._validate_local_time(local_time),
            }
        if target_ids:
            targets, target_snapshots = EvaluationTargetService(
                self.session_factory, self.settings
            ).snapshots(target_ids)
            return {
                "name": normalized_name,
                "dataset_key": normalized_dataset,
                "case_mode": case_mode,
                "case_paths": normalized_cases,
                "method_ids": [str(item.materialized_method_id) for item in targets],
                "target_ids": [item.id for item in targets],
                "targets": target_snapshots,
                "methods": [],
                "judge_runner": self._validate_judge(judge_runner),
                "timezone": self._validate_timezone(timezone),
                "local_time": self._validate_local_time(local_time),
            }
        normalized_method_ids = list(dict.fromkeys(method_ids))
        with transaction(self.session_factory) as session:
            methods: list[EvaluationMethod] = []
            for method_id in normalized_method_ids:
                method = session.get(EvaluationMethod, method_id)
                if method is None:
                    raise AnalystBenchError(
                        "evaluation_schedule_method_unavailable",
                        "找不到计划引用的测评方式。",
                    )
                if method.status != "frozen":
                    raise AnalystBenchError(
                        "evaluation_schedule_method_unavailable",
                        f"测评方式 {method.method_key} 不是可用的冻结版本。",
                    )
                methods.append(method)
        if judge_runner not in JUDGE_RUNNERS:
            raise AnalystBenchError("evaluation_schedule_invalid", "不支持的评分 Judge。")
        normalized_timezone = timezone.strip()
        normalized_time = local_time.strip()
        _parse_timezone(normalized_timezone)
        _parse_local_time(normalized_time)
        return {
            "name": normalized_name,
            "dataset_key": normalized_dataset,
            "case_mode": case_mode,
            "case_paths": normalized_cases,
            "method_ids": normalized_method_ids,
            "target_ids": [],
            "targets": [],
            "methods": [
                {
                    "id": method.id,
                    "key": method.method_key,
                    "version": method.version_number,
                }
                for method in methods
            ],
            "judge_runner": self._validate_judge(judge_runner),
            "timezone": self._validate_timezone(normalized_timezone),
            "local_time": self._validate_local_time(normalized_time),
        }

    @staticmethod
    def _validate_judge(value: str) -> str:
        if value not in JUDGE_RUNNERS:
            raise AnalystBenchError("evaluation_schedule_invalid", "不支持的评分 Judge。")
        return value

    @staticmethod
    def _validate_timezone(value: str) -> str:
        normalized = value.strip()
        _parse_timezone(normalized)
        return normalized

    @staticmethod
    def _validate_local_time(value: str) -> str:
        normalized = value.strip()
        _parse_local_time(normalized)
        return normalized

    @staticmethod
    def _snapshot(schedule: EvaluationSchedule) -> dict[str, Any]:
        return {
            "schedule_id": schedule.id,
            "name": schedule.name,
            "dataset_key": schedule.dataset_key,
            "case_mode": schedule.case_mode,
            "case_paths": json.loads(schedule.case_paths_json or "[]"),
            "method_ids": json.loads(schedule.method_ids_json or "[]"),
            "target_ids": json.loads(schedule.target_ids_json or "[]"),
            "judge_runner": schedule.judge_runner,
            "timezone": schedule.timezone,
            "local_time": schedule.local_time,
        }

    def create(
        self,
        *,
        name: str,
        dataset_key: str,
        case_mode: str,
        case_paths: list[str],
        method_ids: list[str],
        target_ids: list[str] | None = None,
        target_selections: list[dict[str, str | None]] | None = None,
        judge_runner: str,
        timezone: str,
        local_time: str,
        enabled: bool = True,
    ) -> EvaluationSchedule:
        config = self._validate_config(
            name=name,
            dataset_key=dataset_key,
            case_mode=case_mode,
            case_paths=case_paths,
            method_ids=method_ids,
            target_ids=target_ids or [],
            target_selections=target_selections or [],
            judge_runner=judge_runner,
            timezone=timezone,
            local_time=local_time,
        )
        with transaction(self.session_factory) as session:
            item = EvaluationSchedule(
                id=str(uuid4()),
                name=config["name"],
                dataset_key=config["dataset_key"],
                case_mode=config["case_mode"],
                case_paths_json=canonical_json(config["case_paths"]),
                method_ids_json=canonical_json(config["method_ids"]),
                target_ids_json=canonical_json(config["target_ids"]),
                judge_runner=config["judge_runner"],
                timezone=config["timezone"],
                local_time=config["local_time"],
                enabled=enabled,
                next_run_at=(
                    next_daily_run(config["local_time"], config["timezone"])
                    if enabled
                    else None
                ),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
            return item

    def get(self, schedule_id: str) -> EvaluationSchedule:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSchedule, schedule_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_schedule_not_found",
                    "找不到定时测评计划。",
                    status_code=404,
                )
            session.expunge(item)
            return item

    def list(self) -> list[EvaluationSchedule]:
        self.sync_terminal_runs()
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationSchedule).order_by(
                        EvaluationSchedule.enabled.desc(),
                        EvaluationSchedule.created_at.desc(),
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def update(
        self,
        schedule_id: str,
        *,
        name: str,
        dataset_key: str,
        case_mode: str,
        case_paths: list[str],
        method_ids: list[str],
        target_ids: list[str] | None = None,
        target_selections: list[dict[str, str | None]] | None = None,
        judge_runner: str,
        timezone: str,
        local_time: str,
        enabled: bool,
    ) -> EvaluationSchedule:
        config = self._validate_config(
            name=name,
            dataset_key=dataset_key,
            case_mode=case_mode,
            case_paths=case_paths,
            method_ids=method_ids,
            target_ids=target_ids or [],
            target_selections=target_selections or [],
            judge_runner=judge_runner,
            timezone=timezone,
            local_time=local_time,
        )
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSchedule, schedule_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_schedule_not_found",
                    "找不到定时测评计划。",
                    status_code=404,
                )
            item.name = config["name"]
            item.dataset_key = config["dataset_key"]
            item.case_mode = config["case_mode"]
            item.case_paths_json = canonical_json(config["case_paths"])
            item.method_ids_json = canonical_json(config["method_ids"])
            item.target_ids_json = canonical_json(config["target_ids"])
            item.judge_runner = config["judge_runner"]
            item.timezone = config["timezone"]
            item.local_time = config["local_time"]
            item.enabled = enabled
            item.next_run_at = (
                next_daily_run(config["local_time"], config["timezone"])
                if enabled
                else None
            )
        return self.get(schedule_id)

    def set_enabled(self, schedule_id: str, enabled: bool) -> EvaluationSchedule:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSchedule, schedule_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_schedule_not_found",
                    "找不到定时测评计划。",
                    status_code=404,
                )
            item.enabled = enabled
            item.next_run_at = (
                next_daily_run(item.local_time, item.timezone) if enabled else None
            )
        return self.get(schedule_id)

    def delete(self, schedule_id: str) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSchedule, schedule_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_schedule_not_found",
                    "找不到定时测评计划。",
                    status_code=404,
                )
            run_count = session.scalar(
                select(func.count(EvaluationScheduleRun.id)).where(
                    EvaluationScheduleRun.schedule_id == schedule_id
                )
            )
            if run_count:
                raise AnalystBenchError(
                    "evaluation_schedule_in_use",
                    "该计划已有执行历史，不能删除，只能停用。",
                    status_code=409,
                )
            session.delete(item)

    def _enqueue_run(
        self,
        session: Session,
        schedule: EvaluationSchedule,
        *,
        trigger_type: str,
        scheduled_for: datetime,
        trigger_key: str,
    ) -> EvaluationScheduleRun:
        item = EvaluationScheduleRun(
            id=str(uuid4()),
            schedule_id=schedule.id,
            trigger_key=trigger_key,
            trigger_type=trigger_type,
            scheduled_for=_as_utc(scheduled_for),
            config_snapshot_json=canonical_json(self._snapshot(schedule)),
            status="queued",
        )
        session.add(item)
        session.flush()
        self.jobs.enqueue(
            session,
            "evaluation_schedule_trigger",
            {"evaluation_schedule_run_id": item.id},
        )
        return item

    def run_now(self, schedule_id: str) -> EvaluationScheduleRun:
        self.sync_terminal_runs()
        now = datetime.now(UTC)
        with transaction(self.session_factory) as session:
            schedule = session.get(EvaluationSchedule, schedule_id)
            if schedule is None:
                raise AnalystBenchError(
                    "evaluation_schedule_not_found",
                    "找不到定时测评计划。",
                    status_code=404,
                )
            active = session.scalar(
                select(EvaluationScheduleRun.id)
                .where(
                    EvaluationScheduleRun.schedule_id == schedule_id,
                    EvaluationScheduleRun.status.in_(("queued", "submitted")),
                )
                .limit(1)
            )
            if active:
                raise AnalystBenchError(
                    "evaluation_schedule_overlap",
                    "该计划已有运行中或等待触发的批次。",
                    status_code=409,
                )
            item = self._enqueue_run(
                session,
                schedule,
                trigger_type="manual",
                scheduled_for=now,
                trigger_key=f"{schedule.id}:manual:{uuid4()}",
            )
            session.flush()
            session.expunge(item)
            return item

    def enqueue_due(self, *, now: datetime | None = None) -> int:
        current = _as_utc(now or datetime.now(UTC))
        created = 0
        try:
            self.sync_terminal_runs()
            with transaction(self.session_factory) as session:
                schedules = list(
                    session.scalars(
                        select(EvaluationSchedule)
                        .where(
                            EvaluationSchedule.enabled.is_(True),
                            EvaluationSchedule.next_run_at.is_not(None),
                            EvaluationSchedule.next_run_at <= current,
                        )
                        .order_by(EvaluationSchedule.next_run_at, EvaluationSchedule.id)
                        .with_for_update(skip_locked=True)
                    )
                )
                for schedule in schedules:
                    scheduled_for = latest_due_run(
                        schedule.local_time,
                        schedule.timezone,
                        now=current,
                    )
                    trigger_key = f"{schedule.id}:{scheduled_for.isoformat()}"
                    exists = session.scalar(
                        select(EvaluationScheduleRun.id).where(
                            EvaluationScheduleRun.trigger_key == trigger_key
                        )
                    )
                    if not exists:
                        trigger_type = (
                            "catch_up"
                            if current - scheduled_for > timedelta(minutes=1)
                            else "scheduled"
                        )
                        self._enqueue_run(
                            session,
                            schedule,
                            trigger_type=trigger_type,
                            scheduled_for=scheduled_for,
                            trigger_key=trigger_key,
                        )
                        created += 1
                        logger.info(
                            "schedule_due_claimed",
                            extra={
                                "schedule_id": schedule.id,
                                "scheduled_for": scheduled_for.isoformat(),
                            },
                        )
                    schedule.last_triggered_at = current
                    schedule.next_run_at = next_daily_run(
                        schedule.local_time,
                        schedule.timezone,
                        after=current,
                    )
        except (IntegrityError, OperationalError) as exc:
            # Another worker can win the unique trigger-key insert. SQLite may
            # report this race as a short database lock instead; either case is
            # safe to retry on the next poll.
            logger.warning(
                "schedule_due_race_lost",
                extra={"error": str(exc)},
            )
            return 0
        return created

    def execute_trigger(self, schedule_run_id: str) -> None:
        with transaction(self.session_factory) as session:
            run = session.get(EvaluationScheduleRun, schedule_run_id)
            if run is None:
                raise AnalystBenchError(
                    "evaluation_schedule_run_not_found",
                    "找不到计划执行记录。",
                    status_code=404,
                )
            existing = session.scalar(
                select(EvaluationSubmission).where(
                    EvaluationSubmission.schedule_run_id == run.id
                )
            )
            if existing is not None:
                run.status = (
                    existing.status
                    if existing.status in SUBMISSION_TERMINAL
                    else "submitted"
                )
                return
            if run.status != "queued":
                return
            overlap = session.scalar(
                select(EvaluationSubmission)
                .join(
                    EvaluationScheduleRun,
                    EvaluationScheduleRun.id == EvaluationSubmission.schedule_run_id,
                )
                .where(
                    EvaluationScheduleRun.schedule_id == run.schedule_id,
                    EvaluationScheduleRun.id != run.id,
                    EvaluationSubmission.status.not_in(SUBMISSION_TERMINAL),
                )
                .limit(1)
            )
            if overlap is not None:
                run.status = "skipped_overlap"
                run.error_json = canonical_json(
                    {
                        "code": "evaluation_schedule_overlap",
                        "message": "上一批次尚未结束，本次计划已跳过。",
                        "submission_id": overlap.id,
                    }
                )
                logger.info(
                    "schedule_skipped",
                    extra={"schedule_run_id": run.id, "reason": "overlap"},
                )
                return
            snapshot = json.loads(run.config_snapshot_json)

        case_paths = (
            None if snapshot["case_mode"] == "all_ready" else snapshot["case_paths"]
        )
        target_ids = snapshot.get("target_ids") or []
        try:
            submission = self.submissions.create_submission(
                snapshot["dataset_key"],
                [] if target_ids else snapshot["method_ids"],
                snapshot["judge_runner"],
                target_ids=target_ids or None,
                case_paths=case_paths,
                schedule_run_id=schedule_run_id,
            )
        except AnalystBenchError as exc:
            no_cases = exc.code in {
                "case_logs_missing",
                "evaluation_cases_missing",
                "test_set_empty",
            }
            with transaction(self.session_factory) as session:
                run = session.get(EvaluationScheduleRun, schedule_run_id)
                if run is not None:
                    run.status = "skipped_no_cases" if no_cases else "failed_preflight"
                    run.error_json = canonical_json(
                        {
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        }
                    )
            logger.warning(
                "schedule_trigger_failed",
                extra={"schedule_run_id": schedule_run_id, "error_code": exc.code},
            )
            return
        with transaction(self.session_factory) as session:
            run = session.get(EvaluationScheduleRun, schedule_run_id)
            if run is not None:
                run.status = "submitted"
                run.error_json = "{}"
        logger.info(
            "schedule_submission_created",
            extra={"schedule_run_id": schedule_run_id, "submission_id": submission.id},
        )

    def sync_terminal_runs(self) -> None:
        with transaction(self.session_factory) as session:
            active_runs = list(
                session.scalars(
                    select(EvaluationScheduleRun).where(
                        EvaluationScheduleRun.status == "submitted"
                    )
                )
            )
            for run in active_runs:
                submission = session.scalar(
                    select(EvaluationSubmission).where(
                        EvaluationSubmission.schedule_run_id == run.id
                    )
                )
                if submission is not None and submission.status in SUBMISSION_TERMINAL:
                    run.status = submission.status

    def list_runs(self, schedule_id: str) -> list[EvaluationScheduleRun]:
        self.get(schedule_id)
        self.sync_terminal_runs()
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationScheduleRun)
                    .where(EvaluationScheduleRun.schedule_id == schedule_id)
                    .order_by(EvaluationScheduleRun.scheduled_for.desc())
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def get_run(self, run_id: str) -> EvaluationScheduleRun:
        self.sync_terminal_runs()
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationScheduleRun, run_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_schedule_run_not_found",
                    "找不到计划执行记录。",
                    status_code=404,
                )
            session.expunge(item)
            return item

    def run_view(self, item: EvaluationScheduleRun) -> dict[str, Any]:
        with transaction(self.session_factory) as session:
            submission = session.scalar(
                select(EvaluationSubmission).where(
                    EvaluationSubmission.schedule_run_id == item.id
                )
            )
        return {
            "id": item.id,
            "schedule_id": item.schedule_id,
            "trigger_type": item.trigger_type,
            "scheduled_for": _as_utc(item.scheduled_for),
            "status": (
                submission.status
                if submission is not None and submission.status in SUBMISSION_TERMINAL
                else item.status
            ),
            "submission_id": submission.id if submission is not None else None,
            "submission_timestamp": (
                submission.run_timestamp if submission is not None else None
            ),
            "config": json.loads(item.config_snapshot_json),
            "error": json.loads(item.error_json or "{}"),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def view(self, item: EvaluationSchedule) -> dict[str, Any]:
        stored_method_ids = json.loads(item.method_ids_json or "[]")
        target_ids = json.loads(item.target_ids_json or "[]")
        # Target schedules keep materialized Method ids only as an internal
        # execution bridge.  The public schedule contract remains Target-only.
        method_ids = [] if target_ids else stored_method_ids
        with transaction(self.session_factory) as session:
            methods = [
                method
                for method_id in method_ids
                if (method := session.get(EvaluationMethod, method_id)) is not None
            ]
            latest = session.scalar(
                select(EvaluationScheduleRun)
                .where(EvaluationScheduleRun.schedule_id == item.id)
                .order_by(EvaluationScheduleRun.scheduled_for.desc())
                .limit(1)
            )
            if latest is not None:
                session.expunge(latest)
        target_views: list[dict[str, Any]] = []
        if target_ids:
            service = EvaluationTargetService(self.session_factory, self.settings)
            for target_id in target_ids:
                try:
                    target_views.append(service.target_view(target_id))
                except AnalystBenchError:
                    target_views.append({"id": target_id, "status": "missing"})
        return {
            "id": item.id,
            "name": item.name,
            "dataset_key": item.dataset_key,
            "case_mode": item.case_mode,
            "case_paths": json.loads(item.case_paths_json or "[]"),
            "method_ids": method_ids,
            "methods": [
                {
                    "id": method.id,
                    "key": method.method_key,
                    "version": method.version_number,
                    "status": method.status,
                }
                for method in methods
            ],
            "target_ids": target_ids,
            "targets": target_views,
            "target_selections": [
                {
                    "harness_id": target.get("harness", {}).get("id"),
                    "model_id": (target.get("model") or {}).get("id"),
                }
                for target in target_views
                if target.get("harness", {}).get("id")
            ],
            "judge_runner": item.judge_runner,
            "timezone": item.timezone,
            "local_time": item.local_time,
            "enabled": item.enabled,
            "next_run_at": _as_utc(item.next_run_at) if item.next_run_at else None,
            "last_triggered_at": (
                _as_utc(item.last_triggered_at) if item.last_triggered_at else None
            ),
            "latest_run": self.run_view(latest) if latest is not None else None,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
