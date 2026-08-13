import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select

from alembic import command
from analystbench.api.routes.skills import (
    SkillBind,
    SkillRollback,
    bind_skill,
    export_skill_version,
    list_skill_binding_history,
    rollback_skill_binding,
)
from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationHarness,
    EvaluationMethod,
    EvaluationTarget,
    ExecutionProfile,
    OptimizationDataSnapshot,
    OptimizationExperiment,
    OptimizerPolicyVersion,
    SkillBindingHistory,
    SkillTargetBinding,
    VerifierBundleVersion,
)
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.patch import StructuredPatchApplier
from analystbench.skill_optimization.promotion import PromotionService
from analystbench.skill_optimization.registry import SkillRegistryService


def configured(tmp_path: Path) -> tuple[Settings, object]:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}",
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
        results_tmp_path=tmp_path / "results-tmp",
        results_formal_path=tmp_path / "results",
        service_runtime_path=tmp_path / "run",
        service_log_path=tmp_path / "logs" / "app.log",
        skill_optimization_enabled=True,
        skill_optimization_managed_root=tmp_path / "managed-skill-versions",
    )
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(settings)
    return settings, create_session_factory(engine)


def write_skill(path: Path, body: str = "# Demo\n\nInitial instructions.\n") -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(body, encoding="utf-8")


def create_target(session_factory: object, *, version_number: int = 1) -> str:
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
                    version_number=version_number,
                    model_policy="none",
                    command_template='claude -p "/demo analyze {input}"',
                    status="frozen",
                    content_hash=f"sha256:{harness_id.replace('-', '') * 2}",
                ),
                EvaluationMethod(
                    id=method_id,
                    method_key="claude",
                    name="claude",
                    version_number=version_number,
                    command_template='claude -p "/demo analyze {input}"',
                    status="frozen",
                    content_hash=f"sha256:{method_id.replace('-', '') * 2}",
                    last_probe_json='{"available":true}',
                ),
                EvaluationTarget(
                    id=target_id,
                    target_key="claude",
                    version_number=version_number,
                    harness_id=harness_id,
                    status="frozen",
                    content_hash=f"sha256:{target_id.replace('-', '') * 2}",
                    materialized_method_id=method_id,
                ),
            ]
        )
    return target_id


def create_experiment_record(
    session_factory: object,
    *,
    skill_id: str,
    base_version_id: str,
    target_id: str,
) -> str:
    experiment_id = str(uuid4())
    profile_id = str(uuid4())
    policy_id = str(uuid4())
    verifier_id = str(uuid4())
    snapshot_id = str(uuid4())
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
        session.flush()
        session.add(
            OptimizerPolicyVersion(
                id=policy_id,
                policy_key="optimizer",
                version_number=1,
                execution_profile_id=profile_id,
                prompt_bundle_hash=f"sha256:{'5' * 64}",
                config_json="{}",
                content_hash=f"sha256:{'6' * 64}",
            )
        )
        session.add(
            VerifierBundleVersion(
                id=verifier_id,
                bundle_key="verifier",
                version_number=1,
                static_policy_json="{}",
                gate_policy_json="{}",
                judge_config_json="{}",
                content_hash=f"sha256:{'7' * 64}",
            )
        )
        session.add(
            OptimizationDataSnapshot(
                id=snapshot_id,
                dataset_key="dataset",
                mode="development_regression",
                content_hash=f"sha256:{'8' * 64}",
            )
        )
        session.flush()
        session.add(
            OptimizationExperiment(
                id=experiment_id,
                name="lifecycle",
                skill_id=skill_id,
                base_skill_version_id=base_version_id,
                evaluation_target_id=target_id,
                data_snapshot_id=snapshot_id,
                optimizer_policy_version_id=policy_id,
                verifier_bundle_version_id=verifier_id,
                status="running",
                max_epochs=1,
            )
        )
    return experiment_id


def mutate(registry: SkillRegistryService, parent_version_id: str, marker: str):
    result = StructuredPatchApplier(registry).apply(
        parent_version_id=parent_version_id,
        structured_patch={
            "operations": [
                {
                    "op": "append",
                    "path": "SKILL.md",
                    "content": f"\n{marker}\n",
                }
            ]
        },
    )
    version, _ = result
    return version


