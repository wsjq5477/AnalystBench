"""P15 filesystem-first evaluation submissions with durable worker orchestration."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import statistics
import string
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from analystbench.case_library import report_payload_from_text
from analystbench.config import Settings
from analystbench.content_store import canonical_json, content_hash
from analystbench.db.models import (
    EvaluationMethod,
    EvaluationSchedule,
    EvaluationScheduleRun,
    EvaluationSubmission,
    EvaluationSubmissionCaseRun,
    EvaluationSubmissionMethodRun,
    EvaluationTarget,
    Job,
)
from analystbench.direct_evaluation import evaluate_direct
from analystbench.errors import AnalystBenchError
from analystbench.evaluation_target import EvaluationTargetService
from analystbench.executable_resolver import resolve_executable
from analystbench.jobs import JobQueue
from analystbench.reporting import render_markdown
from analystbench.services import transaction

METHOD_KEY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._()-]{0,98}[A-Za-z0-9)])?$"
)
RESERVED_METHOD_KEYS = {"result", "run", "inputs", "artifacts", "_artifacts", "logs"}
ALLOWED_PLACEHOLDERS = {"input", "input_dir", "workspace", "tool_dir"}
SHELL_TOKENS = {"|", "||", "&&", ";", ">", ">>", "<", "2>", "2>>"}
TERMINAL_METHOD_STATES = {"succeeded", "failed", "timeout", "cancelled"}


class EvaluationCommandError(Exception):
    def __init__(self, code: str, message: str, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


class EvaluationWorkspacePreparer(Protocol):
    """Optional extension point called before an evaluation command starts."""

    def prepare(
        self, *, method_id: str, workspace: Path
    ) -> dict[str, object] | None: ...


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _error_output_tail(value: object, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text_value = value.decode("utf-8", errors="replace")
    else:
        text_value = str(value)
    return text_value[-limit:]


def _scoring_error_payload(exc: Exception) -> dict[str, str]:
    payload = {"code": "scoring_failed", "message": str(exc)}
    cause_code = getattr(exc, "code", None)
    if cause_code:
        payload["cause_code"] = str(cause_code)
    for stream_name in ("stdout", "stderr"):
        tail = _error_output_tail(getattr(exc, stream_name, None))
        if tail:
            payload[f"{stream_name}_tail"] = tail
    return payload


def _safe_relative_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise AnalystBenchError("path_invalid", f"不安全的相对路径：{value}")
    return candidate


def _safe_case_directory(root: Path, case_path: str) -> Path:
    relative = _safe_relative_path(case_path)
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    if candidate == root_resolved or root_resolved not in candidate.parents:
        raise AnalystBenchError("path_invalid", f"Case 路径越界：{case_path}")
    return candidate


def inspect_case_logs(case_directory: Path) -> dict[str, Any]:
    """Enumerate a local Case logs directory without registration or hashing."""
    logs_directory = case_directory / "logs"
    files: list[str] = []
    unsafe: list[str] = []
    empty: list[str] = []
    if logs_directory.is_dir():
        for item in sorted(logs_directory.rglob("*"), key=lambda value: value.as_posix()):
            if item.name == "manifest.json" and item.parent == logs_directory:
                continue
            if item.is_symlink():
                unsafe.append(item.relative_to(logs_directory).as_posix())
                continue
            if item.is_file():
                relative_name = item.relative_to(logs_directory).as_posix()
                files.append(relative_name)
                if item.stat().st_size == 0:
                    empty.append(relative_name)
    manifest_path = logs_directory / "manifest.json"
    configured_primary = ""
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise AnalystBenchError(
                "case_logs_manifest_invalid",
                f"日志主文件配置无法解析：{manifest_path}",
            ) from exc
        if isinstance(manifest, dict) and isinstance(manifest.get("primary"), str):
            configured_primary = manifest["primary"]
    primary = configured_primary or (files[0] if len(files) == 1 else "")
    issues: list[dict[str, Any]] = []
    if not files:
        issues.append({"code": "case_logs_missing", "message": "Case 没有原始日志"})
    if unsafe:
        issues.append(
            {
                "code": "case_logs_unsafe",
                "message": "日志目录包含符号链接",
                "paths": unsafe,
            }
        )
    if empty:
        issues.append(
            {
                "code": "case_logs_empty",
                "message": "日志目录包含空文件",
                "paths": empty,
            }
        )
    if files and (not primary or primary not in files):
        issues.append(
            {
                "code": "case_primary_log_missing",
                "message": "多日志 Case 必须选择有效主日志",
            }
        )
    return {
        "directory": str(logs_directory),
        "files": files,
        "log_count": len(files),
        "primary_log": primary,
        "submission_ready": not issues,
        "blocking_issues": issues,
    }


class EvaluationMethodService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    def _validate_tool_dir(self, tool_dir: str | None) -> str | None:
        if not tool_dir:
            return None
        resolved = Path(tool_dir).expanduser().resolve()
        for protected in (
            self.settings.results_formal_path.resolve(),
            self.settings.results_tmp_path.resolve(),
            self.settings.workspace_root_path.resolve(),
        ):
            if resolved == protected or protected in resolved.parents:
                raise AnalystBenchError(
                    "evaluation_method_invalid",
                    "工具目录不能位于结果目录或运行工作区内。",
                )
        return str(resolved)

    @staticmethod
    def _validate(
        method_key: str,
        name: str | None,
        command_template: str,
        tool_dir: str | None,
        timeout_seconds: int,
        max_output_bytes: int,
        concurrency_limit: int,
    ) -> list[str]:
        key = method_key.strip()
        if not METHOD_KEY_RE.fullmatch(key) or key.lower() in RESERVED_METHOD_KEYS:
            raise AnalystBenchError(
                "evaluation_method_invalid",
                "测评方式 key 必须以字母或数字开头，以字母、数字或右括号结尾，"
                "只能包含字母、数字、点、括号、-、_，且不能使用保留名。",
            )
        if not (name or "").strip() or not command_template.strip():
            raise AnalystBenchError("evaluation_method_invalid", "Key 和命令不能为空。")
        try:
            argv = shlex.split(command_template, posix=True)
        except ValueError as exc:
            raise AnalystBenchError(
                "evaluation_method_invalid", f"命令模板无法解析：{exc}"
            ) from exc
        if not argv or any(token in SHELL_TOKENS for token in argv):
            raise AnalystBenchError(
                "evaluation_method_invalid", "命令不能为空，也不能包含 Shell 组合操作符。"
            )
        fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(command_template)
            if field_name
        }
        unknown = fields - ALLOWED_PLACEHOLDERS
        if unknown:
            raise AnalystBenchError(
                "evaluation_method_invalid",
                f"命令包含不支持的占位符：{sorted(unknown)}",
            )
        if "tool_dir" in fields and not tool_dir:
            raise AnalystBenchError(
                "evaluation_method_invalid", "命令使用 {tool_dir} 时必须配置工具目录。"
            )
        if not 1 <= timeout_seconds <= 7200:
            raise AnalystBenchError("evaluation_method_invalid", "超时必须在 1 到 7200 秒之间。")
        if not 1024 <= max_output_bytes <= 100 * 1024 * 1024:
            raise AnalystBenchError(
                "evaluation_method_invalid", "输出上限必须在 1 KiB 到 100 MiB。"
            )
        if not 1 <= concurrency_limit <= 32:
            raise AnalystBenchError("evaluation_method_invalid", "并发限制必须在 1 到 32。")
        return argv

    def create(
        self,
        method_key: str,
        name: str | None,
        command_template: str,
        tool_dir: str | None = None,
        timeout_seconds: int = 1800,
        max_output_bytes: int = 10 * 1024 * 1024,
        concurrency_limit: int = 1,
    ) -> EvaluationMethod:
        method_key = method_key.strip()
        name = (name or method_key).strip()
        tool_dir = self._validate_tool_dir(tool_dir)
        self._validate(
            method_key,
            name,
            command_template,
            tool_dir,
            timeout_seconds,
            max_output_bytes,
            concurrency_limit,
        )
        with transaction(self.session_factory) as session:
            current = session.scalar(
                select(func.max(EvaluationMethod.version_number)).where(
                    EvaluationMethod.method_key == method_key
                )
            )
            version = int(current or 0) + 1
            manifest = {
                "method_key": method_key,
                "name": name,
                "version_number": version,
                "tool_dir": tool_dir,
                "command_template": command_template,
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": max_output_bytes,
                "concurrency_limit": concurrency_limit,
            }
            item = EvaluationMethod(
                id=str(uuid4()),
                method_key=method_key,
                name=name,
                version_number=version,
                tool_dir=tool_dir,
                command_template=command_template,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                concurrency_limit=concurrency_limit,
                content_hash=content_hash(canonical_json(manifest).encode("utf-8")),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
            return item

    def get(self, method_id: str) -> EvaluationMethod:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationMethod, method_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_method_not_found", "找不到测评方式。", status_code=404
                )
            session.expunge(item)
            return item

    def list(self) -> list[EvaluationMethod]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationMethod)
                    .where(
                        ~EvaluationMethod.id.in_(
                            select(EvaluationTarget.materialized_method_id).where(
                                EvaluationTarget.materialized_method_id.is_not(None)
                            )
                        )
                    )
                    .order_by(
                        EvaluationMethod.method_key, EvaluationMethod.version_number.desc()
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def probe(self, method_id: str) -> EvaluationMethod:
        item = self.get(method_id)
        self._validate_tool_dir(item.tool_dir)
        argv = self._validate(
            item.method_key,
            item.name,
            item.command_template,
            item.tool_dir,
            item.timeout_seconds,
            item.max_output_bytes,
            item.concurrency_limit,
        )
        executable = resolve_executable(argv[0])
        available = executable is not None
        tool_dir_ok = item.tool_dir is None or Path(item.tool_dir).expanduser().is_dir()
        probe = {
            "available": bool(available and tool_dir_ok),
            "executable": executable or argv[0],
            "tool_dir_ok": tool_dir_ok,
            "checked_at": datetime.now().isoformat(),
        }
        with transaction(self.session_factory) as session:
            stored = session.get(EvaluationMethod, method_id)
            assert stored is not None
            stored.last_probe_json = canonical_json(probe)
        return self.get(method_id)

    def freeze(self, method_id: str) -> EvaluationMethod:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationMethod, method_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_method_not_found", "找不到测评方式。", status_code=404
                )
            probe = json.loads(item.last_probe_json or "{}")
            if not probe.get("available"):
                raise AnalystBenchError(
                    "evaluation_method_unavailable", "请先成功检测命令，再冻结测评方式。"
                )
            item.status = "frozen"
        return self.get(method_id)

    def archive(self, method_id: str) -> EvaluationMethod:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationMethod, method_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_method_not_found", "找不到测评方式。", status_code=404
                )
            item.status = "archived"
        return self.get(method_id)

    def _safe_run_directory(self, value: str) -> Path:
        root = self.settings.results_formal_path.resolve()
        configured = Path(value)
        if configured.is_symlink():
            raise AnalystBenchError(
                "evaluation_method_delete_path_invalid",
                "历史结果目录是符号链接，已拒绝自动删除。",
                status_code=409,
            )
        resolved = configured.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise AnalystBenchError(
                "evaluation_method_delete_path_invalid",
                "历史结果目录不在正式结果根目录内，已拒绝自动删除。",
                status_code=409,
            ) from exc
        if len(relative.parts) != 5 or relative.parts[-2] != "runs":
            raise AnalystBenchError(
                "evaluation_method_delete_path_invalid",
                "历史结果目录结构异常，已拒绝自动删除。",
                status_code=409,
            )
        return resolved

    @staticmethod
    def _job_references(payload_json: str, identifiers: set[str]) -> bool:
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and any(
            isinstance(value, str) and value in identifiers
            for value in payload.values()
        )

    def delete(self, method_id: str) -> dict[str, int]:
        quarantined: list[tuple[Path, Path]] = []
        workspace_roots: list[Path] = []
        committed = False
        summary = {
            "submissions_deleted": 0,
            "schedule_runs_deleted": 0,
            "schedules_deleted": 0,
            "local_directories_deleted": 0,
        }
        try:
            with transaction(self.session_factory) as session:
                item = session.get(EvaluationMethod, method_id)
                if item is None:
                    raise AnalystBenchError(
                        "evaluation_method_not_found",
                        "找不到测评方式。",
                        status_code=404,
                    )
                if session.scalar(
                    select(EvaluationTarget.id).where(
                        EvaluationTarget.materialized_method_id == method_id
                    )
                ):
                    raise AnalystBenchError(
                        "evaluation_method_managed_by_target",
                        "该执行方式由运行组合管理，不能通过旧测评方式接口删除。",
                        status_code=409,
                    )

                submission_ids = set(
                    session.scalars(
                        select(EvaluationSubmissionCaseRun.submission_id)
                        .join(
                            EvaluationSubmissionMethodRun,
                            EvaluationSubmissionMethodRun.case_run_id
                            == EvaluationSubmissionCaseRun.id,
                        )
                        .where(EvaluationSubmissionMethodRun.method_id == method_id)
                    )
                )

                schedules_to_delete: set[str] = set()
                for schedule in session.scalars(select(EvaluationSchedule)):
                    configured_method_ids = json.loads(
                        schedule.method_ids_json or "[]"
                    )
                    if method_id not in configured_method_ids:
                        continue
                    remaining_method_ids = [
                        value
                        for value in configured_method_ids
                        if value != method_id
                    ]
                    if remaining_method_ids:
                        schedule.method_ids_json = canonical_json(
                            remaining_method_ids
                        )
                    else:
                        schedules_to_delete.add(schedule.id)

                schedule_run_ids: set[str] = set()
                schedule_runs = list(
                    session.scalars(select(EvaluationScheduleRun))
                )
                for schedule_run in schedule_runs:
                    try:
                        snapshot = json.loads(
                            schedule_run.config_snapshot_json or "{}"
                        )
                    except json.JSONDecodeError:
                        snapshot = {}
                    snapshot_method_ids = (
                        snapshot.get("method_ids", [])
                        if isinstance(snapshot, dict)
                        else []
                    )
                    if (
                        method_id in snapshot_method_ids
                        or schedule_run.schedule_id in schedules_to_delete
                    ):
                        schedule_run_ids.add(schedule_run.id)

                if schedule_run_ids:
                    submission_ids.update(
                        session.scalars(
                            select(EvaluationSubmission.id).where(
                                EvaluationSubmission.schedule_run_id.in_(
                                    schedule_run_ids
                                )
                            )
                        )
                    )

                if submission_ids:
                    linked_schedule_run_ids = set(
                        value
                        for value in session.scalars(
                            select(EvaluationSubmission.schedule_run_id).where(
                                EvaluationSubmission.id.in_(submission_ids)
                            )
                        )
                        if value
                    )
                    schedule_run_ids.update(linked_schedule_run_ids)

                case_runs = (
                    list(
                        session.scalars(
                            select(EvaluationSubmissionCaseRun).where(
                                EvaluationSubmissionCaseRun.submission_id.in_(
                                    submission_ids
                                )
                            )
                        )
                    )
                    if submission_ids
                    else []
                )
                case_run_ids = {case_run.id for case_run in case_runs}
                method_runs = (
                    list(
                        session.scalars(
                            select(EvaluationSubmissionMethodRun).where(
                                EvaluationSubmissionMethodRun.case_run_id.in_(
                                    case_run_ids
                                )
                            )
                        )
                    )
                    if case_run_ids
                    else []
                )
                method_run_ids = {method_run.id for method_run in method_runs}
                if any(
                    method_run.status in {"running", "cancelling"}
                    for method_run in method_runs
                ):
                    raise AnalystBenchError(
                        "evaluation_method_delete_running",
                        "该测评方式仍有命令正在运行，请先取消批次并等待任务停止。",
                        status_code=409,
                    )

                job_identifiers = (
                    case_run_ids | method_run_ids | schedule_run_ids
                )
                related_jobs = [
                    job
                    for job in session.scalars(select(Job))
                    if self._job_references(job.payload_json, job_identifiers)
                ]
                if any(job.status == "running" for job in related_jobs):
                    raise AnalystBenchError(
                        "evaluation_method_delete_running",
                        "该测评方式仍有后台任务正在运行，请稍后重试删除。",
                        status_code=409,
                    )

                run_directories = {
                    self._safe_run_directory(case_run.run_directory)
                    for case_run in case_runs
                }
                for run_directory in sorted(
                    run_directories, key=lambda value: value.as_posix()
                ):
                    if not run_directory.exists():
                        continue
                    if not run_directory.is_dir():
                        raise AnalystBenchError(
                            "evaluation_method_delete_path_invalid",
                            "历史结果路径不是目录，已拒绝自动删除。",
                            status_code=409,
                        )
                    quarantine = run_directory.with_name(
                        f".{run_directory.name}.delete-{uuid4().hex}"
                    )
                    run_directory.rename(quarantine)
                    quarantined.append((run_directory, quarantine))

                for submission_id in submission_ids:
                    workspace_roots.append(
                        self.settings.workspace_root_path
                        / "evaluation"
                        / submission_id
                    )
                for job in related_jobs:
                    session.delete(job)
                if method_run_ids:
                    session.execute(
                        sql_delete(EvaluationSubmissionMethodRun).where(
                            EvaluationSubmissionMethodRun.id.in_(method_run_ids)
                        )
                    )
                if case_run_ids:
                    session.execute(
                        sql_delete(EvaluationSubmissionCaseRun).where(
                            EvaluationSubmissionCaseRun.id.in_(case_run_ids)
                        )
                    )
                if submission_ids:
                    session.execute(
                        sql_delete(EvaluationSubmission).where(
                            EvaluationSubmission.id.in_(submission_ids)
                        )
                    )
                if schedule_run_ids:
                    session.execute(
                        sql_delete(EvaluationScheduleRun).where(
                            EvaluationScheduleRun.id.in_(schedule_run_ids)
                        )
                    )
                if schedules_to_delete:
                    session.execute(
                        sql_delete(EvaluationSchedule).where(
                            EvaluationSchedule.id.in_(schedules_to_delete)
                        )
                    )
                session.delete(item)

                summary["submissions_deleted"] = len(submission_ids)
                summary["schedule_runs_deleted"] = len(schedule_run_ids)
                summary["schedules_deleted"] = len(schedules_to_delete)

            committed = True
            for _original, quarantine in quarantined:
                shutil.rmtree(quarantine, ignore_errors=True)
                if not quarantine.exists():
                    summary["local_directories_deleted"] += 1
            for workspace_root in workspace_roots:
                if workspace_root.is_dir():
                    shutil.rmtree(workspace_root, ignore_errors=True)
            return summary
        except Exception:
            if not committed:
                for original, quarantine in reversed(quarantined):
                    if quarantine.exists() and not original.exists():
                        quarantine.rename(original)
            raise

    def revise(
        self,
        method_id: str,
        *,
        name: str | None = None,
        command_template: str | None = None,
        tool_dir: str | None = None,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
        concurrency_limit: int | None = None,
    ) -> EvaluationMethod:
        current = self.get(method_id)
        return self.create(
            method_key=current.method_key,
            name=name or current.name,
            command_template=command_template or current.command_template,
            tool_dir=current.tool_dir if tool_dir is None else tool_dir,
            timeout_seconds=timeout_seconds or current.timeout_seconds,
            max_output_bytes=max_output_bytes or current.max_output_bytes,
            concurrency_limit=concurrency_limit or current.concurrency_limit,
        )

    @staticmethod
    def view(item: EvaluationMethod) -> dict[str, Any]:
        return {
            "id": item.id,
            "key": item.method_key,
            "name": item.name,
            "version": item.version_number,
            "tool_dir": item.tool_dir,
            "command_template": item.command_template,
            "timeout_seconds": item.timeout_seconds,
            "max_output_bytes": item.max_output_bytes,
            "concurrency_limit": item.concurrency_limit,
            "status": item.status,
            "content_hash": item.content_hash,
            "probe": json.loads(item.last_probe_json or "{}"),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }


class EvaluationSubmissionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        workspace_preparer: EvaluationWorkspacePreparer | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.workspace_preparer = workspace_preparer
        self.jobs = JobQueue(session_factory)

    def _next_timestamp(self, case_directories: list[Path]) -> str:
        candidate = datetime.now()
        for _ in range(120):
            timestamp = candidate.strftime("%Y%m%d%H%M%S")
            if all(not (directory / "runs" / timestamp).exists() for directory in case_directories):
                return timestamp
            candidate += timedelta(seconds=1)
        raise AnalystBenchError("run_directory_conflict", "无法分配不冲突的运行时间目录。")

    def create_submission(
        self,
        dataset_key: str,
        method_ids: list[str] | None,
        judge_runner: str = "claude",
        *,
        target_ids: list[str] | None = None,
        target_selections: list[dict[str, str | None]] | None = None,
        case_paths: list[str] | None = None,
        schedule_run_id: str | None = None,
        purpose: str = "normal",
        optimization_context: dict[str, Any] | None = None,
    ) -> EvaluationSubmission:
        selection_modes = sum(
            bool(value) for value in (method_ids, target_ids, target_selections)
        )
        if selection_modes != 1:
            raise AnalystBenchError(
                "evaluation_selection_invalid",
                "请选择旧测评方式或 Harness/模型组合中的一种，且不能混用。",
            )
        target_snapshots: list[dict[str, Any]] = []
        if target_selections:
            targets, target_snapshots = EvaluationTargetService(
                self.session_factory, self.settings
            ).resolve_selections(target_selections)
            method_ids = [str(item.materialized_method_id) for item in targets]
        elif target_ids:
            targets, target_snapshots = EvaluationTargetService(
                self.session_factory, self.settings
            ).snapshots(target_ids)
            method_ids = [str(item.materialized_method_id) for item in targets]
        assert method_ids
        if not method_ids:
            raise AnalystBenchError("evaluation_methods_missing", "至少选择一种测评方式。")
        if judge_runner not in {"claude", "opencode", "lexical"}:
            raise AnalystBenchError("validation_failed", "不支持的评分 Judge。")
        if purpose not in {"normal", "skill_optimization"}:
            raise AnalystBenchError("validation_failed", "提交用途无效。")
        results_formal_path = self.settings.results_formal_path.resolve()
        dataset_directory = _safe_case_directory(results_formal_path, dataset_key)
        if not dataset_directory.is_dir():
            raise AnalystBenchError(
                "test_set_not_found", f"找不到测试集：{dataset_key}", status_code=404
            )
        case_files = sorted(dataset_directory.glob("*/*/case.json"))
        if not case_files:
            raise AnalystBenchError("test_set_empty", "测试集没有可用 Case。")
        cases_by_path = {
            case_file.parent.relative_to(results_formal_path).as_posix(): case_file
            for case_file in case_files
        }
        if case_paths is None:
            requested_case_paths = list(cases_by_path)
        else:
            requested_case_paths = []
            for value in dict.fromkeys(case_paths):
                relative = _safe_relative_path(value).as_posix()
                parts = Path(relative).parts
                if len(parts) != 3 or parts[0] != dataset_key:
                    raise AnalystBenchError(
                        "evaluation_case_path_invalid",
                        f"Case 路径不属于测试集 {dataset_key}：{value}",
                    )
                requested_case_paths.append(relative)
            unknown_paths = [
                value for value in requested_case_paths if value not in cases_by_path
            ]
            if unknown_paths:
                raise AnalystBenchError(
                    "evaluation_cases_not_found",
                    "选择中包含不存在的 Case。",
                    [{"case_path": value} for value in unknown_paths],
                )
        if not requested_case_paths:
            raise AnalystBenchError("evaluation_cases_missing", "至少选择一个 Case。")
        methods: list[EvaluationMethod] = []
        with transaction(self.session_factory) as session:
            for method_id in dict.fromkeys(method_ids):
                item = session.get(EvaluationMethod, method_id)
                if item is None:
                    raise AnalystBenchError("evaluation_method_not_found", "找不到测评方式。")
                if item.status != "frozen":
                    raise AnalystBenchError(
                        "evaluation_method_not_frozen", f"测评方式 {item.name} 尚未冻结。"
                    )
                methods.append(item)

        case_inputs: list[dict[str, Any]] = []
        skipped_cases: list[dict[str, Any]] = []
        for relative in requested_case_paths:
            case_file = cases_by_path[relative]
            case_directory = case_file.parent
            try:
                payload = json.loads(case_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                skipped_cases.append({"case_path": relative, "reason": "case_json_invalid"})
                continue
            logs = inspect_case_logs(case_directory)
            if not logs["submission_ready"]:
                skipped_cases.append(
                    {
                        "case_path": relative,
                        "reason": "case_logs_missing",
                        "issues": logs["blocking_issues"],
                    }
                )
                continue
            case_obj = payload.get("case") if isinstance(payload, dict) else {}
            case_key = (
                str(case_obj.get("case_key"))
                if isinstance(case_obj, dict) and case_obj.get("case_key")
                else case_directory.name
            )
            case_inputs.append(
                {
                    "case_file": case_file,
                    "case_directory": case_directory,
                    "case_path": relative,
                    "case_key": case_key,
                    "logs": logs,
                }
            )
        if not case_inputs:
            raise AnalystBenchError(
                "case_logs_missing",
                "所选 Case 均缺少有效日志或 Case JSON 无效，未创建测评批次。",
                skipped_cases,
            )

        timestamp = self._next_timestamp([item["case_directory"] for item in case_inputs])
        submission_id = str(uuid4())
        manifest = {
            "dataset_key": dataset_key,
            "run_timestamp": timestamp,
            "judge_runner": judge_runner,
            "schedule_run_id": schedule_run_id,
            "requested_case_paths": requested_case_paths,
            "selected_case_paths": [item["case_path"] for item in case_inputs],
            "skipped_cases": skipped_cases,
            "method_ids": [item.id for item in methods],
            "methods": [
                {
                    "id": item.id,
                    "key": item.method_key,
                    "name": item.name,
                    "version": item.version_number,
                    "tool_dir": item.tool_dir,
                    "command_template": item.command_template,
                    "timeout_seconds": item.timeout_seconds,
                    "max_output_bytes": item.max_output_bytes,
                    "concurrency_limit": item.concurrency_limit,
                    "content_hash": item.content_hash,
                }
                for item in methods
            ],
            "cases": [
                {
                    "case_path": item["case_path"],
                    "case_key": item["case_key"],
                    "primary_log": item["logs"]["primary_log"],
                    "files": item["logs"]["files"],
                }
                for item in case_inputs
            ],
        }
        if target_snapshots:
            manifest["target_ids"] = [item["id"] for item in target_snapshots]
            manifest["targets"] = target_snapshots
            manifest["execution_mode"] = "targets"
        created_case_runs: list[tuple[EvaluationSubmissionCaseRun, dict[str, Any]]] = []
        with transaction(self.session_factory) as session:
            submission = EvaluationSubmission(
                id=submission_id,
                dataset_key=dataset_key,
                run_timestamp=timestamp,
                status="queued",
                purpose=purpose,
                optimization_context_json=canonical_json(
                    optimization_context or {}
                ),
                schedule_run_id=schedule_run_id,
                manifest_json=canonical_json(manifest),
            )
            session.add(submission)
            session.flush()
            for case_input in case_inputs:
                run_directory = case_input["case_directory"] / "runs" / timestamp
                case_run = EvaluationSubmissionCaseRun(
                    id=str(uuid4()),
                    submission_id=submission.id,
                    case_path=case_input["case_path"],
                    case_key=case_input["case_key"],
                    run_directory=str(run_directory),
                    status="preparing",
                )
                session.add(case_run)
                session.flush()
                for method in methods:
                    method_run = EvaluationSubmissionMethodRun(
                        id=str(uuid4()),
                        case_run_id=case_run.id,
                        method_id=method.id,
                        status="queued",
                    )
                    session.add(method_run)
                created_case_runs.append((case_run, case_input))
            session.flush()
            session.expunge(submission)

        for case_run, case_input in created_case_runs:
            run_directory = Path(case_run.run_directory)
            inputs = run_directory / "inputs"
            inputs.mkdir(parents=True, exist_ok=False)
            logs_root = case_input["case_directory"] / "logs"
            for relative_name in case_input["logs"]["files"]:
                relative_path = _safe_relative_path(relative_name)
                source = logs_root / relative_path
                destination = inputs / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            self._write_case_state(case_run.id)
        with transaction(self.session_factory) as session:
            method_runs = list(
                session.scalars(
                    select(EvaluationSubmissionMethodRun)
                    .join(
                        EvaluationSubmissionCaseRun,
                        EvaluationSubmissionCaseRun.id == EvaluationSubmissionMethodRun.case_run_id,
                    )
                    .where(EvaluationSubmissionCaseRun.submission_id == submission_id)
                )
            )
            for method_run in method_runs:
                self.jobs.enqueue(
                    session,
                    "evaluation_method_run",
                    {"evaluation_method_run_id": method_run.id},
                )
        self._update_submission(submission_id)
        return self.get_submission(submission_id)

    def get_submission(self, submission_id: str) -> EvaluationSubmission:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmission, submission_id)
            if item is None:
                raise AnalystBenchError(
                    "evaluation_submission_not_found", "找不到测评批次。", status_code=404
                )
            session.expunge(item)
            return item

    def list_submissions(self) -> list[EvaluationSubmission]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationSubmission).order_by(EvaluationSubmission.created_at.desc())
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def delete_submission(self, submission_id: str) -> dict[str, int]:
        terminal_states = {
            "completed",
            "completed_with_errors",
            "failed",
            "cancelled",
        }
        quarantined: list[tuple[Path, Path]] = []
        workspace_root = (
            self.settings.workspace_root_path / "evaluation" / submission_id
        )
        committed = False
        try:
            with transaction(self.session_factory) as session:
                submission = session.get(EvaluationSubmission, submission_id)
                if submission is None:
                    raise AnalystBenchError(
                        "evaluation_submission_not_found",
                        "找不到测评批次。",
                        status_code=404,
                    )
                if submission.status not in terminal_states:
                    raise AnalystBenchError(
                        "evaluation_submission_delete_running",
                        "该测评批次仍在排队或运行，请先取消并等待任务停止。",
                        status_code=409,
                    )
                case_runs = list(
                    session.scalars(
                        select(EvaluationSubmissionCaseRun).where(
                            EvaluationSubmissionCaseRun.submission_id
                            == submission_id
                        )
                    )
                )
                case_run_ids = {item.id for item in case_runs}
                method_runs = (
                    list(
                        session.scalars(
                            select(EvaluationSubmissionMethodRun).where(
                                EvaluationSubmissionMethodRun.case_run_id.in_(
                                    case_run_ids
                                )
                            )
                        )
                    )
                    if case_run_ids
                    else []
                )
                if any(
                    item.status in {"running", "cancelling"}
                    for item in method_runs
                ):
                    raise AnalystBenchError(
                        "evaluation_submission_delete_running",
                        "该测评批次仍有命令正在运行，请等待任务停止。",
                        status_code=409,
                    )
                method_run_ids = {item.id for item in method_runs}
                identifiers = case_run_ids | method_run_ids
                related_jobs = [
                    job
                    for job in session.scalars(select(Job))
                    if self._job_references(job.payload_json, identifiers)
                ]
                if any(job.status == "running" for job in related_jobs):
                    raise AnalystBenchError(
                        "evaluation_submission_delete_running",
                        "该测评批次仍有后台任务正在运行，请稍后重试。",
                        status_code=409,
                    )

                run_directories = {
                    self._safe_submission_run_directory(item.run_directory)
                    for item in case_runs
                }
                for run_directory in sorted(
                    run_directories, key=lambda value: value.as_posix()
                ):
                    if not run_directory.exists():
                        continue
                    if not run_directory.is_dir():
                        raise AnalystBenchError(
                            "evaluation_submission_delete_path_invalid",
                            "批次结果路径不是目录，已拒绝自动删除。",
                            status_code=409,
                        )
                    quarantine = run_directory.with_name(
                        f".{run_directory.name}.delete-{uuid4().hex}"
                    )
                    run_directory.rename(quarantine)
                    quarantined.append((run_directory, quarantine))

                for job in related_jobs:
                    session.delete(job)
                if method_run_ids:
                    session.execute(
                        sql_delete(EvaluationSubmissionMethodRun).where(
                            EvaluationSubmissionMethodRun.id.in_(
                                method_run_ids
                            )
                        )
                    )
                if case_run_ids:
                    session.execute(
                        sql_delete(EvaluationSubmissionCaseRun).where(
                            EvaluationSubmissionCaseRun.id.in_(case_run_ids)
                        )
                    )
                session.delete(submission)
            committed = True
            deleted_directories = 0
            for _original, quarantine in quarantined:
                shutil.rmtree(quarantine, ignore_errors=True)
                if not quarantine.exists():
                    deleted_directories += 1
            if workspace_root.is_dir():
                shutil.rmtree(workspace_root, ignore_errors=True)
            return {
                "submissions_deleted": 1,
                "case_runs_deleted": len(case_run_ids),
                "method_runs_deleted": len(method_run_ids),
                "local_directories_deleted": deleted_directories,
            }
        except Exception:
            if not committed:
                for original, quarantine in reversed(quarantined):
                    if quarantine.exists() and not original.exists():
                        quarantine.rename(original)
            raise

    def target_comparison(self, submission_id: str) -> dict[str, Any]:
        """Aggregate one target-mode submission without conflating queue time and runtime."""
        submission = self.get_submission(submission_id)
        manifest = json.loads(submission.manifest_json or "{}")
        targets = [
            item for item in manifest.get("targets", []) if isinstance(item, dict)
        ]
        if not targets:
            raise AnalystBenchError(
                "evaluation_targets_unavailable",
                "该批次是旧测评方式批次，没有结构化运行组合。",
                status_code=409,
            )
        target_by_key = {str(item["key"]): item for item in targets}
        rows: dict[str, list[dict[str, Any]]] = {key: [] for key in target_by_key}
        for case_run in self.list_case_runs(submission_id):
            run_directory = Path(case_run.run_directory)
            result: dict[str, Any] = {}
            try:
                result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            generation = result.get("generation", {}) if isinstance(result, dict) else {}
            generated = {
                str(item.get("target_key")): item
                for item in generation.get("targets", [])
                if isinstance(item, dict) and item.get("target_key")
            }
            reports = {
                str(item.get("candidate_name")): item
                for item in (result.get("summary", {}) or {}).get("reports", [])
                if isinstance(item, dict) and item.get("candidate_name")
            }
            for key in target_by_key:
                generated_item = generated.get(key, {})
                report = reports.get(key)
                rows[key].append(
                    {
                        "case_path": case_run.case_path,
                        "generation_status": generated_item.get("status", "missing"),
                        "duration_ms": generated_item.get("duration_ms"),
                        "score": report.get("score") if report else None,
                        "passed": report.get("passed") if report else None,
                    }
                )

        def percentile_95(values: list[int]) -> int | None:
            if not values:
                return None
            ordered = sorted(values)
            return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]

        aggregates: list[dict[str, Any]] = []
        for key, target in target_by_key.items():
            samples = rows[key]
            scored = [item for item in samples if item["score"] is not None]
            durations = [
                int(item["duration_ms"])
                for item in samples
                if isinstance(item["duration_ms"], int)
            ]
            successes = [item for item in samples if item["generation_status"] == "succeeded"]
            timeouts = [item for item in samples if item["generation_status"] == "timeout"]
            failures = [
                item
                for item in samples
                if item["generation_status"] in {"failed", "timeout", "cancelled"}
            ]
            aggregates.append(
                {
                    "target": target,
                    "requested_case_count": len(samples),
                    "scored_case_count": len(scored),
                    "coverage_rate": len(scored) / len(samples) if samples else 0.0,
                    "generation_success_rate": len(successes) / len(samples) if samples else 0.0,
                    "timeout_rate": len(timeouts) / len(samples) if samples else 0.0,
                    "generation_failure_rate": len(failures) / len(samples) if samples else 0.0,
                    "average_score": (
                        sum(float(item["score"]) for item in scored) / len(scored)
                        if scored
                        else None
                    ),
                    "pass_rate": (
                        sum(bool(item["passed"]) for item in scored) / len(scored)
                        if scored
                        else None
                    ),
                    "duration_sample_count": len(durations),
                    "median_duration_ms": (
                        round(statistics.median(durations)) if durations else None
                    ),
                    "p95_duration_ms": percentile_95(durations),
                    "cases": samples,
                }
            )

        def groups(field: str) -> list[dict[str, Any]]:
            grouped: dict[str, list[str]] = {}
            for item in aggregates:
                value = item["target"].get(field)
                if not isinstance(value, dict):
                    continue
                group_key = f"{value.get('key')}@v{value.get('version')}"
                grouped.setdefault(group_key, []).append(item["target"]["key"])
            return [
                {"key": key, "target_keys": sorted(target_keys)}
                for key, target_keys in sorted(grouped.items())
                if len(target_keys) >= 2
            ]

        def pairs(grouped: list[dict[str, Any]]) -> list[dict[str, Any]]:
            output: list[dict[str, Any]] = []
            by_key = {item["target"]["key"]: item for item in aggregates}
            for group in grouped:
                values = group["target_keys"]
                for index, left_key in enumerate(values):
                    for right_key in values[index + 1 :]:
                        left = {item["case_path"]: item for item in by_key[left_key]["cases"]}
                        right = {item["case_path"]: item for item in by_key[right_key]["cases"]}
                        shared = [
                            path
                            for path in sorted(left.keys() & right.keys())
                            if left[path]["score"] is not None and right[path]["score"] is not None
                        ]
                        deltas = [
                            float(right[path]["score"]) - float(left[path]["score"])
                            for path in shared
                        ]
                        output.append(
                            {
                                "group": group["key"],
                                "baseline": left_key,
                                "candidate": right_key,
                                "shared_scored_case_count": len(shared),
                                "average_score_delta": (
                                    sum(deltas) / len(deltas) if deltas else None
                                ),
                                "baseline_only_case_count": len(left.keys() - right.keys()),
                                "candidate_only_case_count": len(right.keys() - left.keys()),
                            }
                        )
            return output

        by_harness = groups("harness")
        by_model = groups("model")
        uncontrolled = any(
            isinstance((item.get("harness") or {}).get("source_revision"), dict)
            and bool((item.get("harness") or {}).get("source_revision", {}).get("dirty"))
            for item in targets
        )
        return {
            "submission_id": submission.id,
            "controlled": not uncontrolled,
            "warnings": (
                ["至少一个 Harness 工程在冻结时处于 dirty 状态。"]
                if uncontrolled
                else []
            ),
            "targets": aggregates,
            "by_harness": by_harness,
            "by_model": by_model,
            "pairwise": pairs(by_harness) + pairs(by_model),
        }

    def _safe_submission_run_directory(self, value: str) -> Path:
        root = self.settings.results_formal_path.resolve()
        configured = Path(value)
        if configured.is_symlink():
            raise AnalystBenchError(
                "evaluation_submission_delete_path_invalid",
                "批次结果目录是符号链接，已拒绝自动删除。",
                status_code=409,
            )
        resolved = configured.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise AnalystBenchError(
                "evaluation_submission_delete_path_invalid",
                "批次结果目录不在正式结果根目录内，已拒绝自动删除。",
                status_code=409,
            ) from exc
        if len(relative.parts) != 5 or relative.parts[-2] != "runs":
            raise AnalystBenchError(
                "evaluation_submission_delete_path_invalid",
                "批次结果目录结构异常，已拒绝自动删除。",
                status_code=409,
            )
        return resolved

    @staticmethod
    def _job_references(payload_json: str, identifiers: set[str]) -> bool:
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and any(
            isinstance(value, str) and value in identifiers
            for value in payload.values()
        )

    def list_case_runs(self, submission_id: str) -> list[EvaluationSubmissionCaseRun]:
        self.get_submission(submission_id)
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationSubmissionCaseRun)
                    .where(EvaluationSubmissionCaseRun.submission_id == submission_id)
                    .order_by(EvaluationSubmissionCaseRun.case_path)
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_method_runs(self, case_run_id: str) -> list[EvaluationSubmissionMethodRun]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationSubmissionMethodRun)
                    .where(EvaluationSubmissionMethodRun.case_run_id == case_run_id)
                    .order_by(EvaluationSubmissionMethodRun.created_at)
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def method_artifacts(self, method_run_id: str) -> dict[str, Any]:
        with transaction(self.session_factory) as session:
            method_run = session.get(EvaluationSubmissionMethodRun, method_run_id)
            if method_run is None:
                raise AnalystBenchError(
                    "evaluation_method_run_not_found",
                    "找不到方式运行。",
                    status_code=404,
                )
            case_run = session.get(EvaluationSubmissionCaseRun, method_run.case_run_id)
            assert case_run is not None
            run_directory = Path(case_run.run_directory).resolve()
            artifact = json.loads(method_run.artifact_json or "{}")
            status = method_run.status
            attempt = method_run.attempt
            started_at = self._utc_iso(method_run.started_at)
            finished_at = self._utc_iso(method_run.finished_at)
            duration_ms = method_run.duration_ms
        output: dict[str, Any] = {
            "id": method_run_id,
            "status": status,
            "attempt": attempt,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "command": artifact.get("command", []),
            "message": artifact.get("message", ""),
            "stdout": "",
            "stderr": "",
        }
        for key in ("stdout", "stderr"):
            configured = artifact.get(f"{key}_path")
            if not configured:
                continue
            path = Path(str(configured)).resolve()
            if run_directory not in path.parents:
                raise AnalystBenchError("artifact_path_invalid", "审计产物路径越界。")
            if path.is_file():
                output[key] = path.read_text(encoding="utf-8", errors="replace")
        return output

    def cancel_submission(self, submission_id: str) -> EvaluationSubmission:
        with transaction(self.session_factory) as session:
            submission = session.get(EvaluationSubmission, submission_id)
            if submission is None:
                raise AnalystBenchError(
                    "evaluation_submission_not_found",
                    "找不到测评批次。",
                    status_code=404,
                )
            terminal = {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }
            if submission.status in terminal:
                session.expunge(submission)
                return submission
            case_runs = list(
                session.scalars(
                    select(EvaluationSubmissionCaseRun).where(
                        EvaluationSubmissionCaseRun.submission_id == submission_id
                    )
                )
            )
            for case_run in case_runs:
                method_runs = list(
                    session.scalars(
                        select(EvaluationSubmissionMethodRun).where(
                            EvaluationSubmissionMethodRun.case_run_id == case_run.id
                        )
                    )
                )
                for method_run in method_runs:
                    if method_run.status == "queued":
                        method_run.status = "cancelled"
                    elif method_run.status == "running":
                        method_run.status = "cancelling"
                if all(
                    method_run.status in TERMINAL_METHOD_STATES for method_run in method_runs
                ) and not any(method_run.status == "succeeded" for method_run in method_runs):
                    case_run.status = "cancelled"
                    case_run.scoring_status = "skipped"
            submission.status = "cancelled"
        for case_run in self.list_case_runs(submission_id):
            self._write_case_state(case_run.id)
        self._update_submission(submission_id)
        return self.get_submission(submission_id)

    def retry_case(self, case_run_id: str) -> EvaluationSubmissionCaseRun:
        enqueue_method_ids: list[str] = []
        enqueue_scoring = False
        with transaction(self.session_factory) as session:
            case_run = session.get(EvaluationSubmissionCaseRun, case_run_id)
            if case_run is None:
                raise AnalystBenchError(
                    "evaluation_case_run_not_found",
                    "找不到 Case 运行。",
                    status_code=404,
                )
            method_runs = list(
                session.scalars(
                    select(EvaluationSubmissionMethodRun).where(
                        EvaluationSubmissionMethodRun.case_run_id == case_run_id
                    )
                )
            )
            for method_run in method_runs:
                if method_run.status in {"failed", "timeout", "cancelled"}:
                    method_run.status = "queued"
                    method_run.error_code = None
                    method_run.started_at = None
                    method_run.finished_at = None
                    method_run.duration_ms = None
                    enqueue_method_ids.append(method_run.id)
            if enqueue_method_ids:
                case_run.status = "generating"
                case_run.scoring_status = "pending"
                case_run.error_json = "{}"
            elif case_run.scoring_status == "failed":
                case_run.status = "scoring"
                case_run.scoring_status = "queued"
                case_run.error_json = "{}"
                enqueue_scoring = True
            else:
                raise AnalystBenchError(
                    "evaluation_case_not_retryable", "该 Case 没有可重试的失败项。"
                )
            for method_run_id in enqueue_method_ids:
                self.jobs.enqueue(
                    session,
                    "evaluation_method_run",
                    {"evaluation_method_run_id": method_run_id},
                )
            if enqueue_scoring:
                self.jobs.enqueue(
                    session,
                    "evaluation_case_score",
                    {"evaluation_case_run_id": case_run_id},
                )
            submission_id = case_run.submission_id
        self._write_case_state(case_run_id)
        self._update_submission(submission_id)
        return next(item for item in self.list_case_runs(submission_id) if item.id == case_run_id)

    def execute_method_run(self, method_run_id: str) -> None:
        cancel_before_start = False
        with transaction(self.session_factory) as session:
            method_run = session.get(EvaluationSubmissionMethodRun, method_run_id)
            if method_run is None:
                raise AnalystBenchError("evaluation_method_run_not_found", "找不到方式运行。")
            if method_run.status in {"succeeded", "cancelled"}:
                return
            if method_run.status == "cancelling":
                method_run.status = "cancelled"
                cancel_before_start = True
            case_run = session.get(EvaluationSubmissionCaseRun, method_run.case_run_id)
            method = session.get(EvaluationMethod, method_run.method_id)
            assert case_run is not None and method is not None
            if cancel_before_start:
                submission_id = case_run.submission_id
                case_run_id = case_run.id
            else:
                method_run.status = "running"
                method_run.attempt += 1
                method_run.started_at = None
                method_run.finished_at = None
                method_run.duration_ms = None
                case_run.status = "generating"
                attempt = method_run.attempt
                run_directory = Path(case_run.run_directory)
                submission_id = case_run.submission_id
                method_snapshot = {
                    "id": method.id,
                    "key": method.method_key,
                    "tool_dir": method.tool_dir,
                    "command_template": method.command_template,
                    "timeout_seconds": method.timeout_seconds,
                    "max_output_bytes": method.max_output_bytes,
                }
                case_path = case_run.case_path
                submission = session.get(EvaluationSubmission, case_run.submission_id)
                assert submission is not None
                submission_manifest = json.loads(submission.manifest_json)
                case_manifest = next(
                    item for item in submission_manifest["cases"] if item["case_path"] == case_path
                )
                primary_log = str(case_manifest["primary_log"])
        if cancel_before_start:
            self._write_case_state(case_run_id)
            self._schedule_scoring(method_run_id)
            self._update_submission(submission_id)
            return

        workspace = (
            self.settings.workspace_root_path
            / "evaluation"
            / submission_id
            / method_run_id
            / f"attempt-{attempt}"
        )
        logs_workspace = workspace / "logs"
        logs_workspace.parent.mkdir(parents=True, exist_ok=True)
        (workspace / ".git").mkdir(exist_ok=True)
        shutil.copytree(run_directory / "inputs", logs_workspace)
        self._make_read_only(logs_workspace)
        primary = logs_workspace / _safe_relative_path(primary_log)
        artifact_directory = (
            run_directory / "_artifacts" / method_snapshot["key"] / f"attempt-{attempt}"
        )
        stdout = ""
        stderr = ""
        command: list[str] = []
        workspace_metadata: dict[str, object] | None = None
        monotonic_started: float | None = None
        timing_finished = False
        try:
            if self.workspace_preparer is not None:
                try:
                    workspace_metadata = self.workspace_preparer.prepare(
                        method_id=str(method_snapshot["id"]),
                        workspace=workspace,
                    )
                except AnalystBenchError as exc:
                    raise EvaluationCommandError(
                        "workspace_prepare_failed",
                        f"{exc.code}: {exc.message}",
                    ) from exc
            command = self._build_command(method_snapshot, workspace, primary, logs_workspace)
            try:
                process = subprocess.Popen(
                    command,
                    cwd=workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                    start_new_session=os.name == "posix",
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                    ),
                )
            except OSError as exc:
                raise EvaluationCommandError(
                    "process_start_failed",
                    f"命令启动失败：{exc}",
                ) from exc
            monotonic_started = time.monotonic()
            self._persist_method_started(method_run_id)
            deadline = monotonic_started + int(method_snapshot["timeout_seconds"])
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_process_tree(process)
                    stdout, stderr = process.communicate()
                    raise EvaluationCommandError(
                        "timeout", "命令执行超时。", stdout or "", stderr or ""
                    )
                try:
                    stdout, stderr = process.communicate(timeout=min(1.0, remaining))
                    break
                except subprocess.TimeoutExpired:
                    if self._method_cancel_requested(method_run_id):
                        self._terminate_process_tree(process)
                        stdout, stderr = process.communicate()
                        raise EvaluationCommandError(
                            "cancelled",
                            "命令已取消。",
                            stdout or "",
                            stderr or "",
                        ) from None
            duration_ms = self._persist_method_finished(
                method_run_id, monotonic_started
            )
            timing_finished = True
            stdout = stdout or ""
            stderr = stderr or ""
            if len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")) > int(
                method_snapshot["max_output_bytes"]
            ):
                raise EvaluationCommandError(
                    "output_limit_exceeded", "命令输出超过限制。", stdout, stderr
                )
            if process.returncode != 0:
                raise EvaluationCommandError(
                    "process_exit_nonzero",
                    f"命令退出码为 {process.returncode}。",
                    stdout,
                    stderr,
                )
            if not stdout.strip():
                raise EvaluationCommandError(
                    "final_report_missing", "命令未向 stdout 输出非空文本。", stdout, stderr
                )
            _atomic_text(run_directory / f"{method_snapshot['key']}.md", stdout)
            artifact = {
                "command": command,
                "attempt": attempt,
                "stdout_path": str(artifact_directory / "stdout.log"),
                "stderr_path": str(artifact_directory / "stderr.log"),
                "report_path": str(run_directory / f"{method_snapshot['key']}.md"),
                "report_hash": content_hash(stdout.encode("utf-8")),
                "exit_code": process.returncode,
                "duration_ms": duration_ms,
            }
            if workspace_metadata is not None:
                artifact["workspace_extension"] = workspace_metadata
            self._persist_method_success(method_run_id, artifact)
        except EvaluationCommandError as exc:
            stdout, stderr = exc.stdout, exc.stderr
            if monotonic_started is not None and not timing_finished:
                self._persist_method_finished(method_run_id, monotonic_started)
                timing_finished = True
            self._persist_method_failure(method_run_id, exc)
            raise
        finally:
            if monotonic_started is not None and not timing_finished:
                self._persist_method_finished(method_run_id, monotonic_started)
            artifact_directory.mkdir(parents=True, exist_ok=True)
            _atomic_text(artifact_directory / "stdout.log", stdout)
            _atomic_text(artifact_directory / "stderr.log", stderr)
            self._attach_artifact_paths(
                method_run_id,
                command,
                artifact_directory / "stdout.log",
                artifact_directory / "stderr.log",
            )
            self._write_case_state_by_method(method_run_id)
            self._schedule_scoring(method_run_id)
            self._remove_workspace(workspace)

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

    def _method_cancel_requested(self, method_run_id: str) -> bool:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionMethodRun, method_run_id)
            return item is not None and item.status in {"cancelling", "cancelled"}

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif shutil.which("taskkill"):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                shell=False,
            )
        else:
            process.kill()

    @staticmethod
    def _make_read_only(directory: Path) -> None:
        for item in directory.rglob("*"):
            item.chmod(0o555 if item.is_dir() else 0o444)
        directory.chmod(0o555)

    @staticmethod
    def _remove_workspace(workspace: Path) -> None:
        if not workspace.is_dir():
            return
        for item in workspace.rglob("*"):
            try:
                item.chmod(0o755 if item.is_dir() else 0o644)
            except OSError:
                pass
        shutil.rmtree(workspace, ignore_errors=True)

    @staticmethod
    def _build_command(
        method: dict[str, Any], workspace: Path, primary: Path, logs_directory: Path
    ) -> list[str]:
        values = {
            "input": str(primary.resolve()),
            "input_dir": str(logs_directory.resolve()),
            "workspace": str(workspace.resolve()),
            "tool_dir": str(Path(method["tool_dir"]).expanduser().resolve())
            if method.get("tool_dir")
            else "",
        }
        try:
            argv = shlex.split(str(method["command_template"]), posix=True)
            command = [argument.format(**values) for argument in argv]
        except (KeyError, ValueError) as exc:
            raise EvaluationCommandError("command_template_invalid", str(exc)) from exc
        executable = resolve_executable(command[0]) or command[0]
        command[0] = executable
        return command

    def _persist_method_success(self, method_run_id: str, artifact: dict[str, Any]) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionMethodRun, method_run_id)
            assert item is not None
            item.status = "succeeded"
            item.error_code = None
            item.artifact_json = canonical_json(artifact)

    def _persist_method_started(self, method_run_id: str) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionMethodRun, method_run_id)
            assert item is not None
            item.started_at = datetime.now(UTC)
            item.finished_at = None
            item.duration_ms = None

    def _persist_method_finished(
        self,
        method_run_id: str,
        monotonic_started: float,
    ) -> int:
        duration_ms = round(max(0.0, time.monotonic() - monotonic_started) * 1000)
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionMethodRun, method_run_id)
            assert item is not None
            item.finished_at = datetime.now(UTC)
            item.duration_ms = duration_ms
        return duration_ms

    def _persist_method_failure(self, method_run_id: str, error: EvaluationCommandError) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionMethodRun, method_run_id)
            assert item is not None
            if error.code == "timeout":
                item.status = "timeout"
            elif error.code == "cancelled":
                item.status = "cancelled"
            else:
                item.status = "failed"
            item.error_code = error.code
            item.artifact_json = canonical_json({"message": str(error), "attempt": item.attempt})

    def _attach_artifact_paths(
        self,
        method_run_id: str,
        command: list[str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionMethodRun, method_run_id)
            assert item is not None
            artifact = json.loads(item.artifact_json or "{}")
            artifact.update(
                {
                    "command": command,
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                }
            )
            item.artifact_json = canonical_json(artifact)

    def _schedule_scoring(self, method_run_id: str) -> None:
        with transaction(self.session_factory) as session:
            method_run = session.get(EvaluationSubmissionMethodRun, method_run_id)
            assert method_run is not None
            case_run = session.get(EvaluationSubmissionCaseRun, method_run.case_run_id)
            assert case_run is not None
            submission = session.get(EvaluationSubmission, case_run.submission_id)
            assert submission is not None
            statuses = list(
                session.scalars(
                    select(EvaluationSubmissionMethodRun.status).where(
                        EvaluationSubmissionMethodRun.case_run_id == case_run.id
                    )
                )
            )
            if statuses and all(status in TERMINAL_METHOD_STATES for status in statuses):
                if case_run.scoring_status == "pending":
                    successes = sum(status == "succeeded" for status in statuses)
                    if submission.status == "cancelled":
                        case_run.status = "cancelled"
                        case_run.scoring_status = "skipped"
                    elif successes:
                        case_run.status = "scoring"
                        case_run.scoring_status = "queued"
                        self.jobs.enqueue(
                            session,
                            "evaluation_case_score",
                            {"evaluation_case_run_id": case_run.id},
                        )
                    else:
                        case_run.status = "failed"
                        case_run.scoring_status = "skipped"
                        case_run.error_json = canonical_json(
                            {"code": "all_methods_failed", "message": "所有测评方式均失败。"}
                        )
        self._write_case_state_by_method(method_run_id)
        with transaction(self.session_factory) as session:
            method_run = session.get(EvaluationSubmissionMethodRun, method_run_id)
            assert method_run is not None
            case_run = session.get(EvaluationSubmissionCaseRun, method_run.case_run_id)
            assert case_run is not None
            submission_id = case_run.submission_id
        self._update_submission(submission_id)

    def execute_case_scoring(self, case_run_id: str) -> None:
        with transaction(self.session_factory) as session:
            case_run = session.get(EvaluationSubmissionCaseRun, case_run_id)
            if case_run is None:
                raise AnalystBenchError("evaluation_case_run_not_found", "找不到 Case 运行。")
            if case_run.scoring_status == "succeeded":
                return
            submission = session.get(EvaluationSubmission, case_run.submission_id)
            assert submission is not None
            case_run.scoring_status = "running"
            run_directory = Path(case_run.run_directory)
            case_path = case_run.case_path
            case_key = case_run.case_key
            judge_runner = json.loads(submission.manifest_json).get("judge_runner", "claude")
            all_method_rows = list(
                session.execute(
                    select(EvaluationSubmissionMethodRun, EvaluationMethod)
                    .join(
                        EvaluationMethod,
                        EvaluationMethod.id == EvaluationSubmissionMethodRun.method_id,
                    )
                    .where(
                        EvaluationSubmissionMethodRun.case_run_id == case_run.id,
                    )
                    .order_by(EvaluationSubmissionMethodRun.created_at)
                )
            )
            method_rows = [
                (method_run, method)
                for method_run, method in all_method_rows
                if method_run.status == "succeeded"
            ]
            method_generation = [
                self._method_generation_view(method_run, method)
                for method_run, method in all_method_rows
            ]
            target_lookup = self._target_snapshot_lookup(submission)
            generation: dict[str, Any] = {"methods": method_generation}
            if target_lookup:
                generation["targets"] = [
                    self._target_generation_view(entry, target_lookup.get(entry["method_id"]))
                    for entry in method_generation
                ]

        case_file = _safe_case_directory(self.settings.results_formal_path, case_path) / "case.json"
        reports: list[dict[str, Any]] = []
        for _, method in method_rows:
            report_path = run_directory / f"{method.method_key}.md"
            text = report_path.read_text(encoding="utf-8")
            reports.append(report_payload_from_text(report_path.name, text))
        try:
            case_payload = json.loads(case_file.read_text(encoding="utf-8"))
            result = evaluate_direct(
                case_payload,
                case_key,
                reports,
                self.settings,
                str(judge_runner),
                str(case_file.resolve()),
            )
            result_id = f"{case_path}/runs/{run_directory.name}"
            result["id"] = result_id
            result["submission_id"] = self._submission_id_for_case(case_run_id)
            result["generation"] = generation
            _atomic_json(run_directory / "result.json", result)
            _atomic_text(
                run_directory / "result.md",
                render_markdown(result["summary"], generation),
            )
            with transaction(self.session_factory) as session:
                stored = session.get(EvaluationSubmissionCaseRun, case_run_id)
                assert stored is not None
                statuses = list(
                    session.scalars(
                        select(EvaluationSubmissionMethodRun.status).where(
                            EvaluationSubmissionMethodRun.case_run_id == case_run_id
                        )
                    )
                )
                stored.scoring_status = "succeeded"
                stored.status = (
                    "completed"
                    if all(status == "succeeded" for status in statuses)
                    else "completed_with_errors"
                )
                stored.error_json = "{}"
        except Exception as exc:
            with transaction(self.session_factory) as session:
                stored = session.get(EvaluationSubmissionCaseRun, case_run_id)
                assert stored is not None
                stored.scoring_status = "failed"
                stored.status = "completed_with_errors"
                stored.error_json = canonical_json(_scoring_error_payload(exc))
            raise
        finally:
            self._write_case_state(case_run_id)
            self._update_submission(self._submission_id_for_case(case_run_id))

    def _submission_id_for_case(self, case_run_id: str) -> str:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionCaseRun, case_run_id)
            assert item is not None
            return item.submission_id

    def _write_case_state_by_method(self, method_run_id: str) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSubmissionMethodRun, method_run_id)
            if item is None:
                return
            case_run_id = item.case_run_id
        self._write_case_state(case_run_id)

    def _write_case_state(self, case_run_id: str) -> None:
        with transaction(self.session_factory) as session:
            case_run = session.get(EvaluationSubmissionCaseRun, case_run_id)
            if case_run is None:
                return
            submission = session.get(EvaluationSubmission, case_run.submission_id)
            assert submission is not None
            target_lookup = self._target_snapshot_lookup(submission)
            methods = list(
                session.execute(
                    select(EvaluationSubmissionMethodRun, EvaluationMethod)
                    .join(
                        EvaluationMethod,
                        EvaluationMethod.id == EvaluationSubmissionMethodRun.method_id,
                    )
                    .where(EvaluationSubmissionMethodRun.case_run_id == case_run_id)
                    .order_by(EvaluationSubmissionMethodRun.created_at)
                )
            )
            run_directory = Path(case_run.run_directory)
            payload = {
                "submission_id": submission.id,
                "case_run_id": case_run.id,
                "dataset_key": submission.dataset_key,
                "case_path": case_run.case_path,
                "case_key": case_run.case_key,
                "timestamp": submission.run_timestamp,
                "status": case_run.status,
                "scoring_status": case_run.scoring_status,
                "methods": [
                    {
                        "method_id": method.id,
                        "key": method.method_key,
                        "name": method.name,
                        "version": method.version_number,
                        "status": method_run.status,
                        "attempt": method_run.attempt,
                        "started_at": self._utc_iso(method_run.started_at),
                        "finished_at": self._utc_iso(method_run.finished_at),
                        "duration_ms": method_run.duration_ms,
                        "error_code": method_run.error_code,
                        "artifact": json.loads(method_run.artifact_json or "{}"),
                    }
                    for method_run, method in methods
                ],
                "error": json.loads(case_run.error_json or "{}"),
            }
            if target_lookup:
                payload["targets"] = [
                    self._target_generation_view(
                        {
                            "method_id": method["method_id"],
                            "key": method["key"],
                            "status": method["status"],
                            "attempt": method["attempt"],
                            "started_at": method["started_at"],
                            "finished_at": method["finished_at"],
                            "duration_ms": method["duration_ms"],
                            "error_code": method["error_code"],
                        },
                        target_lookup.get(method["method_id"]),
                    )
                    for method in payload["methods"]
                ]
        _atomic_json(run_directory / "run.json", payload)
        result_path = run_directory / "result.json"
        if not result_path.exists() or payload["scoring_status"] != "succeeded":
            _atomic_json(
                result_path,
                {
                    "id": f"{payload['case_path']}/runs/{payload['timestamp']}",
                    "mode": "direct_file",
                    "case_key": payload["case_key"],
                    "status": (
                        payload["status"]
                        if payload["status"] in {"failed", "cancelled"}
                        else "failed"
                        if payload["scoring_status"] == "failed"
                        else "running"
                    ),
                    "reports": [],
                    "comparisons": [],
                    "generation": (
                        {"methods": payload["methods"], "targets": payload["targets"]}
                        if payload.get("targets")
                        else {"methods": payload["methods"]}
                    ),
                    "summary": {
                        "case_key": payload["case_key"],
                        "engine_note": f"提交测评状态：{payload['status']}",
                        "ranking": [],
                        "reports": [
                            {
                                "candidate_name": method["key"],
                                "status": method["status"],
                                "score": "0",
                                "passed": False,
                            }
                            for method in payload["methods"]
                        ],
                        "comparisons": [],
                    },
                    "error": payload["error"],
                    "submission_id": payload["submission_id"],
                },
            )

    def _update_submission(self, submission_id: str) -> None:
        with transaction(self.session_factory) as session:
            submission = session.get(EvaluationSubmission, submission_id)
            if submission is None:
                return
            cases = list(
                session.scalars(
                    select(EvaluationSubmissionCaseRun).where(
                        EvaluationSubmissionCaseRun.submission_id == submission_id
                    )
                )
            )
            statuses = [item.status for item in cases]
            if submission.status == "cancelled":
                pass
            elif statuses and all(status == "completed" for status in statuses):
                submission.status = "completed"
            elif statuses and all(
                status in {"completed", "completed_with_errors", "failed"} for status in statuses
            ):
                submission.status = (
                    "failed"
                    if all(status == "failed" for status in statuses)
                    else "completed_with_errors"
                )
            elif any(status == "scoring" for status in statuses):
                submission.status = "scoring"
            elif any(status in {"generating", "preparing"} for status in statuses):
                submission.status = "generating"
            else:
                submission.status = "queued"
            submission.summary_json = canonical_json(
                {
                    "case_count": len(cases),
                    "completed": sum(status == "completed" for status in statuses),
                    "completed_with_errors": sum(
                        status == "completed_with_errors" for status in statuses
                    ),
                    "failed": sum(status == "failed" for status in statuses),
                }
            )

    @staticmethod
    def submission_view(item: EvaluationSubmission) -> dict[str, Any]:
        manifest = json.loads(item.manifest_json)
        return {
            "id": item.id,
            "dataset_key": item.dataset_key,
            "timestamp": item.run_timestamp,
            "status": item.status,
            "schedule_run_id": item.schedule_run_id,
            "method_ids": manifest.get("method_ids", []),
            "methods": manifest.get("methods", []),
            "target_ids": manifest.get("target_ids", []),
            "targets": manifest.get("targets", []),
            "case_count": len(manifest.get("cases", [])),
            "summary": json.loads(item.summary_json or "{}"),
            "error": json.loads(item.error_json or "{}"),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def case_run_view(self, item: EvaluationSubmissionCaseRun) -> dict[str, Any]:
        methods = self.list_method_runs(item.id)
        method_lookup: dict[str, EvaluationMethod] = {}
        target_lookup: dict[str, dict[str, Any]] = {}
        with transaction(self.session_factory) as session:
            submission = session.get(EvaluationSubmission, item.submission_id)
            assert submission is not None
            target_lookup = self._target_snapshot_lookup(submission)
            for method_run in methods:
                method = session.get(EvaluationMethod, method_run.method_id)
                if method is not None:
                    method_lookup[method.id] = method
        method_rows = [
            {
                "id": method_run.id,
                "method_id": method_run.method_id,
                "key": method_lookup[method_run.method_id].method_key,
                "name": method_lookup[method_run.method_id].name,
                "status": method_run.status,
                "attempt": method_run.attempt,
                "started_at": self._utc_iso(method_run.started_at),
                "finished_at": self._utc_iso(method_run.finished_at),
                "duration_ms": method_run.duration_ms,
                "error_code": method_run.error_code,
                "artifact": json.loads(method_run.artifact_json or "{}"),
            }
            for method_run in methods
            if method_run.method_id in method_lookup
        ]
        payload = {
            "id": item.id,
            "submission_id": item.submission_id,
            "case_path": item.case_path,
            "case_key": item.case_key,
            "run_directory": item.run_directory,
            "status": item.status,
            "scoring_status": item.scoring_status,
            "methods": method_rows,
            "error": json.loads(item.error_json or "{}"),
        }
        if target_lookup:
            payload["targets"] = [
                self._target_generation_view(
                    {
                        "method_id": row["method_id"],
                        "key": row["key"],
                        "status": row["status"],
                        "attempt": row["attempt"],
                        "started_at": row["started_at"],
                        "finished_at": row["finished_at"],
                        "duration_ms": row["duration_ms"],
                        "error_code": row["error_code"],
                    },
                    target_lookup.get(row["method_id"]),
                )
                for row in method_rows
            ]
        return payload

    @staticmethod
    def _utc_iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @classmethod
    def _method_generation_view(
        cls,
        method_run: EvaluationSubmissionMethodRun,
        method: EvaluationMethod,
    ) -> dict[str, Any]:
        return {
            "method_id": method.id,
            "key": method.method_key,
            "version": method.version_number,
            "status": method_run.status,
            "attempt": method_run.attempt,
            "started_at": cls._utc_iso(method_run.started_at),
            "finished_at": cls._utc_iso(method_run.finished_at),
            "duration_ms": method_run.duration_ms,
        }

    @staticmethod
    def _target_snapshot_lookup(submission: EvaluationSubmission) -> dict[str, dict[str, Any]]:
        manifest = json.loads(submission.manifest_json or "{}")
        return {
            str(item["materialized_method_id"]): item
            for item in manifest.get("targets", [])
            if isinstance(item, dict) and item.get("materialized_method_id")
        }

    @staticmethod
    def _target_generation_view(
        method: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if target is None:
            return dict(method)
        return {
            "target_id": target["id"],
            "target_key": target["key"],
            "target_version": target["version"],
            "display_name": target["display_name"],
            "harness": target["harness"],
            "model": target["model"],
            "model_argument": target["model_argument"],
            "method_id": method["method_id"],
            "status": method["status"],
            "attempt": method["attempt"],
            "started_at": method["started_at"],
            "finished_at": method["finished_at"],
            "duration_ms": method["duration_ms"],
            "error_code": method.get("error_code"),
        }
