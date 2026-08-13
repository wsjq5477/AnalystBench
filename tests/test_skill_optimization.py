import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from analystbench.config import Settings
from analystbench.db.models import (
    CandidateComparison,
    EvaluationHarness,
    EvaluationMethod,
    EvaluationSubmission,
    EvaluationTarget,
    ExecutionProfile,
    Job,
    OptimizationRunGroup,
    SkillTargetBinding,
)
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.evaluation.submission import EvaluationSubmissionService
from analystbench.skill_optimization.evidence import (
    build_evidence_summary,
    extract_report_evidence,
)
from analystbench.skill_optimization.experiment import OptimizationExperimentService
from analystbench.skill_optimization.gate import evaluate_gate, evaluate_screening
from analystbench.skill_optimization.patch import StructuredPatchApplier
from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.skill_optimization.sandbox import SkillWorkspacePreparer
from analystbench.skill_optimization.snapshot import verify_snapshot_manifest
from analystbench.skill_optimization.statistics import RunObservation, compare_paired


def configured(tmp_path: Path) -> tuple[Settings, object]:
    database = tmp_path / "analystbench.db"
    settings = Settings(
        database_url=f"sqlite:///{database.as_posix()}",
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
        results_tmp_path=tmp_path / "results-tmp",
        results_formal_path=tmp_path / "results",
        service_runtime_path=tmp_path / "run",
        service_log_path=tmp_path / "logs" / "app.log",
        skill_optimization_enabled=True,
        skill_optimization_managed_root=tmp_path / "managed-skill-versions",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(settings)
    return settings, create_session_factory(engine)


def write_skill(directory: Path, body: str) -> None:
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")


def write_case(
    settings: Settings,
    case_path: str,
    *,
    source_group_key: str | None = None,
) -> None:
    directory = settings.results_formal_path / case_path
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "case": {
            "case_key": directory.name,
            "category": directory.parent.name,
            "problem_statement": f"Analyze {directory.name}",
            "reference_answer": f"Root cause for {directory.name}",
        },
        "eval_spec_draft": {"claims": [{"id": "root", "type": "root_cause"}]},
    }
    if source_group_key is not None:
        payload["source_group_key"] = source_group_key
    (directory / "case.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    logs = directory / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "main.log").write_text(f"log for {directory.name}\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("profile_configuration", "expected_code"),
    [
        ([], "optimizer_policy_invalid"),
        ({"allowed_tools": []}, "optimizer_policy_unsafe_tools"),
        ({"allowed_tools": ["Read", 1]}, "optimizer_policy_unsafe_tools"),
        ({"extra_args": "--allowedTools"}, "optimizer_policy_unsafe_arguments"),
        ({"extra_args": ["--allowedTools=Read"]}, "optimizer_policy_unsafe_arguments"),
    ],
)
def test_optimizer_policy_rejects_ambiguous_or_unsafe_profile_configuration(
    tmp_path: Path,
    profile_configuration: object,
    expected_code: str,
) -> None:
    settings, session_factory = configured(tmp_path)
    profile_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            ExecutionProfile(
                id=profile_id,
                name="optimizer",
                version_number=1,
                runner="claude",
                configuration_json=json.dumps(profile_configuration),
                status="frozen",
                content_hash=f"sha256:{'9' * 64}",
            )
        )
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    service = OptimizationExperimentService(
        session_factory,  # type: ignore[arg-type]
        settings,
        registry,
        EvaluationSubmissionService(session_factory, settings),  # type: ignore[arg-type]
    )

    with pytest.raises(AnalystBenchError) as raised:
        service.create_policy(
            policy_key="unsafe-profile",
            execution_profile_id=profile_id,
            prompt_bundle={"instruction": "Improve safely."},
        )

    assert raised.value.code == expected_code


def create_target(session_factory: object) -> tuple[str, str]:
    harness_id = str(uuid4())
    method_id = str(uuid4())
    target_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            EvaluationHarness(
                id=harness_id,
                harness_key="claude",
                name="claude",
                version_number=1,
                model_policy="none",
                command_template='claude -p "/demo analyze {input}"',
                status="frozen",
                content_hash=f"sha256:{'1' * 64}",
            )
        )
        session.add(
            EvaluationMethod(
                id=method_id,
                method_key="claude",
                name="claude",
                version_number=1,
                command_template='claude -p "/demo analyze {input}"',
                status="frozen",
                content_hash=f"sha256:{'2' * 64}",
                last_probe_json='{"available":true}',
            )
        )
        session.add(
            EvaluationTarget(
                id=target_id,
                target_key="claude",
                version_number=1,
                harness_id=harness_id,
                status="frozen",
                content_hash=f"sha256:{'3' * 64}",
                materialized_method_id=method_id,
            )
        )
    return target_id, method_id