def test_version_export_api_is_immutable_self_describing_zip(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "source-skill"
    original = "# Demo\n\nInitial instructions.\n"
    write_skill(source, original)
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(skill_registry_service=registry))
    )
    skill = registry.create(skill_key="demo", name="Demo", source_path=str(source))
    initial = registry.import_version(skill.id, source_type="initial")
    candidate = mutate(registry, initial.id, "EXPORTED_CANDIDATE")

    first = export_skill_version(skill.id, candidate.id, request)  # type: ignore[arg-type]
    second = export_skill_version(skill.id, candidate.id, request)  # type: ignore[arg-type]

    assert first.status_code == 200
    assert first.media_type == "application/zip"
    assert first.headers["content-disposition"] == 'attachment; filename="demo-v2.zip"'
    assert first.body == second.body
    with zipfile.ZipFile(io.BytesIO(first.body)) as archive:
        assert archive.namelist() == [
            "SKILL.md",
            ".analystbench/version-manifest.json",
        ]
        assert "EXPORTED_CANDIDATE" in archive.read("SKILL.md").decode("utf-8")
        exported = json.loads(
            archive.read(".analystbench/version-manifest.json")
        )
        assert exported["format"] == "analystbench.skill-version-export.v1"
        assert exported["version"]["id"] == candidate.id
        assert exported["version"]["package_hash"] == candidate.package_hash
        assert exported["package_manifest"] == json.loads(candidate.manifest_json)
        assert (
            archive.getinfo("SKILL.md").external_attr >> 16 & 0o777
        ) == 0o444
    assert (source / "SKILL.md").read_text(encoding="utf-8") == original


def test_package_manifest_freezes_modes_and_ignores_local_artifacts(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "source-skill"
    write_skill(source)
    script = source / "scripts" / "check.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    (source / ".svn").mkdir()
    (source / ".svn" / "private").write_text("ignored", encoding="utf-8")
    (source / "SKILL.md.swp").write_text("ignored", encoding="utf-8")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(skill_key="demo", name="Demo", source_path=str(source))
    version = registry.import_version(skill.id, source_type="initial")

    manifest = json.loads(version.manifest_json)
    files = {item["path"]: item for item in manifest["files"]}
    assert manifest["format"] == "analystbench.skill-package.v2"
    assert set(files) == {"SKILL.md", "scripts/check.sh"}
    assert files["SKILL.md"]["mode"] == 0o644
    assert files["scripts/check.sh"]["mode"] == 0o755
    assert ".svn" in manifest["ignored_paths"]["directory_names"]
    materialized = tmp_path / "materialized"
    registry.materialize_version(version.id, materialized)
    assert materialized.joinpath("scripts/check.sh").stat().st_mode & 0o777 == 0o555

    script.chmod(0o4755)
    if script.stat().st_mode & 0o4000:
        with pytest.raises(AnalystBenchError) as forbidden_mode:
            registry.import_version(skill.id)
        assert forbidden_mode.value.code == "skill_file_mode_forbidden"
    else:
        # DrvFS and other shared filesystems may discard setuid at chmod time;
        # there is no privileged mode for the package inspector to reject.
        assert script.stat().st_mode & 0o6000 == 0


def test_per_skill_limits_are_enforced_on_later_imports(tmp_path: Path) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "source-skill"
    write_skill(source)
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        limits={"max_files": 1},
    )
    registry.import_version(skill.id, source_type="initial")
    (source / "references.md").write_text("extra", encoding="utf-8")

    with pytest.raises(AnalystBenchError) as too_many_files:
        registry.import_version(skill.id)
    assert too_many_files.value.code == "skill_package_too_large"


