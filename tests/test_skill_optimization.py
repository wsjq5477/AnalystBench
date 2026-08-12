from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import select

from alembic import command
from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationHarness,
    EvaluationMethod,
    EvaluationSubmission,
    EvaluationTarget,
    ExecutionProfile,
    Job,
    OptimizationRunGroup,
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
from analystbench.skill_optimization.gate import evaluate_gate
from analystbench.skill_optimization.patch import StructuredPatchApplier
from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.skill_optimization.sandbox import SkillWorkspacePreparer
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
    snapshot = experiment_service.create_snapshot(
        dataset_key="kernel",
        validation_case_paths=[
            f"kernel/category/case-{index}" for index in range(1, 5)
        ],
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

    first_epoch = experiment_service._create_epoch(
        experiment,
        epoch_number=1,
        parent_version_id=version.id,
    )
    rejected = experiment_service._record_rejected_candidate(
        experiment,
        first_epoch,
        1,
        rejection_code="skill_patch_path_forbidden",
        rejection_message="Patch 不允许编辑。",
    )
    assert rejected.status == "rejected"
    assert experiment_service._rejected_history(experiment.id)[0][
        "rejection_code"
    ] == "skill_patch_path_forbidden"
    experiment_service._complete_epoch(
        experiment,
        first_epoch,
        decision="no_screening_survivor",
    )
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