def test_registry_uses_internal_git_and_installs_only_frozen_skill(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "user-project" / ".claude" / "skills" / "demo"
    write_skill(source, "# Demo\n\nInitial instructions.\n")
    unrelated = source.parent.parent / "settings.json"
    unrelated.write_text('{"hooks":["must-not-copy"]}', encoding="utf-8")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]

    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        editable_paths=["SKILL.md"],
    )
    version = registry.import_version(skill.id, source_type="initial")
    target_id, base_method_id = create_target(session_factory)
    variant = registry.freeze_variant(
        evaluation_target_id=target_id, version_id=version.id
    )

    internal_repository = (
        settings.skill_optimization_root_path / "repositories" / f"{skill.id}.git"
    )
    assert internal_repository.is_dir()
    assert not (source / ".git").exists()
    assert variant.materialized_method_id != base_method_id

    workspace = tmp_path / "run-workspace"
    workspace.mkdir()
    metadata = SkillWorkspacePreparer(
        session_factory, registry  # type: ignore[arg-type]
    ).prepare(method_id=variant.materialized_method_id, workspace=workspace)
    installed = workspace / ".claude" / "skills" / "demo"
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == (
        "# Demo\n\nInitial instructions.\n"
    )
    assert not (workspace / ".claude" / "settings.json").exists()
    assert metadata is not None
    assert metadata["package_hash"] == version.package_hash

    other = registry.create(
        skill_key="demo-other",
        name="Demo Other",
        source_path=str(source),
        harness_key="claude-skill",
    )
    other_version = registry.import_version(other.id, source_type="initial")
    with pytest.raises(AnalystBenchError) as error:
        registry.freeze_variant(
            evaluation_target_id=target_id,
            version_id=other_version.id,
        )
    assert error.value.code == "evaluation_variant_harness_mismatch"


def test_structured_patch_versions_without_overwriting_source(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "user-skill"
    original = "# Demo\n\nInitial instructions.\n"
    write_skill(source, original)
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        editable_paths=["SKILL.md"],
    )
    parent = registry.import_version(skill.id, source_type="initial")

    candidate, patch_hash = StructuredPatchApplier(registry).apply(
        parent_version_id=parent.id,
        structured_patch={
            "operations": [
                {
                    "op": "replace",
                    "path": "SKILL.md",
                    "old": "Initial instructions.",
                    "new": "Revised instructions.",
                }
            ]
        },
    )

    assert candidate.parent_version_id == parent.id
    assert candidate.package_hash != parent.package_hash
    assert patch_hash.startswith("sha256:")
    assert (source / "SKILL.md").read_text(encoding="utf-8") == original
    assert "Revised instructions." in registry.diff_versions(parent.id, candidate.id)


def test_normal_target_submission_freezes_the_current_active_skill_version(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    write_case(settings, "kernel/panic/case-1")
    source = tmp_path / "user-skill"
    write_skill(source, "# Demo\n\nInitial instructions.\n")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        editable_paths=["SKILL.md"],
    )
    initial = registry.import_version(skill.id, source_type="initial")
    target_id, base_method_id = create_target(session_factory)
    initial_variant = registry.freeze_variant(
        evaluation_target_id=target_id,
        version_id=initial.id,
    )
    registry.bind(
        skill_id=skill.id,
        evaluation_target_id=target_id,
        version_id=initial.id,
    )
    submissions = EvaluationSubmissionService(
        session_factory, settings  # type: ignore[arg-type]
    )

    first = submissions.create_submission(
        dataset_key="kernel",
        method_ids=None,
        target_ids=[target_id],
        judge_runner="lexical",
    )
    first_manifest = json.loads(first.manifest_json)
    assert first_manifest["method_ids"] == [initial_variant.materialized_method_id]
    assert first_manifest["method_ids"] != [base_method_id]
    assert first_manifest["targets"][0]["active_skill"][
        "skill_package_version_id"
    ] == initial.id

    candidate, _ = StructuredPatchApplier(registry).apply(
        parent_version_id=initial.id,
        structured_patch={
            "operations": [
                {
                    "op": "replace",
                    "path": "SKILL.md",
                    "old": "Initial instructions.",
                    "new": "Promoted instructions.",
                }
            ]
        },
    )
    candidate_variant = registry.freeze_variant(
        evaluation_target_id=target_id,
        version_id=candidate.id,
    )
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        binding = session.scalar(
            select(SkillTargetBinding).where(
                SkillTargetBinding.skill_id == skill.id,
                SkillTargetBinding.evaluation_target_id == target_id,
            )
        )
        assert binding is not None
        binding.active_version_id = candidate.id
        binding.active_level = "validated"
        binding.lock_version += 1

    second = submissions.create_submission(
        dataset_key="kernel",
        method_ids=None,
        target_ids=[target_id],
        judge_runner="lexical",
    )
    second_manifest = json.loads(second.manifest_json)
    assert second_manifest["method_ids"] == [candidate_variant.materialized_method_id]
    assert second_manifest["targets"][0]["active_skill"][
        "skill_package_version_id"
    ] == candidate.id
    # The earlier normal submission remains reproducible after Active changes.
    assert json.loads(first.manifest_json)["method_ids"] == [
        initial_variant.materialized_method_id
    ]


