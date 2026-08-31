from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from analystbench.errors import AnalystBenchError
from analystbench.execution.runner import AgentRunnerError
from analystbench.skill_optimization.evidence import (
    build_evidence_summary,
    extract_report_evidence,
)
from analystbench.skill_optimization.experiment import (
    OPTIMIZER_ROLE_SPECS,
    OptimizationExperimentService,
    _merge_role_patches,
    _normalize_patch,
    _parse_role_output,
)


def _patch(marker: str) -> dict[str, object]:
    return {
        "rationale": marker,
        "intent": {
            "change_type": "corrective",
            "target_failure_families": ["family-a"],
        },
        "operations": [
            {
                "op": "replace",
                "path": "SKILL.md",
                "old": "before",
                "new": marker,
            }
        ],
    }


def _bare_service(backoff: object | None = None) -> OptimizationExperimentService:
    service = object.__new__(OptimizationExperimentService)
    service._optimizer_backoff = backoff or (lambda _seconds: None)  # type: ignore[assignment]
    return service


def test_train_evidence_is_complete_and_never_copies_answer_or_report_text() -> None:
    secret = "STANDARD_ANSWER_AND_REPORT_TEXT_MUST_NOT_LEAK"
    claims = [
        {
            "id": f"claim-{index}",
            "type": "root_cause" if index % 2 else "analysis_chain",
            "statement": secret,
            "candidate_quote": secret,
            "evidence_keyword": secret,
            "score": index,
            "weight": 10,
            "relation": "match" if index % 2 else "missing",
            "keyword_match": bool(index % 2),
        }
        for index in range(42)
    ]
    evidence = extract_report_evidence(
        {
            "summary": {
                "reports": [
                    {
                        "candidate_name": "claude",
                        "score": 61,
                        "claims": claims,
                        "metrics": {
                            "forbidden_hit_count": 1,
                            "missing_chain_count": 2,
                            "claim_coverage": 0.75,
                            "unsafe_text_metric": secret,
                        },
                    }
                ]
            }
        },
        "claude",
    )

    assert len(evidence["claim_findings"]) == 42
    assert evidence["metrics"]["claim_coverage"] == 0.75
    assert "unsafe_text_metric" not in evidence["metrics"]
    assert "root_cause:match" in evidence["success_patterns"]
    assert secret not in json.dumps(evidence)

    signals = [
        {
            "case_path": f"kernel/family-a/case-{index}",
            "case_family": "family-a",
            "score": 61,
            "succeeded": True,
            **evidence,
        }
        for index in range(70)
    ]
    summary = build_evidence_summary(signals)

    assert summary["evidence_scope"] == "train_only"
    assert summary["schema_version"] == "optimizer_evidence.v1"
    assert len(summary["claim_findings"]) == 42 * 70
    assert len(summary["failed_cases"]) == 70
    assert summary["truncation"]["applied"] is False
    assert summary["metrics"]["claim_coverage"]["median"] == 0.75
    assert summary["success_patterns"]["root_cause:match"] > 0
    assert secret not in json.dumps(summary)


def test_exact_root_short_circuit_is_a_success_not_a_missing_chain_failure() -> None:
    evidence = extract_report_evidence(
        {
            "summary": {
                "reports": [
                    {
                        "candidate_name": "claude",
                        "score": 100,
                        "claims": [
                            {
                                "type": "root_cause",
                                "score": 100,
                                "relation": "match",
                            }
                        ],
                        "metrics": {
                            "root_cause_exact": True,
                            "missing_chain_count": 2,
                        },
                    }
                ]
            }
        },
        "claude",
    )

    assert evidence["metrics"]["missing_chain_count"] == 0.0
    assert "analysis_chain:missing" not in evidence["failure_tags"]
    assert "root_cause:exact" in evidence["success_patterns"]


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (
            {"op": "replace", "path": "SKILL.md", "old": "a", "new": "b"},
            {"new", "old", "op", "path"},
        ),
        (
            {
                "op": "insert_after",
                "path": "SKILL.md",
                "anchor": "a",
                "content": "b",
            },
            {"anchor", "content", "op", "path"},
        ),
        (
            {"op": "append", "path": "SKILL.md", "content": "b"},
            {"content", "op", "path"},
        ),
        ({"op": "delete", "path": "notes.md"}, {"op", "path"}),
    ],
)
def test_structured_patch_v1_has_exact_operation_schemas(
    operation: dict[str, str], expected: set[str]
) -> None:
    normalized = _normalize_patch({"operations": [operation]})
    assert set(normalized["operations"][0]) == expected


