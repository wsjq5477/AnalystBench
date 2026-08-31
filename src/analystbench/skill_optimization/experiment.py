"""Durable multi-candidate Skill optimization state machine."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy import delete as sql_delete
from sqlalchemy.orm import Session, sessionmaker

from analystbench.config import Settings
from analystbench.db.models import (
    CandidateComparison,
    CandidateMutation,
    DecisionRecord,
    EvaluationMethod,
    EvaluationSubmission,
    EvaluationSubmissionCaseRun,
    EvaluationSubmissionMethodRun,
    ExecutionProfile,
    Job,
    OptimizationDataSnapshot,
    OptimizationEpoch,
    OptimizationEvent,
    OptimizationExperiment,
    OptimizationRunGroup,
    OptimizationSignal,
    OptimizerPolicyVersion,
    SkillBindingHistory,
    SkillPackageVersion,
    SkillTargetBinding,
    VerifierBundleVersion,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.evaluation.submission import EvaluationSubmissionService
from analystbench.execution.runner import AgentRunnerError, create_runner
from analystbench.runtime.jobs import JobQueue
from analystbench.skill_optimization.evidence import (
    build_evidence_summary,
    extract_report_evidence,
)
from analystbench.skill_optimization.gate import evaluate_gate, evaluate_screening
from analystbench.skill_optimization.patch import StructuredPatchApplier
from analystbench.skill_optimization.promotion import PromotionService
from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.skill_optimization.reporting import build_optimization_ledger
from analystbench.skill_optimization.snapshot import (
    build_snapshot_manifest,
    verify_snapshot_manifest,
)
from analystbench.skill_optimization.static_validation import StaticSkillValidator
from analystbench.skill_optimization.statistics import RunObservation, compare_paired
from analystbench.storage.content import canonical_json, content_hash

TERMINAL_SUBMISSION_STATES = {"completed", "completed_with_errors", "failed", "cancelled"}

OPTIMIZER_OUTPUT_SCHEMA_VERSION = "structured_skill_patch.v1"
EXPERIMENT_POLICY_LIMITS = {
    "candidate_count": {"minimum": 1, "maximum": 4},
    "screening_case_count": {"minimum": 1, "maximum": 1000},
    "validation_repeats": {"minimum": 1, "maximum": 7},
    "max_repeats": {"minimum": 1, "maximum": 15},
    "early_stop_patience": {"minimum": 1, "maximum": 20},
}
OPTIMIZER_ROLE_SPECS: tuple[dict[str, str], ...] = (
    {
        "role": "failure_analyst",
        "prompt_version": "skill_optimizer.failure_analyst.v1",
        "mission": (
            "Find recurring failures, low-scoring dimensions, and failure-tag clusters; "
            "propose small corrective patches."
        ),
    },
    {
        "role": "success_analyst",
        "prompt_version": "skill_optimizer.success_analyst.v1",
        "mission": (
            "Find repeatable success patterns and protected behaviors; propose patches "
            "that preserve or generalize them."
        ),
    },
    {
        "role": "generalization_analyst",
        "prompt_version": "skill_optimizer.generalization_analyst.v1",
        "mission": (
            "Find narrow or brittle instructions and propose general rules grounded only "
            "in the supplied Train evidence."
        ),
    },
    {
        "role": "simplification_analyst",
        "prompt_version": "skill_optimizer.simplification_analyst.v1",
        "mission": (
            "Find redundant or conflicting instructions and propose the smallest safe "
            "simplification while preserving successful behavior."
        ),
    },
)

STRUCTURED_PATCH_SCHEMA_TEXT = (
    '{"rationale":"...","intent":{"change_type":"corrective|simplification|'
    'evidence_strengthening|tool_enhancement","target_failure_families":["..."],'
    '"target_dimensions":["..."],"target_failure_tags":["..."],'
    '"protected_behaviors":["..."]},"operations":['
    '{"op":"replace","path":"SKILL.md","old":"exact unique text",'
    '"new":"replacement"}|'
    '{"op":"insert_after","path":"SKILL.md","anchor":"exact unique text",'
    '"content":"inserted text"}|'
    '{"op":"append","path":"SKILL.md","content":"appended text"}|'
    '{"op":"delete","path":"obsolete-file.md"}]}'
)


class _OptimizerJSONDecodeError(ValueError):
    pass


def _decode_optimizer_json(value: str) -> dict[str, Any]:
    stripped = value.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    candidate = fenced.group(1) if fenced else stripped
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise _OptimizerJSONDecodeError("Optimizer output is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise AnalystBenchError(
            "optimizer_output_invalid", "Optimizer JSON 顶层必须是对象。"
        )
    return parsed


def _normalize_patch(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed_top_level = {"rationale", "intent", "operations"}
    unexpected_top_level = sorted(set(value) - allowed_top_level)
    if unexpected_top_level:
        raise AnalystBenchError(
            "optimizer_output_invalid",
            "Optimizer Patch 包含 schema 外字段。",
            [{"unexpected_fields": unexpected_top_level}],
        )
    rationale = value.get("rationale", "")
    if not isinstance(rationale, str):
        raise AnalystBenchError(
            "optimizer_output_invalid", "Optimizer Patch rationale 必须是字符串。"
        )
    raw_intent = value.get("intent", {})
    if not isinstance(raw_intent, dict):
        raise AnalystBenchError(
            "optimizer_output_invalid", "Optimizer Patch intent 必须是对象。"
        )
    allowed_intent = {
        "change_type",
        "target_failure_families",
        "target_dimensions",
        "target_failure_tags",
        "protected_behaviors",
    }
    unexpected_intent = sorted(set(raw_intent) - allowed_intent)
    if unexpected_intent:
        raise AnalystBenchError(
            "optimizer_output_invalid",
            "Optimizer Patch intent 包含 schema 外字段。",
            [{"unexpected_fields": unexpected_intent}],
        )
    normalized_intent: dict[str, Any] = {}
    if "change_type" in raw_intent:
        if not isinstance(raw_intent["change_type"], str) or raw_intent[
            "change_type"
        ] not in (
            "corrective",
            "simplification",
            "evidence_strengthening",
            "tool_enhancement",
        ):
            raise AnalystBenchError(
                "optimizer_output_invalid", "intent.change_type 不属于 schema 枚举。"
            )
        normalized_intent["change_type"] = raw_intent["change_type"]
    for key in sorted(allowed_intent - {"change_type"}):
        if key not in raw_intent:
            continue
        items = raw_intent[key]
        if not isinstance(items, list) or any(
            not isinstance(item, str) for item in items
        ):
            raise AnalystBenchError(
                "optimizer_output_invalid", f"intent.{key} 必须是字符串数组。"
            )
        normalized_intent[key] = items

    operations = value.get("operations")
    if not isinstance(operations, list) or not operations:
        raise AnalystBenchError(
            "optimizer_output_invalid", "Optimizer JSON 缺少 operations。"
        )
    operation_fields = {
        "replace": {"op", "path", "old", "new"},
        "insert_after": {"op", "path", "anchor", "content"},
        "append": {"op", "path", "content"},
        "delete": {"op", "path"},
    }
    normalized_operations: list[dict[str, str]] = []
    for operation_index, raw_operation in enumerate(operations):
        if not isinstance(raw_operation, dict):
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "Optimizer Patch operation 必须是对象。",
                [{"operation_index": operation_index}],
            )
        if "old_text" in raw_operation:
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "replace 只接受精确 old/new，不接受易漂移的 old_text。",
                [{"operation_index": operation_index}],
            )
        operation = raw_operation.get("op")
        if operation not in operation_fields:
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "Optimizer Patch operation 类型无效。",
                [{"operation_index": operation_index, "operation": operation}],
            )
        expected_fields = operation_fields[str(operation)]
        if set(raw_operation) != expected_fields:
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "Optimizer Patch operation 字段与 schema 不匹配。",
                [
                    {
                        "operation_index": operation_index,
                        "expected_fields": sorted(expected_fields),
                        "observed_fields": sorted(raw_operation),
                    }
                ],
            )
        normalized_operation: dict[str, str] = {}
        for key in sorted(expected_fields):
            item = raw_operation[key]
            if not isinstance(item, str):
                raise AnalystBenchError(
                    "optimizer_output_invalid",
                    "Optimizer Patch operation 字段必须是字符串。",
                    [{"operation_index": operation_index, "field": key}],
                )
            if key in {"op", "path", "old", "anchor"} and not item:
                raise AnalystBenchError(
                    "optimizer_output_invalid",
                    "Optimizer Patch 的操作类型、路径和锚点不能为空。",
                    [{"operation_index": operation_index, "field": key}],
                )
            normalized_operation[key] = item
        normalized_operations.append(normalized_operation)

    normalized: dict[str, Any] = {"rationale": rationale}
    if normalized_intent:
        normalized["intent"] = normalized_intent
    normalized["operations"] = normalized_operations
    return normalized


def _parse_role_output(
    parsed: Mapping[str, Any],
    *,
    role: str,
    prompt_version: str,
    candidate_count: int,
) -> dict[str, Any]:
    # Compatibility for existing test and local runners that return one patch
    # directly instead of the role envelope introduced in V1.
    if "operations" in parsed:
        return {
            "role": role,
            "prompt_version": prompt_version,
            "findings": [],
            "patches": [_normalize_patch(parsed)],
            "legacy_output": True,
        }
    unexpected_envelope_fields = sorted(
        set(parsed) - {"role", "prompt_version", "findings", "patches"}
    )
    if unexpected_envelope_fields:
        raise AnalystBenchError(
            "optimizer_output_invalid",
            "Optimizer role envelope 包含 schema 外字段。",
            [{"unexpected_fields": unexpected_envelope_fields}],
        )
    if parsed.get("role") != role or parsed.get("prompt_version") != prompt_version:
        raise AnalystBenchError(
            "optimizer_output_invalid",
            "Optimizer role 或 prompt_version 与请求不一致。",
        )
    findings = parsed.get("findings", [])
    patches = parsed.get("patches")
    if not isinstance(findings, list) or not isinstance(patches, list):
        raise AnalystBenchError(
            "optimizer_output_invalid",
            "Optimizer role 输出必须包含 findings 和 patches 数组。",
        )
    if any(not isinstance(item, dict) for item in patches[:candidate_count]):
        raise AnalystBenchError(
            "optimizer_output_invalid",
            "Optimizer role 的每个 patch 都必须是对象。",
        )
    normalized_findings: list[dict[str, Any]] = []
    for finding_index, finding in enumerate(findings):
        if not isinstance(finding, dict) or set(finding) - {
            "summary",
            "evidence_refs",
            "confidence",
        }:
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "Optimizer finding 字段与 schema 不匹配。",
                [{"finding_index": finding_index}],
            )
        summary = finding.get("summary", "")
        evidence_refs = finding.get("evidence_refs", [])
        confidence = finding.get("confidence", 0.0)
        if (
            not isinstance(summary, str)
            or not isinstance(evidence_refs, list)
            or any(not isinstance(item, str) for item in evidence_refs)
            or isinstance(confidence, bool)
        ):
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "Optimizer finding 类型与 schema 不匹配。",
                [{"finding_index": finding_index}],
            )
        try:
            confidence_number = float(confidence)
        except (TypeError, ValueError) as exc:
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "Optimizer finding confidence 必须是 0 到 1。",
                [{"finding_index": finding_index}],
            ) from exc
        if not 0.0 <= confidence_number <= 1.0:
            raise AnalystBenchError(
                "optimizer_output_invalid",
                "Optimizer finding confidence 必须是 0 到 1。",
                [{"finding_index": finding_index}],
            )
        normalized_findings.append(
            {
                "summary": summary,
                "evidence_refs": evidence_refs,
                "confidence": confidence_number,
            }
        )
    return {
        "role": role,
        "prompt_version": prompt_version,
        "findings": normalized_findings,
        "patches": [
            _normalize_patch(item)
            for item in patches[:candidate_count]
        ],
        "legacy_output": False,
    }


def _merge_role_patches(
    role_outputs: list[dict[str, Any]], candidate_count: int
) -> list[dict[str, Any]]:
    """Deterministically round-robin unique proposals across fixed role order."""

    selected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    maximum_patches = max(
        (len(output.get("patches") or []) for output in role_outputs), default=0
    )
    for patch_index in range(maximum_patches):
        for output in role_outputs:
            patches = output.get("patches") or []
            if patch_index >= len(patches):
                continue
            patch = patches[patch_index]
            patch_hash = content_hash(canonical_json(patch).encode("utf-8"))
            if patch_hash in seen_hashes:
                continue
            seen_hashes.add(patch_hash)
            selected.append(
                {
                    "patch": patch,
                    "patch_hash": patch_hash,
                    "role": output["role"],
                    "prompt_version": output["prompt_version"],
                    "role_patch_index": patch_index,
                }
            )
            if len(selected) >= candidate_count:
                return selected
    return selected


class OptimizationExperimentService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        registry: SkillRegistryService,
        submissions: EvaluationSubmissionService,
        optimizer_backoff: Callable[[float], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.registry = registry
        self.submissions = submissions
        self.patches = StructuredPatchApplier(registry)
        self.static_validator = StaticSkillValidator()
        self.promotions = PromotionService(session_factory)
        self.jobs = JobQueue(session_factory)
        self._optimizer_backoff = optimizer_backoff or time.sleep

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
            if profile.runner != "claude":
                raise AnalystBenchError(
                    "optimizer_policy_invalid",
                    "Skill 自优化 Optimizer 公开契约只支持 claude。",
                )
            try:
                profile_config = json.loads(profile.configuration_json or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise AnalystBenchError(
                    "optimizer_policy_invalid", "Optimizer 执行配置无法解析。"
                ) from exc
            if not isinstance(profile_config, dict):
                raise AnalystBenchError(
                    "optimizer_policy_invalid", "Optimizer 执行配置必须是对象。"
                )
            allowed_tools = profile_config.get(
                "allowed_tools", ["Read", "Grep", "Glob"]
            )
            if (
                not isinstance(allowed_tools, list)
                or not allowed_tools
                or not all(isinstance(tool, str) for tool in allowed_tools)
                or not set(allowed_tools).issubset({"Read", "Grep", "Glob"})
            ):
                raise AnalystBenchError(
                    "optimizer_policy_unsafe_tools",
                    "Optimizer 只允许 Read/Grep/Glob，不能启用 Bash/Write。",
                )
            extra_args = profile_config.get("extra_args", [])
            if not isinstance(extra_args, list) or not all(
                isinstance(argument, str) for argument in extra_args
            ):
                raise AnalystBenchError(
                    "optimizer_policy_unsafe_arguments",
                    "Optimizer extra_args 必须是字符串数组。",
                )
            forbidden_args = {
                "--dangerously-skip-permissions",
                "--permission-mode",
                "--allowedTools",
                "--add-dir",
            }
            if any(
                argument.split("=", 1)[0] in forbidden_args
                for argument in extra_args
            ):
                raise AnalystBenchError(
                    "optimizer_policy_unsafe_arguments",
                    "Optimizer 执行参数不能改写工具权限或增加可读目录。",
                )
            resolved_config = dict(config or {})
            available_roles = {item["role"] for item in OPTIMIZER_ROLE_SPECS}
            optimizer_roles = resolved_config.get(
                "optimizer_roles", [item["role"] for item in OPTIMIZER_ROLE_SPECS]
            )
            if (
                not isinstance(optimizer_roles, list)
                or not optimizer_roles
                or any(not isinstance(role, str) for role in optimizer_roles)
                or not set(optimizer_roles).issubset(available_roles)
            ):
                raise AnalystBenchError(
                    "optimizer_policy_invalid",
                    "optimizer_roles 必须是公开角色的非空数组。",
                )
            attempts = resolved_config.get("optimizer_execution_attempts", 3)
            if type(attempts) is not int or not 1 <= attempts <= 5:
                raise AnalystBenchError(
                    "optimizer_policy_invalid",
                    "optimizer_execution_attempts 必须介于 1 和 5。",
                )
            repair_enabled = resolved_config.get("format_repair_enabled", True)
            if not isinstance(repair_enabled, bool):
                raise AnalystBenchError(
                    "optimizer_policy_invalid",
                    "format_repair_enabled 必须是布尔值。",
                )
            resolved_config["optimizer_roles"] = list(dict.fromkeys(optimizer_roles))
            resolved_config["optimizer_execution_attempts"] = attempts
            resolved_config["format_repair_enabled"] = repair_enabled
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
                "config": resolved_config,
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
                    {"prompt_bundle": prompt_bundle, **resolved_config}
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
        resolved_static = dict(static_policy or {})
        nested_static = resolved_static.get("static_validation", resolved_static)
        if not isinstance(nested_static, dict):
            raise AnalystBenchError(
                "optimization_verifier_invalid", "static_policy 必须是对象。"
            )
        for required_check in (
            "content_security_scan",
            "case_leak_scan",
            "referenced_file_check",
            "script_syntax",
        ):
            check = nested_static.get(required_check, {})
            disabled = check is False or (
                isinstance(check, dict) and check.get("enabled") is False
            )
            if disabled:
                raise AnalystBenchError(
                    "optimization_verifier_critical_check_disabled",
                    f"Verifier 不能关闭关键静态检查 {required_check}。",
                )
        resolved_judge = dict(judge_config or {})
        judge_runner = str(resolved_judge.get("runner") or "claude")
        if judge_runner not in {"claude", "lexical"}:
            raise AnalystBenchError(
                "optimization_verifier_runner_unsupported",
                "Skill 自优化 Verifier 只支持 claude；lexical 仅用于开发调试。",
            )
        resolved_judge["runner"] = judge_runner
        judge_timeout = resolved_judge.get("timeout_seconds", 600)
        judge_output_limit = resolved_judge.get("max_output_bytes", 2 * 1024 * 1024)
        if type(judge_timeout) is not int or not 1 <= judge_timeout <= 7200:
            raise AnalystBenchError(
                "optimization_verifier_judge_invalid",
                "Judge timeout_seconds 必须介于 1 和 7200。",
            )
        if (
            type(judge_output_limit) is not int
            or not 1024 <= judge_output_limit <= 100 * 1024 * 1024
        ):
            raise AnalystBenchError(
                "optimization_verifier_judge_invalid",
                "Judge max_output_bytes 必须介于 1024 和 104857600。",
            )
        resolved_judge["timeout_seconds"] = judge_timeout
        resolved_judge["max_output_bytes"] = judge_output_limit
        resolved_gate = dict(gate_policy or {})
        bootstrap_samples = int(resolved_gate.get("bootstrap_samples", 2000))
        confidence = float(resolved_gate.get("bootstrap_confidence", 0.95))
        win_probability = float(
            resolved_gate.get("min_candidate_win_probability", 0.0)
        )
        if (
            not 0 <= bootstrap_samples <= 100_000
            or not 0.5 < confidence < 1
            or not 0 <= win_probability <= 1
        ):
            raise AnalystBenchError(
                "optimization_verifier_statistics_invalid",
                "Verifier Bootstrap/Gate 统计配置超出可用范围。",
            )
        for field in (
            "require_bootstrap_lower_bound_positive",
            "require_token_usage",
            "reject_failure_increase",
            "reject_new_failure_tags",
        ):
            if field in resolved_gate and not isinstance(resolved_gate[field], bool):
                raise AnalystBenchError(
                    "optimization_verifier_gate_invalid",
                    f"Verifier {field} 必须是布尔值。",
                )
        for field in ("critical_dimensions", "protected_guardrail_metrics"):
            value = resolved_gate.get(field)
            if value is not None and (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item for item in value)
            ):
                raise AnalystBenchError(
                    "optimization_verifier_gate_invalid",
                    f"Verifier {field} 必须是非空字符串数组。",
                )
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
                "static_policy": resolved_static,
                "gate_policy": resolved_gate,
                "judge_config": resolved_judge,
            }
            item = VerifierBundleVersion(
                id=str(uuid4()),
                bundle_key=bundle_key,
                version_number=version_number,
                static_policy_json=canonical_json(resolved_static),
                gate_policy_json=canonical_json(resolved_gate),
                judge_config_json=canonical_json(resolved_judge),
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
        train = list(dict.fromkeys(train_case_paths or []))
        validation = list(dict.fromkeys(validation_case_paths))
        hidden = list(dict.fromkeys(hidden_test_case_paths or []))
        prospective = list(dict.fromkeys(prospective_holdout_case_paths or []))
        if not validation:
            raise AnalystBenchError(
                "optimization_snapshot_invalid", "至少需要一个验证 Case。"
            )
        if mode == "independent_validation":
            if not train:
                raise AnalystBenchError(
                    "optimization_train_cases_missing",
                    "独立验证模式至少需要一个 Train Case。",
                )
            required = self.settings.skill_optimization_minimum_independent_validation_cases
            if len(validation) < required:
                raise AnalystBenchError(
                    "optimization_validation_cases_insufficient",
                    "独立验证 Case 数不足，不能产生 validated 结果。",
                    [{"observed": len(validation), "required": required}],
                )
        elif train:
            raise AnalystBenchError(
                "optimization_snapshot_invalid",
                "development_regression 模式不单独接受 Train Case。",
            )
        manifest = build_snapshot_manifest(
            self.settings,
            dataset_key=dataset_key,
            mode=mode,
            train_cases=train,
            validation_cases=validation,
            hidden_test_cases=hidden,
            prospective_holdout_cases=prospective,
        )
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
                train_cases_json=canonical_json(train),
                validation_cases_json=canonical_json(validation),
                hidden_test_cases_json=canonical_json(hidden),
                prospective_holdout_cases_json=canonical_json(prospective),
                case_input_hashes_json=canonical_json(manifest["case_input_hashes"]),
                eval_spec_hashes_json=canonical_json(manifest["eval_spec_hashes"]),
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
        candidate_count: int | None = None,
        screening_case_count: int | None = None,
        validation_repeats: int | None = None,
        max_repeats: int | None = None,
        early_stop_patience: int | None = None,
        created_by: str | None = None,
    ) -> OptimizationExperiment:
        self.registry._require_enabled()
        base = self.registry.get_version(base_skill_version_id)
        if base.skill_id != skill_id:
            raise AnalystBenchError(
                "optimization_experiment_invalid", "基线版本不属于指定 Skill。"
            )
        binding = self.registry.find_binding(
            skill_id=skill_id, evaluation_target_id=evaluation_target_id
        )
        if binding is not None and binding.active_version_id != base_skill_version_id:
            raise AnalystBenchError(
                "optimization_base_not_active",
                "新实验必须从当前 Active Skill 版本开始；请先显式回滚。",
                [
                    {
                        "active_version_id": binding.active_version_id,
                        "requested_version_id": base_skill_version_id,
                    }
                ],
            )
        self.registry.freeze_variant(
            evaluation_target_id=evaluation_target_id,
            version_id=base_skill_version_id,
        )
        with transaction(self.session_factory) as session:
            snapshot = session.get(OptimizationDataSnapshot, data_snapshot_id)
            policy = session.get(OptimizerPolicyVersion, optimizer_policy_version_id)
            verifier = session.get(VerifierBundleVersion, verifier_bundle_version_id)
            if snapshot is None or policy is None or verifier is None:
                raise AnalystBenchError(
                    "optimization_experiment_invalid",
                    "数据快照、Optimizer Policy 或 Verifier 不存在。",
                )
            epochs = max_epochs or self.settings.skill_optimization_max_epochs
            if not 1 <= epochs <= self.settings.skill_optimization_max_epochs:
                raise AnalystBenchError(
                    "optimization_experiment_invalid", "实验 Epoch 数超过系统上限。"
                )
            if snapshot.mode == "independent_validation" and epochs != 1:
                raise AnalystBenchError(
                    "optimization_independent_validation_epoch_limit",
                    "独立验证模式只能运行一个 Epoch，避免通过晋升结果反复适配 Validation。",
                    [{"observed": epochs, "required": 1}],
                )
            requested_policy = {
                "candidate_count": (
                    self.settings.skill_optimization_candidate_count
                    if candidate_count is None
                    else candidate_count
                ),
                "screening_case_count": (
                    self.settings.skill_optimization_screening_case_count
                    if screening_case_count is None
                    else screening_case_count
                ),
                "validation_repeats": (
                    self.settings.skill_optimization_validation_repeats
                    if validation_repeats is None
                    else validation_repeats
                ),
                "max_repeats": (
                    self.settings.skill_optimization_max_repeats
                    if max_repeats is None
                    else max_repeats
                ),
                "early_stop_patience": (
                    self.settings.skill_optimization_early_stop_patience
                    if early_stop_patience is None
                    else early_stop_patience
                ),
            }
            for field, value in requested_policy.items():
                limits = EXPERIMENT_POLICY_LIMITS[field]
                if (
                    type(value) is not int
                    or value < limits["minimum"]
                    or value > limits["maximum"]
                ):
                    raise AnalystBenchError(
                        "optimization_experiment_policy_invalid",
                        f"实验策略 {field} 超出公开范围。",
                        [{"field": field, "value": value, **limits}],
                    )
            if requested_policy["validation_repeats"] > requested_policy["max_repeats"]:
                raise AnalystBenchError(
                    "optimization_experiment_policy_invalid",
                    "validation_repeats 不能大于 max_repeats。",
                    [
                        {
                            "field": "validation_repeats",
                            "value": requested_policy["validation_repeats"],
                            "max_repeats": requested_policy["max_repeats"],
                        }
                    ],
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
                        **requested_policy,
                        "min_overall_delta": self.settings.skill_optimization_min_overall_delta,
                        "minimum_independent_validation_cases": (
                            self.settings.skill_optimization_minimum_independent_validation_cases
                        ),
                        "max_latency_growth": (
                            self.settings.skill_optimization_max_latency_growth
                        ),
                        "max_token_growth": (
                            self.settings.skill_optimization_max_token_growth
                        ),
                    }
                ),
            )
            session.add(item)
            session.flush()
            session.expunge(item)
        if binding is None:
            self.registry.bind(
                skill_id=skill_id,
                evaluation_target_id=evaluation_target_id,
                version_id=base_skill_version_id,
                active_level="provisional",
            )
        return item

    def start(self, experiment_id: str) -> OptimizationExperiment:
        self.registry._require_enabled()
        self._verify_snapshot(experiment_id)
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            item = session.get(OptimizationExperiment, experiment_id)
            if item is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found", "找不到实验。", status_code=404
                )
            if item.status != "created":
                raise AnalystBenchError(
                    "optimization_experiment_state_invalid", "实验不能重复启动。"
                )
            snapshot = session.get(
                OptimizationDataSnapshot, item.data_snapshot_id, with_for_update=True
            )
            assert snapshot is not None
            self._assert_independent_snapshot_unused(session, item, snapshot)
            self._assert_active_parent(session, item, None)
            item.status = "running"
            item.started_at = datetime.now(UTC)
            self.jobs.enqueue(
                session, "skill_optimization_advance", {"experiment_id": item.id}
            )
            session.flush()
            session.refresh(item)
            session.expunge(item)
            return item

    def _verify_snapshot(self, experiment_id: str) -> None:
        with transaction(self.session_factory) as session:
            experiment = session.get(OptimizationExperiment, experiment_id)
            if experiment is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found", "找不到实验。", status_code=404
                )
            snapshot = session.get(
                OptimizationDataSnapshot, experiment.data_snapshot_id
            )
            if snapshot is None:
                raise AnalystBenchError(
                    "optimization_snapshot_invalid", "实验数据快照不存在。"
                )
            values = {
                "dataset_key": snapshot.dataset_key,
                "mode": snapshot.mode,
                "train_cases": json.loads(snapshot.train_cases_json or "[]"),
                "validation_cases": json.loads(
                    snapshot.validation_cases_json or "[]"
                ),
                "hidden_test_cases": json.loads(
                    snapshot.hidden_test_cases_json or "[]"
                ),
                "prospective_holdout_cases": json.loads(
                    snapshot.prospective_holdout_cases_json or "[]"
                ),
                "expected_case_input_hashes": json.loads(
                    snapshot.case_input_hashes_json or "{}"
                ),
                "expected_eval_spec_hashes": json.loads(
                    snapshot.eval_spec_hashes_json or "{}"
                ),
            }
        verify_snapshot_manifest(self.settings, **values)

    @staticmethod
    def _assert_independent_snapshot_unused(
        session: Session,
        experiment: OptimizationExperiment,
        snapshot: OptimizationDataSnapshot,
    ) -> None:
        if snapshot.mode != "independent_validation":
            return
        consumed = session.scalar(
            select(OptimizationExperiment.id)
            .where(
                OptimizationExperiment.data_snapshot_id == snapshot.id,
                OptimizationExperiment.id != experiment.id,
                OptimizationExperiment.status != "created",
            )
            .limit(1)
        )
        if consumed is not None:
            raise AnalystBenchError(
                "optimization_independent_snapshot_consumed",
                "Independent Validation Snapshot 已被另一实验启动过，不能重复用于选择候选。",
                status_code=409,
                details=[{"consuming_experiment_id": consumed}],
            )

    @staticmethod
    def _assert_active_parent(
        session: Session,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch | None,
    ) -> None:
        binding = session.scalar(
            select(SkillTargetBinding).where(
                SkillTargetBinding.skill_id == experiment.skill_id,
                SkillTargetBinding.evaluation_target_id
                == experiment.evaluation_target_id,
            )
        )
        if binding is None:
            raise AnalystBenchError(
                "skill_binding_conflict",
                "实验的 Skill Active 绑定不存在。",
                status_code=409,
            )
        expected_version_id = experiment.base_skill_version_id
        allowed_recovery_version_id: str | None = None
        if epoch is not None:
            expected_version_id = epoch.parent_skill_version_id
            if epoch.status == "completed":
                expected_version_id = (
                    epoch.best_candidate_version_id
                    or epoch.parent_skill_version_id
                )
            else:
                history_rows = list(
                    session.scalars(
                        select(SkillBindingHistory).where(
                            SkillBindingHistory.binding_id == binding.id,
                            SkillBindingHistory.action == "promotion",
                        )
                    )
                )
                for history in history_rows:
                    try:
                        metadata = json.loads(history.metadata_json or "{}")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        metadata.get("experiment_id") == experiment.id
                        and metadata.get("epoch_id") == epoch.id
                    ):
                        allowed_recovery_version_id = history.active_version_id
                        break
        if binding.active_version_id not in {
            expected_version_id,
            allowed_recovery_version_id,
        }:
            raise AnalystBenchError(
                "skill_binding_conflict",
                "Skill Active 已不再是本实验当前 Epoch 的父版本，拒绝继续覆盖。",
                status_code=409,
                details=[
                    {
                        "expected_active_version_id": expected_version_id,
                        "actual_active_version_id": binding.active_version_id,
                    }
                ],
            )

    @staticmethod
    def _parse_patch(value: str) -> dict[str, Any]:
        try:
            parsed = _decode_optimizer_json(value)
        except _OptimizerJSONDecodeError as exc:
            raise AnalystBenchError(
                "optimizer_output_invalid", "Optimizer 未返回有效 JSON Patch。"
            ) from exc
        return _normalize_patch(parsed)

    def _execute_optimizer_runner(
        self,
        runner: Any,
        runner_config: dict[str, Any],
        workspace: Path,
        prompt: str,
        attempts: int = 3,
    ) -> Any:
        """Run one prompt with a frozen attempt count and exponential backoff."""

        for attempt in range(attempts):
            try:
                return runner.execute(runner_config, workspace, prompt)
            except AgentRunnerError:
                if attempt == attempts - 1:
                    raise
                self._optimizer_backoff(float(2**attempt))
        raise AssertionError("unreachable")

    @staticmethod
    def _role_prompt(
        *,
        instruction: str,
        role_spec: Mapping[str, str],
        role_index: int,
        candidate_count: int,
        skill_root: Path,
        train_evidence: dict[str, Any],
    ) -> str:
        role = role_spec["role"]
        prompt_version = role_spec["prompt_version"]
        role_output_schema = (
            '{"role":"'
            + role
            + '","prompt_version":"'
            + prompt_version
            + '","findings":[{"summary":"...","evidence_refs":['
            '"failure_tags:...|dimensions:...|claim_findings:..."],'
            '"confidence":0.0}],"patches":['
            + STRUCTURED_PATCH_SCHEMA_TEXT
            + "]}"
        )
        return (
            f"{instruction}\n\n"
            f"Optimizer role: {role}.\n"
            f"Prompt version: {prompt_version}.\n"
            f"Role mission: {role_spec['mission']}\n"
            # Kept for backward compatibility with local Fake runners while
            # the V1 role name/version are the authoritative identity.
            f"Candidate index: {role_index}.\n"
            f"Return at most {candidate_count} independent small-scope patches.\n"
            f"Skill directory: {skill_root}\n"
            "Evidence scope: Train-only optimizer-visible aggregate evidence. "
            "Do not infer or request Validation, Hidden, Holdout, Case source, "
            "standard-answer text, or report text.\n"
            f"Train evidence JSON: {canonical_json(train_evidence)}\n\n"
            f"Output schema version: {OPTIMIZER_OUTPUT_SCHEMA_VERSION}.\n"
            f"Output schema: {role_output_schema}\n"
            "Operation schema is exact: replace uses old/new; insert_after uses "
            "anchor/content; append uses content; delete removes the named file. "
            "Never emit old_text, create, unified diff, shell commands, or prose "
            "outside the JSON object."
        )

    def _run_optimizer_role(
        self,
        *,
        runner: Any,
        runner_config: dict[str, Any],
        workspace: Path,
        prompt: str,
        role: str,
        prompt_version: str,
        candidate_count: int,
        execution_attempts: int = 3,
        format_repair_enabled: bool = True,
    ) -> dict[str, Any]:
        result = self._execute_optimizer_runner(
            runner, runner_config, workspace, prompt, attempts=execution_attempts
        )
        try:
            parsed = _decode_optimizer_json(result.final_report)
        except _OptimizerJSONDecodeError:
            if not format_repair_enabled:
                raise AnalystBenchError(
                    "optimizer_output_invalid",
                    "Optimizer 未返回有效 JSON，且本实验未启用格式修复。",
                ) from None
            repair_prompt = (
                f"Format-repair request for role {role}, prompt version "
                f"{prompt_version}. Preserve the proposed meaning, but return one "
                "valid JSON object only. Do not add prose. The accepted direct patch "
                f"schema is {STRUCTURED_PATCH_SCHEMA_TEXT}; the accepted role envelope "
                "contains the exact role, prompt_version, findings array, and patches "
                "array. Invalid output (bounded):\n"
                f"{result.final_report[:8000]}"
            )
            repaired = self._execute_optimizer_runner(
                runner,
                runner_config,
                workspace,
                repair_prompt,
                attempts=execution_attempts,
            )
            try:
                parsed = _decode_optimizer_json(repaired.final_report)
            except _OptimizerJSONDecodeError as exc:
                raise AnalystBenchError(
                    "optimizer_output_invalid",
                    "Optimizer 格式修复后仍未返回有效 JSON。",
                ) from exc
        return _parse_role_output(
            parsed,
            role=role,
            prompt_version=prompt_version,
            candidate_count=candidate_count,
        )

    def _run_optimizer_analysts(
        self,
        *,
        runner: Any,
        runner_config: dict[str, Any],
        workspace: Path,
        instruction: str,
        skill_root: Path,
        train_evidence: dict[str, Any],
        candidate_count: int,
        role_specs: list[Mapping[str, str]] | None = None,
        execution_attempts: int = 3,
        format_repair_enabled: bool = True,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        role_outputs: list[dict[str, Any]] = []
        role_errors: list[dict[str, str]] = []
        for role_index, role_spec in enumerate(
            role_specs or list(OPTIMIZER_ROLE_SPECS), start=1
        ):
            prompt = self._role_prompt(
                instruction=instruction,
                role_spec=role_spec,
                role_index=role_index,
                candidate_count=candidate_count,
                skill_root=skill_root,
                train_evidence=train_evidence,
            )
            try:
                role_outputs.append(
                    self._run_optimizer_role(
                        runner=runner,
                        runner_config=runner_config,
                        workspace=workspace,
                        prompt=prompt,
                        role=role_spec["role"],
                        prompt_version=role_spec["prompt_version"],
                        candidate_count=candidate_count,
                        execution_attempts=execution_attempts,
                        format_repair_enabled=format_repair_enabled,
                    )
                )
            except AgentRunnerError as exc:
                role_errors.append(
                    {
                        "role": role_spec["role"],
                        "code": "optimizer_execution_failed",
                        "message": f"{exc.code}: {exc}"[:1000],
                    }
                )
            except AnalystBenchError as exc:
                role_errors.append(
                    {
                        "role": role_spec["role"],
                        "code": exc.code,
                        "message": exc.message[:1000],
                    }
                )
        return role_outputs, role_errors

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
        rejection_details: list[dict[str, Any]] | None = None,
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
            intended_failure_clusters_json=canonical_json(
                self._candidate_intent(patch).get("target_failure_families", [])
            ),
            intent_json=canonical_json(self._candidate_intent(patch)),
            change_stats_json="{}",
            evidence_refs_json=canonical_json(
                {"epoch_id": epoch.id, "candidate_index": candidate_index}
            ),
            status="rejected",
            rejection_code=rejection_code,
            rejection_detail_json=canonical_json(
                {
                    "code": rejection_code,
                    "message": rejection_message,
                    "details": rejection_details or [],
                }
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

    @staticmethod
    def _candidate_intent(patch: dict[str, Any]) -> dict[str, Any]:
        """Normalize optimizer-declared intent without trusting it as evidence."""

        raw = patch.get("intent")
        source = raw if isinstance(raw, dict) else {}

        def strings(*keys: str) -> list[str]:
            for key in keys:
                value = source.get(key, patch.get(key))
                if isinstance(value, list):
                    return sorted(
                        {
                            str(item).strip()
                            for item in value
                            if str(item).strip()
                        }
                    )
            return []

        return {
            "change_type": str(
                source.get("change_type")
                or patch.get("candidate_direction")
                or "unspecified"
            ).strip(),
            "target_failure_families": strings(
                "target_failure_families", "intended_failure_clusters"
            ),
            "target_dimensions": strings(
                "target_dimensions", "expected_dimensions"
            ),
            "target_failure_tags": strings(
                "target_failure_tags", "improve_tags"
            ),
            "protected_behaviors": strings("protected_behaviors"),
        }

    def _rejected_history(
        self,
        experiment_id: str,
    ) -> list[dict[str, Any]]:
        with transaction(self.session_factory) as session:
            experiment = session.get(OptimizationExperiment, experiment_id)
            snapshot = (
                session.get(OptimizationDataSnapshot, experiment.data_snapshot_id)
                if experiment is not None
                else None
            )
            if experiment is None or snapshot is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found",
                    "找不到实验或其数据快照。",
                    status_code=404,
                )
            conditions = [
                OptimizationEpoch.experiment_id == experiment_id,
                CandidateMutation.status == "rejected",
            ]
            # Validation is an authoritative gate, never optimizer feedback.
            # This applies in both modes: development_regression is explicitly
            # provisional, but its later gate outcome must still not enter a
            # future role prompt through rejected_history.
            validation_comparison = (
                select(CandidateComparison.id)
                .where(
                    CandidateComparison.candidate_mutation_id
                    == CandidateMutation.id,
                    CandidateComparison.comparison_type.in_(
                        (
                            "paired_repeated_validation",
                            "full_validation",
                            "validation",
                        )
                    ),
                )
                .exists()
            )
            conditions.append(~validation_comparison)
            rows = list(
                session.execute(
                    select(CandidateMutation, OptimizationEpoch)
                    .join(
                        OptimizationEpoch,
                        OptimizationEpoch.id == CandidateMutation.epoch_id,
                    )
                    .where(*conditions)
                    .order_by(CandidateMutation.created_at.desc())
                )
            )
        return [
            {
                "epoch": epoch.epoch_number,
                "candidate_type": mutation.candidate_type,
                "rejection_code": mutation.rejection_code,
                "intent": json.loads(mutation.intent_json or "{}"),
            }
            for mutation, epoch in rows
        ]

    def _snapshot_forbidden_tokens(
        self, experiment: OptimizationExperiment
    ) -> tuple[str, ...]:
        """Return frozen Case identifiers only; never load hidden answers."""

        with transaction(self.session_factory) as session:
            snapshot = session.get(
                OptimizationDataSnapshot, experiment.data_snapshot_id
            )
            if snapshot is None:
                raise AnalystBenchError(
                    "optimization_snapshot_invalid", "实验数据快照不存在。"
                )
            paths = {
                path
                for value in (
                    snapshot.train_cases_json,
                    snapshot.validation_cases_json,
                    snapshot.hidden_test_cases_json,
                    snapshot.prospective_holdout_cases_json,
                )
                for path in json.loads(value or "[]")
            }
        identifiers = set(paths)
        identifiers.update(
            Path(path).name for path in paths if len(Path(path).name) >= 8
        )
        return tuple(sorted(identifiers))

    def _optimizer_pipeline(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        *,
        candidate_count: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        with transaction(self.session_factory) as session:
            policy = session.get(
                OptimizerPolicyVersion, experiment.optimizer_policy_version_id
            )
            profile = (
                session.get(ExecutionProfile, policy.execution_profile_id)
                if policy
                else None
            )
            verifier = session.get(
                VerifierBundleVersion, experiment.verifier_bundle_version_id
            )
            if (
                policy is None
                or profile is None
                or profile.status != "frozen"
                or verifier is None
            ):
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
            train_evidence = {
                "evidence_scope": "train_only",
                "schema_version": "optimizer_train_input.v1",
                "current_epoch": json.loads(epoch.evidence_summary_json or "{}"),
                "rejected_train_history": self._rejected_history(experiment.id),
            }
            instruction = str(
                policy_config.get("prompt_bundle", {}).get(
                    "instruction",
                    "Improve the Skill using only the supplied Train evidence.",
                )
            )
            runner = create_runner(runner_id)
            selected_roles = policy_config.get(
                "optimizer_roles", [item["role"] for item in OPTIMIZER_ROLE_SPECS]
            )
            role_specs = [
                item for item in OPTIMIZER_ROLE_SPECS if item["role"] in selected_roles
            ]
            role_outputs, role_errors = self._run_optimizer_analysts(
                runner=runner,
                runner_config=runner_config,
                workspace=workspace,
                instruction=instruction,
                skill_root=skill_root,
                train_evidence=train_evidence,
                candidate_count=candidate_count,
                role_specs=role_specs,
                execution_attempts=int(
                    policy_config.get("optimizer_execution_attempts", 3)
                ),
                format_repair_enabled=bool(
                    policy_config.get("format_repair_enabled", True)
                ),
            )
        selected = _merge_role_patches(role_outputs, candidate_count)
        with transaction(self.session_factory) as session:
            session.add(
                OptimizationEvent(
                    id=str(uuid4()),
                    experiment_id=experiment.id,
                    epoch_id=epoch.id,
                    event_type="optimizer_pipeline_completed",
                    payload_json=canonical_json(
                        {
                            "pipeline_version": "four_role_optimizer.v1",
                            "roles": [
                                {
                                    "role": output["role"],
                                    "prompt_version": output["prompt_version"],
                                    "finding_count": len(output["findings"]),
                                    "patch_count": len(output["patches"]),
                                }
                                for output in role_outputs
                            ],
                            "errors": role_errors,
                            "selected_patch_hashes": [
                                proposal["patch_hash"] for proposal in selected
                            ],
                        }
                    ),
                )
            )
        return selected, role_errors

    def _generate_candidate(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        candidate_index: int,
        *,
        structured_patch: dict[str, Any],
        provenance: Mapping[str, Any] | None = None,
    ) -> CandidateMutation:
        with transaction(self.session_factory) as session:
            policy = session.get(
                OptimizerPolicyVersion, experiment.optimizer_policy_version_id
            )
            verifier = session.get(
                VerifierBundleVersion, experiment.verifier_bundle_version_id
            )
            if policy is None or verifier is None:
                raise AnalystBenchError(
                    "optimizer_policy_invalid", "Optimizer Policy 不可执行。"
                )
            static_policy = json.loads(verifier.static_policy_json or "{}")
        try:
            patch_result = self.patches.apply(
                parent_version_id=epoch.parent_skill_version_id,
                structured_patch=structured_patch,
                created_by=f"optimizer:{policy.id}",
                policy=static_policy,
                epoch_number=epoch.epoch_number,
                candidate_validator=lambda root: self.static_validator.validate(
                    root,
                    static_policy,
                    forbidden_case_tokens=self._snapshot_forbidden_tokens(
                        experiment
                    ),
                ),
            )
            version = patch_result.version
            patch_hash = patch_result.patch_hash
        except AnalystBenchError as exc:
            return self._record_rejected_candidate(
                experiment,
                epoch,
                candidate_index,
                rejection_code=exc.code,
                rejection_message=exc.message,
                structured_patch=structured_patch,
                rejection_details=exc.details,
            )
        change_stats = patch_result.stats.as_dict()
        change_stats["static_validation"] = patch_result.validation
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
                intended_failure_clusters_json=canonical_json(
                    self._candidate_intent(structured_patch).get(
                        "target_failure_families", []
                    )
                ),
                intent_json=canonical_json(
                    self._candidate_intent(structured_patch)
                ),
                change_stats_json=canonical_json(change_stats),
                evidence_refs_json=canonical_json(
                    {
                        "epoch_id": epoch.id,
                        "candidate_index": candidate_index,
                        "optimizer_role": (provenance or {}).get("role"),
                        "prompt_version": (provenance or {}).get(
                            "prompt_version"
                        ),
                        "output_schema_version": OPTIMIZER_OUTPUT_SCHEMA_VERSION,
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
                            "intent": self._candidate_intent(structured_patch),
                            "change_stats": change_stats,
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
            validation_paths = sorted(json.loads(snapshot.validation_cases_json))
            train_paths = sorted(json.loads(snapshot.train_cases_json or "[]"))
            judge = json.loads(verifier.judge_config_json or "{}")
            judge_runner = str(judge.get("runner") or "claude")
            optimization_paths = (
                validation_paths
                if snapshot.mode == "development_regression"
                else train_paths
            )
            config = self._experiment_config(experiment)
            screening_count = int(
                config.get(
                    "screening_case_count",
                    self.settings.skill_optimization_screening_case_count,
                )
            )
            screening_paths = optimization_paths[:screening_count]
            return (
                snapshot.dataset_key,
                validation_paths,
                screening_paths,
                judge_runner,
            )

    @staticmethod
    def _experiment_config(experiment: OptimizationExperiment) -> dict[str, Any]:
        try:
            config = json.loads(
                getattr(experiment, "config_snapshot_json", "{}") or "{}"
            )
        except (TypeError, json.JSONDecodeError):
            return {}
        return config if isinstance(config, dict) else {}

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
        pair_seed: str | None = None,
        pair_position: int | None = None,
    ) -> None:
        dataset_key, _, _, judge_runner = self._snapshot_inputs(experiment)
        with transaction(self.session_factory) as session:
            verifier = session.get(
                VerifierBundleVersion, experiment.verifier_bundle_version_id
            )
            assert verifier is not None
            raw_judge_configuration = json.loads(
                verifier.judge_config_json or "{}"
            )
        if not isinstance(raw_judge_configuration, dict):
            raise AnalystBenchError(
                "optimization_verifier_invalid", "Verifier Judge 配置必须是对象。"
            )
        judge_configuration = {
            key: value
            for key, value in raw_judge_configuration.items()
            if key != "runner"
        }
        variant = self.registry.freeze_variant(
            evaluation_target_id=experiment.evaluation_target_id,
            version_id=version_id,
        )
        for repeat_index in repeat_indices:
            manifest = {
                "experiment_id": experiment.id,
                "epoch_id": epoch.id,
                "candidate_mutation_id": candidate_mutation_id,
                "dataset_key": dataset_key,
                "case_paths": case_paths,
                "method_id": variant.materialized_method_id,
                "judge_runner": judge_runner,
                "judge_configuration": judge_configuration,
                "split_role": split_role,
                "arm": arm,
                "repeat_index": repeat_index,
                "version_id": version_id,
                "pair_seed": pair_seed,
                "pair_position": pair_position,
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
                    "pair_seed": pair_seed,
                    "pair_position": pair_position,
                },
                idempotency_key=f"skillopt:{run_hash}",
                judge_configuration=judge_configuration,
            )
            with transaction(self.session_factory) as session:
                existing_for_submission = session.scalar(
                    select(OptimizationRunGroup).where(
                        OptimizationRunGroup.evaluation_submission_id
                        == submission.id
                    )
                )
                if existing_for_submission is not None:
                    if (
                        existing_for_submission.experiment_id != experiment.id
                        or existing_for_submission.run_config_hash != run_hash
                    ):
                        raise AnalystBenchError(
                            "optimization_run_group_conflict",
                            "同一评测批次已绑定到不同的 Run Group 配置。",
                        )
                    continue
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

    def _ensure_paired_validation_groups(
        self,
        experiment: OptimizationExperiment,
        epoch: OptimizationEpoch,
        mutation: CandidateMutation,
        *,
        repeats: int,
        case_paths: list[str],
    ) -> None:
        assert mutation.candidate_skill_version_id
        for repeat_index in range(repeats):
            pair_seed = content_hash(
                canonical_json(
                    {
                        "experiment_id": experiment.id,
                        "epoch_id": epoch.id,
                        "candidate_mutation_id": mutation.id,
                        "repeat_index": repeat_index,
                    }
                ).encode("utf-8")
            )
            arm_order = (
                ("baseline", "candidate")
                if int(pair_seed.removeprefix("sha256:")[:8], 16) % 2 == 0
                else ("candidate", "baseline")
            )
            for position, arm in enumerate(arm_order):
                self._ensure_run_groups(
                    experiment,
                    epoch,
                    split_role="validation",
                    arm=arm,
                    version_id=(
                        epoch.parent_skill_version_id
                        if arm == "baseline"
                        else mutation.candidate_skill_version_id
                    ),
                    candidate_mutation_id=(None if arm == "baseline" else mutation.id),
                    repeat_indices=range(repeat_index, repeat_index + 1),
                    case_paths=case_paths,
                    pair_seed=pair_seed,
                    pair_position=position,
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
                        "claim_findings": report_evidence.get(
                            "claim_findings", []
                        ),
                        "success_patterns": report_evidence.get(
                            "success_patterns", []
                        ),
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
                            guardrail_metrics={
                                key: float(report_evidence["metrics"][key])
                                for key in (
                                    "forbidden_hit_count",
                                    "missing_chain_count",
                                )
                                if isinstance(
                                    report_evidence["metrics"].get(key),
                                    (int, float),
                                )
                            },
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
        config = self._experiment_config(experiment)
        candidate_count = int(
            config.get(
                "candidate_count",
                self.settings.skill_optimization_candidate_count,
            )
        )
        if len(candidates) >= candidate_count:
            return candidates

        proposals, role_errors = self._optimizer_pipeline(
            experiment,
            epoch,
            candidate_count=candidate_count,
        )
        existing_hashes = {candidate.patch_hash for candidate in candidates}
        for proposal in proposals:
            if len(candidates) >= candidate_count:
                break
            if proposal["patch_hash"] in existing_hashes:
                continue
            candidates.append(
                self._generate_candidate(
                    experiment,
                    epoch,
                    len(candidates) + 1,
                    structured_patch=proposal["patch"],
                    provenance=proposal,
                )
            )
            existing_hashes.add(proposal["patch_hash"])
        while len(candidates) < candidate_count:
            candidates.append(
                self._record_rejected_candidate(
                    experiment,
                    epoch,
                    len(candidates) + 1,
                    rejection_code="optimizer_pipeline_insufficient_candidates",
                    rejection_message=(
                        "四角色 Optimizer 未产生足够的唯一合法 Patch。"
                    ),
                    raw_output=canonical_json(
                        {
                            "candidate_index": len(candidates) + 1,
                            "role_errors": role_errors,
                        }
                    ),
                    rejection_details=role_errors,
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
                critical_dimensions=screening_policy.get(
                    "critical_dimensions", ["root_cause", "classification"]
                ),
                protected_guardrail_metrics=screening_policy.get(
                    "protected_guardrail_metrics",
                    ["forbidden_hit_count", "missing_chain_count"],
                ),
                reject_failure_increase=bool(
                    screening_policy.get("reject_failure_increase", True)
                ),
                reject_new_failure_tags=bool(
                    screening_policy.get("reject_new_failure_tags", True)
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
        config = self._experiment_config(experiment)
        with transaction(self.session_factory) as session:
            snapshot = session.get(
                OptimizationDataSnapshot, experiment.data_snapshot_id
            )
            verifier = session.get(
                VerifierBundleVersion, experiment.verifier_bundle_version_id
            )
            assert snapshot is not None and verifier is not None
            policy = json.loads(verifier.gate_policy_json or "{}")
        comparison = compare_paired(
            observations,
            bootstrap_samples=int(policy.get("bootstrap_samples", 2000)),
            confidence=float(policy.get("bootstrap_confidence", 0.95)),
            bootstrap_seed=f"{experiment.id}:{epoch.id}:{mutation.id}",
        )
        gate = evaluate_gate(
            comparison,
            min_overall_delta=float(
                policy.get(
                    "min_overall_delta",
                    config.get(
                        "min_overall_delta",
                        self.settings.skill_optimization_min_overall_delta,
                    ),
                )
            ),
            minimum_independent_validation_cases=int(
                policy.get(
                    "minimum_independent_validation_cases",
                    config.get(
                        "minimum_independent_validation_cases",
                        self.settings.skill_optimization_minimum_independent_validation_cases,
                    ),
                )
            ),
            max_latency_growth=float(
                policy.get(
                    "max_latency_growth",
                    config.get(
                        "max_latency_growth",
                        self.settings.skill_optimization_max_latency_growth,
                    ),
                )
            ),
            max_token_growth=float(
                policy.get(
                    "max_token_growth",
                    config.get(
                        "max_token_growth",
                        self.settings.skill_optimization_max_token_growth,
                    ),
                )
            ),
            mode=snapshot.mode,
            current_repeats=int(comparison.get("repeat_count") or 0),
            max_repeats=int(
                config.get(
                    "max_repeats", self.settings.skill_optimization_max_repeats
                )
            ),
            critical_dimension_min_delta=float(
                policy.get("critical_dimension_min_delta", 0.0)
            ),
            critical_family_max_regression=float(
                policy.get("critical_family_max_regression", -2.0)
            ),
            require_token_usage=bool(policy.get("require_token_usage", True)),
            min_candidate_win_probability=float(
                policy.get("min_candidate_win_probability", 0.0)
            ),
            require_bootstrap_lower_bound_positive=bool(
                policy.get("require_bootstrap_lower_bound_positive", True)
            ),
            critical_dimensions=policy.get(
                "critical_dimensions", ["root_cause", "classification"]
            ),
            protected_guardrail_metrics=policy.get(
                "protected_guardrail_metrics",
                ["forbidden_hit_count", "missing_chain_count"],
            ),
            reject_failure_increase=bool(
                policy.get("reject_failure_increase", True)
            ),
            reject_new_failure_tags=bool(
                policy.get("reject_new_failure_tags", True)
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
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            stored_epoch = session.get(OptimizationEpoch, epoch.id)
            stored_experiment = session.get(OptimizationExperiment, experiment.id)
            assert stored_epoch is not None and stored_experiment is not None
            if stored_epoch.status == "completed":
                if (
                    stored_epoch.decision != decision
                    or stored_epoch.best_candidate_version_id
                    != best_candidate_version_id
                ):
                    raise AnalystBenchError(
                        "optimization_epoch_completion_conflict",
                        "Epoch 已以不同决策完成。",
                        status_code=409,
                    )
                needs_summary = not json.loads(stored_epoch.summary_json or "{}")
                session.expunge(stored_epoch)
                session.expunge(stored_experiment)
                if not needs_summary:
                    return
            else:
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
        self._persist_epoch_summary(experiment.id, epoch.id)

    def _persist_epoch_summary(self, experiment_id: str, epoch_id: str) -> None:
        """Freeze the deterministic, score-backed summary for a completed Epoch."""

        ledger = build_optimization_ledger(self.detail(experiment_id))
        summary = next(
            (
                item
                for item in ledger["epochs"]
                if item.get("epoch_id") == epoch_id
            ),
            None,
        )
        if summary is None:
            raise AnalystBenchError(
                "optimization_epoch_summary_missing",
                "已完成 Epoch 无法生成优化总结。",
            )
        with transaction(self.session_factory) as session:
            stored_epoch = session.get(OptimizationEpoch, epoch_id)
            if stored_epoch is None:
                raise AnalystBenchError(
                    "optimization_epoch_not_found", "找不到 Epoch。", status_code=404
                )
            stored_epoch.summary_json = canonical_json(summary)
            existing = session.scalar(
                select(OptimizationEvent.id).where(
                    OptimizationEvent.experiment_id == experiment_id,
                    OptimizationEvent.epoch_id == epoch_id,
                    OptimizationEvent.event_type == "epoch_summary_ready",
                )
            )
            if existing is None:
                session.add(
                    OptimizationEvent(
                        id=str(uuid4()),
                        experiment_id=experiment_id,
                        epoch_id=epoch_id,
                        event_type="epoch_summary_ready",
                        payload_json=stored_epoch.summary_json,
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
        config = self._experiment_config(experiment)
        patience = int(
            config.get(
                "early_stop_patience",
                self.settings.skill_optimization_early_stop_patience,
            )
        )
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
        self.registry._require_enabled()
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
            self._assert_active_parent(session, experiment, epoch)
            for item in (experiment, epoch):
                if item is not None:
                    session.expunge(item)
        # Case files remain external to the managed Skill store. Re-hash them
        # at every durable phase transition so one experiment cannot silently
        # mix different Case or Eval Spec contents after its Snapshot freezes.
        self._verify_snapshot(experiment_id)
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
            if not json.loads(epoch.summary_json or "{}"):
                self._persist_epoch_summary(experiment.id, epoch.id)
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
            config = self._experiment_config(experiment)
            repeats = int(
                config.get(
                    "validation_repeats",
                    self.settings.skill_optimization_validation_repeats,
                )
            )
            self._ensure_paired_validation_groups(
                experiment,
                epoch,
                mutation,
                repeats=repeats,
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
                    int(
                        self._experiment_config(experiment).get(
                            "max_repeats",
                            self.settings.skill_optimization_max_repeats,
                        )
                    ),
                    5
                    if int(comparison.get("repeat_count") or 0) < 5
                    else 7,
                ),
            )
            _, case_paths, _, _ = self._snapshot_inputs(experiment)
            self._ensure_paired_validation_groups(
                experiment,
                epoch,
                mutation,
                repeats=requested,
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
                expected_active_version_id=epoch.parent_skill_version_id,
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

    @staticmethod
    def _page(query: Any, *, limit: int, offset: int) -> Any:
        if limit < 1 or limit > 500 or offset < 0:
            raise AnalystBenchError(
                "optimization_pagination_invalid",
                "分页参数无效；limit 必须为 1..500，offset 不能为负数。",
            )
        return query.offset(offset).limit(limit)

    def list_experiments(
        self, *, limit: int = 200, offset: int = 0
    ) -> list[OptimizationExperiment]:
        self.registry._require_enabled()
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    self._page(
                        select(OptimizationExperiment).order_by(
                            OptimizationExperiment.created_at.desc(),
                            OptimizationExperiment.id,
                        ),
                        limit=limit,
                        offset=offset,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_policies(
        self, *, limit: int = 200, offset: int = 0
    ) -> list[OptimizerPolicyVersion]:
        self.registry._require_enabled()
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    self._page(
                        select(OptimizerPolicyVersion).order_by(
                            OptimizerPolicyVersion.created_at.desc(),
                            OptimizerPolicyVersion.id,
                        ),
                        limit=limit,
                        offset=offset,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_verifiers(
        self, *, limit: int = 200, offset: int = 0
    ) -> list[VerifierBundleVersion]:
        self.registry._require_enabled()
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    self._page(
                        select(VerifierBundleVersion).order_by(
                            VerifierBundleVersion.created_at.desc(),
                            VerifierBundleVersion.id,
                        ),
                        limit=limit,
                        offset=offset,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def list_snapshots(
        self, *, limit: int = 200, offset: int = 0
    ) -> list[OptimizationDataSnapshot]:
        self.registry._require_enabled()
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    self._page(
                        select(OptimizationDataSnapshot).order_by(
                            OptimizationDataSnapshot.created_at.desc(),
                            OptimizationDataSnapshot.id,
                        ),
                        limit=limit,
                        offset=offset,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items

    def summary(self, experiment_id: str) -> dict[str, Any]:
        """Return the compact aggregate from frozen Epoch summaries."""

        self.registry._require_enabled()

        experiment = self.get(experiment_id)
        with transaction(self.session_factory) as session:
            rows = list(
                session.execute(
                    select(
                        OptimizationEpoch.epoch_number,
                        OptimizationEpoch.decision,
                        OptimizationEpoch.summary_json,
                    )
                    .where(
                        OptimizationEpoch.experiment_id == experiment_id,
                        OptimizationEpoch.status == "completed",
                    )
                    .order_by(OptimizationEpoch.epoch_number)
                )
            )
        summaries = [
            json.loads(value or "{}")
            for _, _, value in rows
            if value and value != "{}"
        ]
        if rows and len(summaries) != len(rows):
            # 0017 cannot reconstruct historical summaries inside a schema
            # migration. Rebuild the read model from immutable comparisons so
            # upgraded completed experiments still have a useful ledger.
            rebuilt = build_optimization_ledger(self.detail(experiment_id))["summary"]
            return {
                **rebuilt,
                "stop_reason": experiment.stop_reason,
            }
        initial_score = next(
            (
                item.get("baseline_score")
                for item in summaries
                if item.get("baseline_score") is not None
            ),
            None,
        )
        cumulative_delta = (
            summaries[-1].get("cumulative_delta") if summaries else None
        )
        final_score = (
            float(initial_score) + float(cumulative_delta)
            if initial_score is not None and cumulative_delta is not None
            else None
        )
        decisions = [decision for _, decision, _ in rows]
        return {
            "initial_score": initial_score,
            "final_score": final_score,
            "active_path_score": final_score,
            "score_semantics": "initial_score_plus_promoted_epoch_deltas",
            "cumulative_delta": cumulative_delta,
            "promoted_epochs": decisions.count("promote"),
            "retained_epochs": sum(
                decision in {"retain", "no_screening_survivor"}
                for decision in decisions
            ),
            "stop_reason": experiment.stop_reason,
        }

    def detail(
        self,
        experiment_id: str,
        *,
        epoch_offset: int = 0,
        epoch_limit: int | None = None,
        newest_first: bool = False,
    ) -> dict[str, Any]:
        self.registry._require_enabled()
        experiment = self.get(experiment_id)
        if epoch_offset < 0 or epoch_limit is not None and epoch_limit < 1:
            raise AnalystBenchError(
                "optimization_pagination_invalid", "Epoch 分页参数无效。"
            )
        with transaction(self.session_factory) as session:
            epoch_total = int(
                session.scalar(
                    select(func.count(OptimizationEpoch.id)).where(
                        OptimizationEpoch.experiment_id == experiment_id
                    )
                )
                or 0
            )
            order = (
                OptimizationEpoch.epoch_number.desc()
                if newest_first
                else OptimizationEpoch.epoch_number
            )
            epoch_query = (
                select(OptimizationEpoch)
                .where(OptimizationEpoch.experiment_id == experiment_id)
                .order_by(order)
                .offset(epoch_offset)
            )
            if epoch_limit is not None:
                epoch_query = epoch_query.limit(epoch_limit)
            epochs = list(
                session.scalars(epoch_query)
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
            version_ids = {
                experiment.base_skill_version_id,
                *(epoch.parent_skill_version_id for epoch in epochs),
                *(
                    epoch.best_candidate_version_id
                    for epoch in epochs
                    if epoch.best_candidate_version_id
                ),
                *(
                    candidate.candidate_skill_version_id
                    for candidate in candidates
                    if candidate.candidate_skill_version_id
                ),
            }
            versions = (
                list(
                    session.scalars(
                        select(SkillPackageVersion).where(
                            SkillPackageVersion.id.in_(version_ids)
                        )
                    )
                )
                if version_ids
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
            "epoch_total": epoch_total,
            "version_metadata": {
                version.id: {
                    "id": version.id,
                    "version_number": version.version_number,
                    "package_hash": version.package_hash,
                    "parent_version_id": version.parent_version_id,
                    "source_type": version.source_type,
                    "status": version.status,
                    "created_at": version.created_at,
                }
                for version in versions
            },
            "epochs": [
                {
                    "id": epoch.id,
                    "number": epoch.epoch_number,
                    "status": epoch.status,
                    "parent_skill_version_id": epoch.parent_skill_version_id,
                    "best_candidate_version_id": epoch.best_candidate_version_id,
                    "decision": epoch.decision,
                    "evidence": json.loads(epoch.evidence_summary_json or "{}"),
                    "summary": json.loads(epoch.summary_json or "{}"),
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
                            "intended_failure_clusters": json.loads(
                                candidate.intended_failure_clusters_json or "[]"
                            ),
                            "intent": json.loads(candidate.intent_json or "{}"),
                            "change_stats": json.loads(
                                candidate.change_stats_json or "{}"
                            ),
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
        self.registry._require_enabled()
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
                "intended_failure_clusters": json.loads(
                    candidate.intended_failure_clusters_json or "[]"
                ),
                "intent": json.loads(candidate.intent_json or "{}"),
                "change_stats": json.loads(candidate.change_stats_json or "{}"),
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
        self.registry._require_enabled()
        with transaction(self.session_factory) as session:
            item = session.get(OptimizationExperiment, experiment_id)
            if item is None:
                raise AnalystBenchError(
                    "optimization_experiment_not_found", "找不到实验。", status_code=404
                )
            session.expunge(item)
            return item

    def delete(self, experiment_id: str) -> dict[str, int]:
        """Delete one inactive experiment and all data owned by it."""

        self.registry._require_enabled()
        quarantined: list[tuple[Path, Path]] = []
        workspace_roots: list[Path] = []
        committed = False
        try:
            with transaction(self.session_factory) as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.execute(text("BEGIN IMMEDIATE"))
                experiment = session.get(OptimizationExperiment, experiment_id)
                if experiment is None:
                    raise AnalystBenchError(
                        "optimization_experiment_not_found",
                        "找不到实验。",
                        status_code=404,
                    )
                if experiment.status == "running":
                    raise AnalystBenchError(
                        "optimization_experiment_delete_running",
                        "实验仍在运行，请先取消并等待任务停止。",
                        status_code=409,
                    )

                run_groups = list(
                    session.scalars(
                        select(OptimizationRunGroup).where(
                            OptimizationRunGroup.experiment_id == experiment_id
                        )
                    )
                )
                submission_ids = {
                    group.evaluation_submission_id for group in run_groups
                }
                for submission in session.scalars(
                    select(EvaluationSubmission).where(
                        EvaluationSubmission.purpose == "skill_optimization"
                    )
                ):
                    try:
                        context = json.loads(
                            submission.optimization_context_json or "{}"
                        )
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if (
                        isinstance(context, dict)
                        and context.get("experiment_id") == experiment_id
                    ):
                        submission_ids.add(submission.id)
                submissions = (
                    list(
                        session.scalars(
                            select(EvaluationSubmission).where(
                                EvaluationSubmission.id.in_(submission_ids)
                            )
                        )
                    )
                    if submission_ids
                    else []
                )
                if any(
                    submission.status not in TERMINAL_SUBMISSION_STATES
                    for submission in submissions
                ):
                    raise AnalystBenchError(
                        "optimization_experiment_delete_running",
                        "实验仍有评测批次在排队或运行，请先取消并等待任务停止。",
                        status_code=409,
                    )

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
                if any(
                    method_run.status in {"running", "cancelling"}
                    for method_run in method_runs
                ):
                    raise AnalystBenchError(
                        "optimization_experiment_delete_running",
                        "实验仍有命令在运行，请等待任务停止。",
                        status_code=409,
                    )
                method_run_ids = {method_run.id for method_run in method_runs}

                epoch_ids = set(
                    session.scalars(
                        select(OptimizationEpoch.id).where(
                            OptimizationEpoch.experiment_id == experiment_id
                        )
                    )
                )
                candidate_ids = (
                    set(
                        session.scalars(
                            select(CandidateMutation.id).where(
                                CandidateMutation.epoch_id.in_(epoch_ids)
                            )
                        )
                    )
                    if epoch_ids
                    else set()
                )
                identifiers = (
                    {experiment_id}
                    | submission_ids
                    | case_run_ids
                    | method_run_ids
                    | epoch_ids
                    | candidate_ids
                )
                related_jobs = [
                    job
                    for job in session.scalars(select(Job))
                    if self.submissions._job_references(
                        job.payload_json, identifiers
                    )
                ]
                if any(job.status == "running" for job in related_jobs):
                    raise AnalystBenchError(
                        "optimization_experiment_delete_running",
                        "实验仍有后台任务在运行，请稍后重试删除。",
                        status_code=409,
                    )

                run_directories = {
                    self.submissions._safe_submission_run_directory(
                        case_run.run_directory
                    )
                    for case_run in case_runs
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

                workspace_roots = [
                    self.settings.workspace_root_path / "evaluation" / submission_id
                    for submission_id in submission_ids
                ]
                for job in related_jobs:
                    session.delete(job)
                session.execute(
                    sql_delete(OptimizationSignal).where(
                        OptimizationSignal.experiment_id == experiment_id
                    )
                )
                session.execute(
                    sql_delete(CandidateComparison).where(
                        CandidateComparison.experiment_id == experiment_id
                    )
                )
                session.execute(
                    sql_delete(DecisionRecord).where(
                        DecisionRecord.experiment_id == experiment_id
                    )
                )
                session.execute(
                    sql_delete(OptimizationEvent).where(
                        OptimizationEvent.experiment_id == experiment_id
                    )
                )
                session.execute(
                    sql_delete(OptimizationRunGroup).where(
                        OptimizationRunGroup.experiment_id == experiment_id
                    )
                )
                if candidate_ids:
                    session.execute(
                        sql_delete(CandidateMutation).where(
                            CandidateMutation.id.in_(candidate_ids)
                        )
                    )
                if epoch_ids:
                    session.execute(
                        sql_delete(OptimizationEpoch).where(
                            OptimizationEpoch.id.in_(epoch_ids)
                        )
                    )
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
                session.delete(experiment)
            committed = True

            deleted_directories = 0
            for _original, quarantine in quarantined:
                shutil.rmtree(quarantine, ignore_errors=True)
                if not quarantine.exists():
                    deleted_directories += 1
            for workspace_root in workspace_roots:
                if workspace_root.is_dir():
                    shutil.rmtree(workspace_root, ignore_errors=True)
            return {
                "experiments_deleted": 1,
                "submissions_deleted": len(submission_ids),
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

    def resume(self, experiment_id: str) -> OptimizationExperiment:
        self.registry._require_enabled()
        self._verify_snapshot(experiment_id)
        with transaction(self.session_factory) as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
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
            snapshot = session.get(
                OptimizationDataSnapshot, item.data_snapshot_id, with_for_update=True
            )
            assert snapshot is not None
            self._assert_independent_snapshot_unused(session, item, snapshot)
            latest_epoch = session.scalar(
                select(OptimizationEpoch)
                .where(OptimizationEpoch.experiment_id == item.id)
                .order_by(OptimizationEpoch.epoch_number.desc())
                .limit(1)
            )
            self._assert_active_parent(session, item, latest_epoch)
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
            session.refresh(item)
            session.expunge(item)
            return item

    def cancel(self, experiment_id: str) -> OptimizationExperiment:
        self.registry._require_enabled()
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
                self.submissions.cancel_submission(
                    submission_id, allow_optimization=True
                )
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

    def events(
        self, experiment_id: str, *, limit: int = 500, offset: int = 0
    ) -> list[OptimizationEvent]:
        self.registry._require_enabled()
        self.get(experiment_id)
        with transaction(self.session_factory) as session:
            items = list(
                session.scalars(
                    self._page(
                        select(OptimizationEvent)
                        .where(OptimizationEvent.experiment_id == experiment_id)
                        .order_by(OptimizationEvent.created_at, OptimizationEvent.id),
                        limit=limit,
                        offset=offset,
                    )
                )
            )
            for item in items:
                session.expunge(item)
            return items