def test_host_skill_discovery_and_explicit_evaluation_selection_are_idempotent(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    write_case(settings, "kernel/panic/case-1")
    skill_base_dir = tmp_path / "host-claude"
    write_skill(
        skill_base_dir / "skills" / "crash",
        "# Crash\n\nAnalyze crash evidence.\n",
    )
    harness_id = str(uuid4())
    method_id = str(uuid4())
    target_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add_all(
            [
                EvaluationHarness(
                    id=harness_id,
                    harness_key="claude",
                    name="claude",
                    version_number=1,
                    model_policy="none",
                    command_template='claude -p "{skill} analyze {input}"',
                    skill_base_dir=str(skill_base_dir),
                    status="frozen",
                    content_hash=f"sha256:{'a' * 64}",
                ),
                EvaluationMethod(
                    id=method_id,
                    method_key="claude",
                    name="claude",
                    version_number=1,
                    command_template='claude -p "analyze {input}"',
                    status="frozen",
                    content_hash=f"sha256:{'b' * 64}",
                    last_probe_json='{"available":true}',
                ),
                EvaluationTarget(
                    id=target_id,
                    target_key="claude",
                    version_number=1,
                    harness_id=harness_id,
                    status="frozen",
                    content_hash=f"sha256:{'c' * 64}",
                    materialized_method_id=method_id,
                ),
            ]
        )

    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    discovered = registry.discover_host_skills()
    assert [(item["key"], item["status"]) for item in discovered] == [
        ("crash", "available")
    ]
    first_skill, first_version = registry.adopt_host_skill(
        harness_id=harness_id,
        skill_key="crash",
    )
    second_skill, second_version = registry.adopt_host_skill(
        harness_id=harness_id,
        skill_key="crash",
    )
    assert second_skill.id == first_skill.id
    assert second_version.id == first_version.id
    assert registry.discover_host_skills()[0]["status"] == "managed"

    submissions = EvaluationSubmissionService(
        session_factory, settings  # type: ignore[arg-type]
    )
    baseline_methods, _, baseline_snapshots, _ = (
        submissions.resolve_target_selections(
            [{"harness_id": harness_id, "model_id": None, "skill_key": None}]
        )
    )
    assert baseline_methods == [method_id]
    assert baseline_snapshots[0]["skill_resolution"] == "explicit_no_skill"

    skill_methods, _, skill_snapshots, normalized = (
        submissions.resolve_target_selections(
            [
                {
                    "harness_id": harness_id,
                    "model_id": None,
                    "skill_key": "crash",
                }
            ]
        )
    )
    assert skill_methods != [method_id]
    assert skill_snapshots[0]["active_skill"]["skill_key"] == "crash"
    assert normalized[0]["skill_key"] == "crash"
    assert normalized[0]["skill_package_version_id"] == first_version.id
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        variant_method = session.get(EvaluationMethod, skill_methods[0])
        assert variant_method is not None
        assert variant_method.command_template == 'claude -p "/crash analyze {input}"'


def test_paired_gate_keeps_four_case_regression_provisional() -> None:
    observations: list[RunObservation] = []
    for case_index in range(4):
        for repeat in range(3):
            observations.extend(
                [
                    RunObservation(
                        case_path=f"case-{case_index}",
                        arm="baseline",
                        repeat_index=repeat,
                        score=70 + case_index,
                        duration_ms=1000,
                    ),
                    RunObservation(
                        case_path=f"case-{case_index}",
                        arm="candidate",
                        repeat_index=repeat,
                        score=73 + case_index,
                        duration_ms=1050,
                    ),
                ]
            )
    comparison = compare_paired(observations, bootstrap_samples=200)
    gate = evaluate_gate(
        comparison,
        min_overall_delta=1,
        minimum_independent_validation_cases=8,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
    )

    assert comparison["overall_delta"] == 3
    assert gate["verdict"] == "pass"
    assert gate["active_level"] == "provisional"


def test_independent_gate_rejects_when_successful_pairs_are_insufficient() -> None:
    comparison = {
        "overall_delta": 4.0,
        "pairs": [
            {
                "case_path": f"case-{index}",
                "baseline_duration_ms": 100,
                "candidate_duration_ms": 101,
            }
            for index in range(7)
        ],
        "candidate_failure_count": 1,
        "baseline_failure_count": 1,
        "bootstrap_interval": [1.0, 7.0],
        "candidate_win_probability": 0.95,
        "repeat_count": 3,
    }

    gate = evaluate_gate(
        comparison,
        min_overall_delta=1,
        minimum_independent_validation_cases=8,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="independent_validation",
    )

    assert gate["verdict"] == "reject"
    assert gate["active_level"] is None
    assert gate["reasons"][0] == {
        "code": "independent_validation_cases_insufficient",
        "observed": 7,
        "required": 8,
    }


def test_gate_rejects_case_local_new_execution_failure_even_when_totals_tie() -> None:
    observations = [
        RunObservation("case-a", "baseline", 1, 10, succeeded=True),
        RunObservation("case-a", "candidate", 1, 12, succeeded=True),
        RunObservation(
            "case-b", "baseline", 1, None, succeeded=False,
            failure_tags=("execution:failed",),
        ),
        RunObservation("case-b", "candidate", 1, 12, succeeded=True),
        RunObservation("case-c", "baseline", 1, 10, succeeded=True),
        RunObservation(
            "case-c", "candidate", 1, None, succeeded=False,
            failure_tags=("execution:failed",),
        ),
    ]
    comparison = compare_paired(observations, bootstrap_samples=50)

    gate = evaluate_gate(
        comparison,
        min_overall_delta=1,
        minimum_independent_validation_cases=8,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
    )

    assert comparison["baseline_failure_count"] == 1
    assert comparison["candidate_failure_count"] == 1
    assert gate["verdict"] == "reject"
    assert {
        reason["code"] for reason in gate["reasons"]
    } >= {"candidate_case_failures_increased", "candidate_new_failure_type"}


def test_gate_rejects_missing_candidate_observation_and_guardrail_growth() -> None:
    observations = [
        RunObservation(
            "case-a",
            "baseline",
            1,
            70,
            token_count=100,
            guardrail_metrics={"forbidden_hit_count": 1},
        ),
        RunObservation(
            "case-a",
            "candidate",
            1,
            75,
            token_count=110,
            guardrail_metrics={"forbidden_hit_count": 3},
        ),
        RunObservation("case-b", "baseline", 1, 70, token_count=100),
    ]
    comparison = compare_paired(observations, bootstrap_samples=50)
    gate = evaluate_gate(
        comparison,
        min_overall_delta=1,
        minimum_independent_validation_cases=2,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
        require_token_usage=True,
    )

    assert gate["verdict"] == "reject"
    codes = {reason["code"] for reason in gate["reasons"]}
    assert "candidate_case_failures_increased" in codes
    assert "candidate_guardrail_metric_increased" in codes


def test_gate_rejects_when_required_token_observations_are_missing() -> None:
    comparison = compare_paired(
        [
            RunObservation("case-a", "baseline", 1, 70),
            RunObservation("case-a", "candidate", 1, 75),
        ],
        bootstrap_samples=50,
    )
    gate = evaluate_gate(
        comparison,
        min_overall_delta=1,
        minimum_independent_validation_cases=1,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
        require_token_usage=True,
    )

    assert gate["verdict"] == "reject"
    assert {reason["code"] for reason in gate["reasons"]} == {
        "token_usage_missing"
    }


def test_gate_reports_quality_resource_and_critical_regressions() -> None:
    comparison = {
        "overall_delta": -2,
        "pairs": [
            {
                "case_path": "case-a",
                "baseline_duration_ms": 100,
                "candidate_duration_ms": 150,
                "baseline_tokens": 100,
                "candidate_tokens": 140,
            }
        ],
        "baseline_failure_count": 0,
        "candidate_failure_count": 1,
        "case_outcomes": [
            "invalid-outcome",
            {
                "case_path": "case-a",
                "baseline_failure_count": 0,
                "candidate_failure_count": 1,
                "new_failure_tags": ["execution:timeout"],
                "guardrail_metric_increases": {
                    "forbidden_hit_count": 2,
                    "informational_metric": 99,
                },
                "baseline_guardrail_metrics": {"forbidden_hit_count": 0},
                "candidate_guardrail_metrics": {"forbidden_hit_count": 2},
            },
        ],
        "dimension_deltas": {"root_cause": -1, "format": -100},
        "family_deltas": {"HM_OOM": -3},
        "candidate_win_probability": 0.4,
        "bootstrap_interval": [-3, 1],
        "repeat_count": 3,
    }

    gate = evaluate_gate(
        comparison,
        min_overall_delta=1,
        minimum_independent_validation_cases=1,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
        critical_dimension_min_delta=0,
        critical_family_max_regression=-2,
        require_token_usage=True,
        min_candidate_win_probability=0.8,
    )

    codes = {reason["code"] for reason in gate["reasons"]}
    assert gate["verdict"] == "reject"
    assert codes == {
        "minimum_delta_not_met",
        "candidate_failures_increased",
        "candidate_case_failures_increased",
        "candidate_new_failure_type",
        "candidate_guardrail_metric_increased",
        "critical_dimension_regressed",
        "failure_family_regressed",
        "latency_growth_exceeded",
        "token_growth_exceeded",
        "candidate_win_probability_below_minimum",
    }


def test_independent_gate_requires_confidence_and_can_validate() -> None:
    comparison = {
        "overall_delta": 3,
        "pairs": [
            {
                "case_path": "case-a",
                "baseline_duration_ms": 100,
                "candidate_duration_ms": 101,
                "baseline_tokens": 100,
                "candidate_tokens": 101,
            }
        ],
        "bootstrap_interval": [0, 5],
        "candidate_win_probability": 0.9,
        "repeat_count": 3,
    }
    arguments = {
        "min_overall_delta": 1,
        "minimum_independent_validation_cases": 1,
        "max_latency_growth": 0.2,
        "max_token_growth": 0.2,
        "mode": "independent_validation",
    }

    pending = evaluate_gate(comparison, **arguments)
    assert pending["verdict"] == "needs_more_runs"
    assert pending["reasons"][0]["code"] == "gray_zone"

    exhausted = evaluate_gate(comparison, **arguments, current_repeats=7)
    assert exhausted["verdict"] == "reject"
    assert exhausted["reasons"][0]["code"] == "inconclusive_after_max_repeats"

    comparison["bootstrap_interval"] = [0.1, 5]
    validated = evaluate_gate(comparison, **arguments)
    assert validated["verdict"] == "pass"
    assert validated["active_level"] == "validated"

    comparison["bootstrap_interval"] = None
    accepted_without_interval_guard = evaluate_gate(
        comparison,
        **arguments,
        require_bootstrap_lower_bound_positive=False,
    )
    assert accepted_without_interval_guard["verdict"] == "needs_more_runs"


def test_gate_and_screening_reject_missing_or_regressed_results() -> None:
    no_results = evaluate_gate(
        {"pairs": []},
        min_overall_delta=1,
        minimum_independent_validation_cases=1,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
    )
    assert {reason["code"] for reason in no_results["reasons"]} == {
        "no_paired_results",
        "overall_delta_missing",
    }

    screening = evaluate_screening(
        {
            "overall_delta": -3,
            "baseline_failure_count": 0,
            "candidate_failure_count": 1,
            "case_outcomes": [
                None,
                {
                    "case_path": "case-a",
                    "baseline_failure_count": 0,
                    "candidate_failure_count": 1,
                    "new_failure_tags": ["unsupported_claim"],
                    "guardrail_metric_increases": {
                        "missing_chain_count": 1,
                        "informational_metric": 2,
                    },
                },
            ],
            "dimension_deltas": {"classification": -6},
            "pairs": [
                {
                    "baseline_duration_ms": 100,
                    "candidate_duration_ms": 170,
                }
            ],
        }
    )
    assert screening["verdict"] == "reject"
    assert {reason["code"] for reason in screening["reasons"]} == {
        "screening_delta_below_minimum",
        "candidate_failures_increased",
        "candidate_case_failures_increased",
        "candidate_new_failure_type",
        "candidate_guardrail_metric_increased",
        "critical_dimension_screening_regression",
        "screening_latency_growth_exceeded",
    }

    missing = evaluate_screening({"pairs": []})
    assert missing["reasons"] == [{"code": "screening_results_missing"}]


def test_independent_snapshot_uses_train_for_optimizer_and_freezes_content(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    settings.skill_optimization_minimum_independent_validation_cases = 2
    train = ["kernel/train/case-1"]
    validation = ["kernel/validation/case-2", "kernel/validation/case-3"]
    hidden = ["kernel/hidden/case-4"]
    for case_path in train + validation + hidden:
        write_case(settings, case_path)
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    optimization = OptimizationExperimentService(
        session_factory,  # type: ignore[arg-type]
        settings,
        registry,
        EvaluationSubmissionService(
            session_factory, settings  # type: ignore[arg-type]
        ),
    )
    verifier = optimization.create_verifier(
        bundle_key="independent", judge_config={"runner": "lexical"}
    )
    snapshot = optimization.create_snapshot(
        dataset_key="kernel",
        mode="independent_validation",
        train_case_paths=train,
        validation_case_paths=validation,
        hidden_test_case_paths=hidden,
    )

    dataset, validation_paths, optimizer_paths, judge = optimization._snapshot_inputs(
        SimpleNamespace(
            data_snapshot_id=snapshot.id,
            verifier_bundle_version_id=verifier.id,
        )
    )

    assert dataset == "kernel"
    assert validation_paths == validation
    assert optimizer_paths == train
    assert judge == "lexical"
    case_hashes = json.loads(snapshot.case_input_hashes_json)
    spec_hashes = json.loads(snapshot.eval_spec_hashes_json)
    assert set(case_hashes) == set(train + validation + hidden)
    assert set(spec_hashes) == set(train + validation + hidden)

    (settings.results_formal_path / train[0] / "logs" / "main.log").write_text(
        "changed\n", encoding="utf-8"
    )
    with pytest.raises(AnalystBenchError) as error:
        verify_snapshot_manifest(
            settings,
            dataset_key="kernel",
            mode="independent_validation",
            train_cases=train,
            validation_cases=validation,
            hidden_test_cases=hidden,
            prospective_holdout_cases=[],
            expected_case_input_hashes=case_hashes,
            expected_eval_spec_hashes=spec_hashes,
        )
    assert error.value.code == "optimization_case_input_drift"


def test_independent_validation_outcomes_never_enter_optimizer_history(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    settings.skill_optimization_minimum_independent_validation_cases = 1
    train_path = "kernel/train/case-1"
    validation_path = "kernel/validation/case-2"
    for case_path in (train_path, validation_path):
        write_case(settings, case_path)

    source = tmp_path / "user-skill"
    write_skill(source, "# Demo\n\nInitial instructions.\n")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        editable_paths=["SKILL.md"],
    )
    version = registry.import_version(skill.id, source_type="initial")
    target_id, _ = create_target(session_factory)
    profile_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            ExecutionProfile(
                id=profile_id,
                name="optimizer",
                version_number=1,
                runner="claude",
                configuration_json="{}",
                status="frozen",
                content_hash=f"sha256:{'8' * 64}",
            )
        )
    optimization = OptimizationExperimentService(
        session_factory,  # type: ignore[arg-type]
        settings,
        registry,
        EvaluationSubmissionService(
            session_factory, settings  # type: ignore[arg-type]
        ),
    )
    policy = optimization.create_policy(
        policy_key="independent",
        execution_profile_id=profile_id,
        prompt_bundle={"instruction": "Improve using Train evidence only."},
    )
    verifier = optimization.create_verifier(bundle_key="independent")
    snapshot = optimization.create_snapshot(
        dataset_key="kernel",
        mode="independent_validation",
        train_case_paths=[train_path],
        validation_case_paths=[validation_path],
    )
    experiment_parameters = {
        "name": "independent history isolation",
        "skill_id": skill.id,
        "base_skill_version_id": version.id,
        "evaluation_target_id": target_id,
        "data_snapshot_id": snapshot.id,
        "optimizer_policy_version_id": policy.id,
        "verifier_bundle_version_id": verifier.id,
    }
    with pytest.raises(AnalystBenchError) as epoch_limit_error:
        optimization.create_experiment(**experiment_parameters, max_epochs=2)
    assert (
        epoch_limit_error.value.code
        == "optimization_independent_validation_epoch_limit"
    )
    experiment = optimization.create_experiment(
        **experiment_parameters,
        max_epochs=1,
    )
    epoch = optimization._create_epoch(
        experiment,
        epoch_number=1,
        parent_version_id=version.id,
    )
    train_rejection = optimization._record_rejected_candidate(
        experiment,
        epoch,
        1,
        rejection_code="screening_rejected",
        rejection_message="Train screening rejected the candidate.",
    )
    validation_rejection = optimization._record_rejected_candidate(
        experiment,
        epoch,
        2,
        rejection_code="independent_validation_not_confident",
        rejection_message="This Validation outcome must remain sealed.",
        rejection_details=[{"private_validation_delta": -3.5}],
    )
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            CandidateComparison(
                id=str(uuid4()),
                experiment_id=experiment.id,
                epoch_id=epoch.id,
                candidate_mutation_id=validation_rejection.id,
                comparison_type="paired_repeated_validation",
                metrics_json='{"overall_delta":-3.5}',
                gate_result_json='{"verdict":"reject"}',
            )
        )

    history = optimization._rejected_history(experiment.id)

    assert [item["rejection_code"] for item in history] == [
        train_rejection.rejection_code
    ]
    assert "private_validation_delta" not in json.dumps(history)