def test_structured_patch_rejects_old_text_alias_and_schema_drift() -> None:
    with pytest.raises(AnalystBenchError) as raised:
        _normalize_patch(
            {
                "operations": [
                    {
                        "op": "replace",
                        "path": "SKILL.md",
                        "old_text": "a",
                        "new": "b",
                    }
                ]
            }
        )
    assert raised.value.code == "optimizer_output_invalid"
    assert "old_text" in raised.value.message


def test_deterministic_merger_deduplicates_canonical_patch_hashes() -> None:
    first = _parse_role_output(
        _patch("same"),
        role="failure_analyst",
        prompt_version="skill_optimizer.failure_analyst.v1",
        candidate_count=2,
    )
    duplicate_with_different_key_order = _parse_role_output(
        {
            "operations": [
                {"new": "same", "old": "before", "path": "SKILL.md", "op": "replace"}
            ],
            "intent": {
                "target_failure_families": ["family-a"],
                "change_type": "corrective",
            },
            "rationale": "same",
        },
        role="success_analyst",
        prompt_version="skill_optimizer.success_analyst.v1",
        candidate_count=2,
    )
    third = _parse_role_output(
        _patch("different"),
        role="generalization_analyst",
        prompt_version="skill_optimizer.generalization_analyst.v1",
        candidate_count=2,
    )

    selected = _merge_role_patches(
        [first, duplicate_with_different_key_order, third], 2
    )

    assert [item["role"] for item in selected] == [
        "failure_analyst",
        "generalization_analyst",
    ]
    assert len({item["patch_hash"] for item in selected}) == 2


def test_deterministic_merger_round_robins_roles_before_second_proposal() -> None:
    failure = _parse_role_output(
        {
            "role": "failure_analyst",
            "prompt_version": "skill_optimizer.failure_analyst.v1",
            "findings": [],
            "patches": [_patch("failure-first"), _patch("failure-second")],
        },
        role="failure_analyst",
        prompt_version="skill_optimizer.failure_analyst.v1",
        candidate_count=2,
    )
    success = _parse_role_output(
        _patch("success-first"),
        role="success_analyst",
        prompt_version="skill_optimizer.success_analyst.v1",
        candidate_count=2,
    )

    selected = _merge_role_patches([failure, success], 2)

    assert [item["role"] for item in selected] == [
        "failure_analyst",
        "success_analyst",
    ]


def test_runner_error_retries_three_times_with_exponential_backoff(tmp_path: Path) -> None:
    sleeps: list[float] = []
    service = _bare_service(sleeps.append)

    class FlakyRunner:
        def __init__(self) -> None:
            self.calls = 0

        def execute(
            self, _configuration: dict[str, object], _workspace: Path, _prompt: str
        ) -> SimpleNamespace:
            self.calls += 1
            if self.calls < 3:
                raise AgentRunnerError("runner_failed", "transient")
            return SimpleNamespace(final_report="{}")

    runner = FlakyRunner()
    service._execute_optimizer_runner(runner, {}, tmp_path, "prompt")

    assert runner.calls == 3
    assert sleeps == [1.0, 2.0]


