"""Structured, budgeted Skill mutation application."""

from __future__ import annotations

import fnmatch
import json
import tempfile
from pathlib import Path
from typing import Any

from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.package import safe_relative_path
from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.storage.content import canonical_json, content_hash


class StructuredPatchApplier:
    """Apply allowlisted text operations without accepting shell patches."""

    def __init__(self, registry: SkillRegistryService) -> None:
        self.registry = registry

    @staticmethod
    def _editable(path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def apply(
        self,
        *,
        parent_version_id: str,
        structured_patch: dict[str, Any],
        created_by: str | None = None,
    ) -> tuple[object, str]:
        parent = self.registry.get_version(parent_version_id)
        skill = self.registry.get(parent.skill_id)
        editable_paths = json.loads(skill.editable_paths_json or "[]")
        if not isinstance(editable_paths, list) or not editable_paths:
            raise AnalystBenchError(
                "skill_patch_policy_invalid", "Skill 没有可编辑路径配置。"
            )
        operations = structured_patch.get("operations")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 50:
            raise AnalystBenchError(
                "skill_patch_invalid", "结构化 Patch 必须包含 1 到 50 个操作。"
            )
        patch_hash = content_hash(canonical_json(structured_patch).encode("utf-8"))
        with tempfile.TemporaryDirectory(prefix="analystbench-skill-patch-") as temporary:
            root = Path(temporary) / "skill"
            self.registry.materialize_version(parent.id, root)
            for item in root.rglob("*"):
                item.chmod(0o755 if item.is_dir() else 0o644)
            root.chmod(0o755)
            for operation in operations:
                if not isinstance(operation, dict):
                    raise AnalystBenchError(
                        "skill_patch_invalid", "Patch 操作必须是对象。"
                    )
                relative = safe_relative_path(str(operation.get("path", ""))).as_posix()
                if not self._editable(relative, [str(value) for value in editable_paths]):
                    raise AnalystBenchError(
                        "skill_patch_path_forbidden",
                        f"Patch 不允许编辑：{relative}",
                    )
                target = root.joinpath(*safe_relative_path(relative).parts)
                kind = str(operation.get("op", ""))
                current = (
                    target.read_text(encoding="utf-8") if target.is_file() else ""
                )
                if kind == "replace":
                    old = str(operation.get("old", ""))
                    new = str(operation.get("new", ""))
                    if not old or current.count(old) != 1:
                        raise AnalystBenchError(
                            "skill_patch_anchor_invalid",
                            f"replace 锚点必须唯一：{relative}",
                        )
                    self._write_text(target, current.replace(old, new, 1))
                elif kind == "insert_after":
                    anchor = str(operation.get("anchor", ""))
                    content = str(operation.get("content", ""))
                    if not anchor or current.count(anchor) != 1:
                        raise AnalystBenchError(
                            "skill_patch_anchor_invalid",
                            f"insert_after 锚点必须唯一：{relative}",
                        )
                    self._write_text(
                        target, current.replace(anchor, f"{anchor}{content}", 1)
                    )
                elif kind == "append":
                    self._write_text(target, current + str(operation.get("content", "")))
                elif kind == "create":
                    if target.exists():
                        raise AnalystBenchError(
                            "skill_patch_target_exists",
                            f"create 目标已存在：{relative}",
                        )
                    self._write_text(target, str(operation.get("content", "")))
                elif kind == "delete":
                    if not target.is_file():
                        raise AnalystBenchError(
                            "skill_patch_target_missing",
                            f"delete 目标不存在：{relative}",
                        )
                    target.unlink()
                else:
                    raise AnalystBenchError(
                        "skill_patch_operation_invalid", f"不支持的 Patch 操作：{kind}"
                    )
            total_characters = sum(
                len(path.read_text(encoding="utf-8"))
                for path in root.rglob("*")
                if path.is_file()
            )
            if total_characters / 4 > self.registry.settings.skill_optimization_max_skill_tokens:
                raise AnalystBenchError(
                    "skill_token_budget_exceeded", "候选 Skill 超过近似 Token 上限。"
                )
            version = self.registry.import_version(
                skill.id,
                source_path=str(root),
                parent_version_id=parent.id,
                source_type="optimizer_patch",
                created_by=created_by,
            )
            return version, patch_hash