def test_snapshot_rejects_source_group_leakage(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    settings.skill_optimization_minimum_independent_validation_cases = 1
    write_case(settings, "kernel/train/case-1", source_group_key="incident-1")
    write_case(settings, "kernel/validation/case-2", source_group_key="incident-1")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    optimization = OptimizationExperimentService(
        session_factory,  # type: ignore[arg-type]
        settings,
        registry,
        EvaluationSubmissionService(
            session_factory, settings  # type: ignore[arg-type]
        ),
    )

    with pytest.raises(AnalystBenchError) as error:
        optimization.create_snapshot(
            dataset_key="kernel",
            mode="independent_validation",
            train_case_paths=["kernel/train/case-1"],
            validation_case_paths=["kernel/validation/case-2"],
        )
    assert error.value.code == "optimization_source_group_overlap"


def test_gray_zone_requests_five_then_seven_repeats_before_rejecting() -> None:
    def comparison(repeats: int) -> dict:
        observations = [
            RunObservation(
                case_path=f"kernel/family/case-{case_index}",
                case_family="family",
                arm=arm,
                repeat_index=repeat,
                score=70 + (0.5 if arm == "candidate" else 0),
            )
            for case_index in range(4)
            for repeat in range(repeats)
            for arm in ("baseline", "candidate")
        ]
        return compare_paired(observations, bootstrap_samples=100)

    first = evaluate_gate(
        comparison(3),
        min_overall_delta=1,
        minimum_independent_validation_cases=8,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
        max_repeats=7,
    )
    second = evaluate_gate(
        comparison(5),
        min_overall_delta=1,
        minimum_independent_validation_cases=8,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
        max_repeats=7,
    )
    final = evaluate_gate(
        comparison(7),
        min_overall_delta=1,
        minimum_independent_validation_cases=8,
        max_latency_growth=0.2,
        max_token_growth=0.2,
        mode="development_regression",
        max_repeats=7,
    )

    assert first["verdict"] == "needs_more_runs"
    assert first["reasons"][-1]["next_repeats"] == 5
    assert second["verdict"] == "needs_more_runs"
    assert second["reasons"][-1]["next_repeats"] == 7
    assert final["verdict"] == "reject"
    assert final["reasons"][-1]["code"] == "inconclusive_after_max_repeats"


def test_failure_family_and_dimension_evidence_is_optimizer_safe() -> None:
    evidence = extract_report_evidence(
        {
            "summary": {
                "reports": [
                    {
                        "candidate_name": "claude",
                        "score": "62",
                        "metrics": {
                            "forbidden_hit_count": 1,
                            "missing_chain_count": 1,
                        },
                        "claims": [
                            {
                                "type": "root_cause",
                                "score": "0",
                                "relation": "missing",
                            },
                            {
                                "type": "classification",
                                "score": "20",
                                "relation": "match",
                            },
                            {
                                "type": "analysis_chain",
                                "score": "42",
                                "overall_relation": "partial_match",
                            },
                        ],
                    }
                ]
            }
        },
        "claude",
    )
    summary = build_evidence_summary(
        [
            {
                "case_path": "kernel/HM_OOM/case-1",
                "case_family": "HM_OOM",
                "score": evidence["score"],
                "succeeded": True,
                "dimensions": evidence["dimensions"],
                "failure_tags": evidence["failure_tags"],
            }
        ]
    )

    assert summary["failure_families"]["HM_OOM"]["median_score"] == 62
    assert summary["dimensions"]["classification"]["median_score"] == 20
    assert summary["failure_tags"]["root_cause:missing"] == 1
    assert summary["failure_tags"]["unsupported_claim"] == 1
    assert "reference_answer" not in str(summary)


def test_experiment_start_persists_frozen_inputs_and_queues_advance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "user-skill"
    write_skill(source, "# Demo\n\nInitial instructions.\n")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        editable_paths=["SKILL.md"],
    )
    version = registry.import_version(skill.id, source_type="initial")
    target_id, _ = create_target(session_factory)
    profile_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            ExecutionProfile(
                id=profile_id,
                name="optimizer",
                version_number=1,
                runner="claude",
                configuration_json="{}",
                status="frozen",
                content_hash=f"sha256:{'4' * 64}",
            )
        )
    experiment_service = OptimizationExperimentService(
        session_factory,  # type: ignore[arg-type]
        settings,
        registry,
        EvaluationSubmissionService(
            session_factory, settings  # type: ignore[arg-type]
        ),
    )
    policy = experiment_service.create_policy(
        policy_key="default",
        execution_profile_id=profile_id,
        prompt_bundle={"instruction": "Improve the Skill."},
    )
    verifier = experiment_service.create_verifier(bundle_key="default")
    case_paths = [f"kernel/category/case-{index}" for index in range(1, 5)]
    for case_path in case_paths:
        write_case(settings, case_path)
    snapshot = experiment_service.create_snapshot(
        dataset_key="kernel",
        validation_case_paths=case_paths,
    )
    experiment = experiment_service.create_experiment(
        name="demo optimization",
        skill_id=skill.id,
        base_skill_version_id=version.id,
        evaluation_target_id=target_id,
        data_snapshot_id=snapshot.id,
        optimizer_policy_version_id=policy.id,
        verifier_bundle_version_id=verifier.id,
        max_epochs=3,
    )
    candidate, _ = StructuredPatchApplier(registry).apply(
        parent_version_id=version.id,
        structured_patch={
            "operations": [
                {
                    "op": "replace",
                    "path": "SKILL.md",
                    "old": "Initial instructions.",
                    "new": "Rejected candidate instructions.",
                }
            ]
        },
    )
    with pytest.raises(AnalystBenchError) as inactive_error:
        experiment_service.create_experiment(
            name="must not reactivate candidate",
            skill_id=skill.id,
            base_skill_version_id=candidate.id,
            evaluation_target_id=target_id,
            data_snapshot_id=snapshot.id,
            optimizer_policy_version_id=policy.id,
            verifier_bundle_version_id=verifier.id,
            max_epochs=1,
        )
    assert inactive_error.value.code == "optimization_base_not_active"

    started = experiment_service.start(experiment.id)

    assert started.status == "running"
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        job = session.scalar(
            select(Job).where(Job.kind == "skill_optimization_advance")
        )
        assert job is not None
        assert job.status == "queued"

    _, validation_paths, screening_paths, _ = experiment_service._snapshot_inputs(
        experiment
    )
    assert screening_paths == validation_paths
    assert len(screening_paths) == 4
    assert experiment_service._early_stop_reason(experiment) is None
    assert experiment_service._groups_terminal([]) is False
    assert experiment_service._token_count({"total_tokens": 12}) == 12
    assert experiment_service._token_count({"usage": {"token_count": 13.8}}) == 13
    assert experiment_service._token_count({"usage": "invalid"}) is None
    assert experiment_service._experiment_config(
        SimpleNamespace(config_snapshot_json="[]")
    ) == {}
    assert experiment_service._experiment_config(
        SimpleNamespace(config_snapshot_json="not-json")
    ) == {}

    first_epoch = experiment_service._create_epoch(
        experiment,
        epoch_number=1,
        parent_version_id=version.id,
    )
    with pytest.raises(AnalystBenchError) as missing_optimizer_policy:
        experiment_service._optimizer_pipeline(
            SimpleNamespace(
                optimizer_policy_version_id="missing-policy",
                verifier_bundle_version_id=verifier.id,
            ),
            first_epoch,
            candidate_count=1,
        )
    assert missing_optimizer_policy.value.code == "optimizer_policy_invalid"
    with pytest.raises(AnalystBenchError) as missing_snapshot:
        experiment_service._snapshot_inputs(
            SimpleNamespace(
                data_snapshot_id="missing-snapshot",
                verifier_bundle_version_id=verifier.id,
            )
        )
    assert missing_snapshot.value.code == "optimization_snapshot_invalid"
    rejected = experiment_service._record_rejected_candidate(
        experiment,
        first_epoch,
        1,
        rejection_code="skill_patch_path_forbidden",
        rejection_message="Patch 不允许编辑。",
    )
    assert rejected.status == "rejected"
    generated_rejection = experiment_service._generate_candidate(
        experiment,
        first_epoch,
        2,
        structured_patch={
            "operations": [
                {"op": "append", "path": "private.md", "content": "unsafe"}
            ]
        },
    )
    assert generated_rejection.status == "rejected"
    assert generated_rejection.rejection_code == "skill_patch_path_forbidden"
    assert {
        item["rejection_code"]
        for item in experiment_service._rejected_history(experiment.id)
    } == {"skill_patch_path_forbidden"}
    experiment_service._complete_epoch(
        experiment,
        first_epoch,
        decision="no_screening_survivor",
    )
    # Completion is crash-recovery idempotent, but a different replayed
    # decision must not rewrite immutable history.
    experiment_service._complete_epoch(
        experiment,
        first_epoch,
        decision="no_screening_survivor",
    )
    with pytest.raises(AnalystBenchError) as completion_conflict:
        experiment_service._complete_epoch(
            experiment,
            first_epoch,
            decision="retain",
        )
    assert completion_conflict.value.code == "optimization_epoch_completion_conflict"
    second_epoch = experiment_service._create_epoch(
        experiment,
        epoch_number=2,
        parent_version_id=version.id,
    )
    experiment_service._complete_epoch(
        experiment,
        second_epoch,
        decision="no_screening_survivor",
    )
    assert (
        experiment_service._early_stop_reason(experiment)
        == "NO_SCREENING_SURVIVOR"
    )
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        for completed_epoch in (first_epoch, second_epoch):
            stored_epoch = session.get(type(completed_epoch), completed_epoch.id)
            assert stored_epoch is not None
            stored_epoch.decision = "retain"
    assert (
        experiment_service._early_stop_reason(experiment)
        == "NO_VALIDATION_IMPROVEMENT"
    )
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        stored_first = session.get(type(first_epoch), first_epoch.id)
        assert stored_first is not None
        stored_first.decision = "promote"
    assert experiment_service._early_stop_reason(experiment) is None
    experiment_service._finish_experiment(experiment.id, "NO_SCREENING_SURVIVOR")
    experiment_service._finish_experiment(experiment.id, "NO_SCREENING_SURVIVOR")
    assert experiment_service.get(experiment.id).status == "completed"
    experiment_service.advance(experiment.id)
    with pytest.raises(AnalystBenchError) as missing_summary:
        experiment_service._persist_epoch_summary(experiment.id, "missing-epoch")
    assert missing_summary.value.code == "optimization_epoch_summary_missing"
    monkeypatch.setattr(
        "analystbench.skill_optimization.experiment.build_optimization_ledger",
        lambda _detail: {"epochs": [{"epoch_id": "missing-epoch"}]},
    )
    with pytest.raises(AnalystBenchError) as missing_epoch:
        experiment_service._persist_epoch_summary(experiment.id, "missing-epoch")
    assert missing_epoch.value.code == "optimization_epoch_not_found"