def test_runner_error_stops_after_third_attempt(tmp_path: Path) -> None:
    sleeps: list[float] = []
    service = _bare_service(sleeps.append)

    class FailingRunner:
        def __init__(self) -> None:
            self.calls = 0

        def execute(
            self, _configuration: dict[str, object], _workspace: Path, _prompt: str
        ) -> SimpleNamespace:
            self.calls += 1
            raise AgentRunnerError("runner_failed", "still failing")

    runner = FailingRunner()
    with pytest.raises(AgentRunnerError):
        service._execute_optimizer_runner(runner, {}, tmp_path, "prompt")

    assert runner.calls == 3
    assert sleeps == [1.0, 2.0]


def test_invalid_json_gets_exactly_one_repair_with_same_runner(tmp_path: Path) -> None:
    service = _bare_service()

    class RepairingRunner:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def execute(
            self, _configuration: dict[str, object], _workspace: Path, prompt: str
        ) -> SimpleNamespace:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return SimpleNamespace(final_report="not-json")
            return SimpleNamespace(final_report=json.dumps(_patch("repaired")))

    runner = RepairingRunner()
    output = service._run_optimizer_role(
        runner=runner,
        runner_config={},
        workspace=tmp_path,
        prompt="original-role-prompt",
        role="failure_analyst",
        prompt_version="skill_optimizer.failure_analyst.v1",
        candidate_count=2,
    )

    assert runner.prompts[0] == "original-role-prompt"
    assert "Format-repair request" in runner.prompts[1]
    assert len(runner.prompts) == 2
    assert output["legacy_output"] is True
    assert output["patches"][0]["rationale"] == "repaired"


def test_all_four_role_prompts_are_versioned_train_only_and_schema_explicit(
    tmp_path: Path,
) -> None:
    prompts = [
        OptimizationExperimentService._role_prompt(
            instruction="Improve.",
            role_spec=role_spec,
            role_index=index,
            candidate_count=3,
            skill_root=tmp_path / "skill",
            train_evidence={
                "evidence_scope": "train_only",
                "failure_tags": {"TRAIN_ONLY_TAG": 2},
            },
        )
        for index, role_spec in enumerate(OPTIMIZER_ROLE_SPECS, start=1)
    ]

    assert {spec["role"] for spec in OPTIMIZER_ROLE_SPECS} == {
        "failure_analyst",
        "success_analyst",
        "generalization_analyst",
        "simplification_analyst",
    }
    for prompt, spec in zip(prompts, OPTIMIZER_ROLE_SPECS, strict=True):
        assert spec["role"] in prompt
        assert spec["prompt_version"] in prompt
        assert "TRAIN_ONLY_TAG" in prompt
        assert "structured_skill_patch.v1" in prompt
        assert '"old":"exact unique text"' in prompt
        assert '"anchor":"exact unique text"' in prompt
        assert "Never emit old_text" in prompt
        assert "VALIDATION_SECRET" not in prompt
        assert "HIDDEN_SECRET" not in prompt


def test_optimizer_analyst_pipeline_invokes_all_four_roles(tmp_path: Path) -> None:
    service = _bare_service()

    class FourRoleRunner:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def execute(
            self, _configuration: dict[str, object], _workspace: Path, prompt: str
        ) -> SimpleNamespace:
            self.prompts.append(prompt)
            marker = f"role-{len(self.prompts)}"
            return SimpleNamespace(final_report=json.dumps(_patch(marker)))

    runner = FourRoleRunner()
    outputs, errors = service._run_optimizer_analysts(
        runner=runner,
        runner_config={},
        workspace=tmp_path,
        instruction="Improve.",
        skill_root=tmp_path / "skill",
        train_evidence={
            "evidence_scope": "train_only",
            "success_patterns": {"root_cause:match": 1},
        },
        candidate_count=3,
    )

    assert errors == []
    assert len(runner.prompts) == 4
    assert [output["role"] for output in outputs] == [
        spec["role"] for spec in OPTIMIZER_ROLE_SPECS
    ]
    assert len(_merge_role_patches(outputs, 3)) == 3
