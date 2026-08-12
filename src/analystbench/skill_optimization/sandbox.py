"""Optional evaluation workspace hook for installing a frozen Skill package."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analystbench.db.models import EvaluationVariant
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.package import safe_relative_path
from analystbench.skill_optimization.registry import SkillRegistryService


class SkillWorkspacePreparer:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registry: SkillRegistryService,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry

    def prepare(
        self, *, method_id: str, workspace: Path
    ) -> dict[str, object] | None:
        with transaction(self.session_factory) as session:
            variant = session.scalar(
                select(EvaluationVariant).where(
                    EvaluationVariant.materialized_method_id == method_id,
                    EvaluationVariant.status == "frozen",
                )
            )
            if variant is None:
                return None
            version_id = variant.skill_package_version_id
            install_relative_path = variant.install_relative_path
            variant_id = variant.id
            variant_hash = variant.content_hash
        relative = safe_relative_path(install_relative_path)
        destination = workspace.joinpath(*relative.parts)
        resolved_workspace = workspace.resolve()
        resolved_parent = destination.parent.resolve()
        if (
            resolved_workspace != resolved_parent
            and resolved_workspace not in resolved_parent.parents
        ):
            raise AnalystBenchError(
                "skill_install_path_invalid", "Skill 安装路径逃逸运行工作区。"
            )
        metadata = self.registry.materialize_version(version_id, destination)
        return {
            **metadata,
            "evaluation_variant_id": variant_id,
            "evaluation_variant_hash": variant_hash,
        }
