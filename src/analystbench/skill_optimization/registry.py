"""Skill registry, immutable package versions and frozen execution variants."""

from __future__ import annotations

import json
import re
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
    SkillPackageVersion,
    SkillTargetBinding,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.git_store import ManagedGitStore
from analystbench.skill_optimization.package import (
    PackageLimits,
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
        inspect_package(source, self.limits)
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
            limits_json=canonical_json(limits or {}),
        )
        with transaction(self.session_factory) as session:
            if session.scalar(select(Skill.id).where(Skill.skill_key == key)):
                raise AnalystBenchError(
                    "skill_already_exists", f"Skill {key} 已存在。", status_code=409
                )
            session.add(item)
            session.flush()
            session.expunge(item)
        self.store.ensure_repository(item.id)
        return item

    def get(self, skill_id: str) -> Skill:
        self._require_enabled()
        with transaction(self.session_factory) as session:
            item = session.get(Skill, skill_id)
            if item is None:
                raise AnalystBenchError("skill_not_found", "找不到 Skill。", status_code=404)
            session.expunge(item)
            return item

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
        skill = self.get(skill_id)
        snapshot = inspect_package(
            self._source(source_path or skill.source_path), self.limits
        )
        with transaction(self.session_factory) as session:
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
            version_id = str(uuid4())
            parent_id = parent.id if parent else None
            parent_commit = parent.git_commit if parent else None
        commit, tree, object_format = self.store.commit(
            skill_id=skill_id,
            version_id=version_id,
            snapshot=snapshot,
            parent_commit=parent_commit,
            message=f"Skill {skill.skill_key} v{version_number}",
        )
        item = SkillPackageVersion(
            id=version_id,
            skill_id=skill_id,
            version_number=version_number,
            parent_version_id=parent_id,
            package_hash=snapshot.package_hash,
            git_commit=commit,
            git_tree=tree,
            git_object_format=object_format,
            manifest_json=canonical_json(snapshot.manifest),
            source_type=source_type,
            status=status,
            created_by=created_by,
        )
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            duplicate = session.scalar(
                select(SkillPackageVersion).where(
                    SkillPackageVersion.skill_id == skill_id,
                    SkillPackageVersion.package_hash == snapshot.package_hash,
                )
            )
            if duplicate is not None:
                session.expunge(duplicate)
                return duplicate
            session.add(item)
            session.flush()
            session.expunge(item)
        return item

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

    def materialize_version(self, version_id: str, destination: Path) -> dict[str, Any]:
        version = self.get_version(version_id)
        skill = self.get(version.skill_id)
        self.store.materialize(
            skill_id=skill.id, commit=version.git_commit, destination=destination
        )
        observed = inspect_package(destination, self.limits)
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
                binding = SkillTargetBinding(
                    id=str(uuid4()),
                    skill_id=skill_id,
                    evaluation_target_id=evaluation_target_id,
                    active_version_id=version_id,
                    active_level=active_level,
                    lock_version=1,
                )
                session.add(binding)
            else:
                if (
                    expected_lock_version is not None
                    and binding.lock_version != expected_lock_version
                ):
                    raise AnalystBenchError(
                        "skill_binding_conflict", "Skill 激活版本已发生变化。", status_code=409
                    )
                binding.active_version_id = version_id
                binding.active_level = active_level
                binding.lock_version += 1
            version.status = "active"
            session.flush()
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
