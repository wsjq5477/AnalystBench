from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic.config import Config

from alembic import command
from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationHarness,
    EvaluationMethod,
    EvaluationTarget,
    ExecutionProfile,
    OptimizationDataSnapshot,
    OptimizerPolicyVersion,
    Skill,
    VerifierBundleVersion,
)
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.db.transaction import transaction
from analystbench.skill_optimization.preflight import (
    SkillOptimizationPreflightService,
    VersionProbe,
    probe_bubblewrap_sandbox,
    probe_executable_version,
)
from analystbench.skill_optimization.snapshot import build_snapshot_manifest
from analystbench.storage.content import canonical_json, content_hash


def configured(
    tmp_path: Path,
    *,
    enabled: bool = True,
    managed_root: Path | None = None,
) -> tuple[Settings, Any]:
    database = tmp_path / "analystbench.db"
    settings = Settings(
        database_url=f"sqlite:///{database.as_posix()}",
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
        results_tmp_path=tmp_path / "results-tmp",
        results_formal_path=tmp_path / "results",
        service_runtime_path=tmp_path / "run",
        service_log_path=tmp_path / "logs" / "app.log",
        skill_optimization_enabled=enabled,
        skill_optimization_managed_root=managed_root or tmp_path / "managed-skills",
        skill_optimization_minimum_independent_validation_cases=2,
    )
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(settings)
    return settings, create_session_factory(engine)


def fake_resolver(name: str) -> str:
    return f"/private/bin/{name}"


def successful_version(_: str) -> VersionProbe:
    return VersionProbe(True, version="1.2.3")


def checks_by_code(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["code"]: item for item in result["checks"]}


def write_case(settings: Settings, case_path: str, *, source_group: str) -> None:
    directory = settings.results_formal_path / case_path
    directory.mkdir(parents=True)
    (directory / "case.json").write_text(
        json.dumps(
            {
                "source_group_key": source_group,
                "case": {
                    "case_key": directory.name,
                    "category": directory.parent.name,
                    "problem_statement": "Diagnose the failure.",
                    "reference_answer": "Reference.",
                },
                "eval_spec_draft": {"claims": [{"id": "c1"}]},
            }
        ),
        encoding="utf-8",
    )
    logs = directory / "logs"
    logs.mkdir()
    (logs / "input.log").write_text("panic", encoding="utf-8")


