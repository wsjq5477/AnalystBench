"""Atomic promotion and rollback records for accepted Skill versions."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from analystbench.db.models import (
    DecisionRecord,
    OptimizationEvent,
    SkillBindingHistory,
    SkillPackageVersion,
    SkillTargetBinding,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.storage.content import canonical_json


class PromotionService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def promote(
        self,
        *,
        experiment_id: str,
        epoch_id: str | None,
        candidate_mutation_id: str | None,
        skill_id: str,
        evaluation_target_id: str,
        version_id: str,
        expected_active_version_id: str,
        gate_result: dict[str, Any],
        expected_lock_version: int | None,
        evidence: dict[str, Any],
    ) -> SkillTargetBinding:
        if gate_result.get("verdict") != "pass":
            raise AnalystBenchError(
                "skill_promotion_gate_failed", "候选版本未通过 Gate，不能晋升。"
            )
        active_level = str(gate_result.get("active_level") or "provisional")
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            binding = session.scalar(
                select(SkillTargetBinding).where(
                    SkillTargetBinding.skill_id == skill_id,
                    SkillTargetBinding.evaluation_target_id == evaluation_target_id,
                )
            )
            version = session.get(SkillPackageVersion, version_id)
            if binding is None or version is None or version.skill_id != skill_id:
                raise AnalystBenchError(
                    "skill_promotion_invalid",
                    "找不到待更新的 Skill 绑定或候选版本。",
                    status_code=404,
                )
            if (
                expected_lock_version is not None
                and binding.lock_version != expected_lock_version
            ):
                raise AnalystBenchError(
                    "skill_binding_conflict", "Skill 激活版本已发生变化。", status_code=409
                )
            if binding.active_version_id == version.id:
                histories = list(
                    session.scalars(
                        select(SkillBindingHistory)
                        .where(
                            SkillBindingHistory.binding_id == binding.id,
                            SkillBindingHistory.active_version_id == version.id,
                            SkillBindingHistory.action == "promotion",
                        )
                        .order_by(
                            SkillBindingHistory.lock_version.desc(),
                            SkillBindingHistory.id,
                        )
                    )
                )
                recovered = any(
                    _same_promotion(
                        item.metadata_json,
                        experiment_id=experiment_id,
                        epoch_id=epoch_id,
                        candidate_mutation_id=candidate_mutation_id,
                    )
                    for item in histories
                )
                if recovered:
                    # The binding transition and its audit rows commit in one
                    # transaction.  A worker may crash immediately afterwards,
                    # before the Epoch is marked completed.  Recognize only
                    # that exact promotion as an idempotent recovery; merely
                    # seeing the same Active version is not sufficient.
                    session.expunge(binding)
                    return binding
                raise AnalystBenchError(
                    "skill_binding_conflict",
                    "Skill Active 虽是候选版本，但不属于本 Epoch 已提交的晋升。",
                    status_code=409,
                )
            if binding.active_version_id != expected_active_version_id:
                raise AnalystBenchError(
                    "skill_binding_conflict",
                    "Skill Active 已不再是本 Epoch 的父版本，拒绝覆盖并发晋升或回滚。",
                    status_code=409,
                    details=[
                        {
                            "expected_active_version_id": expected_active_version_id,
                            "actual_active_version_id": binding.active_version_id,
                        }
                    ],
                )
            previous_version_id = binding.active_version_id
            binding.active_version_id = version.id
            binding.active_level = active_level
            binding.lock_version += 1
            version.status = "active"
            session.add(
                SkillBindingHistory(
                    id=str(uuid4()),
                    binding_id=binding.id,
                    skill_id=skill_id,
                    evaluation_target_id=evaluation_target_id,
                    previous_version_id=previous_version_id,
                    active_version_id=version.id,
                    active_level=active_level,
                    lock_version=binding.lock_version,
                    action="promotion",
                    metadata_json=canonical_json(
                        {
                            "experiment_id": experiment_id,
                            "epoch_id": epoch_id,
                            "candidate_mutation_id": candidate_mutation_id,
                            "gate_verdict": gate_result.get("verdict"),
                        }
                    ),
                )
            )
            session.add(
                DecisionRecord(
                    id=str(uuid4()),
                    experiment_id=experiment_id,
                    epoch_id=epoch_id,
                    candidate_mutation_id=candidate_mutation_id,
                    diagnosis_json="{}",
                    revision_json=canonical_json(
                        {
                            "previous_version_id": previous_version_id,
                            "promoted_version_id": version.id,
                        }
                    ),
                    evidence_json=canonical_json(evidence),
                    outcome_json=canonical_json(gate_result),
                )
            )
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment_id,
                    epoch_id=epoch_id,
                    candidate_mutation_id=candidate_mutation_id,
                    event_type="skill_version_promoted",
                    payload_json=canonical_json(
                        {
                            "previous_version_id": previous_version_id,
                            "active_version_id": version.id,
                            "active_level": active_level,
                            "lock_version": binding.lock_version,
                        }
                    ),
                )
            )
            session.flush()
            session.refresh(binding)
            session.expunge(binding)
            return binding


def _same_promotion(
    metadata_json: str,
    *,
    experiment_id: str,
    epoch_id: str | None,
    candidate_mutation_id: str | None,
) -> bool:
    try:
        metadata = json.loads(metadata_json or "{}")
    except (TypeError, ValueError):
        return False
    return (
        metadata.get("experiment_id") == experiment_id
        and metadata.get("epoch_id") == epoch_id
        and metadata.get("candidate_mutation_id") == candidate_mutation_id
    )
