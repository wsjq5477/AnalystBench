"""Skill registry, immutable package versions and frozen execution variants."""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationHarness,
    EvaluationMethod,
    EvaluationTarget,
    EvaluationVariant,
    Skill,
    SkillBindingHistory,
    SkillPackageVersion,
    SkillTargetBinding,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.git_store import ManagedGitStore
from analystbench.skill_optimization.package import (
    PackageLimits,
    copy_package,
    inspect_package,
    make_package_read_only,
    validate_install_path,
)
from analystbench.storage.content import canonical_json, content_hash

SKILL_KEY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")
INVOKE_RE = re.compile(r"^/[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")
HARNESS_KEY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._()-]{0,98}[A-Za-z0-9)])?$"
)


class SkillRegistryService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.store = ManagedGitStore(settings.skill_optimization_root_path)
        self.limits = PackageLimits(
            max_files=settings.skill_optimization_max_files,
            max_total_bytes=settings.skill_optimization_max_total_bytes,
            max_single_file_bytes=settings.skill_optimization_max_single_file_bytes,
        )

    def _resolved_skill_limits(
        self, configured: dict[str, Any] | None
    ) -> tuple[PackageLimits, int]:
        values = configured or {}
        allowed = {
            "max_files",
            "max_total_bytes",
            "max_single_file_bytes",
            "max_skill_tokens",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise AnalystBenchError(
                "skill_limits_invalid",
                "Skill limits 包含不支持的字段。",
                [{"fields": unknown}],
            )
        global_values = {
            "max_files": self.limits.max_files,
            "max_total_bytes": self.limits.max_total_bytes,
            "max_single_file_bytes": self.limits.max_single_file_bytes,
            "max_skill_tokens": self.settings.skill_optimization_max_skill_tokens,
        }
        resolved: dict[str, int] = {}
        for key, maximum in global_values.items():
            raw = values.get(key, maximum)
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
                raise AnalystBenchError(
                    "skill_limits_invalid",
                    f"Skill limit {key} 必须是正整数。",
                )
            if raw > maximum:
                raise AnalystBenchError(
                    "skill_limits_exceed_global",
                    f"Skill limit {key} 不能超过服务全局上限。",
                    [{"field": key, "requested": raw, "global_maximum": maximum}],
                )
            resolved[key] = raw
        return (
            PackageLimits(
                max_files=resolved["max_files"],
                max_total_bytes=resolved["max_total_bytes"],
                max_single_file_bytes=resolved["max_single_file_bytes"],
            ),
            resolved["max_skill_tokens"],
        )

    def skill_limits(self, skill: Skill) -> tuple[PackageLimits, int]:
        try:
            configured = json.loads(skill.limits_json or "{}")
        except (TypeError, json.JSONDecodeError):
            configured = {}
        if not isinstance(configured, dict):
            raise AnalystBenchError("skill_limits_invalid", "Skill limits 必须是对象。")
        return self._resolved_skill_limits(configured)

    def _require_enabled(self) -> None:
        if not self.settings.skill_optimization_enabled:
            raise AnalystBenchError(
                "skill_optimization_disabled",
                "Skill 自优化功能未启用。",
                status_code=404,
            )

    def _source(self, value: str) -> Path:
        source = Path(value).expanduser().resolve()
        protected = (
            self.settings.workspace_root_path.resolve(),
            self.settings.results_formal_path.resolve(),
            self.settings.results_tmp_path.resolve(),
            self.settings.skill_optimization_root_path.resolve(),
        )
        if any(source == root or root in source.parents for root in protected):
            raise AnalystBenchError(
                "skill_source_invalid",
                "Skill 源目录不能位于结果、运行工作区或内部版本库中。",
            )
        return source

    def create(
        self,
        *,
        skill_key: str,
        name: str,
        source_path: str,
        invoke_as: str | None = None,
        harness_key: str = "claude",
        install_relative_path: str | None = None,
        description: str = "",
        editable_paths: list[str] | None = None,
        limits: dict[str, int] | None = None,
        require_harness_source: bool = False,
    ) -> Skill:
        self._require_enabled()
        key = skill_key.strip()
        invocation = (invoke_as or f"/{key}").strip()
        if not SKILL_KEY_RE.fullmatch(key) or not INVOKE_RE.fullmatch(invocation):
            raise AnalystBenchError(
                "skill_invalid", "Skill key 或斜杠调用名格式无效。"
            )
        if invocation.removeprefix("/") != key:
            raise AnalystBenchError(
                "skill_invalid", "当前版本要求斜杠调用名与 Skill key 一致。"
            )
        normalized_harness_key = harness_key.strip()
        if not HARNESS_KEY_RE.fullmatch(normalized_harness_key):
            raise AnalystBenchError(
                "skill_harness_invalid", "Harness key 格式无效。"
            )
        source = self._source(source_path)
        if require_harness_source:
            with transaction(self.session_factory) as session:
                harnesses = list(
                    session.scalars(
                        select(EvaluationHarness).where(
                            EvaluationHarness.harness_key
                            == normalized_harness_key,
                            EvaluationHarness.status == "frozen",
                            EvaluationHarness.skill_base_dir.is_not(None),
                        )
                    )
                )
            allowed_sources = {
                (
                    Path(str(harness.skill_base_dir)).expanduser().resolve()
                    / "skills"
                    / key
                ).resolve()
                for harness in harnesses
                if harness.skill_base_dir
            }
            if source not in allowed_sources:
                raise AnalystBenchError(
                    "skill_source_not_harness_managed",
                    (
                        "Skill source_path 必须精确等于已冻结 Harness 的 "
                        "{skill_base_dir}/skills/{skill_key}。"
                    ),
                    status_code=403,
                )
        normalized_limits = dict(limits or {})
        package_limits, _ = self._resolved_skill_limits(normalized_limits)
        inspect_package(source, package_limits)
        install_path = validate_install_path(
            install_relative_path or f".claude/skills/{key}", skill_key=key
        )
        item = Skill(
            id=str(uuid4()),
            skill_key=key,
            name=name.strip() or key,
            description=description,
            source_path=str(source),
            invoke_as=invocation,
            harness_key=normalized_harness_key,
            install_relative_path=install_path,
            editable_paths_json=canonical_json(editable_paths or ["SKILL.md"]),
            limits_json=canonical_json(normalized_limits),
        )
        with transaction(self.session_factory) as session:
            if session.scalar(select(Skill.id).where(Skill.skill_key == key)):
                raise AnalystBenchError(
                    "skill_already_exists", f"Skill {key} 已存在。", status_code=409
                )
            session.add(item)
            session.flush()
            self.store.ensure_repository(item.id)
            session.expunge(item)
        return item

    def get(self, skill_id: str) -> Skill:
        self._require_enabled()
        with transaction(self.session_factory) as session:
            item = session.get(Skill, skill_id)
            if item is None:
                raise AnalystBenchError("skill_not_found", "找不到 Skill。", status_code=404)
            session.expunge(item)
            return item

    def discard_empty(self, skill_id: str) -> bool:
        """Compensate a failed create+initial-import request without data loss."""

        self._require_enabled()
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            skill = session.get(Skill, skill_id, with_for_update=True)
            if skill is None:
                return False
            version_exists = session.scalar(
                select(SkillPackageVersion.id)
                .where(SkillPackageVersion.skill_id == skill_id)
                .limit(1)
            )
            binding_exists = session.scalar(
                select(SkillTargetBinding.id)
                .where(SkillTargetBinding.skill_id == skill_id)
                .limit(1)
            )
            if version_exists is not None or binding_exists is not None:
                return False
            session.delete(skill)
        self.store.delete_repository_if_unreferenced(skill_id)
        return True

    def list(self) -> list[Skill]:
        self._require_enabled()
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(Skill)
                    .where(Skill.archived_at.is_(None))
                    .order_by(Skill.skill_key)
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def import_version(
        self,
        skill_id: str,
        *,
        source_path: str | None = None,
        parent_version_id: str | None = None,
        source_type: str = "import",
        status: str = "candidate",
        created_by: str | None = None,
    ) -> SkillPackageVersion:
        self._require_enabled()
        detached_skill = self.get(skill_id)
        package_limits, _ = self.skill_limits(detached_skill)
        source_snapshot = inspect_package(
            self._source(source_path or detached_skill.source_path), package_limits
        )
        version_id = str(uuid4())
        ref_created = False
        try:
            self.store.tmp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                dir=self.settings.skill_optimization_root_path / "tmp",
                prefix="registry-import-",
            ) as temporary:
                staged_root = Path(temporary) / "package"
                copy_package(source_snapshot, staged_root)
                snapshot = inspect_package(staged_root, package_limits)
                with transaction(self.session_factory) as session:
                    if session.get_bind().dialect.name == "sqlite":
                        session.execute(text("BEGIN IMMEDIATE"))
                    skill = session.get(Skill, skill_id, with_for_update=True)
                    if skill is None:
                        raise AnalystBenchError(
                            "skill_not_found", "找不到 Skill。", status_code=404
                        )
                    existing = session.scalar(
                        select(SkillPackageVersion).where(
                            SkillPackageVersion.skill_id == skill_id,
                            SkillPackageVersion.package_hash == snapshot.package_hash,
                        )
                    )
                    if existing is not None:
                        session.expunge(existing)
                        return existing
                    parent = (
                        session.get(SkillPackageVersion, parent_version_id)
                        if parent_version_id
                        else session.scalar(
                            select(SkillPackageVersion)
                            .where(SkillPackageVersion.skill_id == skill_id)
                            .order_by(SkillPackageVersion.version_number.desc())
                            .limit(1)
                        )
                    )
                    if parent is not None and parent.skill_id != skill_id:
                        raise AnalystBenchError(
                            "skill_version_parent_invalid", "父版本不属于当前 Skill。"
                        )
                    version_number = int(
                        session.scalar(
                            select(func.max(SkillPackageVersion.version_number)).where(
                                SkillPackageVersion.skill_id == skill_id
                            )
                        )
                        or 0
                    ) + 1
                    commit, tree, object_format = self.store.commit(
                        skill_id=skill_id,
                        version_id=version_id,
                        snapshot=snapshot,
                        parent_commit=parent.git_commit if parent else None,
                        message=f"Skill {skill.skill_key} v{version_number}",
                    )
                    ref_created = True
                    item = SkillPackageVersion(
                        id=version_id,
                        skill_id=skill_id,
                        version_number=version_number,
                        parent_version_id=parent.id if parent else None,
                        package_hash=snapshot.package_hash,
                        git_commit=commit,
                        git_tree=tree,
                        git_object_format=object_format,
                        manifest_json=canonical_json(snapshot.manifest),
                        source_type=source_type,
                        status=status,
                        created_by=created_by,
                    )
                    session.add(item)
                    session.flush()
                    session.expunge(item)
                return item
        except Exception:
            if ref_created:
                try:
                    self.store.delete_version_ref(
                        skill_id=skill_id, version_id=version_id
                    )
                except AnalystBenchError:
                    # Keep the original DB/import failure stable. A dangling
                    # internal ref is unreachable and may be pruned later.
                    pass
            raise

    def get_version(self, version_id: str) -> SkillPackageVersion:
        self._require_enabled()
        with transaction(self.session_factory) as session:
            item = session.get(SkillPackageVersion, version_id)
            if item is None:
                raise AnalystBenchError(
                    "skill_version_not_found", "找不到 Skill 版本。", status_code=404
                )
            session.expunge(item)
            return item

    def list_versions(self, skill_id: str) -> list[SkillPackageVersion]:
        self.get(skill_id)
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(SkillPackageVersion)
                    .where(SkillPackageVersion.skill_id == skill_id)
                    .order_by(SkillPackageVersion.version_number.desc())
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def find_binding(
        self, *, skill_id: str, evaluation_target_id: str
    ) -> SkillTargetBinding | None:
        self.get(skill_id)
        with transaction(self.session_factory) as session:
            item = session.scalar(
                select(SkillTargetBinding).where(
                    SkillTargetBinding.skill_id == skill_id,
                    SkillTargetBinding.evaluation_target_id == evaluation_target_id,
                )
            )
            if item is not None:
                session.expunge(item)
            return item

    def list_bindings(self, skill_id: str) -> list[SkillTargetBinding]:
        self.get(skill_id)
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(SkillTargetBinding)
                    .where(SkillTargetBinding.skill_id == skill_id)
                    .order_by(SkillTargetBinding.evaluation_target_id)
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_binding_history(
        self,
        skill_id: str,
        *,
        evaluation_target_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SkillBindingHistory]:
        self.get(skill_id)
        if not 1 <= limit <= 500 or offset < 0:
            raise AnalystBenchError(
                "skill_binding_history_page_invalid",
                "绑定审计分页参数无效。",
            )
        with transaction(self.session_factory) as session:
            query = select(SkillBindingHistory).where(
                SkillBindingHistory.skill_id == skill_id
            )
            if evaluation_target_id is not None:
                query = query.where(
                    SkillBindingHistory.evaluation_target_id == evaluation_target_id
                )
            items = list(
                session.scalars(
                    query.order_by(
                        SkillBindingHistory.created_at.desc(),
                        SkillBindingHistory.lock_version.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def materialize_version(self, version_id: str, destination: Path) -> dict[str, Any]:
        version = self.get_version(version_id)
        skill = self.get(version.skill_id)
        self.store.materialize(
            skill_id=skill.id, commit=version.git_commit, destination=destination
        )
        package_limits, _ = self.skill_limits(skill)
        try:
            frozen_manifest = json.loads(version.manifest_json or "{}")
        except (TypeError, json.JSONDecodeError):
            frozen_manifest = {}
        frozen_files = frozen_manifest.get("files", [])
        includes_modes = any(
            isinstance(item, dict) and "mode" in item for item in frozen_files
        )
        observed = inspect_package(
            destination, package_limits, include_modes=includes_modes
        )
        if observed.package_hash != version.package_hash:
            raise AnalystBenchError(
                "skill_package_integrity_failed",
                "内部 Git 版本与冻结包哈希不一致。",
            )
        make_package_read_only(destination)
        return {
            "skill_id": skill.id,
            "skill_key": skill.skill_key,
            "skill_package_version_id": version.id,
            "package_hash": version.package_hash,
            "git_commit": version.git_commit,
            "install_relative_path": skill.install_relative_path,
            "invoke_as": skill.invoke_as,
        }

    def export_version_archive(
        self, *, skill_id: str, version_id: str
    ) -> dict[str, Any]:
        """Build an in-memory, deterministic archive for one immutable version."""

        self._require_enabled()
        skill = self.get(skill_id)
        version = self.get_version(version_id)
        if version.skill_id != skill.id:
            raise AnalystBenchError(
                "skill_export_version_mismatch",
                "导出版本不属于 URL 指定的 Skill。",
            )
        package_manifest = json.loads(version.manifest_json or "{}")
        package_files = package_manifest.get("files", [])
        if any(
            isinstance(entry, dict)
            and entry.get("path") == ".analystbench/version-manifest.json"
            for entry in package_files
        ):
            raise AnalystBenchError(
                "skill_export_reserved_path",
                "Skill 包占用了导出清单的保留路径。",
            )
        export_manifest = {
            "format": "analystbench.skill-version-export.v1",
            "skill": {
                "id": skill.id,
                "key": skill.skill_key,
                "name": skill.name,
                "invoke_as": skill.invoke_as,
                "harness_key": skill.harness_key,
                "install_relative_path": skill.install_relative_path,
            },
            "version": {
                "id": version.id,
                "number": version.version_number,
                "parent_version_id": version.parent_version_id,
                "package_hash": version.package_hash,
                "git_commit": version.git_commit,
                "git_tree": version.git_tree,
                "git_object_format": version.git_object_format,
                "source_type": version.source_type,
                "status": version.status,
                "created_by": version.created_by,
                "created_at": version.created_at.isoformat(),
            },
            "package_manifest": package_manifest,
        }
        self.store.ensure_repository(skill.id)
        with tempfile.TemporaryDirectory(dir=self.store.tmp_root) as temporary:
            package = Path(temporary) / "package"
            self.store.materialize(
                skill_id=skill.id,
                commit=version.git_commit,
                destination=package,
            )
            package_limits, _ = self.skill_limits(skill)
            include_modes = any(
                isinstance(item, dict) and "mode" in item
                for item in package_files
            )
            observed = inspect_package(
                package, package_limits, include_modes=include_modes
            )
            if observed.package_hash != version.package_hash:
                raise AnalystBenchError(
                    "skill_package_integrity_failed",
                    "内部 Git 版本与冻结包哈希不一致。",
                )
            output = io.BytesIO()
            with zipfile.ZipFile(
                output,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                observed_files = observed.manifest.get("files", [])
                assert isinstance(observed_files, list)
                for entry in observed_files:
                    assert isinstance(entry, dict)
                    relative = str(entry["path"])
                    self._write_archive_entry(
                        archive,
                        relative,
                        (package / relative).read_bytes(),
                        mode=int(entry.get("mode", 0o644)),
                    )
                self._write_archive_entry(
                    archive,
                    ".analystbench/version-manifest.json",
                    (canonical_json(export_manifest) + "\n").encode("utf-8"),
                )
        return {
            "filename": f"{skill.skill_key}-v{version.version_number}.zip",
            "content": output.getvalue(),
            "manifest": export_manifest,
        }

    @staticmethod
    def _write_archive_entry(
        archive: zipfile.ZipFile,
        path: str,
        content: bytes,
        *,
        mode: int = 0o644,
    ) -> None:
        info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        readonly_mode = 0o555 if mode & 0o111 else 0o444
        info.external_attr = ((0o100000 | readonly_mode) & 0xFFFF) << 16
        archive.writestr(info, content)

    def diff_versions(self, old_version_id: str, new_version_id: str) -> str:
        old = self.get_version(old_version_id)
        new = self.get_version(new_version_id)
        if old.skill_id != new.skill_id:
            raise AnalystBenchError(
                "skill_version_mismatch", "只能比较同一 Skill 的版本。"
            )
        return self.store.diff(
            skill_id=old.skill_id,
            old_commit=old.git_commit,
            new_commit=new.git_commit,
        )

    def bind(
        self,
        *,
        skill_id: str,
        evaluation_target_id: str,
        version_id: str,
        active_level: str = "provisional",
        expected_lock_version: int | None = None,
        allow_initial_unbound: bool = True,
    ) -> SkillTargetBinding:
        self._require_enabled()
        if active_level not in {"provisional", "validated"}:
            raise AnalystBenchError("skill_binding_invalid", "激活级别无效。")
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            skill = session.get(Skill, skill_id)
            version = session.get(SkillPackageVersion, version_id)
            target = session.get(EvaluationTarget, evaluation_target_id)
            if skill is None or version is None or target is None:
                raise AnalystBenchError(
                    "skill_binding_not_found", "Skill、版本或运行组合不存在。", status_code=404
                )
            if version.skill_id != skill.id:
                raise AnalystBenchError(
                    "skill_binding_invalid", "Skill 版本不属于指定 Skill。"
                )
            if target.status != "frozen" or not target.materialized_method_id:
                raise AnalystBenchError(
                    "skill_binding_invalid", "运行组合必须先冻结。"
                )
            other_skill_id = session.scalar(
                select(SkillTargetBinding.skill_id)
                .where(
                    SkillTargetBinding.evaluation_target_id
                    == evaluation_target_id,
                    SkillTargetBinding.skill_id != skill_id,
                )
                .limit(1)
            )
            if other_skill_id is not None:
                raise AnalystBenchError(
                    "evaluation_target_skill_binding_conflict",
                    "V1 一个 Evaluation Target 只能绑定一个 Active Skill。",
                    status_code=409,
                    details=[{"existing_skill_id": other_skill_id}],
                )
            binding = session.scalar(
                select(SkillTargetBinding).where(
                    SkillTargetBinding.skill_id == skill_id,
                    SkillTargetBinding.evaluation_target_id == evaluation_target_id,
                )
            )
            if binding is None:
                if expected_lock_version not in {None, 0}:
                    raise AnalystBenchError(
                        "skill_binding_conflict", "Skill 激活版本已发生变化。", status_code=409
                    )
                if not allow_initial_unbound:
                    raise AnalystBenchError(
                        "skill_binding_version_not_active",
                        "未绑定的运行组合只能由实验初始化可信基线，不能通过通用绑定 API 激活版本。",
                        status_code=409,
                    )
                if active_level != "provisional":
                    raise AnalystBenchError(
                        "skill_binding_initial_level_invalid",
                        "首次内部绑定只能创建 provisional 基线；validated 必须来自 Gate。",
                        status_code=409,
                    )
                if version.status != "active" and not (
                    version.parent_version_id is None
                    and version.source_type == "initial"
                ):
                    raise AnalystBenchError(
                        "skill_binding_version_not_active",
                        "首次内部绑定只能使用可信初始版本或显式选择的 provisional 基线。",
                        status_code=409,
                    )
                binding = SkillTargetBinding(
                    id=str(uuid4()),
                    skill_id=skill_id,
                    evaluation_target_id=evaluation_target_id,
                    active_version_id=version_id,
                    active_level=active_level,
                    lock_version=1,
                )
                session.add(binding)
                session.flush()
                session.add(
                    SkillBindingHistory(
                        id=str(uuid4()),
                        binding_id=binding.id,
                        skill_id=skill.id,
                        evaluation_target_id=target.id,
                        previous_version_id=None,
                        active_version_id=version.id,
                        active_level=active_level,
                        lock_version=binding.lock_version,
                        action="initial_bind",
                        metadata_json=canonical_json({"source": "registry"}),
                    )
                )
            else:
                if (
                    expected_lock_version is not None
                    and binding.lock_version != expected_lock_version
                ):
                    raise AnalystBenchError(
                        "skill_binding_conflict", "Skill 激活版本已发生变化。", status_code=409
                    )
                if binding.active_version_id != version_id:
                    raise AnalystBenchError(
                        "skill_binding_change_requires_promotion_or_rollback",
                        "已存在的 Active 绑定只能通过 Gate 晋升或显式回滚变更。",
                        status_code=409,
                    )
            version.status = "active"
            session.flush()
            session.expunge(binding)
            return binding

    def rollback(
        self,
        *,
        skill_id: str,
        evaluation_target_id: str,
        version_id: str,
        expected_lock_version: int,
        reason: str = "",
    ) -> SkillTargetBinding:
        """Explicitly restore a previously active version with optimistic locking."""

        self._require_enabled()
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            binding = session.scalar(
                select(SkillTargetBinding).where(
                    SkillTargetBinding.skill_id == skill_id,
                    SkillTargetBinding.evaluation_target_id == evaluation_target_id,
                )
            )
            if binding is None:
                raise AnalystBenchError(
                    "skill_rollback_binding_not_found",
                    "找不到要回滚的 Skill 绑定。",
                    status_code=404,
                )
            if binding.lock_version != expected_lock_version:
                raise AnalystBenchError(
                    "skill_binding_conflict",
                    "Skill 激活版本已发生变化。",
                    status_code=409,
                )
            version = session.get(SkillPackageVersion, version_id)
            if version is None:
                raise AnalystBenchError(
                    "skill_rollback_version_not_found",
                    "找不到要回滚的 Skill 版本。",
                    status_code=404,
                )
            if version.skill_id != skill_id:
                raise AnalystBenchError(
                    "skill_rollback_version_mismatch",
                    "回滚版本不属于 URL 指定的 Skill。",
                )
            if version.status != "active":
                raise AnalystBenchError(
                    "skill_rollback_version_not_active",
                    "只能回滚到曾经通过绑定或 Gate 激活的版本。",
                    status_code=409,
                )
            if binding.active_version_id == version.id:
                session.expunge(binding)
                return binding
            prior_history = session.scalar(
                select(SkillBindingHistory)
                .where(
                    SkillBindingHistory.skill_id == skill_id,
                    SkillBindingHistory.evaluation_target_id == evaluation_target_id,
                    SkillBindingHistory.active_version_id == version.id,
                )
                .order_by(SkillBindingHistory.lock_version.desc())
                .limit(1)
            )
            if prior_history is None:
                raise AnalystBenchError(
                    "skill_rollback_version_not_active",
                    "只能回滚到曾在同一运行组合中通过绑定或 Gate 激活的版本。",
                    status_code=409,
                )
            previous = binding.active_version_id
            binding.active_version_id = version.id
            binding.active_level = prior_history.active_level
            binding.lock_version += 1
            session.add(
                SkillBindingHistory(
                    id=str(uuid4()),
                    binding_id=binding.id,
                    skill_id=skill_id,
                    evaluation_target_id=evaluation_target_id,
                    previous_version_id=previous,
                    active_version_id=version.id,
                    active_level=binding.active_level,
                    lock_version=binding.lock_version,
                    action="rollback",
                    metadata_json=canonical_json({"reason": reason.strip()}),
                )
            )
            session.flush()
            session.refresh(binding)
            session.expunge(binding)
            return binding

    def freeze_variant(
        self, *, evaluation_target_id: str, version_id: str
    ) -> EvaluationVariant:
        self._require_enabled()
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            existing = session.scalar(
                select(EvaluationVariant).where(
                    EvaluationVariant.evaluation_target_id == evaluation_target_id,
                    EvaluationVariant.skill_package_version_id == version_id,
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
            target = session.get(EvaluationTarget, evaluation_target_id)
            version = session.get(SkillPackageVersion, version_id)
            skill = session.get(Skill, version.skill_id) if version else None
            base_method = (
                session.get(EvaluationMethod, target.materialized_method_id)
                if target and target.materialized_method_id
                else None
            )
            harness = (
                session.get(EvaluationHarness, target.harness_id)
                if target is not None
                else None
            )
            if (
                target is None
                or target.status != "frozen"
                or version is None
                or skill is None
                or harness is None
                or base_method is None
            ):
                raise AnalystBenchError(
                    "evaluation_variant_invalid",
                    "冻结 Variant 需要已冻结 Target 和有效 Skill 版本。",
                )
            if skill.harness_key != harness.harness_key:
                raise AnalystBenchError(
                    "evaluation_variant_harness_mismatch",
                    (
                        f"Skill 配置的 Harness {skill.harness_key} 与 Target 的 "
                        f"Harness {harness.harness_key} 不一致。"
                    ),
                )
            variant_manifest = {
                "evaluation_target_hash": target.content_hash,
                "base_method_hash": base_method.content_hash,
                "skill_package_hash": version.package_hash,
                "install_relative_path": skill.install_relative_path,
                "invoke_as": skill.invoke_as,
            }
            variant_hash = content_hash(canonical_json(variant_manifest).encode("utf-8"))
            method = EvaluationMethod(
                id=str(uuid4()),
                method_key=f"sv-{variant_hash.removeprefix('sha256:')[:16]}",
                name=f"{base_method.name} + {skill.invoke_as} v{version.version_number}",
                version_number=1,
                tool_dir=base_method.tool_dir,
                command_template=base_method.command_template,
                timeout_seconds=base_method.timeout_seconds,
                max_output_bytes=base_method.max_output_bytes,
                concurrency_limit=base_method.concurrency_limit,
                status="frozen",
                content_hash=content_hash(
                    canonical_json(
                        {"base_method": base_method.content_hash, "variant": variant_hash}
                    ).encode("utf-8")
                ),
                last_probe_json=base_method.last_probe_json,
            )
            item = EvaluationVariant(
                id=str(uuid4()),
                evaluation_target_id=target.id,
                skill_package_version_id=version.id,
                materialized_method_id=method.id,
                install_relative_path=skill.install_relative_path,
                invoke_as=skill.invoke_as,
                content_hash=variant_hash,
                status="frozen",
            )
            session.add_all([method, item])
            session.flush()
            session.expunge(item)
            return item

    @staticmethod
    def skill_view(item: Skill) -> dict[str, Any]:
        return {
            "id": item.id,
            "key": item.skill_key,
            "name": item.name,
            "description": item.description,
            "source_path": item.source_path,
            "invoke_as": item.invoke_as,
            "harness_key": item.harness_key,
            "install_relative_path": item.install_relative_path,
            "publish_mode": item.publish_mode,
            "editable_paths": json.loads(item.editable_paths_json or "[]"),
            "limits": json.loads(item.limits_json or "{}"),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    @staticmethod
    def version_view(item: SkillPackageVersion) -> dict[str, Any]:
        return {
            "id": item.id,
            "skill_id": item.skill_id,
            "version": item.version_number,
            "parent_version_id": item.parent_version_id,
            "package_hash": item.package_hash,
            "git_commit": item.git_commit,
            "git_tree": item.git_tree,
            "git_object_format": item.git_object_format,
            "manifest": json.loads(item.manifest_json or "{}"),
            "source_type": item.source_type,
            "status": item.status,
            "created_by": item.created_by,
            "created_at": item.created_at,
        }

    @staticmethod
    def binding_history_view(item: SkillBindingHistory) -> dict[str, Any]:
        return {
            "id": item.id,
            "binding_id": item.binding_id,
            "skill_id": item.skill_id,
            "evaluation_target_id": item.evaluation_target_id,
            "previous_version_id": item.previous_version_id,
            "active_version_id": item.active_version_id,
            "active_level": item.active_level,
            "lock_version": item.lock_version,
            "action": item.action,
            "metadata": json.loads(item.metadata_json or "{}"),
            "created_at": item.created_at,
        }
