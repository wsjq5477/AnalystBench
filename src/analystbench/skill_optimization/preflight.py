"""Read-only private-environment preflight for Skill optimization.

The service is intentionally independent from the CLI and HTTP layers.  Both
callers can pass their existing session factory and serialize the returned
dictionary directly.  Checks never persist application state.  The only write
is a short-lived file used to prove that the managed root is writable.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationHarness,
    EvaluationMethod,
    EvaluationModel,
    EvaluationTarget,
    ExecutionProfile,
    OptimizationDataSnapshot,
    OptimizerPolicyVersion,
    Skill,
    VerifierBundleVersion,
)
from analystbench.evaluation.submission import inspect_case_logs
from analystbench.execution.resolver import resolve_executable
from analystbench.skill_optimization.snapshot import build_snapshot_manifest
from analystbench.storage.content import canonical_json, content_hash

CheckStatus = Literal["PASS", "WARN", "FAIL"]

DEFAULT_MINIMUM_FREE_BYTES = 1024 * 1024 * 1024
VERSION_TIMEOUT_SECONDS = 5
VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9])v?(\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?)"
)
SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "AUTH",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)
CORE_TABLES = frozenset(
    {
        "alembic_version",
        "candidate_comparisons",
        "candidate_mutations",
        "decision_records",
        "evaluation_harnesses",
        "evaluation_methods",
        "evaluation_models",
        "evaluation_targets",
        "evaluation_variants",
        "execution_profiles",
        "optimization_data_snapshots",
        "optimization_epochs",
        "optimization_events",
        "optimization_experiments",
        "optimization_run_groups",
        "optimization_signals",
        "optimizer_policy_versions",
        "skill_package_versions",
        "skill_binding_history",
        "skill_target_bindings",
        "skills",
        "verifier_bundle_versions",
    }
)


@dataclass(frozen=True)
class PreflightCheck:
    """One stable, serializable preflight result."""

    code: str
    status: CheckStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class VersionProbe:
    """Sanitized executable probe result; raw process output is never retained."""

    runnable: bool
    version: str | None = None
    reason: str | None = None


VersionRunner = Callable[[str], VersionProbe]
ExecutableResolver = Callable[[str], str | None]


def _safe_version(output: str) -> str | None:
    match = VERSION_RE.search(output[:512])
    return match.group(1) if match is not None else None


def probe_executable_version(executable: str) -> VersionProbe:
    """Run ``--version`` without forwarding credentials or process output."""

    safe_environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            env=safe_environment,
            stdin=subprocess.DEVNULL,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return VersionProbe(False, reason="timeout")
    except OSError:
        return VersionProbe(False, reason="execution_failed")
    if completed.returncode != 0:
        return VersionProbe(False, reason="nonzero_exit")
    # Keep only a version-shaped token.  Arbitrary stdout/stderr could contain
    # data owned by a third-party CLI and must not enter API responses or logs.
    version = _safe_version(f"{completed.stdout}\n{completed.stderr}")
    return VersionProbe(True, version=version)


def probe_bubblewrap_sandbox(executable: str) -> VersionProbe:
    """Verify both the binary and the namespace capability used by package tests."""

    version_probe = probe_executable_version(executable)
    if not version_probe.runnable:
        return version_probe
    try:
        completed = subprocess.run(
            [
                executable,
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--ro-bind",
                "/",
                "/",
                "--",
                "/bin/true",
            ],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            env={"PATH": "/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return VersionProbe(False, version=version_probe.version, reason="timeout")
    except OSError:
        return VersionProbe(
            False,
            version=version_probe.version,
            reason="execution_failed",
        )
    if completed.returncode != 0:
        return VersionProbe(
            False,
            version=version_probe.version,
            reason="namespace_unavailable",
        )
    return version_probe


def _overall_status(checks: Sequence[PreflightCheck]) -> CheckStatus:
    if any(item.status == "FAIL" for item in checks):
        return "FAIL"
    if any(item.status == "WARN" for item in checks):
        return "WARN"
    return "PASS"


def _nearest_existing_path(path: Path) -> Path | None:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return current


def _safe_case_directory(root: Path, case_path: str) -> Path | None:
    relative = Path(case_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return None
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        return None
    return candidate


class SkillOptimizationPreflightService:
    """Perform environment and optional experiment-input checks without writes."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        executable_resolver: ExecutableResolver = resolve_executable,
        version_runner: VersionRunner = probe_executable_version,
        sandbox_runner: VersionRunner = probe_bubblewrap_sandbox,
        minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
        alembic_config_path: Path | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.executable_resolver = executable_resolver
        self.version_runner = version_runner
        self.sandbox_runner = sandbox_runner
        self.minimum_free_bytes = max(0, minimum_free_bytes)
        self.alembic_config_path = alembic_config_path or (
            Path(__file__).resolve().parents[3] / "alembic.ini"
        )

    def run(
        self,
        *,
        skill_key: str | None = None,
        evaluation_target_id: str | None = None,
        execution_profile_id: str | None = None,
        optimizer_policy_version_id: str | None = None,
        verifier_bundle_version_id: str | None = None,
        case_paths: Sequence[str] | None = None,
        data_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Return ``{status, checks}`` for base and requested contextual checks."""

        checks: list[PreflightCheck] = []
        checks.append(self._check_feature_switch())
        checks.extend(self._check_managed_root())
        executable_results = self._check_executables(checks)
        database_available = self._check_database(checks)
        checks.append(self._check_disk_space())

        requested_cases = list(dict.fromkeys(case_paths or ()))
        if database_available:
            try:
                with self.session_factory() as session:
                    context = self._check_database_context(
                        session,
                        checks,
                        skill_key=skill_key,
                        evaluation_target_id=evaluation_target_id,
                        execution_profile_id=execution_profile_id,
                        optimizer_policy_version_id=optimizer_policy_version_id,
                        verifier_bundle_version_id=verifier_bundle_version_id,
                        executable_results=executable_results,
                    )
                    snapshot_cases = self._check_snapshot(
                        session, checks, data_snapshot_id=data_snapshot_id
                    )
                    all_cases = list(
                        dict.fromkeys([*requested_cases, *snapshot_cases])
                    )
                    if all_cases:
                        self._check_cases(checks, all_cases)
                    if requested_cases and snapshot_cases:
                        missing = sorted(set(requested_cases) - set(snapshot_cases))
                        checks.append(
                            PreflightCheck(
                                code="requested_cases_in_snapshot",
                                status="FAIL" if missing else "PASS",
                                message=(
                                    "显式指定的 Case 全部属于数据快照。"
                                    if not missing
                                    else "部分显式指定的 Case 不属于数据快照。"
                                ),
                                details={"missing_count": len(missing)},
                            )
                        )
                    self._check_skill_directory(checks, context)
            except SQLAlchemyError:
                checks.append(
                    PreflightCheck(
                        code="context_checks_failed",
                        status="FAIL",
                        message="读取 Skill 自优化上下文时数据库发生变化或不可用。",
                    )
                )
        elif any(
            value
            for value in (
                skill_key,
                evaluation_target_id,
                execution_profile_id,
                optimizer_policy_version_id,
                verifier_bundle_version_id,
                data_snapshot_id,
            )
        ) or requested_cases:
            checks.append(
                PreflightCheck(
                    code="context_checks_skipped",
                    status="FAIL",
                    message="数据库不可用，无法执行 Skill 自优化上下文检查。",
                )
            )

        return {
            "status": _overall_status(checks),
            "checks": [item.as_dict() for item in checks],
        }

    def _check_feature_switch(self) -> PreflightCheck:
        enabled = self.settings.skill_optimization_enabled
        return PreflightCheck(
            code="feature_switch",
            status="PASS" if enabled else "FAIL",
            message=(
                "Skill 自优化功能开关已启用。"
                if enabled
                else "Skill 自优化功能开关未启用。"
            ),
        )

    def _check_managed_root(self) -> list[PreflightCheck]:
        root = self.settings.skill_optimization_root_path.expanduser()
        explicitly_configured = self.settings.skill_optimization_managed_root is not None
        checks = [
            PreflightCheck(
                code="managed_root_configured",
                status="PASS" if explicitly_configured else "FAIL",
                message=(
                    "Managed root 已显式配置。"
                    if explicitly_configured
                    else "必须显式配置 Skill 自优化 Managed root。"
                ),
            ),
            PreflightCheck(
                code="managed_root_absolute",
                status="PASS" if root.is_absolute() else "FAIL",
                message=(
                    "Managed root 是绝对路径。"
                    if root.is_absolute()
                    else "Managed root 必须配置为绝对路径。"
                ),
                details={"path": str(root)},
            )
        ]
        if not root.is_dir():
            checks.append(
                PreflightCheck(
                    code="managed_root_writable",
                    status="FAIL",
                    message="Managed root 不存在或不是目录；预检不会自动创建它。",
                    details={"path": str(root)},
                )
            )
            return checks

        file_descriptor: int | None = None
        probe_path: str | None = None
        cleanup_ok = True
        try:
            file_descriptor, probe_path = tempfile.mkstemp(
                dir=root, prefix=".analystbench-preflight-"
            )
            os.write(file_descriptor, b"preflight")
            os.fsync(file_descriptor)
            writable = True
        except OSError:
            writable = False
        finally:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    cleanup_ok = False
            if probe_path is not None:
                try:
                    Path(probe_path).unlink(missing_ok=True)
                except OSError:
                    cleanup_ok = False
        writable = writable and cleanup_ok
        checks.append(
            PreflightCheck(
                code="managed_root_writable",
                status="PASS" if writable else "FAIL",
                message=(
                    "Managed root 可写，临时探测文件已清理。"
                    if writable
                    else "Managed root 不可写。"
                ),
                details={"path": str(root)},
            )
        )
        return checks

    def _probe_named_executable(self, name: str) -> tuple[str | None, VersionProbe]:
        try:
            path = self.executable_resolver(name)
        except OSError:
            path = None
        if path is None:
            return None, VersionProbe(False, reason="not_found")
        try:
            runner = self.sandbox_runner if name == "bwrap" else self.version_runner
            return path, runner(path)
        except OSError:
            return path, VersionProbe(False, reason="execution_failed")

    def _check_executables(
        self, checks: list[PreflightCheck]
    ) -> dict[str, tuple[str | None, VersionProbe]]:
        results = {
            name: self._probe_named_executable(name)
            for name in ("git", "claude", "bwrap")
        }
        git_path, git_probe = results["git"]
        checks.append(
            PreflightCheck(
                code="git_executable",
                status="PASS" if git_path and git_probe.runnable else "FAIL",
                message=(
                    "Git 可发现且版本命令可运行。"
                    if git_path and git_probe.runnable
                    else "Git 不可发现或版本命令不可运行。"
                ),
                details={
                    "executable": git_path,
                    "version": git_probe.version,
                    "reason": git_probe.reason,
                },
            )
        )
        available_runners = (
            ["claude"]
            if results["claude"][0] and results["claude"][1].runnable
            else []
        )
        runner_details = {
            name: {
                "available": bool(path and probe.runnable),
                "executable": path,
                "version": probe.version,
                "reason": probe.reason,
            }
            for name, (path, probe) in results.items()
            if name == "claude"
        }
        checks.append(
            PreflightCheck(
                code="agent_runners",
                status="PASS" if available_runners else "FAIL",
                message=(
                    "至少一个受支持的 Agent CLI 可运行。"
                    if available_runners
                    else "claude 不可发现或版本命令不可运行。"
                ),
                details={
                    "available": available_runners,
                    "runners": runner_details,
                },
            )
        )
        sandbox_path, sandbox_probe = results["bwrap"]
        checks.append(
            PreflightCheck(
                code="package_test_sandbox",
                status=(
                    "PASS"
                    if sandbox_path and sandbox_probe.runnable
                    else "WARN"
                ),
                message=(
                    "bubblewrap 可运行；声明式 Skill 包测试将使用无网络命名空间。"
                    if sandbox_path and sandbox_probe.runnable
                    else (
                        "bubblewrap 不可用；未声明包测试的 Skill 可运行，"
                        "声明了包测试的候选会被静态拒绝。"
                    )
                ),
                details={
                    "executable": sandbox_path,
                    "version": sandbox_probe.version,
                    "reason": sandbox_probe.reason,
                },
            )
        )
        return results

    def _expected_migration_heads(self) -> set[str] | None:
        if not self.alembic_config_path.is_file():
            return None
        try:
            config = Config(str(self.alembic_config_path))
            return set(ScriptDirectory.from_config(config).get_heads())
        except Exception:  # Alembic configuration errors are reported, not raised.
            return None

    def _check_database(self, checks: list[PreflightCheck]) -> bool:
        try:
            with self.session_factory() as session:
                connection = session.connection()
                table_names = set(inspect(connection).get_table_names())
                missing_tables = sorted(CORE_TABLES - table_names)
                checks.append(
                    PreflightCheck(
                        code="database_core_tables",
                        status="FAIL" if missing_tables else "PASS",
                        message=(
                            "Skill 自优化核心表齐全。"
                            if not missing_tables
                            else "数据库缺少 Skill 自优化核心表。"
                        ),
                        details={"missing_tables": missing_tables},
                    )
                )
                current_heads = (
                    set(
                        session.execute(
                            text("SELECT version_num FROM alembic_version")
                        ).scalars()
                    )
                    if "alembic_version" in table_names
                    else set()
                )
                expected_heads = self._expected_migration_heads()
                if expected_heads is None:
                    migration_status: CheckStatus = "WARN" if current_heads else "FAIL"
                    migration_message = (
                        "已读取数据库迁移版本，但无法定位仓库 Alembic head。"
                        if current_heads
                        else "数据库没有 Alembic 迁移版本。"
                    )
                else:
                    migration_status = (
                        "PASS" if current_heads == expected_heads else "FAIL"
                    )
                    migration_message = (
                        "数据库已位于当前 Alembic head。"
                        if migration_status == "PASS"
                        else "数据库迁移版本与当前 Alembic head 不一致。"
                    )
                checks.append(
                    PreflightCheck(
                        code="database_migration_head",
                        status=migration_status,
                        message=migration_message,
                        details={
                            "current_heads": sorted(current_heads),
                            "expected_heads": (
                                sorted(expected_heads)
                                if expected_heads is not None
                                else None
                            ),
                        },
                    )
                )
                return not missing_tables
        except Exception:
            checks.append(
                PreflightCheck(
                    code="database_access",
                    status="FAIL",
                    message="数据库不可访问；未输出底层连接错误或连接信息。",
                )
            )
            return False

    def _check_disk_space(self) -> PreflightCheck:
        root = self.settings.skill_optimization_root_path.expanduser()
        anchor = _nearest_existing_path(root)
        if anchor is None:
            return PreflightCheck(
                code="disk_space",
                status="FAIL",
                message="无法确定 Managed root 所在文件系统的可用空间。",
            )
        try:
            usage = shutil.disk_usage(anchor)
        except OSError:
            return PreflightCheck(
                code="disk_space",
                status="FAIL",
                message="无法读取 Managed root 所在文件系统的可用空间。",
                details={"path": str(anchor)},
            )
        enough = usage.free >= self.minimum_free_bytes
        return PreflightCheck(
            code="disk_space",
            status="PASS" if enough else "FAIL",
            message=(
                "Managed root 所在文件系统空间充足。"
                if enough
                else "Managed root 所在文件系统可用空间不足。"
            ),
            details={
                "path": str(anchor),
                "free_bytes": usage.free,
                "required_free_bytes": self.minimum_free_bytes,
            },
        )

    @staticmethod
    def _target_by_reference(
        session: Session, reference: str
    ) -> EvaluationTarget | None:
        return session.scalar(
            select(EvaluationTarget)
            .where(
                or_(
                    EvaluationTarget.id == reference,
                    EvaluationTarget.target_key == reference,
                )
            )
            .order_by(EvaluationTarget.version_number.desc())
            .limit(1)
        )

    @staticmethod
    def _profile_by_reference(
        session: Session, reference: str
    ) -> ExecutionProfile | None:
        return session.scalar(
            select(ExecutionProfile)
            .where(
                or_(
                    ExecutionProfile.id == reference,
                    ExecutionProfile.name == reference,
                )
            )
            .order_by(ExecutionProfile.version_number.desc())
            .limit(1)
        )

    def _check_database_context(
        self,
        session: Session,
        checks: list[PreflightCheck],
        *,
        skill_key: str | None,
        evaluation_target_id: str | None,
        execution_profile_id: str | None,
        optimizer_policy_version_id: str | None,
        verifier_bundle_version_id: str | None,
        executable_results: dict[str, tuple[str | None, VersionProbe]],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {"skill": None, "target": None, "harness": None}
        skill = (
            session.scalar(
                select(Skill).where(
                    Skill.skill_key == skill_key,
                    Skill.archived_at.is_(None),
                )
            )
            if skill_key
            else None
        )
        if skill_key:
            checks.append(
                PreflightCheck(
                    code="skill_registered",
                    status="PASS" if skill is not None else "FAIL",
                    message=(
                        "Skill 已注册。" if skill is not None else "找不到指定的 Skill。"
                    ),
                    details={"skill_key": skill_key},
                )
            )
        context["skill"] = skill

        target = (
            self._target_by_reference(session, evaluation_target_id)
            if evaluation_target_id
            else None
        )
        harness = session.get(EvaluationHarness, target.harness_id) if target else None
        model = (
            session.get(EvaluationModel, target.model_id)
            if target is not None and target.model_id
            else None
        )
        if evaluation_target_id:
            target_ready = bool(
                target
                and target.status == "frozen"
                and target.materialized_method_id
                and harness
                and harness.status == "frozen"
                and (
                    (harness.model_policy == "none" and target.model_id is None)
                    or (
                        harness.model_policy == "required"
                        and model is not None
                        and model.status == "frozen"
                    )
                )
            )
            checks.append(
                PreflightCheck(
                    code="evaluation_target_frozen",
                    status="PASS" if target_ready else "FAIL",
                    message=(
                        "Evaluation Target、Harness 和所需 Model 已冻结。"
                        if target_ready
                        else "Evaluation Target 不存在、未冻结或其 Harness/Model 未就绪。"
                    ),
                    details={"target_id": target.id if target else None},
                )
            )
            target_runner_ready = False
            target_runner_details: dict[str, Any] = {"reason": "target_unavailable"}
            if target is not None and harness is not None:
                method = (
                    session.get(EvaluationMethod, target.materialized_method_id)
                    if target.materialized_method_id
                    else None
                )
                command_template = (
                    method.command_template
                    if method is not None
                    else harness.command_template
                )
                try:
                    argv = shlex.split(command_template, posix=True)
                except ValueError:
                    argv = []
                if argv:
                    requested_executable = argv[0]
                    if requested_executable == "claude":
                        path, probe = executable_results["claude"]
                    elif requested_executable == "opencode":
                        path = None
                        probe = VersionProbe(False, reason="unsupported_runner")
                    else:
                        path, probe = self._probe_named_executable(requested_executable)
                    target_runner_ready = bool(path and probe.runnable)
                    target_runner_details = {
                        "executable": path,
                        "version": probe.version,
                        "reason": probe.reason,
                    }
                else:
                    target_runner_details = {"reason": "command_template_invalid"}
            checks.append(
                PreflightCheck(
                    code="evaluation_target_runner",
                    status="PASS" if target_runner_ready else "FAIL",
                    message=(
                        "Evaluation Target 当前冻结命令的可执行文件可运行。"
                        if target_runner_ready
                        else "Evaluation Target 当前冻结命令的可执行文件不可运行。"
                    ),
                    details=target_runner_details,
                )
            )
        context.update({"target": target, "harness": harness})

        if skill is not None and target is not None and harness is not None:
            compatible = skill.harness_key == harness.harness_key
            checks.append(
                PreflightCheck(
                    code="skill_target_compatible",
                    status="PASS" if compatible else "FAIL",
                    message=(
                        "Skill 与 Target 的 Harness key 匹配。"
                        if compatible
                        else "Skill 与 Target 的 Harness key 不匹配。"
                    ),
                    details={
                        "skill_harness_key": skill.harness_key,
                        "target_harness_key": harness.harness_key,
                    },
                )
            )
            invocation_present = (
                skill.invoke_as in harness.command_template
                or "{skill}" in harness.command_template
            )
            checks.append(
                PreflightCheck(
                    code="skill_invocation_in_harness",
                    status="PASS" if invocation_present else "WARN",
                    message=(
                        "Harness 命令显式包含 Skill 调用名或 {skill} 占位符。"
                        if invocation_present
                        else "Harness 命令未显式包含 Skill 调用名，请确认其通过其他方式调用 Skill。"
                    ),
                    details={"invoke_as": skill.invoke_as},
                )
            )

        profile = (
            self._profile_by_reference(session, execution_profile_id)
            if execution_profile_id
            else None
        )
        policy = (
            session.get(OptimizerPolicyVersion, optimizer_policy_version_id)
            if optimizer_policy_version_id
            else None
        )
        if policy is not None and profile is None:
            profile = session.get(ExecutionProfile, policy.execution_profile_id)
        if optimizer_policy_version_id:
            checks.append(
                PreflightCheck(
                    code="optimizer_policy_exists",
                    status="PASS" if policy is not None else "FAIL",
                    message=(
                        "Optimizer Policy 存在。"
                        if policy is not None
                        else "找不到指定的 Optimizer Policy。"
                    ),
                )
            )
        if policy is not None and execution_profile_id and profile is not None:
            policy_matches = policy.execution_profile_id == profile.id
            checks.append(
                PreflightCheck(
                    code="optimizer_policy_profile_compatible",
                    status="PASS" if policy_matches else "FAIL",
                    message=(
                        "Optimizer Policy 使用指定的执行配置。"
                        if policy_matches
                        else "Optimizer Policy 与指定的执行配置不匹配。"
                    ),
                )
            )
        if execution_profile_id or policy is not None:
            frozen = profile is not None and profile.status == "frozen"
            checks.append(
                PreflightCheck(
                    code="execution_profile_frozen",
                    status="PASS" if frozen else "FAIL",
                    message=(
                        "Optimizer 执行配置存在且已冻结。"
                        if frozen
                        else "Optimizer 执行配置不存在或未冻结。"
                    ),
                    details={"profile_id": profile.id if profile else None},
                )
            )
            runner_ready = False
            runner_name = profile.runner if profile else None
            if profile is not None and profile.runner == "claude":
                try:
                    configuration = json.loads(profile.configuration_json or "{}")
                except json.JSONDecodeError:
                    configuration = None
                requested_executable = (
                    str(configuration.get("executable") or profile.runner)
                    if isinstance(configuration, dict)
                    else profile.runner
                )
                if requested_executable == profile.runner:
                    path, probe = executable_results["claude"]
                else:
                    path, probe = self._probe_named_executable(requested_executable)
                runner_ready = bool(path and probe.runnable)
                runner_details = {
                    "runner": runner_name,
                    "executable": path,
                    "version": probe.version,
                    "reason": probe.reason,
                }
            else:
                runner_details = {"reason": "unsupported_runner"}
            checks.append(
                PreflightCheck(
                    code="execution_profile_runner",
                    status="PASS" if runner_ready else "FAIL",
                    message=(
                        "Optimizer 执行配置所需的 Agent CLI 可运行。"
                        if runner_ready
                        else "Optimizer 执行配置所需的 Agent CLI 不可运行。"
                    ),
                    details=runner_details,
                )
            )

        verifier = (
            session.get(VerifierBundleVersion, verifier_bundle_version_id)
            if verifier_bundle_version_id
            else None
        )
        if verifier_bundle_version_id:
            checks.append(
                PreflightCheck(
                    code="verifier_bundle_exists",
                    status="PASS" if verifier is not None else "FAIL",
                    message=(
                        "Verifier Bundle 存在。"
                        if verifier is not None
                        else "找不到指定的 Verifier Bundle。"
                    ),
                    details={
                        "verifier_bundle_version_id": (
                            verifier.id if verifier is not None else None
                        )
                    },
                )
            )
            self._check_verifier_runner(checks, verifier, executable_results)
        return context

    def _check_verifier_runner(
        self,
        checks: list[PreflightCheck],
        verifier: VerifierBundleVersion | None,
        executable_results: dict[str, tuple[str | None, VersionProbe]],
    ) -> None:
        if verifier is None:
            checks.append(
                PreflightCheck(
                    code="verifier_judge_runner",
                    status="FAIL",
                    message="Verifier Bundle 不存在，无法检查 Judge runner。",
                    details={"reason": "verifier_not_found"},
                )
            )
            return
        try:
            judge = json.loads(verifier.judge_config_json or "{}")
            if not isinstance(judge, dict):
                raise ValueError
            configuration = judge.get("configuration", {})
            if not isinstance(configuration, dict):
                raise ValueError
        except (json.JSONDecodeError, TypeError, ValueError):
            checks.append(
                PreflightCheck(
                    code="verifier_judge_runner",
                    status="FAIL",
                    message="Verifier Judge 配置无法解析。",
                    details={"reason": "invalid_judge_config"},
                )
            )
            return

        runner = str(judge.get("runner") or "claude").strip().lower()
        if runner in {"lexical", "debug"}:
            checks.append(
                PreflightCheck(
                    code="verifier_judge_runner",
                    status="WARN",
                    message="Verifier 使用 lexical/debug Judge，仅适合调试，不作为正式语义验收。",
                    details={"runner": runner, "reason": "debug_judge"},
                )
            )
            return
        if runner != "claude":
            checks.append(
                PreflightCheck(
                    code="verifier_judge_runner",
                    status="FAIL",
                    message="Verifier Judge runner 不受 Skill 自优化公开流程支持。",
                    details={"reason": "unsupported_runner"},
                )
            )
            return

        requested_executable = str(
            configuration.get("executable") or judge.get("executable") or "claude"
        )
        if requested_executable == "claude":
            path, probe = executable_results["claude"]
        else:
            path, probe = self._probe_named_executable(requested_executable)
        runner_ready = bool(path and probe.runnable)
        checks.append(
            PreflightCheck(
                code="verifier_judge_runner",
                status="PASS" if runner_ready else "FAIL",
                message=(
                    "Verifier claude Judge 配置有效且当前可运行。"
                    if runner_ready
                    else "Verifier claude Judge 当前不可运行。"
                ),
                details={
                    "runner": runner,
                    "executable": path,
                    "version": probe.version,
                    "reason": probe.reason,
                },
            )
        )

    def _check_skill_directory(
        self, checks: list[PreflightCheck], context: dict[str, Any]
    ) -> None:
        skill: Skill | None = context.get("skill")
        if skill is None:
            return
        harness: EvaluationHarness | None = context.get("harness")
        if harness is not None:
            base_dir = (
                Path(harness.skill_base_dir).expanduser()
                if harness.skill_base_dir
                else None
            )
            expected = base_dir / skill.skill_key if base_dir else None
            structure_ready = bool(
                base_dir
                and base_dir.is_absolute()
                and base_dir.is_dir()
                and expected
                and expected.is_dir()
                and (expected / "SKILL.md").is_file()
            )
            source_matches = bool(
                structure_ready
                and Path(skill.source_path).expanduser().resolve() == expected.resolve()
            )
            checks.append(
                PreflightCheck(
                    code="harness_skill_directory",
                    status="PASS" if structure_ready and source_matches else "FAIL",
                    message=(
                        "Harness Skill 目录结构正确，且与注册源目录一致。"
                        if structure_ready and source_matches
                        else "Harness Skill 目录缺失、结构无效或与注册源目录不一致。"
                    ),
                    details={
                        "skill_base_dir": str(base_dir) if base_dir else None,
                        "expected_source": str(expected) if expected else None,
                        "registered_source": skill.source_path,
                    },
                )
            )
            return

        source = Path(skill.source_path).expanduser()
        ready = source.is_absolute() and source.is_dir() and (source / "SKILL.md").is_file()
        checks.append(
            PreflightCheck(
                code="skill_source_directory",
                status="PASS" if ready else "FAIL",
                message=(
                    "Skill 源目录存在且包含 SKILL.md。"
                    if ready
                    else "Skill 源目录必须是包含 SKILL.md 的绝对路径。"
                ),
                details={"source_path": str(source)},
            )
        )

    def _check_snapshot(
        self,
        session: Session,
        checks: list[PreflightCheck],
        *,
        data_snapshot_id: str | None,
    ) -> list[str]:
        if not data_snapshot_id:
            return []
        snapshot = session.get(OptimizationDataSnapshot, data_snapshot_id)
        if snapshot is None:
            checks.append(
                PreflightCheck(
                    code="data_snapshot_exists",
                    status="FAIL",
                    message="找不到指定的数据快照。",
                )
            )
            return []
        checks.append(
            PreflightCheck(
                code="data_snapshot_exists",
                status="PASS",
                message="数据快照存在。",
                details={"snapshot_id": snapshot.id, "mode": snapshot.mode},
            )
        )
        try:
            train = json.loads(snapshot.train_cases_json or "[]")
            validation = json.loads(snapshot.validation_cases_json or "[]")
            hidden = json.loads(snapshot.hidden_test_cases_json or "[]")
            prospective = json.loads(snapshot.prospective_holdout_cases_json or "[]")
            stored_inputs = json.loads(snapshot.case_input_hashes_json or "{}")
            stored_specs = json.loads(snapshot.eval_spec_hashes_json or "{}")
            if not all(
                isinstance(value, list)
                for value in (train, validation, hidden, prospective)
            ) or not isinstance(stored_inputs, dict) or not isinstance(stored_specs, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError, TypeError):
            checks.append(
                PreflightCheck(
                    code="data_snapshot_manifest",
                    status="FAIL",
                    message="数据快照清单无法解析。",
                )
            )
            return []
        mode_valid = snapshot.mode in {"development_regression", "independent_validation"}
        independent_ready = (
            snapshot.mode != "independent_validation"
            or (
                bool(train)
                and len(validation)
                >= self.settings.skill_optimization_minimum_independent_validation_cases
            )
        )
        development_ready = snapshot.mode != "development_regression" or not train
        checks.append(
            PreflightCheck(
                code="data_snapshot_mode",
                status=(
                    "PASS"
                    if mode_valid and validation and independent_ready and development_ready
                    else "FAIL"
                ),
                message=(
                    "数据快照模式及 Case 数量满足要求。"
                    if mode_valid and validation and independent_ready and development_ready
                    else "数据快照模式、Train/Validation 语义或 Case 数量不满足要求。"
                ),
                details={
                    "mode": snapshot.mode,
                    "train_count": len(train),
                    "validation_count": len(validation),
                    "hidden_test_count": len(hidden),
                    "prospective_holdout_count": len(prospective),
                    "minimum_independent_validation_cases": (
                        self.settings.skill_optimization_minimum_independent_validation_cases
                    ),
                },
            )
        )
        all_cases = [*train, *validation, *hidden, *prospective]
        try:
            observed = build_snapshot_manifest(
                self.settings,
                dataset_key=snapshot.dataset_key,
                mode=snapshot.mode,
                train_cases=train,
                validation_cases=validation,
                hidden_test_cases=hidden,
                prospective_holdout_cases=prospective,
            )
            observed_hash = content_hash(canonical_json(observed).encode("utf-8"))
            immutable = (
                stored_inputs == observed["case_input_hashes"]
                and stored_specs == observed["eval_spec_hashes"]
                and snapshot.content_hash == observed_hash
            )
            checks.append(
                PreflightCheck(
                    code="data_snapshot_integrity",
                    status="PASS" if immutable else "FAIL",
                    message=(
                        "数据快照切分不相交，且 Case/Eval Spec 内容未漂移。"
                        if immutable
                        else "数据快照哈希不匹配，Case 或 Eval Spec 可能已漂移。"
                    ),
                    details={"case_count": len(all_cases)},
                )
            )
        except Exception as exc:
            # Snapshot helpers raise domain errors for unsafe paths, missing
            # logs and split overlap.  Their text may contain local case names;
            # expose only the stable exception code where available.
            checks.append(
                PreflightCheck(
                    code="data_snapshot_integrity",
                    status="FAIL",
                    message="数据快照切分、Case 输入或内容哈希校验失败。",
                    details={"reason": getattr(exc, "code", "snapshot_invalid")},
                )
            )
        return [value for value in all_cases if isinstance(value, str)]

    def _check_cases(
        self, checks: list[PreflightCheck], case_paths: Sequence[str]
    ) -> None:
        failed: list[dict[str, Any]] = []
        for case_path in case_paths:
            directory = _safe_case_directory(self.settings.results_formal_path, case_path)
            if directory is None:
                failed.append({"case_path": case_path, "reason": "path_invalid"})
                continue
            if not (directory / "case.json").is_file():
                failed.append({"case_path": case_path, "reason": "case_not_found"})
                continue
            try:
                log_info = inspect_case_logs(directory)
            except Exception as exc:
                failed.append(
                    {
                        "case_path": case_path,
                        "reason": getattr(exc, "code", "case_logs_invalid"),
                    }
                )
                continue
            if not log_info["submission_ready"]:
                failed.append(
                    {
                        "case_path": case_path,
                        "reason": "case_logs_not_ready",
                        "issue_codes": [
                            issue["code"] for issue in log_info["blocking_issues"]
                        ],
                    }
                )
        checks.append(
            PreflightCheck(
                code="case_logs",
                status="FAIL" if failed else "PASS",
                message=(
                    "所有指定 Case 均存在且日志可用于运行。"
                    if not failed
                    else "部分指定 Case 不存在或日志尚不可运行。"
                ),
                details={
                    "checked_count": len(case_paths),
                    "failed_count": len(failed),
                    "failures": failed,
                },
            )
        )


def run_skill_optimization_preflight(
    session_factory: sessionmaker[Session],
    settings: Settings,
    **context: Any,
) -> dict[str, Any]:
    """Convenience entry point for CLI/API callers using default probes."""

    return SkillOptimizationPreflightService(session_factory, settings).run(**context)
