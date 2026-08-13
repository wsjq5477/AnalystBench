"""Structured, budgeted Skill mutation application."""

from __future__ import annotations

import fnmatch
import json
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.package import safe_relative_path
from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.storage.content import canonical_json, content_hash

SUPPORTED_OPERATIONS = frozenset({"append", "delete", "insert_after", "replace"})
DEFAULT_PATCH_POLICY: dict[str, object] = {
    "allowed_operations": ["append", "insert_after", "replace", "delete"],
    "edit_budget_schedule": [4, 4, 3, 2, 1],
    "max_changed_files": 2,
    "max_added_tokens": 600,
    "max_deleted_tokens": 300,
    "max_single_file_change_ratio": 0.25,
}


@dataclass(frozen=True, slots=True)
class PatchFileStats:
    """Deterministic, approximate token statistics for one changed file."""

    added_tokens: int
    deleted_tokens: int
    before_tokens: int
    after_tokens: int
    change_ratio: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "added_tokens": self.added_tokens,
            "deleted_tokens": self.deleted_tokens,
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "change_ratio": self.change_ratio,
        }


@dataclass(frozen=True, slots=True)
class PatchApplicationStats:
    """Auditable facts calculated from operations and the resulting package diff."""

    changed_files: tuple[str, ...]
    operation_count: int
    operation_types: dict[str, int]
    added_tokens: int
    deleted_tokens: int
    per_file: dict[str, PatchFileStats]

    def as_dict(self) -> dict[str, object]:
        return {
            "changed_files": list(self.changed_files),
            "operation_count": self.operation_count,
            "operation_types": dict(self.operation_types),
            "added_tokens": self.added_tokens,
            "deleted_tokens": self.deleted_tokens,
            "per_file": {path: stats.as_dict() for path, stats in self.per_file.items()},
        }


@dataclass(frozen=True, slots=True)
class PatchApplicationResult:
    """Patch result with legacy two-value unpacking compatibility."""

    version: object
    patch_hash: str
    stats: PatchApplicationStats
    validation: dict[str, object] | None = None

    def __iter__(self) -> Iterator[object]:
        # Existing callers use ``version, patch_hash = apply(...)``. Keep that
        # contract while exposing deterministic statistics to new callers.
        yield self.version
        yield self.patch_hash