def create_ready_context(settings: Settings, session_factory: Any) -> dict[str, Any]:
    skill_base_dir = settings.results_formal_path.parent / "harness-config"
    source = skill_base_dir / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

    identifiers = {
        name: str(uuid4())
        for name in (
            "skill",
            "harness",
            "method",
            "target",
            "profile",
            "policy",
            "verifier",
            "snapshot",
        )
    }
    with transaction(session_factory) as session:
        session.add(
            Skill(
                id=identifiers["skill"],
                skill_key="demo",
                name="Demo",
                source_path=str(source),
                invoke_as="/demo",
                harness_key="claude",
                install_relative_path=".claude/skills/demo",
                editable_paths_json='["SKILL.md"]',
            )
        )
        session.add(
            EvaluationHarness(
                id=identifiers["harness"],
                harness_key="claude",
                name="claude",
                version_number=1,
                model_policy="none",
                skill_base_dir=str(skill_base_dir),
                command_template='claude -p "/demo analyze {input}"',
                status="frozen",
                content_hash=f"sha256:{'1' * 64}",
            )
        )
        session.add(
            EvaluationMethod(
                id=identifiers["method"],
                method_key="claude-demo",
                name="claude demo",
                version_number=1,
                command_template='claude -p "/demo analyze {input}"',
                status="frozen",
                content_hash=f"sha256:{'2' * 64}",
                last_probe_json='{"available":true}',
            )
        )
        session.add(
            EvaluationTarget(
                id=identifiers["target"],
                target_key="claude-demo",
                version_number=1,
                harness_id=identifiers["harness"],
                status="frozen",
                content_hash=f"sha256:{'3' * 64}",
                materialized_method_id=identifiers["method"],
            )
        )
        session.add(
            ExecutionProfile(
                id=identifiers["profile"],
                name="optimizer",
                version_number=1,
                runner="claude",
                configuration_json="{}",
                status="frozen",
                content_hash=f"sha256:{'4' * 64}",
            )
        )
        session.add(
            OptimizerPolicyVersion(
                id=identifiers["policy"],
                policy_key="default",
                version_number=1,
                execution_profile_id=identifiers["profile"],
                prompt_bundle_hash=f"sha256:{'5' * 64}",
                config_json="{}",
                content_hash=f"sha256:{'6' * 64}",
            )
        )
        session.add(
            VerifierBundleVersion(
                id=identifiers["verifier"],
                bundle_key="formal",
                version_number=1,
                static_policy_json="{}",
                gate_policy_json="{}",
                judge_config_json='{"runner":"claude"}',
                content_hash=f"sha256:{'7' * 64}",
            )
        )

    case_paths = ["kernel/panic/case-1", "kernel/panic/case-2"]
    for index, case_path in enumerate(case_paths, start=1):
        write_case(settings, case_path, source_group=f"group-{index}")
    manifest = build_snapshot_manifest(
        settings,
        dataset_key="kernel",
        mode="development_regression",
        train_cases=[],
        validation_cases=case_paths,
        hidden_test_cases=[],
        prospective_holdout_cases=[],
    )
    with transaction(session_factory) as session:
        session.add(
            OptimizationDataSnapshot(
                id=identifiers["snapshot"],
                dataset_key="kernel",
                mode="development_regression",
                train_cases_json="[]",
                validation_cases_json=canonical_json(case_paths),
                hidden_test_cases_json="[]",
                prospective_holdout_cases_json="[]",
                case_input_hashes_json=canonical_json(manifest["case_input_hashes"]),
                eval_spec_hashes_json=canonical_json(manifest["eval_spec_hashes"]),
                content_hash=content_hash(canonical_json(manifest).encode("utf-8")),
            )
        )
    return {**identifiers, "case_paths": case_paths, "source": source}


