"""Durable multi-candidate Skill optimization state machine."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from analystbench.agent_runner import AgentRunnerError, create_runner
from analystbench.config import Settings
from analystbench.content_store import canonical_json, content_hash
from analystbench.db.models import (
    CandidateComparison,
    CandidateMutation,
    EvaluationMethod,
    EvaluationSubmission,
    EvaluationSubmissionCaseRun,
    EvaluationSubmissionMethodRun,
    ExecutionProfile,
    OptimizationDataSnapshot,
    OptimizationEpoch,
    OptimizationEvent,
    OptimizationExperiment,
    OptimizationRunGroup,
    OptimizationSignal,
    OptimizerPolicyVersion,
    SkillTargetBinding,
    VerifierBundleVersion,
)
from analystbench.errors import AnalystBenchError
from analystbench.evaluation_submission import EvaluationSubmissionService
from analystbench.jobs import JobQueue
from analystbench.services import transaction
from analystbench.skill_optimization.evidence import (
    build_evidence_summary,
    extract_report_evidence,
)
from analystbench.skill_optimization.gate import evaluate_gate, evaluate_screening
from analystbench.skill_optimization.patch import StructuredPatchApplier
from analystbench.skill_optimization.promotion import PromotionService
from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.skill_optimization.statistics import RunObservation, compare_paired

TERMINAL_SUBMISSION_STATES = {"completed", "completed_with_errors", "failed", "cancelled"}


class OptimizationExperimentService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        registry: SkillRegistryService,
        submissions: EvaluationSubmissionService,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.registry = registry
        self.submissions = submissions
        self.patches = StructuredPatchApplier(registry)
        self.promotions = PromotionService(session_factory)
        self.jobs = JobQueue(session_factory)

    def create_policy(
        self,
        *,
        policy_key: str,
        execution_profile_id: str,
        prompt_bundle: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OptimizerPolicyVersion:
        self.registry._require_enabled()
        with transaction(self.session_factory) as session:
            profile = session.get(ExecutionProfile, execution_profile_id)
            if profile is None or profile.status != "frozen":
                raise AnalystBenchError(
                    "optimizer_policy_invalid", "Optimizer 执行配置必须存在且已冻结。"
                )
            version_number = int(
                session.scalar(
                    select(func.max(OptimizerPolicyVersion.version_number)).where(
                        OptimizerPolicyVersion.policy_key == policy_key
                    )
                )
                or 0
            ) + 1
            manifest = {
                "policy_key": policy_key,
                "version_number": version_number,
                "execution_profile_hash": profile.content_hash,
                "prompt_bundle": prompt_bundle,
                "config": config or {},
            }
            item = OptimizerPolicyVersion(
                id=str(uuid4()),
                policy_key=policy_key,
                version_number=version_number,
                execution_profile_id=profile.id,
                prompt_bundle_hash=content_hash(
                    canonical_json(prompt_bundle).encode("utf-8")
                ),
                config_json=canonical_json(
                    {"prompt_bundle": prompt_bundle, **(config or {})}
                ),
                content_hash=content_hash(canonical_json(manifest).encode("utf-8")),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
            return item

    def create_verifier(
        self,
        *,
        bundle_key: str,
        static_policy: dict[str, Any] | None = None,
        gate_policy: dict[str, Any] | None = None,
        judge_config: dict[str, Any] | None = None,
    ) -> VerifierBundleVersion:
        self.registry._require_enabled()
        with transaction(self.session_factory) as session:
            version_number = int(
                session.scalar(
                    select(func.max(VerifierBundleVersion.version_number)).where(
                        VerifierBundleVersion.bundle_key == bundle_key
                    )
                )
                or 0
            ) + 1
            manifest = {
                "bundle_key": bundle_key,
                "version_number": version_number,
                "static_policy": static_policy or {},
                "gate_policy": gate_policy or {},
                "judge_config": judge_config or {},
            }
            item = VerifierBundleVersion(
                id=str(uuid4()),
                bundle_key=bundle_key,
                version_number=version_number,
                static_policy_json=canonical_json(static_policy or {}),
                gate_policy_json=canonical_json(gate_policy or {}),
                judge_config_json=canonical_json(judge_config or {}),
                content_hash=content_hash(canonical_json(manifest).encode("utf-8")),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
            return item

    def create_snapshot(
        self,
        *,
        dataset_key: str,
        validation_case_paths: list[str],
        mode: str = "development_regression",
        train_case_paths: list[str] | None = None,
        hidden_test_case_paths: list[str] | None = None,
        prospective_holdout_case_paths: list[str] | None = None,
    ) -> OptimizationDataSnapshot:
        self.registry._require_enabled()
        if mode not in {"development_regression", "independent_validation"}:
            raise AnalystBenchError("optimization_snapshot_invalid", "数据快照模式无效。")
        validation = list(dict.fromkeys(validation_case_paths))
        if not validation:
            raise AnalystBenchError(
                "optimization_snapshot_invalid", "至少需要一个验证 Case。"
            )
        manifest = {
            "dataset_key": dataset_key,
            "mode": mode,
            "train_cases": train_case_paths or [],
            "validation_cases": validation,
            "hidden_test_cases": hidden_test_case_paths or [],
            "prospective_holdout_cases": prospective_holdout_case_paths or [],
        }
        digest = content_hash(canonical_json(manifest).encode("utf-8"))
        with transaction(self.session_factory) as session:
            existing = session.scalar(
                select(OptimizationDataSnapshot).where(
                    OptimizationDataSnapshot.content_hash == digest
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
            item = OptimizationDataSnapshot(
                id=str(uuid4()),
                dataset_key=dataset_key,
                mode=mode,
                train_cases_json=canonical_json(train_case_paths or []),
                validation_cases_json=canonical_json(validation),
                hidden_test_cases_json=canonical_json(
                    hidden_test_case_paths or []
                ),
                prospective_holdout_cases_json=canonical_json(
                    prospective_holdout_case_paths or []
                ),
                case_input_hashes_json="{}",
                eval_spec_hashes_json="{}",
                content_hash=digest,
            )
            session.add(item)
            session.flush()
            session.expunge(item)
            return item

    def create_experiment(
        self,
        *,
        name: str,
        skill_id: str,
        base_skill_version_id: str,
        evaluation_target_id: str,
        data_snapshot_id: str,
        optimizer_policy_version_id: str,
        verifier_bundle_version_id: str,
        max_epochs: int | None = None,
        created_by: str | None = None,
    ) -> OptimizationExperiment:
        self.registry._require_enabled()
        base = self.registry.get_version(base_skill_version_id)
        if base.skill_id != skill_id:
            raise AnalystBenchError(
                "optimization_experiment_invalid", "基线版本不属于指定 Skill。"
            )
        self.registry.freeze_variant(
            evaluation_target_id=evaluation_target_id,
            version_id=base_skill_version_id,
        )
        with transaction(self.session_factory) as session:
            if any(
                session.get(model, identifier) is None
                for model, identifier in (
                    (OptimizationDataSnapshot, data_snapshot_id),
                    (OptimizerPolicyVersion, optimizer_policy_version_id),
                    (VerifierBundleVersion, verifier_bundle_version_id),
                )
            ):
                raise AnalystBenchError(
                    "optimization_experiment_invalid",
                    "数据快照、Optimizer Policy 或 Verifier 不存在。",
                )
            epochs = max_epochs or self.settings.skill_optimization_max_epochs
            if not 1 <= epochs <= self.settings.skill_optimization_max_epochs:
                raise AnalystBenchError(
                    "optimization_experiment_invalid", "实验 Epoch 数超过系统上限。"
                )
            item = OptimizationExperiment(
                id=str(uuid4()),
                name=name,
                skill_id=skill_id,
                base_skill_version_id=base_skill_version_id,
                evaluation_target_id=evaluation_target_id,
                data_snapshot_id=data_snapshot_id,
                optimizer_policy_version_id=optimizer_policy_version_id,
                verifier_bundle_version_id=verifier_bundle_version_id,
                max_epochs=epochs,
                status="created",
                created_by=created_by,
                config_snapshot_json=canonical_json(
                    {
                        "candidate_count": self.settings.skill_optimization_candidate_count,
                        "screening_case_count": (
                            self.settings.skill_optimization_screening_case_count
                        ),
                        "validation_repeats": self.settings.skill_optimization_validation_repeats,
                        "max_repeats": self.settings.skill_optimization_max_repeats,
                        "early_stop_patience": self.settings.skill_optimization_early_stop_patience,
                        "min_overall_delta": self.settings.skill_optimization_min_overall_delta,
                    }
                ),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
        self.registry.bind(
            skill_id=skill_id,
            evaluation_target_id=evaluation_target_id,
            version_id=base_skill_version_id,
            active_level="provisional",
        )
        return item

    def start(self, experiment_id: str) -> OptimizationExperiment:
        with transaction(self.session_factory) as session:
            item = session.get(OptimizationExperiment, experiment_id)
            if item is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found", "找不到实验。", status_code=404
                )
            if item.status != "created":
                raise AnalystBenchError(
                    "optimization_experiment_state_invalid", "实验不能重复启动。"
                )
            item.status = "running"
            item.started_at = datetime.now(UTC)
            self.jobs.enqueue(
                session, "skill_optimization_advance", {"experiment_id": item.id}
            )
            session.flush()
            session.expunge(item)
            return item

    @staticmethod
    def _parse_patch(value: str) -> dict[str, Any]:
        stripped = value.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
        candidate = fenced.group(1) if fenced else stripped
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise AnalystBenchError(
                "optimizer_output_invalid", "Optimizer 未返回有效 JSON Patch。"
            ) from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("operations"), list):
            raise AnalystBenchError(
                "optimizer_output_invalid", "Optimizer JSON 缺少 operations。"
            )
        return parsed

    def _record_rejected_candidate(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        candidate_index: int,
        *,
        rejection_code: str,
        rejection_message: str,
        structured_patch: dict[str, Any] | None = None,
        raw_output: str = "",
    ) -> CandidateMutation:
        patch = structured_patch or {}
        patch_hash = content_hash(
            (
                canonical_json(patch)
                if patch
                else raw_output or f"{rejection_code}:{candidate_index}"
            ).encode("utf-8")
        )
        mutation = CandidateMutation(
            id=str(uuid4()),
            epoch_id=epoch.id,
            parent_skill_version_id=epoch.parent_skill_version_id,
            candidate_skill_version_id=None,
            candidate_type=f"structured_patch_{candidate_index}",
            structured_patch_json=canonical_json(patch),
            patch_hash=patch_hash,
            rationale=str(patch.get("rationale", "")),
            evidence_refs_json=canonical_json(
                {"epoch_id": epoch.id, "candidate_index": candidate_index}
            ),
            status="rejected",
            rejection_code=rejection_code,
            rejection_detail_json=canonical_json(
                {"code": rejection_code, "message": rejection_message}
            ),
        )
        with transaction(self.session_factory) as session:
            session.add(mutation)
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    candidate_mutation_id=mutation.id,
                    event_type="candidate_static_rejected",
                    payload_json=mutation.rejection_detail_json,
                )
            )
            session.flush()
            session.expunge(mutation)
        return mutation

    def _rejected_history(
        self,
        experiment_id: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        with transaction(self.session_factory) as session:
            rows = list(
                session.execute(
                    select(CandidateMutation, OptimizationEpoch)
                    .join(
                        OptimizationEpoch,
                        OptimizationEpoch.id == CandidateMutation.epoch_id,
                    )
                    .where(
                        OptimizationEpoch.experiment_id == experiment_id,
                        CandidateMutation.status == "rejected",
                    )
                    .order_by(CandidateMutation.created_at.desc())
                    .limit(limit)
                )
            )
        return [
            {
                "epoch": epoch.epoch_number,
                "candidate_type": mutation.candidate_type,
                "rationale": mutation.rationale,
                "rejection_code": mutation.rejection_code,
                "rejection_detail": json.loads(
                    mutation.rejection_detail_json or "{}"
                ),
            }
            for mutation, epoch in rows
        ]

    def _generate_candidate(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        candidate_index: int,
    ) -> CandidateMutation:
        with transaction(self.session_factory) as session:
            policy = session.get(
                OptimizerPolicyVersion, experiment.optimizer_policy_version_id
            )
            profile = (
                session.get(ExecutionProfile, policy.execution_profile_id)
                if policy
                else None
            )
            if policy is None or profile is None or profile.status != "frozen":
                raise AnalystBenchError(
                    "optimizer_policy_invalid", "Optimizer Policy 不可执行。"
                )
            policy_config = json.loads(policy.config_json or "{}")
            runner_id = profile.runner
            runner_config = json.loads(profile.configuration_json or "{}")
        with tempfile.TemporaryDirectory(prefix="analystbench-optimizer-") as temporary:
            workspace = Path(temporary)
            skill_root = workspace / "skill"
            self.registry.materialize_version(epoch.parent_skill_version_id, skill_root)
            evidence = {
                "current_epoch": json.loads(epoch.evidence_summary_json or "{}"),
                "rejected_history": self._rejected_history(experiment.id),
            }
            instruction = str(
                policy_config.get("prompt_bundle", {}).get(
                    "instruction",
                    "Improve the Skill using the evidence. Return only a structured JSON patch.",
                )
            )
            prompt = (
                f"{instruction}\n\n"
                f"Candidate index: {candidate_index}. Produce an independent, "
                "small-scope approach that is meaningfully different from other candidates.\n"
                f"Skill directory: {skill_root}\n"
                f"Evidence JSON: {canonical_json(evidence)}\n\n"
                "Output schema: "
                '{"rationale":"...",'
                '"operations":[{"op":"replace|insert_after|append|create|delete",'
                '"path":"SKILL.md", "...":"..."}]}'
            )
            try:
                result = create_runner(runner_id).execute(
                    runner_config, workspace, prompt
                )
            except AgentRunnerError as exc:
                return self._record_rejected_candidate(
                    experiment,
                    epoch,
                    candidate_index,
                    rejection_code="optimizer_execution_failed",
                    rejection_message=f"{exc.code}: {exc}",
                )
            try:
                structured_patch = self._parse_patch(result.final_report)
            except AnalystBenchError as exc:
                return self._record_rejected_candidate(
                    experiment,
                    epoch,
                    candidate_index,
                    rejection_code=exc.code,
                    rejection_message=exc.message,
                    raw_output=result.final_report,
                )
        try:
            version, patch_hash = self.patches.apply(
                parent_version_id=epoch.parent_skill_version_id,
                structured_patch=structured_patch,
                created_by=f"optimizer:{policy.id}",
            )
        except AnalystBenchError as exc:
            return self._record_rejected_candidate(
                experiment,
                epoch,
                candidate_index,
                rejection_code=exc.code,
                rejection_message=exc.message,
                structured_patch=structured_patch,
            )
        with transaction(self.session_factory) as session:
            mutation = CandidateMutation(
                id=str(uuid4()),
                epoch_id=epoch.id,
                parent_skill_version_id=epoch.parent_skill_version_id,
                candidate_skill_version_id=version.id,
                candidate_type=f"structured_patch_{candidate_index}",
                structured_patch_json=canonical_json(structured_patch),
                patch_hash=patch_hash,
                rationale=str(structured_patch.get("rationale", "")),
                evidence_refs_json=canonical_json(
                    {
                        "epoch_id": epoch.id,
                        "candidate_index": candidate_index,
                    }
                ),
                status="validated_static",
            )
            session.add(mutation)
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    candidate_mutation_id=mutation.id,
                    event_type="candidate_generated",
                    payload_json=canonical_json(
                        {
                            "candidate_version_id": version.id,
                            "candidate_index": candidate_index,
                            "patch_hash": patch_hash,
                        }
                    ),
                )
            )
            session.flush()
            session.expunge(mutation)
            return mutation

    def _snapshot_inputs(
        self,
        experiment: OptimizationExperiment,
    ) -> tuple[str, list[str], list[str], str]:
        with transaction(self.session_factory) as session:
            snapshot = session.get(
                OptimizationDataSnapshot, experiment.data_snapshot_id
            )
            verifier = session.get(
                VerifierBundleVersion, experiment.verifier_bundle_version_id
            )
            if snapshot is None or verifier is None:
                raise AnalystBenchError(
                    "optimization_snapshot_invalid",
                    "实验数据快照或 Verifier 不存在。",
                )
            case_paths = sorted(json.loads(snapshot.validation_cases_json))
            judge = json.loads(verifier.judge_config_json or "{}")
            judge_runner = str(judge.get("runner") or "claude")
            screening_paths = (
                case_paths
                if snapshot.mode == "development_regression"
                else case_paths[
                    : min(
                        len(case_paths),
                        self.settings.skill_optimization_screening_case_count,
                    )
                ]
            )
            return snapshot.dataset_key, case_paths, screening_paths, judge_runner

    def _ensure_run_groups(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        *,
        split_role: str,
        arm: str,
        version_id: str,
        candidate_mutation_id: str | None,
        repeat_indices: range,
        case_paths: list[str],
    ) -> None:
        dataset_key, _, _, judge_runner = self._snapshot_inputs(experiment)
        variant = self.registry.freeze_variant(
            evaluation_target_id=experiment.evaluation_target_id,
            version_id=version_id,
        )
        for repeat_index in repeat_indices:
            manifest = {
                "dataset_key": dataset_key,
                "case_paths": case_paths,
                "method_id": variant.materialized_method_id,
                "judge_runner": judge_runner,
                "split_role": split_role,
                "arm": arm,
                "repeat_index": repeat_index,
                "version_id": version_id,
            }
            run_hash = content_hash(canonical_json(manifest).encode("utf-8"))
            with transaction(self.session_factory) as session:
                candidate_clause = (
                    OptimizationRunGroup.candidate_mutation_id.is_(None)
                    if candidate_mutation_id is None
                    else OptimizationRunGroup.candidate_mutation_id
                    == candidate_mutation_id
                )
                existing = session.scalar(
                    select(OptimizationRunGroup).where(
                        OptimizationRunGroup.experiment_id == experiment.id,
                        OptimizationRunGroup.epoch_id == epoch.id,
                        candidate_clause,
                        OptimizationRunGroup.split_role == split_role,
                        OptimizationRunGroup.arm == arm,
                        OptimizationRunGroup.repeat_index == repeat_index,
                    )
                )
                if existing is not None:
                    if existing.run_config_hash != run_hash:
                        raise AnalystBenchError(
                            "optimization_run_group_conflict",
                            "恢复运行时发现 Run Group 配置发生变化。",
                        )
                    continue
            submission = self.submissions.create_submission(
                dataset_key=dataset_key,
                method_ids=[variant.materialized_method_id],
                case_paths=case_paths,
                judge_runner=judge_runner,
                purpose="skill_optimization",
                optimization_context={
                    "experiment_id": experiment.id,
                    "epoch_id": epoch.id,
                    "candidate_mutation_id": candidate_mutation_id,
                    "split_role": split_role,
                    "arm": arm,
                    "repeat_index": repeat_index,
                },
            )
            with transaction(self.session_factory) as session:
                session.add(
                    OptimizationRunGroup(
                        id=str(uuid4()),
                        experiment_id=experiment.id,
                        epoch_id=epoch.id,
                        candidate_mutation_id=candidate_mutation_id,
                        split_role=split_role,
                        arm=arm,
                        skill_package_version_id=version_id,
                        repeat_index=repeat_index,
                        evaluation_submission_id=submission.id,
                        status="queued",
                        run_config_hash=run_hash,
                    )
                )

    def _selected_groups(
        self,
        epoch_id: str,
        *,
        split_role: str,
        candidate_mutation_id: str | None,
    ) -> list[OptimizationRunGroup]:
        with transaction(self.session_factory) as session:
            if candidate_mutation_id is None:
                candidate_clause = OptimizationRunGroup.candidate_mutation_id.is_(None)
            else:
                candidate_clause = or_(
                    and_(
                        OptimizationRunGroup.arm == "baseline",
                        OptimizationRunGroup.candidate_mutation_id.is_(None),
                    ),
                    OptimizationRunGroup.candidate_mutation_id
                    == candidate_mutation_id,
                )
            items = list(
                session.scalars(
                    select(OptimizationRunGroup)
                    .where(
                        OptimizationRunGroup.epoch_id == epoch_id,
                        OptimizationRunGroup.split_role == split_role,
                        candidate_clause,
                    )
                    .order_by(
                        OptimizationRunGroup.arm,
                        OptimizationRunGroup.repeat_index,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def _groups_terminal(self, groups: list[OptimizationRunGroup]) -> bool:
        if not groups:
            return False
        with transaction(self.session_factory) as session:
            submissions = {
                item.id: item
                for item in session.scalars(
                    select(EvaluationSubmission).where(
                        EvaluationSubmission.id.in_(
                            [group.evaluation_submission_id for group in groups]
                        )
                    )
                )
            }
            terminal = True
            for group in groups:
                submission = submissions.get(group.evaluation_submission_id)
                if submission is None:
                    raise AnalystBenchError(
                        "optimization_run_group_broken",
                        "Run Group 引用的 Evaluation Submission 不存在。",
                    )
                stored = session.get(OptimizationRunGroup, group.id)
                assert stored is not None
                stored.status = submission.status
                terminal = (
                    terminal
                    and submission.status in TERMINAL_SUBMISSION_STATES
                )
            return terminal

    @staticmethod
    def _token_count(artifact: dict[str, Any]) -> int | None:
        for container in (artifact, artifact.get("usage") or {}):
            if not isinstance(container, dict):
                continue
            for key in ("total_tokens", "token_count", "tokens"):
                value = container.get(key)
                if isinstance(value, (int, float)):
                    return int(value)
        return None

    def _collect_observations(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        *,
        split_role: str,
        candidate_mutation_id: str | None,
    ) -> tuple[list[RunObservation], list[dict[str, Any]]]:
        groups = self._selected_groups(
            epoch.id,
            split_role=split_role,
            candidate_mutation_id=candidate_mutation_id,
        )
        observations: list[RunObservation] = []
        signal_payloads: list[dict[str, Any]] = []
        with transaction(self.session_factory) as session:
            for group in groups:
                case_runs = list(
                    session.scalars(
                        select(EvaluationSubmissionCaseRun).where(
                            EvaluationSubmissionCaseRun.submission_id
                            == group.evaluation_submission_id
                        )
                    )
                )
                for case_run in case_runs:
                    row = session.execute(
                        select(EvaluationSubmissionMethodRun, EvaluationMethod)
                        .join(
                            EvaluationMethod,
                            EvaluationMethod.id
                            == EvaluationSubmissionMethodRun.method_id,
                        )
                        .where(
                            EvaluationSubmissionMethodRun.case_run_id == case_run.id
                        )
                    ).one_or_none()
                    if row is None:
                        continue
                    method_run, method = row
                    artifact = json.loads(method_run.artifact_json or "{}")
                    result_path = Path(case_run.run_directory) / "result.json"
                    report_evidence: dict[str, Any] = {
                        "score": None,
                        "dimensions": {},
                        "failure_tags": [],
                        "metrics": {},
                    }
                    if result_path.is_file():
                        try:
                            result = json.loads(
                                result_path.read_text(encoding="utf-8")
                            )
                            report_evidence = extract_report_evidence(
                                result, method.method_key
                            )
                        except (OSError, json.JSONDecodeError):
                            pass
                    score = report_evidence["score"]
                    succeeded = (
                        method_run.status == "succeeded"
                        and case_run.scoring_status == "succeeded"
                        and score is not None
                    )
                    failure_tags = set(report_evidence["failure_tags"])
                    if not succeeded:
                        failure_tags.add(
                            method_run.error_code or f"execution:{method_run.status}"
                        )
                    parts = Path(case_run.case_path).parts
                    case_family = parts[-2] if len(parts) >= 2 else "unknown"
                    signal = {
                        "case_path": case_run.case_path,
                        "case_family": case_family,
                        "arm": group.arm,
                        "split_role": split_role,
                        "repeat_index": group.repeat_index,
                        "score": score,
                        "duration_ms": method_run.duration_ms,
                        "token_count": self._token_count(artifact),
                        "succeeded": succeeded,
                        "dimensions": report_evidence["dimensions"],
                        "failure_tags": sorted(failure_tags),
                        "metrics": report_evidence["metrics"],
                        "trace_uri": artifact.get("trace_uri"),
                    }
                    observations.append(
                        RunObservation(
                            case_path=case_run.case_path,
                            case_family=case_family,
                            arm=group.arm,
                            repeat_index=group.repeat_index,
                            score=score,
                            duration_ms=method_run.duration_ms,
                            token_count=self._token_count(artifact),
                            succeeded=succeeded,
                            dimensions=report_evidence["dimensions"],
                            failure_tags=tuple(sorted(failure_tags)),
                        )
                    )
                    signal_payloads.append(signal)
                    if not session.scalar(
                        select(OptimizationSignal.id).where(
                            OptimizationSignal.evaluation_method_run_id
                            == method_run.id
                        )
                    ):
                        session.add(
                            OptimizationSignal(
                                id=str(uuid4()),
                                experiment_id=experiment.id,
                                epoch_id=epoch.id,
                                case_path=case_run.case_path,
                                evaluation_method_run_id=method_run.id,
                                run_role=f"{split_role}_{group.arm}",
                                case_family=case_family,
                                score=score,
                                signal_json=canonical_json(signal),
                                signal_hash=content_hash(
                                    canonical_json(signal).encode("utf-8")
                                ),
                            )
                        )
        return observations, signal_payloads

    def _build_epoch_evidence(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
    ) -> None:
        _, signals = self._collect_observations(
            experiment,
            epoch,
            split_role="screening",
            candidate_mutation_id=None,
        )
        baseline_signals = [
            signal for signal in signals if signal.get("arm") == "baseline"
        ]
        summary = build_evidence_summary(baseline_signals)
        with transaction(self.session_factory) as session:
            stored = session.get(OptimizationEpoch, epoch.id)
            assert stored is not None
            stored.evidence_summary_json = canonical_json(summary)
            stored.status = "generating_candidates"
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    event_type="evidence_built",
                    payload_json=canonical_json(summary),
                )
            )

    def _ensure_candidates(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
    ) -> list[CandidateMutation]:
        with transaction(self.session_factory) as session:
            candidates = list(
                session.scalars(
                    select(CandidateMutation)
                    .where(CandidateMutation.epoch_id == epoch.id)
                    .order_by(CandidateMutation.created_at, CandidateMutation.id)
                )
            )
            for candidate in candidates:
                session.expunge(candidate)
        while len(candidates) < self.settings.skill_optimization_candidate_count:
            candidates.append(
                self._generate_candidate(
                    experiment,
                    epoch,
                    len(candidates) + 1,
                )
            )
        return candidates

    def _save_comparison(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        mutation: CandidateMutation,
        *,
        comparison_type: str,
        comparison: dict[str, Any],
        gate: dict[str, Any],
    ) -> None:
        with transaction(self.session_factory) as session:
            item = session.scalar(
                select(CandidateComparison).where(
                    CandidateComparison.candidate_mutation_id == mutation.id,
                    CandidateComparison.comparison_type == comparison_type,
                )
            )
            if item is None:
                item = CandidateComparison(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    candidate_mutation_id=mutation.id,
                    comparison_type=comparison_type,
                    metrics_json="{}",
                )
                session.add(item)
            item.metrics_json = canonical_json(comparison)
            item.gate_result_json = canonical_json(gate)

    def _screen_candidates(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        candidates: list[CandidateMutation],
    ) -> CandidateMutation | None:
        with transaction(self.session_factory) as session:
            verifier = session.get(
                VerifierBundleVersion, experiment.verifier_bundle_version_id
            )
            assert verifier is not None
            screening_policy = json.loads(verifier.gate_policy_json or "{}")
        survivors: list[tuple[float, CandidateMutation]] = []
        for mutation in candidates:
            if (
                mutation.status == "rejected"
                or mutation.candidate_skill_version_id is None
            ):
                continue
            observations, _ = self._collect_observations(
                experiment,
                epoch,
                split_role="screening",
                candidate_mutation_id=mutation.id,
            )
            comparison = compare_paired(observations, bootstrap_samples=0)
            gate = evaluate_screening(
                comparison,
                minimum_delta=float(
                    screening_policy.get("screening_minimum_delta", -1.0)
                ),
                max_latency_growth=float(
                    screening_policy.get(
                        "screening_max_latency_growth",
                        0.50,
                    )
                ),
                critical_dimension_min_delta=float(
                    screening_policy.get(
                        "screening_critical_dimension_min_delta",
                        -5.0,
                    )
                ),
            )
            self._save_comparison(
                experiment,
                epoch,
                mutation,
                comparison_type="screening",
                comparison=comparison,
                gate=gate,
            )
            with transaction(self.session_factory) as session:
                stored = session.get(CandidateMutation, mutation.id)
                assert stored is not None
                if gate["verdict"] == "pass":
                    stored.status = "screening_passed"
                    survivors.append(
                        (float(comparison.get("overall_delta") or 0), mutation)
                    )
                else:
                    stored.status = "rejected"
                    stored.rejection_code = "screening_rejected"
                    stored.rejection_detail_json = canonical_json(gate)
                session.add(
                    OptimizationEvent(
                        id=str(uuid4()),
                        experiment_id=experiment.id,
                        epoch_id=epoch.id,
                        candidate_mutation_id=mutation.id,
                        event_type="candidate_screening_completed",
                        payload_json=canonical_json(gate),
                    )
                )
        if not survivors:
            return None
        survivors.sort(key=lambda item: (-item[0], item[1].id))
        selected = survivors[0][1]
        with transaction(self.session_factory) as session:
            for _, mutation in survivors:
                stored = session.get(CandidateMutation, mutation.id)
                assert stored is not None
                if mutation.id == selected.id:
                    stored.status = "screening_selected"
                else:
                    stored.status = "rejected"
                    stored.rejection_code = "screening_not_selected"
                    stored.rejection_detail_json = canonical_json(
                        {"selected_candidate_mutation_id": selected.id}
                    )
            stored_epoch = session.get(OptimizationEpoch, epoch.id)
            assert stored_epoch is not None
            stored_epoch.status = "full_validating"
        return selected

    def _finalize_validation(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        mutation: CandidateMutation,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        observations, _ = self._collect_observations(
            experiment,
            epoch,
            split_role="validation",
            candidate_mutation_id=mutation.id,
        )
        comparison = compare_paired(observations)
        with transaction(self.session_factory) as session:
            snapshot = session.get(
                OptimizationDataSnapshot, experiment.data_snapshot_id
            )
            verifier = session.get(
                VerifierBundleVersion, experiment.verifier_bundle_version_id
            )
            assert snapshot is not None and verifier is not None
            policy = json.loads(verifier.gate_policy_json or "{}")
        gate = evaluate_gate(
            comparison,
            min_overall_delta=float(
                policy.get(
                    "min_overall_delta",
                    self.settings.skill_optimization_min_overall_delta,
                )
            ),
            minimum_independent_validation_cases=int(
                policy.get(
                    "minimum_independent_validation_cases",
                    self.settings.skill_optimization_minimum_independent_validation_cases,
                )
            ),
            max_latency_growth=float(
                policy.get(
                    "max_latency_growth",
                    self.settings.skill_optimization_max_latency_growth,
                )
            ),
            max_token_growth=float(
                policy.get(
                    "max_token_growth",
                    self.settings.skill_optimization_max_token_growth,
                )
            ),
            mode=snapshot.mode,
            current_repeats=int(comparison.get("repeat_count") or 0),
            max_repeats=self.settings.skill_optimization_max_repeats,
            critical_dimension_min_delta=float(
                policy.get("critical_dimension_min_delta", 0.0)
            ),
            critical_family_max_regression=float(
                policy.get("critical_family_max_regression", -2.0)
            ),
        )
        self._save_comparison(
            experiment,
            epoch,
            mutation,
            comparison_type="paired_repeated_validation",
            comparison=comparison,
            gate=gate,
        )
        with transaction(self.session_factory) as session:
            stored = session.get(CandidateMutation, mutation.id)
            assert stored is not None
            stored.status = {
                "pass": "accepted",
                "reject": "rejected",
                "needs_more_runs": "needs_more_runs",
            }[gate["verdict"]]
            if gate["verdict"] == "reject":
                stored.rejection_code = (
                    gate["reasons"][-1]["code"]
                    if gate.get("reasons")
                    else "gate_rejected"
                )
                stored.rejection_detail_json = canonical_json(gate)
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    candidate_mutation_id=mutation.id,
                    event_type="candidate_gate_decided",
                    payload_json=canonical_json(gate),
                )
            )
        return comparison, gate

    def _create_epoch(
        self,
        experiment: OptimizationExperiment,
        *,
        epoch_number: int,
        parent_version_id: str,
    ) -> OptimizationEpoch:
        epoch = OptimizationEpoch(
            id=str(uuid4()),
            experiment_id=experiment.id,
            epoch_number=epoch_number,
            parent_skill_version_id=parent_version_id,
            status="collecting_evidence",
            evidence_summary_json="{}",
        )
        with transaction(self.session_factory) as session:
            session.add(epoch)
            stored = session.get(OptimizationExperiment, experiment.id)
            assert stored is not None
            stored.current_epoch_number = epoch_number
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    event_type="epoch_started",
                    payload_json=canonical_json(
                        {
                            "epoch_number": epoch_number,
                            "parent_skill_version_id": parent_version_id,
                        }
                    ),
                )
            )
        return epoch

    def _complete_epoch(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        *,
        decision: str,
        best_candidate_version_id: str | None = None,
    ) -> None:
        with transaction(self.session_factory) as session:
            stored_epoch = session.get(OptimizationEpoch, epoch.id)
            stored_experiment = session.get(OptimizationExperiment, experiment.id)
            assert stored_epoch is not None and stored_experiment is not None
            stored_epoch.decision = decision
            stored_epoch.best_candidate_version_id = best_candidate_version_id
            stored_epoch.status = "completed"
            stored_epoch.finished_at = datetime.now(UTC)
            stored_experiment.status = "running"
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    event_type="epoch_completed",
                    payload_json=canonical_json(
                        {
                            "decision": decision,
                            "best_candidate_version_id": best_candidate_version_id,
                        }
                    ),
                )
            )

    def _early_stop_reason(
        self,
        experiment: OptimizationExperiment,
    ) -> str | None:
        with transaction(self.session_factory) as session:
            epochs = list(
                session.scalars(
                    select(OptimizationEpoch)
                    .where(
                        OptimizationEpoch.experiment_id == experiment.id,
                        OptimizationEpoch.status == "completed",
                    )
                    .order_by(OptimizationEpoch.epoch_number.desc())
                )
            )
        if not epochs:
            return None
        if epochs[0].epoch_number >= experiment.max_epochs:
            return "MAX_EPOCHS"
        patience = self.settings.skill_optimization_early_stop_patience
        recent = [item.decision for item in epochs[:patience]]
        if len(recent) >= patience and all(
            item == "no_screening_survivor" for item in recent
        ):
            return "NO_SCREENING_SURVIVOR"
        if len(recent) >= patience and all(item == "retain" for item in recent):
            return "NO_VALIDATION_IMPROVEMENT"
        return None

    def _finish_experiment(
        self,
        experiment_id: str,
        stop_reason: str,
    ) -> None:
        with transaction(self.session_factory) as session:
            stored = session.get(OptimizationExperiment, experiment_id)
            if stored is None or stored.status in {"completed", "failed", "cancelled"}:
                return
            stored.status = "completed"
            stored.stop_reason = stop_reason
            stored.finished_at = datetime.now(UTC)
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment_id,
                    event_type="experiment_completed",
                    payload_json=canonical_json({"stop_reason": stop_reason}),
                )
            )

    def advance(self, experiment_id: str) -> None:
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            experiment = session.get(OptimizationExperiment, experiment_id)
            if experiment is None or experiment.status in {"completed", "failed", "cancelled"}:
                return
            epoch = session.scalar(
                select(OptimizationEpoch)
                .where(OptimizationEpoch.experiment_id == experiment_id)
                .order_by(OptimizationEpoch.epoch_number.desc())
                .limit(1)
            )
            for item in (experiment, epoch):
                if item is not None:
                    session.expunge(item)
        if epoch is None:
            epoch = self._create_epoch(
                experiment,
                epoch_number=1,
                parent_version_id=experiment.base_skill_version_id,
            )
            _, _, screening_paths, _ = self._snapshot_inputs(experiment)
            self._ensure_run_groups(
                experiment,
                epoch,
                split_role="screening",
                arm="baseline",
                version_id=epoch.parent_skill_version_id,
                candidate_mutation_id=None,
                repeat_indices=range(1),
                case_paths=screening_paths,
            )
            self._requeue(experiment.id)
            return
        if epoch.status == "completed":
            stop_reason = self._early_stop_reason(experiment)
            if stop_reason:
                self._finish_experiment(experiment.id, stop_reason)
                return
            epoch = self._create_epoch(
                experiment,
                epoch_number=epoch.epoch_number + 1,
                parent_version_id=(
                    epoch.best_candidate_version_id
                    or epoch.parent_skill_version_id
                ),
            )
            _, _, screening_paths, _ = self._snapshot_inputs(experiment)
            self._ensure_run_groups(
                experiment,
                epoch,
                split_role="screening",
                arm="baseline",
                version_id=epoch.parent_skill_version_id,
                candidate_mutation_id=None,
                repeat_indices=range(1),
                case_paths=screening_paths,
            )
            self._requeue(experiment.id)
            return
        if epoch.status == "collecting_evidence":
            _, _, screening_paths, _ = self._snapshot_inputs(experiment)
            self._ensure_run_groups(
                experiment,
                epoch,
                split_role="screening",
                arm="baseline",
                version_id=epoch.parent_skill_version_id,
                candidate_mutation_id=None,
                repeat_indices=range(1),
                case_paths=screening_paths,
            )
            groups = self._selected_groups(
                epoch.id,
                split_role="screening",
                candidate_mutation_id=None,
            )
            if self._groups_terminal(groups):
                self._build_epoch_evidence(experiment, epoch)
            self._requeue(experiment.id)
            return
        if epoch.status in {"generating", "generating_candidates"}:
            candidates = self._ensure_candidates(experiment, epoch)
            _, _, screening_paths, _ = self._snapshot_inputs(experiment)
            for mutation in candidates:
                if (
                    mutation.status == "rejected"
                    or mutation.candidate_skill_version_id is None
                ):
                    continue
                self._ensure_run_groups(
                    experiment,
                    epoch,
                    split_role="screening",
                    arm="candidate",
                    version_id=mutation.candidate_skill_version_id,
                    candidate_mutation_id=mutation.id,
                    repeat_indices=range(1),
                    case_paths=screening_paths,
                )
            with transaction(self.session_factory) as session:
                stored_epoch = session.get(OptimizationEpoch, epoch.id)
                assert stored_epoch is not None
                stored_epoch.status = "screening"
                for mutation in candidates:
                    stored = session.get(CandidateMutation, mutation.id)
                    assert stored is not None
                    if stored.status != "rejected":
                        stored.status = "screening"
            self._requeue(experiment.id)
            return
        if epoch.status == "screening":
            candidates = self._ensure_candidates(experiment, epoch)
            eligible = [
                mutation
                for mutation in candidates
                if mutation.status != "rejected"
                and mutation.candidate_skill_version_id is not None
            ]
            all_terminal = all(
                self._groups_terminal(
                    self._selected_groups(
                        epoch.id,
                        split_role="screening",
                        candidate_mutation_id=mutation.id,
                    )
                )
                for mutation in eligible
            )
            if not all_terminal:
                self._requeue(experiment.id)
                return
            mutation = self._screen_candidates(experiment, epoch, candidates)
            if mutation is None:
                self._complete_epoch(
                    experiment,
                    epoch,
                    decision="no_screening_survivor",
                )
                self._requeue(experiment.id)
                return
            assert mutation.candidate_skill_version_id
            _, case_paths, _, _ = self._snapshot_inputs(experiment)
            repeats = self.settings.skill_optimization_validation_repeats
            self._ensure_run_groups(
                experiment,
                epoch,
                split_role="validation",
                arm="baseline",
                version_id=epoch.parent_skill_version_id,
                candidate_mutation_id=None,
                repeat_indices=range(repeats),
                case_paths=case_paths,
            )
            self._ensure_run_groups(
                experiment,
                epoch,
                split_role="validation",
                arm="candidate",
                version_id=mutation.candidate_skill_version_id,
                candidate_mutation_id=mutation.id,
                repeat_indices=range(repeats),
                case_paths=case_paths,
            )
            self._requeue(experiment.id)
            return
        if epoch.status not in {"validating", "full_validating"}:
            raise AnalystBenchError(
                "optimization_epoch_state_invalid",
                f"无法恢复 Epoch 状态：{epoch.status}",
            )
        with transaction(self.session_factory) as session:
            mutation = session.scalar(
                select(CandidateMutation)
                .where(
                    CandidateMutation.epoch_id == epoch.id,
                    CandidateMutation.status.in_(
                        {
                            "screening_selected",
                            "validating",
                            "needs_more_runs",
                            "accepted",
                        }
                    ),
                )
                .order_by(CandidateMutation.created_at, CandidateMutation.id)
            )
            if mutation is not None:
                session.expunge(mutation)
        if mutation is None or not mutation.candidate_skill_version_id:
            raise AnalystBenchError(
                "optimization_candidate_missing",
                "完整验证阶段找不到已选候选。",
            )
        groups = self._selected_groups(
            epoch.id,
            split_role="validation",
            candidate_mutation_id=mutation.id,
        )
        if not self._groups_terminal(groups):
            self._requeue(experiment.id)
            return
        comparison, gate = self._finalize_validation(
            experiment,
            epoch,
            mutation,
        )
        if gate["verdict"] == "needs_more_runs":
            requested = next(
                (
                    int(reason["next_repeats"])
                    for reason in gate.get("reasons", [])
                    if reason.get("next_repeats")
                ),
                min(
                    self.settings.skill_optimization_max_repeats,
                    5
                    if int(comparison.get("repeat_count") or 0) < 5
                    else 7,
                ),
            )
            _, case_paths, _, _ = self._snapshot_inputs(experiment)
            self._ensure_run_groups(
                experiment,
                epoch,
                split_role="validation",
                arm="baseline",
                version_id=epoch.parent_skill_version_id,
                candidate_mutation_id=None,
                repeat_indices=range(requested),
                case_paths=case_paths,
            )
            self._ensure_run_groups(
                experiment,
                epoch,
                split_role="validation",
                arm="candidate",
                version_id=mutation.candidate_skill_version_id,
                candidate_mutation_id=mutation.id,
                repeat_indices=range(requested),
                case_paths=case_paths,
            )
            with transaction(self.session_factory) as session:
                stored = session.get(CandidateMutation, mutation.id)
                assert stored is not None
                stored.status = "validating"
            self._requeue(experiment.id)
            return
        if gate["verdict"] == "pass":
            with transaction(self.session_factory) as session:
                binding = session.scalar(
                    select(SkillTargetBinding).where(
                        SkillTargetBinding.skill_id == experiment.skill_id,
                        SkillTargetBinding.evaluation_target_id
                        == experiment.evaluation_target_id,
                    )
                )
                assert binding is not None
                lock_version = binding.lock_version
            assert mutation.candidate_skill_version_id
            self.promotions.promote(
                experiment_id=experiment.id,
                epoch_id=epoch.id,
                candidate_mutation_id=mutation.id,
                skill_id=experiment.skill_id,
                evaluation_target_id=experiment.evaluation_target_id,
                version_id=mutation.candidate_skill_version_id,
                gate_result=gate,
                expected_lock_version=lock_version,
                evidence={"comparison_type": "paired_repeated_validation"},
            )
        self._complete_epoch(
            experiment,
            epoch,
            decision="promote" if gate["verdict"] == "pass" else "retain",
            best_candidate_version_id=(
                mutation.candidate_skill_version_id
                if gate["verdict"] == "pass"
                else None
            ),
        )
        self._requeue(experiment.id)

    def _requeue(self, experiment_id: str) -> None:
        with transaction(self.session_factory) as session:
            self.jobs.enqueue(
                session, "skill_optimization_advance", {"experiment_id": experiment_id}
            )

    def list_experiments(self) -> list[OptimizationExperiment]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(OptimizationExperiment).order_by(
                        OptimizationExperiment.created_at.desc(),
                        OptimizationExperiment.id,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_policies(self) -> list[OptimizerPolicyVersion]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(OptimizerPolicyVersion).order_by(
                        OptimizerPolicyVersion.created_at.desc(),
                        OptimizerPolicyVersion.id,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_verifiers(self) -> list[VerifierBundleVersion]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(VerifierBundleVersion).order_by(
                        VerifierBundleVersion.created_at.desc(),
                        VerifierBundleVersion.id,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_snapshots(self) -> list[OptimizationDataSnapshot]:
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(OptimizationDataSnapshot).order_by(
                        OptimizationDataSnapshot.created_at.desc(),
                        OptimizationDataSnapshot.id,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def detail(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.get(experiment_id)
        with transaction(self.session_factory) as session:
            epochs = list(
                session.scalars(
                    select(OptimizationEpoch)
                    .where(OptimizationEpoch.experiment_id == experiment_id)
                    .order_by(OptimizationEpoch.epoch_number)
                )
            )
            epoch_ids = [item.id for item in epochs]
            candidates = (
                list(
                    session.scalars(
                        select(CandidateMutation)
                        .where(CandidateMutation.epoch_id.in_(epoch_ids))
                        .order_by(CandidateMutation.created_at, CandidateMutation.id)
                    )
                )
                if epoch_ids
                else []
            )
            comparisons = (
                list(
                    session.scalars(
                        select(CandidateComparison)
                        .where(CandidateComparison.epoch_id.in_(epoch_ids))
                        .order_by(CandidateComparison.created_at)
                    )
                )
                if epoch_ids
                else []
            )
            groups = (
                list(
                    session.scalars(
                        select(OptimizationRunGroup)
                        .where(OptimizationRunGroup.epoch_id.in_(epoch_ids))
                        .order_by(
                            OptimizationRunGroup.created_at,
                            OptimizationRunGroup.id,
                        )
                    )
                )
                if epoch_ids
                else []
            )
        comparison_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for item in comparisons:
            comparison_by_candidate.setdefault(item.candidate_mutation_id, []).append(
                {
                    "id": item.id,
                    "type": item.comparison_type,
                    "metrics": json.loads(item.metrics_json or "{}"),
                    "gate": json.loads(item.gate_result_json or "{}"),
                    "created_at": item.created_at,
                }
            )
        groups_by_epoch: dict[str, list[OptimizationRunGroup]] = {}
        for item in groups:
            assert item.epoch_id is not None
            groups_by_epoch.setdefault(item.epoch_id, []).append(item)
        candidates_by_epoch: dict[str, list[CandidateMutation]] = {}
        for item in candidates:
            candidates_by_epoch.setdefault(item.epoch_id, []).append(item)
        return {
            "experiment": experiment,
            "epochs": [
                {
                    "id": epoch.id,
                    "number": epoch.epoch_number,
                    "status": epoch.status,
                    "parent_skill_version_id": epoch.parent_skill_version_id,
                    "best_candidate_version_id": epoch.best_candidate_version_id,
                    "decision": epoch.decision,
                    "evidence": json.loads(epoch.evidence_summary_json or "{}"),
                    "finished_at": epoch.finished_at,
                    "candidates": [
                        {
                            "id": candidate.id,
                            "candidate_skill_version_id": (
                                candidate.candidate_skill_version_id
                            ),
                            "candidate_type": candidate.candidate_type,
                            "patch": json.loads(
                                candidate.structured_patch_json or "{}"
                            ),
                            "patch_hash": candidate.patch_hash,
                            "rationale": candidate.rationale,
                            "status": candidate.status,
                            "rejection_code": candidate.rejection_code,
                            "rejection_detail": json.loads(
                                candidate.rejection_detail_json or "{}"
                            ),
                            "comparisons": comparison_by_candidate.get(
                                candidate.id, []
                            ),
                        }
                        for candidate in candidates_by_epoch.get(epoch.id, [])
                    ],
                    "run_groups": [
                        {
                            "id": group.id,
                            "candidate_mutation_id": group.candidate_mutation_id,
                            "split_role": group.split_role,
                            "arm": group.arm,
                            "repeat_index": group.repeat_index,
                            "status": group.status,
                            "evaluation_submission_id": (
                                group.evaluation_submission_id
                            ),
                            "skill_package_version_id": (
                                group.skill_package_version_id
                            ),
                        }
                        for group in groups_by_epoch.get(epoch.id, [])
                    ],
                }
                for epoch in epochs
            ],
        }

    def candidate_detail(self, candidate_id: str) -> dict[str, Any]:
        with transaction(self.session_factory) as session:
            candidate = session.get(CandidateMutation, candidate_id)
            if candidate is None:
                raise AnalystBenchError(
                    "optimization_candidate_not_found",
                    "找不到候选版本。",
                    status_code=404,
                )
            epoch = session.get(OptimizationEpoch, candidate.epoch_id)
            assert epoch is not None
            comparisons = list(
                session.scalars(
                    select(CandidateComparison)
                    .where(
                        CandidateComparison.candidate_mutation_id == candidate_id
                    )
                    .order_by(CandidateComparison.created_at)
                )
            )
            values = {
                "id": candidate.id,
                "experiment_id": epoch.experiment_id,
                "epoch_id": epoch.id,
                "parent_skill_version_id": candidate.parent_skill_version_id,
                "candidate_skill_version_id": candidate.candidate_skill_version_id,
                "candidate_type": candidate.candidate_type,
                "patch": json.loads(candidate.structured_patch_json or "{}"),
                "patch_hash": candidate.patch_hash,
                "rationale": candidate.rationale,
                "status": candidate.status,
                "rejection_code": candidate.rejection_code,
                "rejection_detail": json.loads(
                    candidate.rejection_detail_json or "{}"
                ),
                "comparisons": [
                    {
                        "type": item.comparison_type,
                        "metrics": json.loads(item.metrics_json or "{}"),
                        "gate": json.loads(item.gate_result_json or "{}"),
                    }
                    for item in comparisons
                ],
            }
        candidate_version_id = values["candidate_skill_version_id"]
        values["diff"] = (
            self.registry.diff_versions(
                str(values["parent_skill_version_id"]),
                str(candidate_version_id),
            )
            if candidate_version_id
            else ""
        )
        return values

    def get(self, experiment_id: str) -> OptimizationExperiment:
        with transaction(self.session_factory) as session:
            item = session.get(OptimizationExperiment, experiment_id)
            if item is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found", "找不到实验。", status_code=404
                )
            session.expunge(item)
            return item

    def resume(self, experiment_id: str) -> OptimizationExperiment:
        with transaction(self.session_factory) as session:
            item = session.get(OptimizationExperiment, experiment_id)
            if item is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found",
                    "找不到实验。",
                    status_code=404,
                )
            if item.status in {"completed", "cancelled"}:
                raise AnalystBenchError(
                    "optimization_experiment_state_invalid",
                    "已完成或已取消的实验不能恢复。",
                )
            item.status = "running"
            item.stop_reason = None
            item.error_json = "{}"
            self.jobs.enqueue(
                session,
                "skill_optimization_advance",
                {"experiment_id": experiment_id},
            )
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment_id,
                    event_type="experiment_resumed",
                    payload_json="{}",
                )
            )
            session.flush()
            session.expunge(item)
            return item

    def cancel(self, experiment_id: str) -> OptimizationExperiment:
        with transaction(self.session_factory) as session:
            item = session.get(OptimizationExperiment, experiment_id)
            if item is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found", "找不到实验。", status_code=404
                )
            if item.status in {"completed", "failed", "cancelled"}:
                session.expunge(item)
                return item
            submission_ids = list(
                session.scalars(
                    select(OptimizationRunGroup.evaluation_submission_id).where(
                        OptimizationRunGroup.experiment_id == experiment_id
                    )
                )
            )
            item.status = "cancelled"
            item.stop_reason = "user_cancelled"
            item.finished_at = datetime.now(UTC)
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=item.id,
                    event_type="experiment_cancelled",
                    payload_json="{}",
                )
            )
        for submission_id in submission_ids:
            try:
                self.submissions.cancel_submission(submission_id)
            except AnalystBenchError:
                # Cancellation is idempotent; already terminal submissions are
                # retained as immutable evidence.
                pass
        return self.get(experiment_id)

    def fail(self, experiment_id: str, error: Exception) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(OptimizationExperiment, experiment_id)
            if item is None or item.status in {"completed", "cancelled"}:
                return
            item.status = "failed"
            item.stop_reason = "optimizer_error"
            item.error_json = canonical_json(
                {
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
            item.finished_at = datetime.now(UTC)
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=item.id,
                    event_type="experiment_failed",
                    payload_json=item.error_json,
                )
            )

    def events(self, experiment_id: str) -> list[OptimizationEvent]:
        self.get(experiment_id)
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    select(OptimizationEvent)
                    .where(OptimizationEvent.experiment_id == experiment_id)
                    .order_by(OptimizationEvent.created_at, OptimizationEvent.id)
                )
            )
            for item in items:
                session.expunge(item)
            return items
