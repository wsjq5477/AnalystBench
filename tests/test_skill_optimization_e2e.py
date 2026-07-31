"""End-to-end checks for isolated slash-Skill execution workspaces."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import threading
import time
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
    CandidateMutation,
    EvaluationHarness,
    EvaluationMethod,
    EvaluationSubmissionCaseRun,
    EvaluationSubmissionMethodRun,
    EvaluationTarget,
    ExecutionProfile,
    OptimizationExperiment,
    OptimizationRunGroup,
    SkillTargetBinding,
)
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.evaluation_submission import EvaluationSubmissionService
from analystbench.services import transaction
from analystbench.skill_optimization.experiment import OptimizationExperimentService
from analystbench.skill_optimization.registry import SkillRegistryService
from analystbench.skill_optimization.sandbox import SkillWorkspacePreparer
from analystbench.worker import LocalWorker

REAL_CLAUDE = os.getenv("ANALYSTBENCH_REAL_CLAUDE") or shutil.which(
    "claude"
)


def _configured(tmp_path: Path) -> tuple[Settings, object]:
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
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(settings)
    return settings, create_session_factory(engine)


def _write_skill(directory: Path, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")


def _write_case(settings: object, case_key: str = "skill-e2e") -> str:
    case_path = f"kernel/SYSTEM_DEADLOCK/{case_key}"
    case_directory = settings.results_formal_path / case_path  # type: ignore[attr-defined]
    case_directory.mkdir(parents=True)
    payload = {
        "case": {
            "case_key": case_key,
            "test_set": "kernel",
            "category": "SYSTEM_DEADLOCK",
            "problem_statement": "分析日志。",
            "reference_answer": (
                "问题分类：SYSTEM_DEADLOCK\n"
                "问题根因：chmod 进程发生系统死锁\n"
                "证据：chmod hung\n"
                "结论：chmod 进程长期阻塞"
            ),
        },
        "eval_spec_draft": {
            "claims": [
                {
                    "id": "root",
                    "type": "root_cause",
                    "statement": "chmod 进程发生系统死锁",
                    "importance": "critical",
                    "weight": 100,
                    "quote": "问题根因：chmod 进程发生系统死锁",
                },
                {
                    "id": "category",
                    "type": "classification",
                    "statement": "SYSTEM_DEADLOCK",
                    "importance": "high",
                    "weight": 20,
                    "quote": "问题分类：SYSTEM_DEADLOCK",
                },
                {
                    "id": "chain-1",
                    "type": "analysis_chain",
                    "statement": "chmod 进程长期阻塞",
                    "importance": "normal",
                    "weight": 60,
                    "evidence_keyword": "chmod hung",
                    "conclusion": "chmod 进程长期阻塞",
                    "quote": "证据：chmod hung\n结论：chmod 进程长期阻塞",
                }
            ],
            "scoring_strategy": {
                "mode": "root_category_chain",
                "root_cause_score": 100,
                "category_score": 20,
                "chain_total_score": 60,
            },
            "causal_edges": [],
            "forbidden_claims": [],
            "unresolved_items": [],
        },
    }
    (case_directory / "case.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    logs = case_directory / "logs"
    logs.mkdir()
    (logs / "input.log").write_text("chmod hung", encoding="utf-8")
    return case_path


def _create_target(
    session_factory: object,
    *,
    command_template: str,
    tool_dir: Path | None,
) -> str:
    harness_id = str(uuid4())
    method_id = str(uuid4())
    target_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            EvaluationHarness(
                id=harness_id,
                harness_key="claude-skill",
                name="claude Skill",
                version_number=1,
                model_policy="none",
                tool_dir=str(tool_dir) if tool_dir else None,
                command_template=command_template,
                concurrency_limit=2,
                status="frozen",
                content_hash=f"sha256:{'a' * 64}",
            )
        )
        session.add(
            EvaluationMethod(
                id=method_id,
                method_key="claude-skill",
                name="claude Skill",
                version_number=1,
                tool_dir=str(tool_dir) if tool_dir else None,
                command_template=command_template,
                concurrency_limit=2,
                timeout_seconds=180,
                status="frozen",
                content_hash=f"sha256:{'b' * 64}",
                last_probe_json='{"available":true}',
            )
        )
        session.add(
            EvaluationTarget(
                id=target_id,
                target_key="claude-skill",
                version_number=1,
                harness_id=harness_id,
                concurrency_limit=2,
                status="frozen",
                content_hash=f"sha256:{'c' * 64}",
                materialized_method_id=method_id,
            )
        )
    return target_id


def _run_pipeline(
    tmp_path: Path,
    *,
    command_template: str,
    tool_dir: Path | None,
) -> tuple[list[dict[str, object]], list[EvaluationSubmissionMethodRun]]:
    settings, session_factory = _configured(tmp_path)
    settings.worker_concurrency_limit = 2
    settings.worker_poll_interval_seconds = 0.02
    case_path = _write_case(settings)
    source = tmp_path / "source-skill"
    _write_skill(
        source,
        "---\nname: demo\ndescription: AnalystBench E2E marker Skill.\n---\n\n"
        "# Demo\n\n"
        "When invoked, begin the final answer with the exact text VERSION_A.\n",
    )
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        harness_key="claude-skill",
    )
    version_a = registry.import_version(skill.id, source_type="initial")
    _write_skill(
        source,
        "---\nname: demo\ndescription: AnalystBench E2E marker Skill.\n---\n\n"
        "# Demo\n\n"
        "When invoked, begin the final answer with the exact text VERSION_B.\n",
    )
    version_b = registry.import_version(
        skill.id,
        parent_version_id=version_a.id,
        source_type="candidate",
    )
    target_id = _create_target(
        session_factory,
        command_template=command_template,
        tool_dir=tool_dir,
    )
    variants = [
        registry.freeze_variant(
            evaluation_target_id=target_id,
            version_id=version.id,
        )
        for version in (version_a, version_b)
    ]
    submissions = EvaluationSubmissionService(
        session_factory,  # type: ignore[arg-type]
        settings,
        workspace_preparer=SkillWorkspacePreparer(
            session_factory, registry  # type: ignore[arg-type]
        ),
    )
    submission = submissions.create_submission(
        dataset_key="kernel",
        method_ids=[variant.materialized_method_id for variant in variants],
        case_paths=[case_path],
        judge_runner="lexical",
        purpose="skill_optimization",
    )

    worker = LocalWorker(settings)
    stop = threading.Event()
    worker_thread = threading.Thread(target=worker.serve, args=(stop,))
    worker_thread.start()
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = submissions.get_submission(submission.id).status
            if status in {"completed", "completed_with_errors", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Skill E2E submission did not finish before timeout")
    finally:
        stop.set()
        worker_thread.join(timeout=10)
        worker.close()

    with transaction(session_factory) as session:  # type: ignore[arg-type]
        runs = list(
            session.scalars(
                select(EvaluationSubmissionMethodRun)
                .join(
                    EvaluationSubmissionCaseRun,
                    EvaluationSubmissionCaseRun.id
                    == EvaluationSubmissionMethodRun.case_run_id,
                )
                .where(EvaluationSubmissionCaseRun.submission_id == submission.id)
                .order_by(EvaluationSubmissionMethodRun.started_at)
            )
        )
        artifacts = [json.loads(run.artifact_json or "{}") for run in runs]
        for run in runs:
            session.expunge(run)
    return artifacts, runs


def test_slash_skill_versions_are_installed_and_executed_concurrently(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    timings = tools / "timings.jsonl"
    (tools / "fake_claude.py").write_text(
        "import json\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "assert sys.argv[1] == '-p'\n"
        "prompt = sys.argv[2]\n"
        "assert prompt.startswith('/demo ')\n"
        "skill = Path.cwd() / '.claude' / 'skills' / 'demo' / 'SKILL.md'\n"
        "body = skill.read_text(encoding='utf-8')\n"
        "marker = 'VERSION_A' if 'VERSION_A' in body else 'VERSION_B'\n"
        "start = time.monotonic()\n"
        "time.sleep(0.4)\n"
        "end = time.monotonic()\n"
        "record = {'cwd': str(Path.cwd()), 'prompt': prompt, 'marker': marker, "
        "'start': start, 'end': end}\n"
        "with (Path(__file__).parent / 'timings.jsonl').open('a', encoding='utf-8') "
        "as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
        "print(marker)\n"
        "print('问题根因：chmod 进程发生系统死锁')\n",
        encoding="utf-8",
    )
    artifacts, runs = _run_pipeline(
        tmp_path,
        command_template=(
            f"{shlex.quote(sys.executable)} {{tool_dir}}/fake_claude.py "
            '-p "/demo 分析 {input}"'
        ),
        tool_dir=tools,
    )

    assert len(runs) == 2
    assert all(run.status == "succeeded" for run in runs)
    records = [
        json.loads(line)
        for line in timings.read_text(encoding="utf-8").splitlines()
    ]
    assert {record["marker"] for record in records} == {"VERSION_A", "VERSION_B"}
    assert len({record["cwd"] for record in records}) == 2
    assert max(record["start"] for record in records) < min(
        record["end"] for record in records
    )
    assert all(record["prompt"].startswith("/demo ") for record in records)
    extensions = [artifact["workspace_extension"] for artifact in artifacts]
    assert len({item["package_hash"] for item in extensions}) == 2


def test_full_optimization_state_machine_screens_two_candidates_and_promotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, session_factory = _configured(tmp_path)
    settings.worker_concurrency_limit = 4
    settings.worker_poll_interval_seconds = 0.02
    case_path = _write_case(settings, "full-loop")
    tools = tmp_path / "optimizer-tools"
    tools.mkdir()
    (tools / "fake_claude.py").write_text(
        "from pathlib import Path\n"
        "body = (Path.cwd() / '.claude' / 'skills' / 'demo' / "
        "'SKILL.md').read_text(encoding='utf-8')\n"
        "if 'BETTER_ONE' in body:\n"
        "    print('问题根因：chmod 进程发生系统死锁')\n"
        "else:\n"
        "    print('问题根因：未知')\n"
        "print('问题分类：SYSTEM_DEADLOCK')\n"
        "print('证据：chmod hung')\n"
        "print('结论：chmod 进程长期阻塞')\n",
        encoding="utf-8",
    )
    command_template = (
        f"{shlex.quote(sys.executable)} {{tool_dir}}/fake_claude.py "
        '-p "/demo 分析 {input}"'
    )
    target_id = _create_target(
        session_factory,
        command_template=command_template,
        tool_dir=tools,
    )
    source = tmp_path / "loop-skill"
    _write_skill(source, "# Demo\n\nInitial instructions.\n")
    registry = SkillRegistryService(session_factory, settings)  # type: ignore[arg-type]
    skill = registry.create(
        skill_key="demo",
        name="Demo",
        source_path=str(source),
        harness_key="claude-skill",
    )
    version = registry.import_version(skill.id, source_type="initial")
    profile_id = str(uuid4())
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        session.add(
            ExecutionProfile(
                id=profile_id,
                name="fake optimizer",
                version_number=1,
                runner="claude",
                configuration_json="{}",
                status="frozen",
                content_hash=f"sha256:{'d' * 64}",
            )
        )
    submissions = EvaluationSubmissionService(
        session_factory,  # type: ignore[arg-type]
        settings,
        workspace_preparer=SkillWorkspacePreparer(
            session_factory, registry  # type: ignore[arg-type]
        ),
    )
    optimization = OptimizationExperimentService(
        session_factory,  # type: ignore[arg-type]
        settings,
        registry,
        submissions,
    )
    policy = optimization.create_policy(
        policy_key="fake",
        execution_profile_id=profile_id,
        prompt_bundle={"instruction": "Improve."},
    )
    verifier = optimization.create_verifier(
        bundle_key="lexical",
        gate_policy={
            "max_latency_growth": 10.0,
            "screening_max_latency_growth": 10.0,
        },
        judge_config={"runner": "lexical"},
    )
    snapshot = optimization.create_snapshot(
        dataset_key="kernel",
        validation_case_paths=[case_path],
    )
    experiment = optimization.create_experiment(
        name="full loop",
        skill_id=skill.id,
        base_skill_version_id=version.id,
        evaluation_target_id=target_id,
        data_snapshot_id=snapshot.id,
        optimizer_policy_version_id=policy.id,
        verifier_bundle_version_id=verifier.id,
        max_epochs=1,
    )

    class FakeOptimizerRunner:
        def execute(
            self,
            _configuration: dict[str, object],
            _workspace: Path,
            prompt: str,
        ) -> SimpleNamespace:
            marker = (
                "BETTER_ONE"
                if "Candidate index: 1." in prompt
                else "WORSE_TWO"
            )
            return SimpleNamespace(
                final_report=json.dumps(
                    {
                        "rationale": marker,
                        "operations": [
                            {
                                "op": "append",
                                "path": "SKILL.md",
                                "content": f"\n{marker}\n",
                            }
                        ],
                    }
                )
            )

    monkeypatch.setattr(
        "analystbench.skill_optimization.experiment.create_runner",
        lambda _runner_id: FakeOptimizerRunner(),
    )
    optimization.start(experiment.id)
    worker = LocalWorker(settings)
    stop = threading.Event()
    worker_thread = threading.Thread(target=worker.serve, args=(stop,))
    worker_thread.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = optimization.get(experiment.id)
            if current.status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Full optimization loop did not finish before timeout")
    finally:
        stop.set()
        worker_thread.join(timeout=10)
        worker.close()

    current = optimization.get(experiment.id)
    assert current.status == "completed"
    assert current.stop_reason == "MAX_EPOCHS"
    with transaction(session_factory) as session:  # type: ignore[arg-type]
        candidates = list(
            session.scalars(
                select(CandidateMutation).order_by(CandidateMutation.created_at)
            )
        )
        comparisons = list(session.scalars(select(CandidateComparison)))
        binding = session.scalar(
            select(SkillTargetBinding).where(
                SkillTargetBinding.skill_id == skill.id,
                SkillTargetBinding.evaluation_target_id == target_id,
            )
        )
        stored_experiment = session.get(OptimizationExperiment, experiment.id)
        run_group_count = session.query(OptimizationRunGroup).count()
    assert len(candidates) == 2
    assert {candidate.status for candidate in candidates} == {
        "accepted",
        "rejected",
    }
    assert len(comparisons) == 3
    assert run_group_count == 9
    assert binding is not None
    accepted = next(item for item in candidates if item.status == "accepted")
    assert binding.active_version_id == accepted.candidate_skill_version_id
    assert stored_experiment is not None
    assert stored_experiment.current_epoch_number == 1


@pytest.mark.skipif(
    not REAL_CLAUDE,
    reason=(
        "Install claude on PATH or set ANALYSTBENCH_REAL_CLAUDE to run "
        "the real /skill E2E."
    ),
)
def test_real_claude_slash_skill_concurrent_e2e(tmp_path: Path) -> None:
    assert REAL_CLAUDE is not None
    executable = Path(REAL_CLAUDE).expanduser().resolve()
    if not executable.is_file():
        pytest.fail(f"ANALYSTBENCH_REAL_CLAUDE does not exist: {executable}")
    artifacts, runs = _run_pipeline(
        tmp_path,
        command_template=(
            f'{shlex.quote(str(executable))} -p "/demo 分析日志 {{input}}"'
        ),
        tool_dir=None,
    )

    assert len(runs) == 2
    assert all(run.status == "succeeded" for run in runs)
    reports = [
        Path(str(artifact["report_path"])).read_text(encoding="utf-8")
        for artifact in artifacts
    ]
    assert any("VERSION_A" in report for report in reports)
    assert any("VERSION_B" in report for report in reports)
    assert runs[0].started_at is not None and runs[1].started_at is not None
    assert runs[0].finished_at is not None and runs[1].finished_at is not None
    assert max(runs[0].started_at, runs[1].started_at) < min(
        runs[0].finished_at, runs[1].finished_at
    )