def test_completed_or_queued_run_group_is_reused_after_resume(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    case_directory = (
        settings.results_formal_path / "kernel" / "panic" / "case-1"
    )
    case_directory.mkdir(parents=True)
    (case_directory / "case.json").write_text(
        '{"case":{"case_key":"case-1"}}',
        encoding="utf-8",
    )
    logs = case_directory / "logs"
    logs.mkdir()
    (logs / "panic.log").write_text("panic", encoding="utf-8")

    source = tmp_path / "user-skill"
    write_skill(source, "# Demo\n\nInitial instructions.\n")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        editable_paths=["SKILL.md"],
    )
    version = registry.import_version(skill.id, source_type="initial")
    target_id, _ = create_target(session_factory)
    profile_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            ExecutionProfile(
                id=profile_id,
                name="optimizer",
                version_number=1,
                runner="claude",
                configuration_json="{}",
                status="frozen",
                content_hash=f"sha256:{'5' * 64}",
            )
        )
    optimization = OptimizationExperimentService(
        session_factory,  # type: ignore[arg-type]
        settings,
        registry,
        EvaluationSubmissionService(
            session_factory, settings  # type: ignore[arg-type]
        ),
    )
    policy = optimization.create_policy(
        policy_key="default",
        execution_profile_id=profile_id,
        prompt_bundle={"instruction": "Improve."},
    )
    verifier = optimization.create_verifier(
        bundle_key="default",
        judge_config={"runner": "lexical"},
    )
    snapshot = optimization.create_snapshot(
        dataset_key="kernel",
        validation_case_paths=["kernel/panic/case-1"],
    )
    experiment = optimization.create_experiment(
        name="resume",
        skill_id=skill.id,
        base_skill_version_id=version.id,
        evaluation_target_id=target_id,
        data_snapshot_id=snapshot.id,
        optimizer_policy_version_id=policy.id,
        verifier_bundle_version_id=verifier.id,
        max_epochs=1,
    )
    epoch = optimization._create_epoch(
        experiment,
        epoch_number=1,
        parent_version_id=version.id,
    )

    optimization._ensure_run_groups(
        experiment,
        epoch,
        split_role="screening",
        arm="baseline",
        version_id=version.id,
        candidate_mutation_id=None,
        repeat_indices=range(1),
        case_paths=["kernel/panic/case-1"],
    )
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        stored = session.get(type(experiment), experiment.id)
        assert stored is not None
        stored.status = "failed"
    assert optimization.resume(experiment.id).status == "running"
    optimization._ensure_run_groups(
        experiment,
        epoch,
        split_role="screening",
        arm="baseline",
        version_id=version.id,
        candidate_mutation_id=None,
        repeat_indices=range(1),
        case_paths=["kernel/panic/case-1"],
    )

    with transaction(session_factory) as session:  # type: ignore[arg-type]
        assert session.query(OptimizationRunGroup).count() == 1
        assert session.query(EvaluationSubmission).count() == 1
        assert (
            session.query(Job)
            .filter(Job.kind == "skill_optimization_advance")
            .count()
            == 1
        )