def test_failed_initial_import_can_discard_only_the_empty_new_skill(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "source-skill"
    write_skill(source)
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    empty = registry.create(skill_key="empty", name="Empty", source_path=str(source))
    empty_repository = (
        settings.skill_optimization_root_path / "repositories" / f"{empty.id}.git"
    )
    assert empty_repository.is_dir()
    assert registry.discard_empty(empty.id) is True
    assert not empty_repository.exists()
    with pytest.raises(AnalystBenchError) as missing:
        registry.get(empty.id)
    assert missing.value.code == "skill_not_found"

    retained = registry.create(
        skill_key="retained", name="Retained", source_path=str(source)
    )
    registry.import_version(retained.id, source_type="initial")
    assert registry.discard_empty(retained.id) is False
    assert registry.get(retained.id).id == retained.id


def test_binding_changes_are_audited_and_api_rollback_is_guarded(
    tmp_path: Path,
) -> None:
    settings, session_factory = configured(tmp_path)
    source = tmp_path / "source-skill"
    write_skill(source)
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(skill_registry_service=registry))
    )
    skill = registry.create(skill_key="demo", name="Demo", source_path=str(source))
    initial = registry.import_version(skill.id, source_type="initial")
    target_id = create_target(session_factory)
    binding = registry.bind(
        skill_id=skill.id,
        evaluation_target_id=target_id,
        version_id=initial.id,
    )
    promoted = mutate(registry, initial.id, "PROMOTED")
    experiment_id = create_experiment_record(
        session_factory,
        skill_id=skill.id,
        base_version_id=initial.id,
        target_id=target_id,
    )
    binding = PromotionService(session_factory).promote(
        experiment_id=experiment_id,
        epoch_id=None,
        candidate_mutation_id=None,
        skill_id=skill.id,
        evaluation_target_id=target_id,
        version_id=promoted.id,
        expected_active_version_id=initial.id,
        gate_result={"verdict": "pass", "active_level": "validated"},
        expected_lock_version=binding.lock_version,
        evidence={"comparison_id": "comparison"},
    )
    recovered = PromotionService(session_factory).promote(
        experiment_id=experiment_id,
        epoch_id=None,
        candidate_mutation_id=None,
        skill_id=skill.id,
        evaluation_target_id=target_id,
        version_id=promoted.id,
        expected_active_version_id=initial.id,
        gate_result={"verdict": "pass", "active_level": "validated"},
        expected_lock_version=binding.lock_version,
        evidence={"comparison_id": "comparison"},
    )
    assert recovered.lock_version == binding.lock_version
    concurrent_candidate = mutate(registry, promoted.id, "CONCURRENT")
    with pytest.raises(AnalystBenchError) as stale_parent:
        PromotionService(session_factory).promote(
            experiment_id=experiment_id,
            epoch_id=None,
            candidate_mutation_id=None,
            skill_id=skill.id,
            evaluation_target_id=target_id,
            version_id=concurrent_candidate.id,
            expected_active_version_id=initial.id,
            gate_result={"verdict": "pass", "active_level": "validated"},
            # Reading the current lock immediately before promotion must not
            # make a stale experiment parent safe to overwrite.
            expected_lock_version=binding.lock_version,
            evidence={"comparison_id": "stale-parent"},
        )
    assert stale_parent.value.code == "skill_binding_conflict"
    never_active = mutate(registry, promoted.id, "NEVER_ACTIVE")
    unbound_target_id = create_target(session_factory, version_number=2)
    globally_active_but_unbound_target_id = create_target(
        session_factory, version_number=3
    )

    idempotent = bind_skill(
        skill.id,
        SkillBind(
            evaluation_target_id=target_id,
            version_id=promoted.id,
            active_level="provisional",
            expected_lock_version=binding.lock_version,
        ),
        request,  # type: ignore[arg-type]
    )
    with pytest.raises(AnalystBenchError) as bypass:
        bind_skill(
            skill.id,
            SkillBind(
                evaluation_target_id=target_id,
                version_id=initial.id,
                expected_lock_version=binding.lock_version,
            ),
            request,  # type: ignore[arg-type]
        )
    with pytest.raises(AnalystBenchError) as first_bind_bypass:
        bind_skill(
            skill.id,
            SkillBind(
                evaluation_target_id=unbound_target_id,
                version_id=never_active.id,
                expected_lock_version=0,
            ),
            request,  # type: ignore[arg-type]
        )
    with pytest.raises(AnalystBenchError) as cross_target_first_bind_bypass:
        bind_skill(
            skill.id,
            SkillBind(
                evaluation_target_id=globally_active_but_unbound_target_id,
                version_id=promoted.id,
                active_level="validated",
                expected_lock_version=0,
            ),
            request,  # type: ignore[arg-type]
        )
    other_binding = registry.bind(
        skill_id=skill.id,
        evaluation_target_id=unbound_target_id,
        version_id=initial.id,
    )
    with pytest.raises(AnalystBenchError) as cross_target_bypass:
        rollback_skill_binding(
            skill.id,
            unbound_target_id,
            SkillRollback(
                version_id=promoted.id,
                expected_lock_version=other_binding.lock_version,
                reason="must have target-specific activation history",
            ),
            request,  # type: ignore[arg-type]
        )
    with pytest.raises(AnalystBenchError) as rejected:
        rollback_skill_binding(
            skill.id,
            target_id,
            SkillRollback(
                version_id=never_active.id,
                expected_lock_version=binding.lock_version,
                reason="must not activate an unverified candidate",
            ),
            request,  # type: ignore[arg-type]
        )
    rolled_back = rollback_skill_binding(
        skill.id,
        target_id,
        SkillRollback(
            version_id=initial.id,
            expected_lock_version=binding.lock_version,
            reason="validated release regression",
        ),
        request,  # type: ignore[arg-type]
    )
    with pytest.raises(AnalystBenchError) as stale:
        rollback_skill_binding(
            skill.id,
            target_id,
            SkillRollback(
                version_id=promoted.id,
                expected_lock_version=binding.lock_version,
            ),
            request,  # type: ignore[arg-type]
        )
    history = list_skill_binding_history(
        skill.id,
        request,  # type: ignore[arg-type]
        evaluation_target_id=target_id,
        limit=100,
        offset=0,
    )

    assert bypass.value.code == "skill_binding_change_requires_promotion_or_rollback"
    assert first_bind_bypass.value.code == "skill_binding_version_not_active"
    assert (
        cross_target_first_bind_bypass.value.code
        == "skill_binding_version_not_active"
    )
    assert cross_target_bypass.value.code == "skill_rollback_version_not_active"
    assert idempotent["active_level"] == "validated"
    assert idempotent["lock_version"] == binding.lock_version
    assert rejected.value.code == "skill_rollback_version_not_active"
    assert rolled_back["active_version_id"] == initial.id
    assert rolled_back["active_level"] == "provisional"
    assert rolled_back["lock_version"] == 3
    assert stale.value.code == "skill_binding_conflict"
    assert [row["action"] for row in history] == [
        "rollback",
        "promotion",
        "initial_bind",
    ]
    assert history[0]["metadata"]["reason"] == "validated release regression"
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        rows = list(
            session.scalars(
                select(SkillBindingHistory)
                .where(SkillBindingHistory.evaluation_target_id == target_id)
                .order_by(SkillBindingHistory.lock_version)
            )
        )
        assert [row.lock_version for row in rows] == [1, 2, 3]


