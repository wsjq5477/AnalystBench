"""P19 Harness, Model and frozen Evaluation Target configuration services."""

from __future__ import annotations

import json
import re
import shlex
import string
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationHarness,
    EvaluationMethod,
    EvaluationModel,
    EvaluationTarget,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.execution.resolver import resolve_executable
from analystbench.storage.content import canonical_json, content_hash

KEY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._()-]{0,98}[A-Za-z0-9)])?$")
RESERVED_KEYS = {"result", "run", "inputs", "artifacts", "_artifacts", "logs"}
BASE_PLACEHOLDERS = {"input", "input_dir", "workspace", "tool_dir"}
ALL_PLACEHOLDERS = BASE_PLACEHOLDERS | {"model"}
SHELL_TOKENS = {"|", "||", "&&", ";", ">", ">>", "<", "2>", "2>>"}


def _key(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not KEY_RE.fullmatch(normalized) or normalized.lower() in RESERVED_KEYS:
        raise AnalystBenchError(
            "evaluation_target_invalid",
            f"{label} key 必须以字母或数字开头，以字母、数字或右括号结尾，"
            "只能包含字母、数字、点、括号、-、_，且不能使用保留名。",
        )
    return normalized


def _safe_model_argument(value: str) -> str:
    normalized = value.strip()
    if not normalized or "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        raise AnalystBenchError("evaluation_target_invalid", "模型参数不能为空或包含换行。")
    return normalized


def _template_fields(template: str) -> list[str]:
    try:
        return [
            field_name
            for _, field_name, format_spec, conversion in string.Formatter().parse(template)
            if field_name
            and not format_spec
            and not conversion
        ]
    except ValueError as exc:
        raise AnalystBenchError(
            "evaluation_target_invalid", f"命令模板无法解析：{exc}"
        ) from exc


def _validate_command(
    *,
    command_template: str,
    tool_dir: str | None,
    model_policy: str,
    timeout_seconds: int,
    max_output_bytes: int,
    concurrency_limit: int,
) -> list[str]:
    if model_policy not in {"required", "none"}:
        raise AnalystBenchError("evaluation_target_invalid", "模型策略只能为 required 或 none。")
    if not command_template.strip():
        raise AnalystBenchError("evaluation_target_invalid", "命令不能为空。")
    try:
        argv = shlex.split(command_template, posix=True)
    except ValueError as exc:
        raise AnalystBenchError(
            "evaluation_target_invalid", f"命令模板无法解析：{exc}"
        ) from exc
    if not argv or any(token in SHELL_TOKENS for token in argv):
        raise AnalystBenchError("evaluation_target_invalid", "命令不能包含 Shell 组合操作符。")
    fields = _template_fields(command_template)
    unknown = set(fields) - ALL_PLACEHOLDERS
    if unknown:
        raise AnalystBenchError(
            "evaluation_target_invalid", f"命令包含不支持的占位符：{sorted(unknown)}"
        )
    if "tool_dir" in fields and not tool_dir:
        raise AnalystBenchError("evaluation_target_invalid", "命令使用 {tool_dir} 时必须配置工具目录。")
    model_count = fields.count("model")
    if model_policy == "required" and model_count != 1:
        raise AnalystBenchError(
            "evaluation_target_invalid", "需要模型的 Harness 命令必须且只能包含一次 {model}。"
        )
    if model_policy == "none" and model_count:
        raise AnalystBenchError(
            "evaluation_target_invalid", "无模型 Harness 命令不能包含 {model}。"
        )
    if not 1 <= timeout_seconds <= 7200:
        raise AnalystBenchError("evaluation_target_invalid", "超时必须在 1 到 7200 秒之间。")
    if not 1024 <= max_output_bytes <= 100 * 1024 * 1024:
        raise AnalystBenchError("evaluation_target_invalid", "输出上限必须在 1 KiB 到 100 MiB。")
    if not 1 <= concurrency_limit <= 32:
        raise AnalystBenchError("evaluation_target_invalid", "并发限制必须在 1 到 32。")
    return argv


def _source_revision(tool_dir: str | None) -> dict[str, Any] | None:
    if not tool_dir:
        return None
    directory = Path(tool_dir)
    if not directory.is_dir():
        return None
    try:
        revision = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if revision.returncode:
            return {"kind": "directory", "path": str(directory)}
        dirty = subprocess.run(
            ["git", "-C", str(directory), "diff", "--quiet"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return {
            "kind": "git",
            "revision": revision.stdout.strip(),
            "dirty": dirty.returncode != 0,
        }
    except (OSError, subprocess.SubprocessError):
        return {"kind": "directory", "path": str(directory)}


class EvaluationHarnessService:
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
                    "evaluation_target_invalid", "工具目录不能位于结果目录或运行工作区内。"
                )
        return str(resolved)

    def _validate_skill_base_dir(self, skill_base_dir: str | None) -> str | None:
        if not skill_base_dir:
            return None
        resolved = Path(skill_base_dir).expanduser().resolve()
        for protected in (
            self.settings.results_formal_path.resolve(),
            self.settings.results_tmp_path.resolve(),
            self.settings.workspace_root_path.resolve(),
        ):
            if resolved == protected or protected in resolved.parents:
                raise AnalystBenchError(
                    "evaluation_target_invalid",
                    "Skill 本地配置目录不能位于结果目录或运行工作区内。",
                )
        return str(resolved)

    def create(
        self,
        *,
        harness_key: str,
        name: str | None,
        family: str | None,
        model_policy: str,
        command_template: str,
        tool_dir: str | None = None,
        skill_base_dir: str | None = None,
        timeout_seconds: int = 1800,
        max_output_bytes: int = 10 * 1024 * 1024,
        concurrency_limit: int = 1,
    ) -> EvaluationHarness:
        key = _key(harness_key, label="Harness")
        normalized_name = (name or key).strip()
        if not normalized_name:
            raise AnalystBenchError("evaluation_target_invalid", "Harness 名称不能为空。")
        normalized_family = family.strip() if family else None
        normalized_tool_dir = self._validate_tool_dir(tool_dir)
        normalized_skill_base_dir = self._validate_skill_base_dir(skill_base_dir)
        _validate_command(
            command_template=command_template,
            tool_dir=normalized_tool_dir,
            model_policy=model_policy,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            concurrency_limit=concurrency_limit,
        )
        with transaction(self.session_factory) as session:
            version = int(
                session.scalar(
                    select(func.max(EvaluationHarness.version_number)).where(
                        EvaluationHarness.harness_key == key
                    )
                )
                or 0
            ) + 1
            manifest = {
                "harness_key": key,
                "name": normalized_name,
                "family": normalized_family,
                "version_number": version,
                "model_policy": model_policy,
                "tool_dir": normalized_tool_dir,
                "skill_base_dir": normalized_skill_base_dir,
                "command_template": command_template,
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": max_output_bytes,
                "concurrency_limit": concurrency_limit,
            }
            item = EvaluationHarness(
                id=str(uuid4()),
                harness_key=key,
                name=normalized_name,
                family=normalized_family,
                version_number=version,
                model_policy=model_policy,
                tool_dir=normalized_tool_dir,
                skill_base_dir=normalized_skill_base_dir,
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

    def get(self, harness_id: str) -> EvaluationHarness:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationHarness, harness_id)
            if item is None:
                raise AnalystBenchError("evaluation_harness_not_found", "找不到 Harness。", status_code=404)
            session.expunge(item)
            return item

    def list(self) -> list[EvaluationHarness]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationHarness).order_by(
                        EvaluationHarness.harness_key,
                        EvaluationHarness.version_number.desc(),
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def probe(self, harness_id: str) -> EvaluationHarness:
        item = self.get(harness_id)
        tool_dir = self._validate_tool_dir(item.tool_dir)
        skill_base_dir = self._validate_skill_base_dir(item.skill_base_dir)
        argv = _validate_command(
            command_template=item.command_template,
            tool_dir=tool_dir,
            model_policy=item.model_policy,
            timeout_seconds=item.timeout_seconds,
            max_output_bytes=item.max_output_bytes,
            concurrency_limit=item.concurrency_limit,
        )
        executable = resolve_executable(argv[0])
        tool_dir_ok = tool_dir is None or Path(tool_dir).is_dir()
        skill_base_dir_ok = (
            skill_base_dir is None or Path(skill_base_dir).is_dir()
        )
        reason = (
            "executable_not_found"
            if executable is None
            else "tool_dir_not_found"
            if not tool_dir_ok
            else "skill_base_dir_not_found"
            if not skill_base_dir_ok
            else None
        )
        probe = {
            "available": bool(executable and tool_dir_ok and skill_base_dir_ok),
            "requested_executable": argv[0],
            "executable": executable,
            "tool_dir_ok": tool_dir_ok,
            "skill_base_dir_ok": skill_base_dir_ok,
            "reason": reason,
            "source_revision": _source_revision(tool_dir),
            "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        with transaction(self.session_factory) as session:
            stored = session.get(EvaluationHarness, harness_id)
            assert stored is not None
            stored.last_probe_json = canonical_json(probe)
        return self.get(harness_id)

    def freeze(self, harness_id: str) -> EvaluationHarness:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationHarness, harness_id)
            if item is None:
                raise AnalystBenchError("evaluation_harness_not_found", "找不到 Harness。", status_code=404)
            if not json.loads(item.last_probe_json or "{}").get("available"):
                raise AnalystBenchError(
                    "evaluation_harness_unavailable", "请先成功检测命令，再冻结 Harness。"
                )
            item.status = "frozen"
        return self.get(harness_id)

    def revise(self, harness_id: str, **changes: Any) -> EvaluationHarness:
        current = self.get(harness_id)
        command_template = changes.get(
            "command_template", current.command_template
        )
        if changes.get("model_policy") is not None:
            model_policy = changes["model_policy"]
        elif "command_template" in changes:
            model_policy = "required" if "{model}" in command_template else "none"
        else:
            model_policy = current.model_policy
        return self.create(
            harness_key=current.harness_key,
            name=changes.get("name", current.name),
            family=changes.get("family", current.family),
            model_policy=model_policy,
            command_template=command_template,
            tool_dir=changes.get("tool_dir", current.tool_dir),
            skill_base_dir=changes.get(
                "skill_base_dir", current.skill_base_dir
            ),
            timeout_seconds=changes.get("timeout_seconds", current.timeout_seconds),
            max_output_bytes=changes.get("max_output_bytes", current.max_output_bytes),
            concurrency_limit=changes.get("concurrency_limit", current.concurrency_limit),
        )

    def archive(self, harness_id: str) -> EvaluationHarness:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationHarness, harness_id)
            if item is None:
                raise AnalystBenchError("evaluation_harness_not_found", "找不到 Harness。", status_code=404)
            item.status = "archived"
        return self.get(harness_id)

    @staticmethod
    def view(item: EvaluationHarness) -> dict[str, Any]:
        return {
            "id": item.id,
            "key": item.harness_key,
            "name": item.name,
            "family": item.family,
            "version": item.version_number,
            "model_policy": item.model_policy,
            "tool_dir": item.tool_dir,
            "skill_base_dir": item.skill_base_dir,
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


class EvaluationModelService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, *, model_key: str, name: str | None, argument: str) -> EvaluationModel:
        key = _key(model_key, label="模型")
        normalized_name = (name or key).strip()
        if not normalized_name:
            raise AnalystBenchError("evaluation_target_invalid", "模型名称不能为空。")
        normalized_argument = _safe_model_argument(argument)
        with transaction(self.session_factory) as session:
            version = int(
                session.scalar(
                    select(func.max(EvaluationModel.version_number)).where(
                        EvaluationModel.model_key == key
                    )
                )
                or 0
            ) + 1
            manifest = {
                "model_key": key,
                "name": normalized_name,
                "version_number": version,
                "argument": normalized_argument,
            }
            item = EvaluationModel(
                id=str(uuid4()),
                model_key=key,
                name=normalized_name,
                version_number=version,
                argument=normalized_argument,
                status="frozen",
                content_hash=content_hash(canonical_json(manifest).encode("utf-8")),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
            return item

    def get(self, model_id: str) -> EvaluationModel:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationModel, model_id)
            if item is None:
                raise AnalystBenchError("evaluation_model_not_found", "找不到模型。", status_code=404)
            session.expunge(item)
            return item

    def list(self) -> list[EvaluationModel]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(EvaluationModel).order_by(
                        EvaluationModel.model_key, EvaluationModel.version_number.desc()
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def revise(self, model_id: str, **changes: Any) -> EvaluationModel:
        current = self.get(model_id)
        return self.create(
            model_key=current.model_key,
            name=changes.get("name", current.name),
            argument=changes.get("argument", current.argument),
        )

    def archive(self, model_id: str) -> EvaluationModel:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationModel, model_id)
            if item is None:
                raise AnalystBenchError("evaluation_model_not_found", "找不到模型。", status_code=404)
            item.status = "archived"
        return self.get(model_id)

    @staticmethod
    def view(item: EvaluationModel) -> dict[str, Any]:
        return {
            "id": item.id,
            "key": item.model_key,
            "name": item.name,
            "version": item.version_number,
            "argument": item.argument,
            "status": item.status,
            "content_hash": item.content_hash,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }


class EvaluationTargetService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.harnesses = EvaluationHarnessService(session_factory, settings)
        self.models = EvaluationModelService(session_factory)

    def _resolve(self, target_id: str) -> tuple[EvaluationTarget, EvaluationHarness, EvaluationModel | None]:
        with transaction(self.session_factory) as session:
            target = session.get(EvaluationTarget, target_id)
            if target is None:
                raise AnalystBenchError("evaluation_target_not_found", "找不到运行组合。", status_code=404)
            harness = session.get(EvaluationHarness, target.harness_id)
            assert harness is not None
            model = session.get(EvaluationModel, target.model_id) if target.model_id else None
            session.expunge(target)
            session.expunge(harness)
            if model is not None:
                session.expunge(model)
            return target, harness, model

    @staticmethod
    def _target_key(harness: EvaluationHarness, model: EvaluationModel | None) -> str:
        return f"{harness.harness_key}@{model.model_key}" if model else harness.harness_key

    def create(
        self,
        *,
        harness_id: str,
        model_id: str | None = None,
        model_argument: str | None = None,
        concurrency_limit: int | None = None,
    ) -> EvaluationTarget:
        harness = self.harnesses.get(harness_id)
        model = self.models.get(model_id) if model_id else None
        if harness.model_policy == "required" and model is None:
            raise AnalystBenchError("evaluation_target_invalid", "该 Harness 必须选择一个模型。")
        if harness.model_policy == "none" and model is not None:
            raise AnalystBenchError("evaluation_target_invalid", "该 Harness 不接受模型。")
        if harness.model_policy == "none" and model_argument:
            raise AnalystBenchError("evaluation_target_invalid", "无模型 Harness 不能设置模型参数。")
        argument = _safe_model_argument(model_argument or (model.argument if model else "")) if model else None
        if concurrency_limit is not None and not 1 <= concurrency_limit <= 32:
            raise AnalystBenchError("evaluation_target_invalid", "组合并发限制必须在 1 到 32。")
        target_key = self._target_key(harness, model)
        with transaction(self.session_factory) as session:
            model_match = (
                EvaluationTarget.model_id == model.id
                if model is not None
                else EvaluationTarget.model_id.is_(None)
            )
            existing = session.scalar(
                select(EvaluationTarget.id).where(
                    EvaluationTarget.harness_id == harness.id,
                    model_match,
                    EvaluationTarget.model_argument == argument,
                    EvaluationTarget.concurrency_limit == concurrency_limit,
                    EvaluationTarget.status.in_(("draft", "frozen")),
                )
            )
            if existing is not None:
                raise AnalystBenchError(
                    "evaluation_target_exists", "该 Harness 和模型组合已有未归档版本。", status_code=409
                )
            version = int(
                session.scalar(
                    select(func.max(EvaluationTarget.version_number)).where(
                        EvaluationTarget.target_key == target_key
                    )
                )
                or 0
            ) + 1
            manifest = {
                "target_key": target_key,
                "version_number": version,
                "harness_id": harness.id,
                "harness_hash": harness.content_hash,
                "model_id": model.id if model else None,
                "model_hash": model.content_hash if model else None,
                "model_argument": argument,
                "concurrency_limit": concurrency_limit,
            }
            item = EvaluationTarget(
                id=str(uuid4()),
                target_key=target_key,
                version_number=version,
                harness_id=harness.id,
                model_id=model.id if model else None,
                model_argument=argument,
                concurrency_limit=concurrency_limit,
                content_hash=content_hash(canonical_json(manifest).encode("utf-8")),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
            return item

    def resolve_selections(
        self,
        selections: list[dict[str, str | None]],
    ) -> tuple[list[EvaluationTarget], list[dict[str, Any]]]:
        """Resolve user-facing Harness x Model selections to frozen Targets."""
        if not selections:
            raise AnalystBenchError("evaluation_targets_missing", "至少选择一个 Harness。")
        target_ids: list[str] = []
        seen: set[tuple[str, str | None]] = set()
        for selection in selections:
            harness_id = str(selection.get("harness_id") or "")
            model_id = (
                str(selection["model_id"]) if selection.get("model_id") else None
            )
            identity = (harness_id, model_id)
            if identity in seen:
                continue
            seen.add(identity)
            harness = self.harnesses.get(harness_id)
            model = self.models.get(model_id) if model_id else None
            if harness.status != "frozen":
                raise AnalystBenchError(
                    "evaluation_harness_not_frozen",
                    f"Harness {harness.harness_key} 尚未冻结。",
                )
            if harness.model_policy == "required" and model is None:
                raise AnalystBenchError(
                    "evaluation_target_invalid",
                    f"Harness {harness.harness_key} 必须选择模型。",
                )
            if harness.model_policy == "none" and model is not None:
                raise AnalystBenchError(
                    "evaluation_target_invalid",
                    f"Harness {harness.harness_key} 是无模型基线。",
                )
            if model is not None and model.status != "frozen":
                raise AnalystBenchError(
                    "evaluation_model_not_frozen",
                    f"模型 {model.model_key} 尚未冻结。",
                )
            model_match = (
                EvaluationTarget.model_id == model.id
                if model is not None
                else EvaluationTarget.model_id.is_(None)
            )
            argument = model.argument if model is not None else None
            with transaction(self.session_factory) as session:
                target_id = session.scalar(
                    select(EvaluationTarget.id)
                    .where(
                        EvaluationTarget.harness_id == harness.id,
                        model_match,
                        EvaluationTarget.model_argument == argument,
                        EvaluationTarget.concurrency_limit.is_(None),
                        EvaluationTarget.status == "frozen",
                    )
                    .order_by(EvaluationTarget.version_number.desc())
                    .limit(1)
                )
                draft_id = (
                    None
                    if target_id
                    else session.scalar(
                        select(EvaluationTarget.id)
                        .where(
                            EvaluationTarget.harness_id == harness.id,
                            model_match,
                            EvaluationTarget.model_argument == argument,
                            EvaluationTarget.concurrency_limit.is_(None),
                            EvaluationTarget.status == "draft",
                        )
                        .order_by(EvaluationTarget.version_number.desc())
                        .limit(1)
                    )
                )
            if target_id is None:
                if draft_id is None:
                    draft_id = self.create(
                        harness_id=harness.id,
                        model_id=model.id if model else None,
                    ).id
                self.probe(draft_id)
                target_id = self.freeze(draft_id).id
            target_ids.append(target_id)
        return self.snapshots(target_ids)

    def get(self, target_id: str) -> EvaluationTarget:
        return self._resolve(target_id)[0]

    def list(self) -> list[tuple[EvaluationTarget, EvaluationHarness, EvaluationModel | None]]:
        with transaction(self.session_factory) as session:
            rows = list(
                session.execute(
                    select(EvaluationTarget, EvaluationHarness, EvaluationModel)
                    .join(EvaluationHarness, EvaluationHarness.id == EvaluationTarget.harness_id)
                    .outerjoin(EvaluationModel, EvaluationModel.id == EvaluationTarget.model_id)
                    .order_by(EvaluationHarness.harness_key, EvaluationTarget.target_key, EvaluationTarget.version_number.desc())
                )
            )
            for target, harness, model in rows:
                session.expunge(target)
                session.expunge(harness)
                if model is not None:
                    session.expunge(model)
            return rows

    def probe(self, target_id: str) -> EvaluationTarget:
        target, harness, model = self._resolve(target_id)
        self.harnesses.probe(harness.id)
        harness = self.harnesses.get(harness.id)
        compatible = (
            (harness.model_policy == "required" and model is not None and bool(target.model_argument))
            or (harness.model_policy == "none" and model is None)
        )
        harness_probe = json.loads(harness.last_probe_json or "{}")
        probe = {
            "available": bool(harness_probe.get("available") and compatible),
            "harness_probe": harness_probe,
            "model_argument": target.model_argument,
            "reason": (
                harness_probe.get("reason")
                if not harness_probe.get("available")
                else "model_incompatible"
                if not compatible
                else None
            ),
            "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        with transaction(self.session_factory) as session:
            stored = session.get(EvaluationTarget, target_id)
            assert stored is not None
            stored.last_probe_json = canonical_json(probe)
        return self.get(target_id)

    def freeze(self, target_id: str) -> EvaluationTarget:
        target, harness, model = self._resolve(target_id)
        if harness.status != "frozen" or (model is not None and model.status != "frozen"):
            raise AnalystBenchError(
                "evaluation_target_unavailable", "Harness 和模型必须均为冻结状态。"
            )
        probe = json.loads(target.last_probe_json or "{}")
        if not probe.get("available"):
            raise AnalystBenchError("evaluation_target_unavailable", "请先成功检测运行组合。")
        with transaction(self.session_factory) as session:
            target_statement = select(EvaluationTarget).where(
                EvaluationTarget.id == target_id
            )
            if session.get_bind().dialect.name == "sqlite":
                # Target materialization is a short read-modify-write operation.
                # Serialize it on SQLite so concurrent submissions cannot both
                # insert the same Method key/version.
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                target_statement = target_statement.with_for_update()
            stored = session.scalar(target_statement)
            assert stored is not None
            command_template = harness.command_template
            if harness.model_policy == "required":
                assert stored.model_argument
                command_template = command_template.replace("{model}", stored.model_argument)
            method_manifest = {
                "target_id": stored.id,
                "target_key": stored.target_key,
                "target_version": stored.version_number,
                "harness_hash": harness.content_hash,
                "model_hash": model.content_hash if model else None,
                "tool_dir": harness.tool_dir,
                "command_template": command_template,
                "timeout_seconds": harness.timeout_seconds,
                "max_output_bytes": harness.max_output_bytes,
                "concurrency_limit": stored.concurrency_limit or 32,
            }
            method_hash = content_hash(
                canonical_json(method_manifest).encode("utf-8")
            )
            method = (
                session.get(EvaluationMethod, stored.materialized_method_id)
                if stored.materialized_method_id
                else None
            )
            if method is None:
                method = session.scalar(
                    select(EvaluationMethod).where(
                        EvaluationMethod.method_key == stored.target_key,
                        EvaluationMethod.version_number == stored.version_number,
                    )
                )
            if method is None:
                method = EvaluationMethod(
                    id=str(uuid4()),
                    method_key=stored.target_key,
                    version_number=stored.version_number,
                )
                session.add(method)
            if method.content_hash != method_hash:
                method.name = self.display_name(harness, model)
                method.tool_dir = harness.tool_dir
                method.command_template = command_template
                method.timeout_seconds = harness.timeout_seconds
                method.max_output_bytes = harness.max_output_bytes
                method.concurrency_limit = stored.concurrency_limit or 32
                method.content_hash = method_hash
            method.status = "frozen"
            method.last_probe_json = stored.last_probe_json
            session.flush()
            stored.materialized_method_id = method.id
            stored.status = "frozen"
            session.flush()
        return self.get(target_id)

    def archive(self, target_id: str) -> EvaluationTarget:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationTarget, target_id)
            if item is None:
                raise AnalystBenchError("evaluation_target_not_found", "找不到运行组合。", status_code=404)
            item.status = "archived"
        return self.get(target_id)

    @staticmethod
    def display_name(harness: EvaluationHarness, model: EvaluationModel | None) -> str:
        return f"{harness.name} · {model.name}" if model else harness.name

    @classmethod
    def view(
        cls,
        target: EvaluationTarget,
        harness: EvaluationHarness,
        model: EvaluationModel | None,
    ) -> dict[str, Any]:
        return {
            "id": target.id,
            "key": target.target_key,
            "version": target.version_number,
            "display_name": cls.display_name(harness, model),
            "harness": EvaluationHarnessService.view(harness),
            "model": EvaluationModelService.view(model) if model else None,
            "model_argument": target.model_argument,
            "concurrency_limit": target.concurrency_limit,
            "status": target.status,
            "content_hash": target.content_hash,
            "probe": json.loads(target.last_probe_json or "{}"),
            "materialized_method_id": target.materialized_method_id,
            "created_at": target.created_at,
            "updated_at": target.updated_at,
        }

    def target_view(self, target_id: str) -> dict[str, Any]:
        return self.view(*self._resolve(target_id))

    def list_views(self) -> list[dict[str, Any]]:
        return [self.view(*row) for row in self.list()]

    def snapshots(self, target_ids: list[str]) -> tuple[list[EvaluationTarget], list[dict[str, Any]]]:
        if not target_ids:
            raise AnalystBenchError("evaluation_targets_missing", "至少选择一个运行组合。")
        selected: list[EvaluationTarget] = []
        snapshots: list[dict[str, Any]] = []
        keys: set[str] = set()
        for target_id in dict.fromkeys(target_ids):
            target, harness, model = self._resolve(target_id)
            if target.status != "frozen" or not target.materialized_method_id:
                raise AnalystBenchError(
                    "evaluation_target_not_frozen", f"运行组合 {target.target_key} 尚未冻结。"
                )
            if target.target_key in keys:
                raise AnalystBenchError(
                    "evaluation_target_duplicate", "同一次提交不能选择同 Key 的多个版本。"
                )
            keys.add(target.target_key)
            selected.append(target)
            snapshots.append(
                {
                    "id": target.id,
                    "key": target.target_key,
                    "version": target.version_number,
                    "display_name": self.display_name(harness, model),
                    "model_argument": target.model_argument,
                    "concurrency_limit": target.concurrency_limit,
                    "content_hash": target.content_hash,
                    "materialized_method_id": target.materialized_method_id,
                    "harness": {
                        "id": harness.id,
                        "key": harness.harness_key,
                        "name": harness.name,
                        "version": harness.version_number,
                        "content_hash": harness.content_hash,
                        "model_policy": harness.model_policy,
                        "source_revision": json.loads(harness.last_probe_json or "{}").get(
                            "source_revision"
                        ),
                    },
                    "model": (
                        {
                            "id": model.id,
                            "key": model.model_key,
                            "name": model.name,
                            "version": model.version_number,
                            "argument": model.argument,
                            "content_hash": model.content_hash,
                        }
                        if model
                        else None
                    ),
                }
            )
        return selected, snapshots