def test_base_preflight_passes_and_cleans_writable_probe(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    root = settings.skill_optimization_root_path
    before = list(root.iterdir())
    result = SkillOptimizationPreflightService(
        session_factory,
        settings,
        executable_resolver=fake_resolver,
        version_runner=successful_version,
        sandbox_runner=successful_version,
        minimum_free_bytes=0,
    ).run()

    assert result["status"] == "PASS"
    assert list(root.iterdir()) == before
    checks = checks_by_code(result)
    assert checks["managed_root_configured"]["status"] == "PASS"
    assert checks["managed_root_writable"]["status"] == "PASS"
    assert checks["git_executable"]["details"]["version"] == "1.2.3"
    assert checks["package_test_sandbox"]["status"] == "PASS"
    assert checks["database_migration_head"]["status"] == "PASS"
    assert checks["database_core_tables"]["status"] == "PASS"


def test_preflight_resolves_relative_root_and_reports_missing_tools(
    tmp_path: Path,
) -> None:
    relative_root = tmp_path / "relative-managed-root"
    relative_root.mkdir()
    configured_relative_root = Path(os.path.relpath(relative_root, Path.cwd()))
    settings, session_factory = configured(
        tmp_path,
        enabled=False,
        managed_root=configured_relative_root,
    )
    result = SkillOptimizationPreflightService(
        session_factory,
        settings,
        executable_resolver=lambda _: None,
        minimum_free_bytes=0,
    ).run()

    assert result["status"] == "FAIL"
    checks = checks_by_code(result)
    assert checks["feature_switch"]["status"] == "FAIL"
    assert checks["managed_root_configured"]["status"] == "PASS"
    assert checks["managed_root_absolute"]["status"] == "PASS"
    assert checks["managed_root_absolute"]["details"] == {
        "path": str(relative_root),
        "configured_path": str(configured_relative_root),
    }
    assert checks["managed_root_writable"]["status"] == "PASS"
    assert checks["git_executable"]["status"] == "FAIL"
    assert checks["agent_runners"]["status"] == "FAIL"
    assert checks["package_test_sandbox"]["status"] == "WARN"
    assert list(relative_root.iterdir()) == []


def test_context_preflight_checks_frozen_compatible_inputs(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    context = create_ready_context(settings, session_factory)
    result = SkillOptimizationPreflightService(
        session_factory,
        settings,
        executable_resolver=fake_resolver,
        version_runner=successful_version,
        sandbox_runner=successful_version,
        minimum_free_bytes=0,
    ).run(
        skill_key="demo",
        evaluation_target_id=context["target"],
        execution_profile_id=context["profile"],
        optimizer_policy_version_id=context["policy"],
        verifier_bundle_version_id=context["verifier"],
        case_paths=context["case_paths"],
        data_snapshot_id=context["snapshot"],
    )

    assert result["status"] == "PASS"
    checks = checks_by_code(result)
    for code in (
        "skill_registered",
        "evaluation_target_frozen",
        "skill_target_compatible",
        "skill_invocation_in_harness",
        "harness_skill_directory",
        "optimizer_policy_exists",
        "optimizer_policy_profile_compatible",
        "execution_profile_frozen",
        "execution_profile_runner",
        "evaluation_target_runner",
        "verifier_bundle_exists",
        "verifier_judge_runner",
        "data_snapshot_exists",
        "data_snapshot_mode",
        "data_snapshot_integrity",
        "case_logs",
        "requested_cases_in_snapshot",
    ):
        assert checks[code]["status"] == "PASS", code


def test_context_preflight_probes_selected_target_command_now(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    context = create_ready_context(settings, session_factory)
    with transaction(session_factory) as session:
        harness = session.get(EvaluationHarness, context["harness"])
        method = session.get(EvaluationMethod, context["method"])
        assert harness is not None and method is not None
        harness.command_template = 'missing-target-runner -p "/demo analyze {input}"'
        method.command_template = harness.command_template

    def resolver(name: str) -> str | None:
        if name == "missing-target-runner":
            return None
        return fake_resolver(name)

    result = SkillOptimizationPreflightService(
        session_factory,
        settings,
        executable_resolver=resolver,
        version_runner=successful_version,
        sandbox_runner=successful_version,
        minimum_free_bytes=0,
    ).run(evaluation_target_id=context["target"])

    checks = checks_by_code(result)
    assert checks["agent_runners"]["status"] == "PASS"
    assert checks["evaluation_target_frozen"]["status"] == "PASS"
    assert checks["evaluation_target_runner"]["status"] == "FAIL"
    assert checks["evaluation_target_runner"]["details"]["reason"] == "not_found"


def test_verifier_lexical_warns_and_unsupported_runners_fail(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    context = create_ready_context(settings, session_factory)
    with transaction(session_factory) as session:
        verifier = session.get(VerifierBundleVersion, context["verifier"])
        profile = session.get(ExecutionProfile, context["profile"])
        assert verifier is not None and profile is not None
        verifier.judge_config_json = '{"runner":"lexical"}'

    service = SkillOptimizationPreflightService(
        session_factory,
        settings,
        executable_resolver=fake_resolver,
        version_runner=successful_version,
        sandbox_runner=successful_version,
        minimum_free_bytes=0,
    )
    lexical = service.run(verifier_bundle_version_id=context["verifier"])
    lexical_checks = checks_by_code(lexical)
    assert lexical["status"] == "WARN"
    assert lexical_checks["verifier_bundle_exists"]["status"] == "PASS"
    assert lexical_checks["verifier_judge_runner"]["status"] == "WARN"
    assert lexical_checks["verifier_judge_runner"]["details"]["reason"] == "debug_judge"

    with transaction(session_factory) as session:
        verifier = session.get(VerifierBundleVersion, context["verifier"])
        profile = session.get(ExecutionProfile, context["profile"])
        assert verifier is not None and profile is not None
        verifier.judge_config_json = '{"runner":"opencode"}'
        profile.runner = "opencode"

    unsupported = service.run(
        execution_profile_id=context["profile"],
        verifier_bundle_version_id=context["verifier"],
    )
    unsupported_checks = checks_by_code(unsupported)
    assert unsupported["status"] == "FAIL"
    assert unsupported_checks["execution_profile_runner"]["status"] == "FAIL"
    assert unsupported_checks["execution_profile_runner"]["details"]["reason"] == (
        "unsupported_runner"
    )
    assert unsupported_checks["verifier_judge_runner"]["status"] == "FAIL"
    assert unsupported_checks["verifier_judge_runner"]["details"]["reason"] == (
        "unsupported_runner"
    )


def test_context_preflight_rejects_drift_and_unready_configuration(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    context = create_ready_context(settings, session_factory)
    with transaction(session_factory) as session:
        target = session.get(EvaluationTarget, context["target"])
        profile = session.get(ExecutionProfile, context["profile"])
        assert target is not None and profile is not None
        target.status = "draft"
        profile.status = "draft"
    first_case = settings.results_formal_path / context["case_paths"][0]
    (first_case / "logs" / "input.log").unlink()

    result = SkillOptimizationPreflightService(
        session_factory,
        settings,
        executable_resolver=fake_resolver,
        version_runner=successful_version,
        sandbox_runner=successful_version,
        minimum_free_bytes=0,
    ).run(
        skill_key="demo",
        evaluation_target_id=context["target"],
        execution_profile_id=context["profile"],
        data_snapshot_id=context["snapshot"],
    )

    assert result["status"] == "FAIL"
    checks = checks_by_code(result)
    assert checks["evaluation_target_frozen"]["status"] == "FAIL"
    assert checks["execution_profile_frozen"]["status"] == "FAIL"
    assert checks["data_snapshot_integrity"]["status"] == "FAIL"
    assert checks["case_logs"]["status"] == "FAIL"


def test_context_preflight_rejects_case_split_overlap(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    context = create_ready_context(settings, session_factory)
    with transaction(session_factory) as session:
        snapshot = session.get(OptimizationDataSnapshot, context["snapshot"])
        assert snapshot is not None
        snapshot.mode = "independent_validation"
        snapshot.train_cases_json = canonical_json([context["case_paths"][0]])

    result = SkillOptimizationPreflightService(
        session_factory,
        settings,
        executable_resolver=fake_resolver,
        version_runner=successful_version,
        sandbox_runner=successful_version,
        minimum_free_bytes=0,
    ).run(data_snapshot_id=context["snapshot"])

    checks = checks_by_code(result)
    assert result["status"] == "FAIL"
    assert checks["data_snapshot_integrity"]["status"] == "FAIL"
    assert (
        checks["data_snapshot_integrity"]["details"]["reason"]
        == "optimization_split_overlap"
    )


def test_version_probe_keeps_only_version_shaped_output(monkeypatch: Any) -> None:
    secret = "sk-proj-this-must-never-appear"

    def fake_run(*_: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert secret not in kwargs["env"].values()
        return subprocess.CompletedProcess(
            args=["claude", "--version"],
            returncode=0,
            stdout=f"claude 9.8.7 {secret}",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    result = probe_executable_version("/private/bin/claude")

    assert result == VersionProbe(True, version="9.8.7")
    assert secret not in repr(result)


def test_bubblewrap_probe_requires_namespace_creation(monkeypatch: Any) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "bubblewrap 0.9.0", "")
        return subprocess.CompletedProcess(argv, 1, "", "Operation not permitted")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = probe_bubblewrap_sandbox("/usr/bin/bwrap")

    assert result == VersionProbe(
        False,
        version="0.9.0",
        reason="namespace_unavailable",
    )
    assert calls[1][-2:] == ["--", "/bin/true"]
