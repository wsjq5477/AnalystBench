"""Skill optimization policy, snapshot and experiment endpoints."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from analystbench.skill_optimization.experiment import (
    EXPERIMENT_POLICY_LIMITS,
    OPTIMIZER_ROLE_SPECS,
    OptimizationExperimentService,
)
from analystbench.skill_optimization.patch import DEFAULT_PATCH_POLICY
from analystbench.skill_optimization.preflight import (
    DEFAULT_MINIMUM_FREE_BYTES,
    SkillOptimizationPreflightService,
)
from analystbench.skill_optimization.reporting import (
    build_optimization_ledger,
    render_optimization_ledger_csv,
    render_optimization_ledger_markdown,
    serialize_optimization_ledger,
)

router = APIRouter(tags=["skill-optimization"])

LEDGER_EXPORT_RESPONSES = {
    200: {
        "description": "Complete immutable optimization ledger download.",
        "headers": {
            "Content-Disposition": {"schema": {"type": "string"}},
            "Cache-Control": {"schema": {"type": "string"}},
            "X-Content-Type-Options": {"schema": {"type": "string"}},
        },
        "content": {
            "application/json": {"schema": {"type": "object"}},
            "text/markdown": {"schema": {"type": "string"}},
            "text/csv": {"schema": {"type": "string"}},
        },
    }
}


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    execution_profile_id: str
    prompt_bundle: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class VerifierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    static_policy: dict[str, Any] = Field(default_factory=dict)
    gate_policy: dict[str, Any] = Field(default_factory=dict)
    judge_config: dict[str, Any] = Field(default_factory=dict)


class SnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_key: str
    validation_case_paths: list[str] = Field(min_length=1)
    mode: str = "development_regression"
    train_case_paths: list[str] = Field(default_factory=list)
    hidden_test_case_paths: list[str] = Field(default_factory=list)
    prospective_holdout_case_paths: list[str] = Field(default_factory=list)


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    skill_id: str
    base_skill_version_id: str
    evaluation_target_id: str
    data_snapshot_id: str
    optimizer_policy_version_id: str
    verifier_bundle_version_id: str
    max_epochs: int | None = Field(default=None, ge=1)
    candidate_count: int | None = Field(default=None, ge=1, le=4)
    screening_case_count: int | None = Field(default=None, ge=1, le=1000)
    validation_repeats: int | None = Field(default=None, ge=1, le=7)
    max_repeats: int | None = Field(default=None, ge=1, le=15)
    early_stop_patience: int | None = Field(default=None, ge=1, le=20)
    created_by: str | None = Field(default=None, max_length=128)


class PreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_key: str | None = Field(default=None, min_length=1)
    evaluation_target_id: str | None = Field(default=None, min_length=1)
    execution_profile_id: str | None = Field(default=None, min_length=1)
    optimizer_policy_version_id: str | None = Field(default=None, min_length=1)
    verifier_bundle_version_id: str | None = Field(default=None, min_length=1)
    case_paths: list[str] | None = None
    data_snapshot_id: str | None = Field(default=None, min_length=1)


def service(request: Request) -> OptimizationExperimentService:
    return request.app.state.skill_optimization_service


@router.get("/skill-optimization/capabilities")
def optimization_capabilities(request: Request) -> dict[str, Any]:
    """Describe every user-facing strategy default and server safety ceiling."""

    settings = request.app.state.settings
    return {
        "schema_version": "skill_optimization.capabilities.v1",
        "defaults": {
            "max_epochs": settings.skill_optimization_max_epochs,
            "candidate_count": settings.skill_optimization_candidate_count,
            "screening_case_count": settings.skill_optimization_screening_case_count,
            "validation_repeats": settings.skill_optimization_validation_repeats,
            "max_repeats": settings.skill_optimization_max_repeats,
            "early_stop_patience": settings.skill_optimization_early_stop_patience,
            "min_overall_delta": settings.skill_optimization_min_overall_delta,
            "max_latency_growth": settings.skill_optimization_max_latency_growth,
            "max_token_growth": settings.skill_optimization_max_token_growth,
            "edit_budget_schedule": DEFAULT_PATCH_POLICY["edit_budget_schedule"],
            "screening_minimum_delta": -1.0,
            "screening_max_latency_growth": 0.50,
            "screening_critical_dimension_min_delta": -5.0,
            "bootstrap_samples": 2000,
            "bootstrap_confidence": 0.95,
            "critical_dimension_min_delta": 0.0,
            "critical_family_max_regression": -2.0,
            "min_candidate_win_probability": 0.0,
            "require_bootstrap_lower_bound_positive": True,
            "require_token_usage": True,
            "reject_failure_increase": True,
            "reject_new_failure_tags": True,
            "critical_dimensions": ["root_cause", "classification"],
            "protected_guardrail_metrics": [
                "forbidden_hit_count",
                "missing_chain_count",
            ],
            "optimizer_execution_attempts": 3,
            "format_repair_enabled": True,
            "optimizer_timeout_seconds": 1800,
            "optimizer_max_output_bytes": 10 * 1024 * 1024,
            "judge_timeout_seconds": 600,
            "judge_max_output_bytes": 2 * 1024 * 1024,
            "package_test_timeout_seconds": 30,
        },
        "ranges": {
            "max_epochs": {"minimum": 1, "maximum": settings.skill_optimization_max_epochs},
            **EXPERIMENT_POLICY_LIMITS,
            "edit_operations_per_epoch": {"minimum": 1, "maximum": 50},
            "bootstrap_samples": {"minimum": 0, "maximum": 100000},
            "bootstrap_confidence": {"exclusive_minimum": 0.5, "exclusive_maximum": 1.0},
            "optimizer_timeout_seconds": {"minimum": 1, "maximum": 7200},
            "optimizer_max_output_bytes": {
                "minimum": 1024,
                "maximum": 100 * 1024 * 1024,
            },
            "judge_timeout_seconds": {"minimum": 1, "maximum": 7200},
            "judge_max_output_bytes": {
                "minimum": 1024,
                "maximum": 100 * 1024 * 1024,
            },
            "package_test_timeout_seconds": {"minimum": 1, "maximum": 300},
            "optimizer_execution_attempts": {"minimum": 1, "maximum": 5},
        },
        "package_ceiling": {
            "max_files": settings.skill_optimization_max_files,
            "max_total_bytes": settings.skill_optimization_max_total_bytes,
            "max_single_file_bytes": settings.skill_optimization_max_single_file_bytes,
            "minimum_free_bytes": DEFAULT_MINIMUM_FREE_BYTES,
        },
        "patch_policy": {
            "supported_operations": sorted(DEFAULT_PATCH_POLICY["allowed_operations"]),
            "content_size_limits_removed": True,
            "removed_limits": [
                "max_changed_files",
                "max_added_tokens",
                "max_deleted_tokens",
                "max_single_file_change_ratio",
            ],
        },
        "evidence_policy": {
            "claims_truncated": False,
            "failed_cases_truncated": False,
            "rejected_history_truncated": False,
            "failed_case_threshold_removed": True,
            "label_length": 128,
            "format_repair_input_chars": 8000,
            "static_finding_details": 50,
            "diagnostic_tail_chars": 2000,
        },
        "optimizer_roles": [
            {"role": item["role"], "mission": item["mission"]}
            for item in OPTIMIZER_ROLE_SPECS
        ],
        "fixed_strategy": [
            {
                "key": "screening_survivors",
                "value": 1,
                "reason": "当前状态机每轮只对最优候选做成对验证。",
            },
            {
                "key": "optimizer_role_execution",
                "value": "sequential",
                "reason": "保持 Runner 输出与事件顺序可复现。",
            },
            {
                "key": "gray_zone_repeat_targets",
                "value": [5, 7],
                "reason": "统计灰区优先补到 5 次、再补到 7 次，且不超过用户设置的最大重复数。",
            },
            {
                "key": "statistical_gray_win_probability",
                "value": 0.55,
                "reason": "只对有轻微正向胜率但置信区间跨 0 的独立验证追加运行。",
            },
            {
                "key": "independent_validation_epochs",
                "value": 1,
                "reason": "避免根据同一 Validation 结果反复调参。",
            },
        ],
        "system_protections": [
            "禁止路径逃逸、符号链接和 setuid/setgid 文件",
            "Skill 根目录必须包含 SKILL.md",
            "Case 与同源组不能跨 Train/Validation 切分",
            "冻结后的 Case、日志和 Eval Spec 不允许漂移",
            "Optimizer 不接收 Validation、Hidden、Holdout 或标准答案正文",
            "凭据、私有路径、隐藏测试访问和网络外传会被拦截",
            "候选必须从当前 Active 不可变版本派生",
            "晋升使用原子绑定并拒绝覆盖并发变更",
            "Patch 只接受结构化操作和唯一文本锚点",
            "独立验证固定一个 Epoch，避免反复适配 Validation",
        ],
    }


@router.post("/skill-optimization/policies", status_code=status.HTTP_201_CREATED)
def create_policy(payload: PolicyCreate, request: Request) -> dict[str, Any]:
    item = service(request).create_policy(
        policy_key=payload.key,
        **payload.model_dump(exclude={"key"}),
    )
    return {
        "id": item.id,
        "key": item.policy_key,
        "version": item.version_number,
        "execution_profile_id": item.execution_profile_id,
        "prompt_bundle_hash": item.prompt_bundle_hash,
        "config": json.loads(item.config_json),
        "content_hash": item.content_hash,
        "created_at": item.created_at,
    }


@router.get("/skill-optimization/policies")
def list_policies(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "key": item.policy_key,
            "version": item.version_number,
            "execution_profile_id": item.execution_profile_id,
            "config": json.loads(item.config_json or "{}"),
            "content_hash": item.content_hash,
            "created_at": item.created_at,
        }
        for item in service(request).list_policies(limit=limit, offset=offset)
    ]


@router.post("/skill-optimization/verifiers", status_code=status.HTTP_201_CREATED)
def create_verifier(payload: VerifierCreate, request: Request) -> dict[str, Any]:
    item = service(request).create_verifier(
        bundle_key=payload.key,
        **payload.model_dump(exclude={"key"}),
    )
    return {
        "id": item.id,
        "key": item.bundle_key,
        "version": item.version_number,
        "static_policy": json.loads(item.static_policy_json),
        "gate_policy": json.loads(item.gate_policy_json),
        "judge_config": json.loads(item.judge_config_json),
        "content_hash": item.content_hash,
        "created_at": item.created_at,
    }


@router.get("/skill-optimization/verifiers")
def list_verifiers(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "key": item.bundle_key,
            "version": item.version_number,
            "static_policy": json.loads(item.static_policy_json or "{}"),
            "gate_policy": json.loads(item.gate_policy_json or "{}"),
            "judge_config": json.loads(item.judge_config_json or "{}"),
            "content_hash": item.content_hash,
            "created_at": item.created_at,
        }
        for item in service(request).list_verifiers(limit=limit, offset=offset)
    ]


@router.post("/skill-optimization/data-snapshots", status_code=status.HTTP_201_CREATED)
def create_snapshot(payload: SnapshotCreate, request: Request) -> dict[str, Any]:
    item = service(request).create_snapshot(**payload.model_dump())
    return {
        "id": item.id,
        "dataset_key": item.dataset_key,
        "mode": item.mode,
        "train_cases": json.loads(item.train_cases_json),
        "validation_cases": json.loads(item.validation_cases_json),
        "hidden_test_cases": json.loads(item.hidden_test_cases_json),
        "prospective_holdout_cases": json.loads(item.prospective_holdout_cases_json),
        "content_hash": item.content_hash,
        "created_at": item.created_at,
    }


@router.get("/skill-optimization/data-snapshots")
def list_snapshots(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "dataset_key": item.dataset_key,
            "mode": item.mode,
            "train_cases": json.loads(item.train_cases_json or "[]"),
            "validation_cases": json.loads(item.validation_cases_json or "[]"),
            "hidden_test_cases": json.loads(item.hidden_test_cases_json or "[]"),
            "prospective_holdout_cases": json.loads(item.prospective_holdout_cases_json or "[]"),
            "content_hash": item.content_hash,
            "created_at": item.created_at,
        }
        for item in service(request).list_snapshots(limit=limit, offset=offset)
    ]


@router.post("/skill-optimization/experiments", status_code=status.HTTP_201_CREATED)
def create_experiment(payload: ExperimentCreate, request: Request) -> dict[str, Any]:
    item = service(request).create_experiment(**payload.model_dump(exclude_none=True))
    return experiment_view(item)


@router.get("/skill-optimization/experiments")
def list_experiments(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return [
        experiment_view(item)
        for item in service(request).list_experiments(limit=limit, offset=offset)
    ]


@router.get("/skill-optimization/experiments/{experiment_id}")
def get_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).get(experiment_id))


@router.delete("/skill-optimization/experiments/{experiment_id}")
def delete_experiment(experiment_id: str, request: Request) -> dict[str, int]:
    return service(request).delete(experiment_id)


@router.post("/skill-optimization/experiments/{experiment_id}:start")
def start_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).start(experiment_id))


@router.post("/skill-optimization/experiments/{experiment_id}:resume")
def resume_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).resume(experiment_id))


@router.post("/skill-optimization/experiments/{experiment_id}:cancel")
def cancel_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    return experiment_view(service(request).cancel(experiment_id))


@router.get("/skill-optimization/experiments/{experiment_id}/events")
def list_events(
    experiment_id: str,
    request: Request,
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "epoch_id": item.epoch_id,
            "candidate_mutation_id": item.candidate_mutation_id,
            "type": item.event_type,
            "payload": json.loads(item.payload_json or "{}"),
            "created_at": item.created_at,
        }
        for item in service(request).events(
            experiment_id, limit=limit, offset=offset
        )
    ]


@router.get("/skill-optimization/experiments/{experiment_id}/detail")
def experiment_detail(
    experiment_id: str,
    request: Request,
    epoch_offset: int = Query(default=0, ge=0),
    epoch_limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    detail = service(request).detail(
        experiment_id,
        epoch_offset=epoch_offset,
        epoch_limit=epoch_limit,
        newest_first=True,
    )
    epochs = detail["epochs"]
    return {
        "experiment": experiment_view(detail["experiment"]),
        "summary": service(request).summary(experiment_id),
        "version_metadata": detail["version_metadata"],
        "epochs": epochs,
        "pagination": {
            "offset": epoch_offset,
            "limit": epoch_limit,
            "total": detail["epoch_total"],
            "has_more": epoch_offset + epoch_limit < detail["epoch_total"],
            "newest_first": True,
        },
    }


@router.get("/skill-optimization/experiments/{experiment_id}/ledger")
def experiment_ledger(experiment_id: str, request: Request) -> dict[str, Any]:
    return build_optimization_ledger(service(request).detail(experiment_id))


@router.get(
    "/skill-optimization/experiments/{experiment_id}/export",
    response_class=Response,
    responses=LEDGER_EXPORT_RESPONSES,
)
def export_experiment_ledger(
    experiment_id: str,
    request: Request,
    format: Literal["json", "markdown", "csv"] = Query(default="json"),
) -> Response:
    ledger = build_optimization_ledger(service(request).detail(experiment_id))
    if format == "markdown":
        content = render_optimization_ledger_markdown(ledger)
        media_type = "text/markdown; charset=utf-8"
        suffix = "md"
    elif format == "csv":
        content = render_optimization_ledger_csv(ledger)
        media_type = "text/csv; charset=utf-8"
        suffix = "csv"
    else:
        content = serialize_optimization_ledger(ledger)
        media_type = "application/json"
        suffix = "json"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="skill-optimization-{experiment_id}.{suffix}"'
        ),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type=media_type, headers=headers)


@router.post("/skill-optimization/preflight")
def run_preflight(payload: PreflightRequest, request: Request) -> dict[str, Any]:
    checker = SkillOptimizationPreflightService(
        request.app.state.session_factory, request.app.state.settings
    )
    return checker.run(**payload.model_dump())


@router.get("/skill-optimization/candidates/{candidate_id}")
def candidate_detail(candidate_id: str, request: Request) -> dict[str, Any]:
    return service(request).candidate_detail(candidate_id)


def experiment_view(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "skill_id": item.skill_id,
        "base_skill_version_id": item.base_skill_version_id,
        "evaluation_target_id": item.evaluation_target_id,
        "data_snapshot_id": item.data_snapshot_id,
        "optimizer_policy_version_id": item.optimizer_policy_version_id,
        "verifier_bundle_version_id": item.verifier_bundle_version_id,
        "status": item.status,
        "current_epoch_number": item.current_epoch_number,
        "max_epochs": item.max_epochs,
        "stop_reason": item.stop_reason,
        "config": json.loads(item.config_snapshot_json or "{}"),
        "error": json.loads(item.error_json or "{}"),
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