def test_migration_exposes_binding_history_table(tmp_path: Path) -> None:
    settings, _ = configured(tmp_path)
    engine = create_database_engine(settings)
    try:
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("skill_binding_history")
        }
        assert {
            "binding_id",
            "previous_version_id",
            "active_version_id",
            "lock_version",
            "action",
            "metadata_json",
        } <= columns
    finally:
        engine.dispose()


def test_migration_backfills_existing_binding_as_audit_baseline(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}",
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
        results_tmp_path=tmp_path / "results-tmp",
        results_formal_path=tmp_path / "results",
        service_runtime_path=tmp_path / "run",
        service_log_path=tmp_path / "logs" / "app.log",
        skill_optimization_enabled=True,
        skill_optimization_managed_root=tmp_path / "managed-skill-versions",
    )
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "0016_harness_skill_base_dir")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    source = tmp_path / "source-skill"
    write_skill(source)
    registry = SkillRegistryService(session_factory, settings)
    skill = registry.create(skill_key="demo", name="Demo", source_path=str(source))
    version = registry.import_version(skill.id, source_type="initial", status="active")
    target_id = create_target(session_factory)
    with transaction(session_factory) as session:
        session.add(
            SkillTargetBinding(
                id=str(uuid4()),
                skill_id=skill.id,
                evaluation_target_id=target_id,
                active_version_id=version.id,
                active_level="validated",
                lock_version=7,
            )
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        with transaction(session_factory) as session:
            history = session.scalar(select(SkillBindingHistory))
            assert history is not None
            assert history.skill_id == skill.id
            assert history.active_version_id == version.id
            assert history.active_level == "validated"
            assert history.lock_version == 7
            assert history.action == "migration_baseline"
    finally:
        engine.dispose()