@dataclass(frozen=True, slots=True)
class _ResolvedPatchPolicy:
    allowed_operations: frozenset[str]
    max_operations: int
    max_changed_files: int
    max_added_tokens: int
    max_deleted_tokens: int
    max_single_file_change_ratio: float
    epoch_number: int


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

    @staticmethod
    def _policy_error(field: str, message: str) -> AnalystBenchError:
        return AnalystBenchError(
            "skill_patch_policy_invalid",
            message,
            [{"field": field}],
        )

    @classmethod
    def _resolve_policy(
        cls,
        policy: Mapping[str, Any] | None,
        epoch_number: int | None,
    ) -> _ResolvedPatchPolicy:
        if policy is not None and not isinstance(policy, Mapping):
            raise cls._policy_error("policy", "Patch policy 必须是对象。")

        supplied = policy or {}
        nested = supplied.get("edit_budget", {})
        if nested is not None and not isinstance(nested, Mapping):
            raise cls._policy_error("edit_budget", "Patch policy.edit_budget 必须是对象。")

        def value(key: str) -> object:
            if key in supplied:
                return supplied[key]
            if isinstance(nested, Mapping) and key in nested:
                return nested[key]
            return DEFAULT_PATCH_POLICY[key]

        allowed_raw = value("allowed_operations")
        if not isinstance(allowed_raw, (list, tuple)) or not allowed_raw:
            raise cls._policy_error("allowed_operations", "allowed_operations 必须是非空数组。")
        if any(not isinstance(item, str) for item in allowed_raw):
            raise cls._policy_error("allowed_operations", "allowed_operations 只能包含字符串。")
        allowed = frozenset(allowed_raw)
        unsupported = sorted(allowed - SUPPORTED_OPERATIONS)
        if unsupported:
            raise AnalystBenchError(
                "skill_patch_policy_invalid",
                "allowed_operations 包含不支持的操作。",
                [
                    {
                        "field": "allowed_operations",
                        "unsupported_operations": unsupported,
                    }
                ],
            )

        schedule_raw = value("edit_budget_schedule")
        if not isinstance(schedule_raw, (list, tuple)) or not schedule_raw:
            raise cls._policy_error("edit_budget_schedule", "edit_budget_schedule 必须是非空数组。")
        if any(type(item) is not int or item < 1 or item > 50 for item in schedule_raw):
            raise cls._policy_error(
                "edit_budget_schedule",
                "edit_budget_schedule 只能包含 1 到 50 的整数。",
            )
        epoch = 1 if epoch_number is None else epoch_number
        if type(epoch) is not int or epoch < 1:
            raise cls._policy_error("epoch_number", "epoch_number 必须是正整数。")
        max_operations = int(schedule_raw[min(epoch - 1, len(schedule_raw) - 1)])

        def nonnegative_integer(key: str, *, minimum: int = 0) -> int:
            raw = value(key)
            if type(raw) is not int or raw < minimum:
                qualifier = "正整数" if minimum == 1 else "非负整数"
                raise cls._policy_error(key, f"{key} 必须是{qualifier}。")
            return raw

        ratio_raw = value("max_single_file_change_ratio")
        if (
            isinstance(ratio_raw, bool)
            or not isinstance(ratio_raw, (int, float))
            or not 0 <= float(ratio_raw) <= 1
        ):
            raise cls._policy_error(
                "max_single_file_change_ratio",
                "max_single_file_change_ratio 必须介于 0 和 1 之间。",
            )

        return _ResolvedPatchPolicy(
            allowed_operations=allowed,
            max_operations=max_operations,
            max_changed_files=nonnegative_integer("max_changed_files", minimum=1),
            max_added_tokens=nonnegative_integer("max_added_tokens"),
            max_deleted_tokens=nonnegative_integer("max_deleted_tokens"),
            max_single_file_change_ratio=float(ratio_raw),
            epoch_number=epoch,
        )

    @staticmethod
    def _budget_error(
        rule: str,
        *,
        actual: int | float,
        limit: int | float,
        epoch_number: int,
        path: str | None = None,
    ) -> AnalystBenchError:
        detail: dict[str, Any] = {
            "rule": rule,
            "actual": actual,
            "limit": limit,
            "epoch_number": epoch_number,
        }
        if path is not None:
            detail["path"] = path
        return AnalystBenchError(
            "edit_budget_exceeded",
            f"Patch 超过编辑预算：{rule}。",
            [detail],
        )

    @staticmethod
    def _approx_tokens(character_count: int) -> int:
        return (character_count + 3) // 4

    @classmethod
    def _file_stats(cls, before: str, after: str) -> PatchFileStats:
        added_characters = 0
        deleted_characters = 0
        matcher = SequenceMatcher(a=before, b=after, autojunk=True)
        for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
            if tag == "equal":
                continue
            deleted_characters += before_end - before_start
            added_characters += after_end - after_start
        added_tokens = cls._approx_tokens(added_characters)
        deleted_tokens = cls._approx_tokens(deleted_characters)
        before_tokens = cls._approx_tokens(len(before))
        after_tokens = cls._approx_tokens(len(after))
        # Use characters for the normalized edit ratio so short files are not
        # disproportionately penalized by approximate-token rounding. Token
        # budgets remain conservative integer approximations below.
        # Replacements contribute both removed and added text, so normalize the
        # symmetric edit size by twice the larger file. A complete same-size
        # rewrite remains 1.0 without making a short targeted replacement look
        # larger merely because its replacement text is longer.
        denominator = max(2 * max(len(before), len(after)), 1)
        return PatchFileStats(
            added_tokens=added_tokens,
            deleted_tokens=deleted_tokens,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            change_ratio=(added_characters + deleted_characters) / denominator,
        )

    @classmethod
    def _calculate_stats(
        cls,
        *,
        root: Path,
        originals: dict[str, str | None],
        operation_types: Counter[str],
        operation_count: int,
    ) -> PatchApplicationStats:
        per_file: dict[str, PatchFileStats] = {}
        for relative in sorted(originals):
            target = root.joinpath(*safe_relative_path(relative).parts)
            after = target.read_text(encoding="utf-8") if target.is_file() else None
            before = originals[relative]
            if before == after:
                continue
            per_file[relative] = cls._file_stats(before or "", after or "")
        return PatchApplicationStats(
            changed_files=tuple(per_file),
            operation_count=operation_count,
            operation_types=dict(sorted(operation_types.items())),
            added_tokens=sum(item.added_tokens for item in per_file.values()),
            deleted_tokens=sum(item.deleted_tokens for item in per_file.values()),
            per_file=per_file,
        )

    @classmethod
    def _enforce_stats(
        cls,
        stats: PatchApplicationStats,
        policy: _ResolvedPatchPolicy,
    ) -> None:
        checks = (
            ("max_changed_files", len(stats.changed_files), policy.max_changed_files),
            ("max_added_tokens", stats.added_tokens, policy.max_added_tokens),
            ("max_deleted_tokens", stats.deleted_tokens, policy.max_deleted_tokens),
        )
        for rule, actual, limit in checks:
            if actual > limit:
                raise cls._budget_error(
                    rule,
                    actual=actual,
                    limit=limit,
                    epoch_number=policy.epoch_number,
                )
        for path, item in stats.per_file.items():
            if item.change_ratio > policy.max_single_file_change_ratio:
                raise cls._budget_error(
                    "max_single_file_change_ratio",
                    actual=item.change_ratio,
                    limit=policy.max_single_file_change_ratio,
                    epoch_number=policy.epoch_number,
                    path=path,
                )

    def apply(
        self,
        *,
        parent_version_id: str,
        structured_patch: dict[str, Any],
        created_by: str | None = None,
        policy: Mapping[str, Any] | None = None,
        epoch_number: int | None = None,
        candidate_validator: Callable[[Path], dict[str, object]] | None = None,
    ) -> PatchApplicationResult:
        resolved_policy = self._resolve_policy(policy, epoch_number)
        parent = self.registry.get_version(parent_version_id)
        skill = self.registry.get(parent.skill_id)
        editable_paths = json.loads(skill.editable_paths_json or "[]")
        if not isinstance(editable_paths, list) or not editable_paths:
            raise AnalystBenchError("skill_patch_policy_invalid", "Skill 没有可编辑路径配置。")
        operations = structured_patch.get("operations")
        if not isinstance(operations, list) or not operations:
            raise AnalystBenchError("skill_patch_invalid", "结构化 Patch 必须包含至少 1 个操作。")
        if len(operations) > resolved_policy.max_operations:
            raise self._budget_error(
                "max_operations",
                actual=len(operations),
                limit=resolved_policy.max_operations,
                epoch_number=resolved_policy.epoch_number,
            )
        patch_hash = content_hash(canonical_json(structured_patch).encode("utf-8"))
        with tempfile.TemporaryDirectory(prefix="analystbench-skill-patch-") as temporary:
            root = Path(temporary) / "skill"
            self.registry.materialize_version(parent.id, root)
            for item in root.rglob("*"):
                item.chmod(0o755 if item.is_dir() else 0o644)
            root.chmod(0o755)
            originals: dict[str, str | None] = {}
            operation_types: Counter[str] = Counter()
            for operation_index, operation in enumerate(operations):
                if not isinstance(operation, dict):
                    raise AnalystBenchError(
                        "skill_patch_invalid",
                        "Patch 操作必须是对象。",
                        [{"operation_index": operation_index}],
                    )
                kind = str(operation.get("op", ""))
                if kind not in SUPPORTED_OPERATIONS:
                    raise AnalystBenchError(
                        "skill_patch_operation_invalid",
                        f"不支持的 Patch 操作：{kind}",
                        [{"operation_index": operation_index, "operation": kind}],
                    )
                if kind not in resolved_policy.allowed_operations:
                    raise AnalystBenchError(
                        "skill_patch_operation_forbidden",
                        f"Patch policy 不允许操作：{kind}",
                        [
                            {
                                "operation_index": operation_index,
                                "operation": kind,
                                "allowed_operations": sorted(resolved_policy.allowed_operations),
                            }
                        ],
                    )
                relative = safe_relative_path(str(operation.get("path", ""))).as_posix()
                if not self._editable(relative, [str(value) for value in editable_paths]):
                    raise AnalystBenchError(
                        "skill_patch_path_forbidden",
                        f"Patch 不允许编辑：{relative}",
                        [{"operation_index": operation_index, "path": relative}],
                    )
                target = root.joinpath(*safe_relative_path(relative).parts)
                if relative not in originals:
                    originals[relative] = (
                        target.read_text(encoding="utf-8") if target.is_file() else None
                    )
                current = target.read_text(encoding="utf-8") if target.is_file() else ""
                operation_types[kind] += 1
                if kind == "replace":
                    if not target.is_file():
                        raise AnalystBenchError(
                            "skill_patch_target_missing",
                            f"replace 目标不存在：{relative}",
                            [{"operation_index": operation_index, "path": relative}],
                        )
                    old = str(operation.get("old", ""))
                    new = str(operation.get("new", ""))
                    if not old or current.count(old) != 1:
                        raise AnalystBenchError(
                            "skill_patch_anchor_invalid",
                            f"replace 锚点必须唯一：{relative}",
                            [{"operation_index": operation_index, "path": relative}],
                        )
                    self._write_text(target, current.replace(old, new, 1))
                elif kind == "insert_after":
                    if not target.is_file():
                        raise AnalystBenchError(
                            "skill_patch_target_missing",
                            f"insert_after 目标不存在：{relative}",
                            [{"operation_index": operation_index, "path": relative}],
                        )
                    anchor = str(operation.get("anchor", ""))
                    content = str(operation.get("content", ""))
                    if not anchor or current.count(anchor) != 1:
                        raise AnalystBenchError(
                            "skill_patch_anchor_invalid",
                            f"insert_after 锚点必须唯一：{relative}",
                            [{"operation_index": operation_index, "path": relative}],
                        )
                    self._write_text(target, current.replace(anchor, f"{anchor}{content}", 1))
                elif kind == "append":
                    if not target.is_file():
                        raise AnalystBenchError(
                            "skill_patch_target_missing",
                            f"append 目标不存在：{relative}",
                            [{"operation_index": operation_index, "path": relative}],
                        )
                    self._write_text(target, current + str(operation.get("content", "")))
                else:
                    if not target.is_file():
                        raise AnalystBenchError(
                            "skill_patch_target_missing",
                            f"delete 目标不存在：{relative}",
                            [{"operation_index": operation_index, "path": relative}],
                        )
                    target.unlink()

            stats = self._calculate_stats(
                root=root,
                originals=originals,
                operation_types=operation_types,
                operation_count=len(operations),
            )
            self._enforce_stats(stats, resolved_policy)
            total_characters = sum(
                len(path.read_text(encoding="utf-8")) for path in root.rglob("*") if path.is_file()
            )
            _, max_skill_tokens = self.registry.skill_limits(skill)
            if total_characters / 4 > max_skill_tokens:
                raise AnalystBenchError(
                    "skill_token_budget_exceeded", "候选 Skill 超过近似 Token 上限。"
                )
            validation = candidate_validator(root) if candidate_validator else None
            version = self.registry.import_version(
                skill.id,
                source_path=str(root),
                parent_version_id=parent.id,
                source_type="optimizer_patch",
                created_by=created_by,
            )
            return PatchApplicationResult(
                version=version,
                patch_hash=patch_hash,
                stats=stats,
                validation=validation,
            )
