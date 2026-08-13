from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace

from analystbench.skill_optimization.reporting import (
    build_optimization_ledger,
    render_optimization_ledger_csv,
    render_optimization_ledger_markdown,
    serialize_optimization_ledger,
)


def _comparison(
    comparison_id: str,
    comparison_type: str,
    *,
    pairs: list[dict[str, object]],
    verdict: str,
    active_level: str | None = None,
    reasons: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    deltas = [float(item["delta"]) for item in pairs]
    return {
        "id": comparison_id,
        "type": comparison_type,
        "created_at": "2026-08-12T10:00:00+00:00",
        "metrics": {
            "case_count": len(pairs),
            "overall_delta": sum(deltas) / len(deltas),
            "repeat_count": 3,
            "pairs": pairs,
            "family_deltas": {str(item["case_family"]): float(item["delta"]) for item in pairs},
            "dimension_deltas": {
                "root_cause": sum(
                    float(item["dimension_deltas"]["root_cause"])  # type: ignore[index]
                    for item in pairs
                )
                / len(pairs)
            },
        },
        "gate": {
            "verdict": verdict,
            "active_level": active_level,
            "reasons": reasons or [],
            "metrics": {"overall_delta": sum(deltas) / len(deltas)},
        },
    }


def test_build_optimization_ledger_records_scores_changes_and_decisions() -> None:
    epoch_one_pairs = [
        {
            "case_path": "cases/b.json",
            "case_family": "family-b",
            "baseline_score": 72,
            "candidate_score": 75,
            "delta": 3,
            "dimension_deltas": {"root_cause": 1},
        },
        {
            "case_path": "cases/a.json",
            "case_family": "family-a",
            "baseline_score": 68,
            "candidate_score": 73,
            "delta": 5,
            "dimension_deltas": {"root_cause": 3},
        },
    ]
    epoch_two_pairs = [
        {
            "case_path": "cases/a.json",
            "case_family": "family-a",
            "baseline_score": 75,
            "candidate_score": 74,
            "delta": -1,
            "dimension_deltas": {"root_cause": -1},
        },
        {
            "case_path": "cases/b.json",
            "case_family": "family-b",
            "baseline_score": 77,
            "candidate_score": 76,
            "delta": -1,
            "dimension_deltas": {"root_cause": -1},
        },
    ]
    detail = {
        "experiment": SimpleNamespace(
            id="exp-1",
            name="Kernel Skill",
            status="completed",
            skill_id="skill-1",
            base_skill_version_id="version-1",
            evaluation_target_id="target-1",
            data_snapshot_id="snapshot-1",
            optimizer_policy_version_id="policy-1",
            verifier_bundle_version_id="verifier-1",
            current_epoch_number=2,
            max_epochs=2,
            stop_reason="MAX_EPOCHS",
            started_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
            finished_at=datetime(2026, 8, 12, 11, tzinfo=UTC),
        ),
        "version_metadata": {
            "version-3": {
                "version_number": 3,
                "package_hash": "sha256:333",
            },
            "version-1": {
                "version_number": 1,
                "package_hash": "sha256:111",
            },
            "version-2": {
                "version_number": 2,
                "package_hash": "sha256:222",
                "change_stats": {
                    "tokens_added": 12,
                    "deleted_tokens": 4,
                    "lines_added": 5,
                    "lines_removed": 1,
                    "per_file": {"SKILL.md": {"added_tokens": 8, "deleted_tokens": 4}},
                },
            },
        },
        # Deliberately reversed: the ledger must be ordered by epoch number.
        "epochs": [
            {
                "id": "epoch-2",
                "number": 2,
                "status": "completed",
                "parent_skill_version_id": "version-2",
                "best_candidate_version_id": None,
                "decision": "retain",
                "finished_at": "2026-08-12T11:00:00+00:00",
                "candidates": [
                    {
                        "id": "candidate-3",
                        "candidate_skill_version_id": "version-3",
                        "candidate_type": "structured_patch_1",
                        "status": "rejected",
                        "patch_hash": "sha256:patch3",
                        "rationale": "Avoid an unsupported conclusion.",
                        "patch": {
                            "operations": [
                                {
                                    "op": "replace",
                                    "path": "SKILL.md",
                                    "old": "old",
                                    "new": "new",
                                }
                            ]
                        },
                        "rejection_code": "minimum_delta_not_met",
                        "rejection_detail": {"observed": -1},
                        "comparisons": [
                            _comparison(
                                "comparison-3",
                                "paired_repeated_validation",
                                pairs=epoch_two_pairs,
                                verdict="reject",
                                reasons=[
                                    {
                                        "code": "minimum_delta_not_met",
                                        "observed": -1,
                                    }
                                ],
                            )
                        ],
                    }
                ],
            },
            {
                "id": "epoch-1",
                "number": 1,
                "status": "completed",
                "parent_skill_version_id": "version-1",
                "best_candidate_version_id": "version-2",
                "decision": "promote",
                "finished_at": "2026-08-12T10:00:00+00:00",
                "candidates": [
                    {
                        "id": "candidate-2",
                        "candidate_skill_version_id": "version-rejected",
                        "candidate_type": "structured_patch_2",
                        "status": "rejected",
                        "patch": {"operations": []},
                        "patch_hash": "sha256:patch2",
                        "rationale": "A weaker alternative.",
                        "rejection_code": "screening_rejected",
                        "rejection_detail": {"verdict": "reject"},
                        "comparisons": [
                            _comparison(
                                "screening-2",
                                "screening",
                                pairs=[epoch_one_pairs[0]],
                                verdict="reject",
                                reasons=[{"code": "screening_delta_below_minimum"}],
                            )
                        ],
                    },
                    {
                        "id": "candidate-1",
                        "candidate_skill_version_id": "version-2",
                        "candidate_type": "structured_patch_1",
                        "status": "accepted",
                        "patch_hash": "sha256:patch1",
                        "rationale": "Bind root-cause claims to evidence.",
                        "intended_failure_clusters": ["unsupported_claim", "evidence"],
                        "intent": {
                            "change_type": "evidence_binding",
                            "expected_dimensions": ["root_cause"],
                            "protected_behaviors": ["classification"],
                        },
                        "patch": {
                            "operations": [
                                {
                                    "op": "append",
                                    "path": "SKILL.md",
                                    "content": "Evidence rules",
                                },
                                {
                                    "op": "create",
                                    "path": "references/evidence.md",
                                    "content": "Evidence guide",
                                },
                            ]
                        },
                        "rejection_code": None,
                        "rejection_detail": {},
                        "comparisons": [
                            _comparison(
                                "comparison-1",
                                "paired_repeated_validation",
                                pairs=epoch_one_pairs,
                                verdict="pass",
                                active_level="provisional",
                            )
                        ],
                    },
                ],
            },
        ],
    }

    ledger = build_optimization_ledger(detail)

    assert ledger["schema_version"] == "1"
    assert ledger["experiment"]["started_at"] == "2026-08-12T09:00:00+00:00"
    assert ledger["summary"] == {
        "initial_score": 70.0,
        "final_score": 74.0,
        "active_path_score": 74.0,
        "score_semantics": "initial_score_plus_promoted_epoch_deltas",
        "cumulative_delta": 4.0,
        "promoted_epochs": 1,
        "retained_epochs": 1,
        "stop_reason": "MAX_EPOCHS",
    }
    assert [item["epoch_number"] for item in ledger["epochs"]] == [1, 2]

    first = ledger["epochs"][0]
    assert first["parent"]["version_number"] == 1
    assert first["selected_candidate"]["candidate_id"] == "candidate-1"
    assert first["selected_candidate"]["version"]["version_number"] == 2
    assert first["baseline_score"] == 70.0
    assert first["candidate_score"] == 74.0
    assert first["epoch_delta"] == 4.0
    assert first["cumulative_delta"] == 4.0
    assert [item["case_path"] for item in first["case_deltas"]] == [
        "cases/a.json",
        "cases/b.json",
    ]
    assert first["family_deltas"] == {"family-a": 5.0, "family-b": 3.0}
    assert first["dimension_deltas"] == {"root_cause": 2.0}
    assert first["changes"] == {
        "operation_count": 2,
        "operation_types": {"append": 1, "create": 1},
        "file_count": 2,
        "files": ["SKILL.md", "references/evidence.md"],
        "tokens_added": 12,
        "tokens_removed": 4,
        "token_delta": 8,
        "characters_added": None,
        "characters_removed": None,
        "lines_added": 5,
        "lines_removed": 1,
        "per_file": {"SKILL.md": {"added_tokens": 8, "deleted_tokens": 4}},
        "operations": [
            {"op": "append", "path": "SKILL.md"},
            {"op": "create", "path": "references/evidence.md"},
        ],
    }
    assert first["gate"]["verdict"] == "pass"
    assert first["decision"] == {
        "action": "promote",
        "active_changed": True,
        "previous_active_version_id": "version-1",
        "new_active_version_id": "version-2",
    }
    assert first["rejections"][0]["code"] == "screening_rejected"

    second = ledger["epochs"][1]
    assert second["selected_candidate"]["candidate_id"] == "candidate-3"
    assert second["epoch_delta"] == -1.0
    assert second["cumulative_delta"] == 4.0
    assert second["decision"]["active_changed"] is False
    assert second["decision"]["new_active_version_id"] == "version-2"
    assert second["gate"]["reasons"][0]["code"] == "minimum_delta_not_met"


def test_ledger_exports_are_deterministic_and_human_readable() -> None:
    detail = {
        "experiment": {
            "id": "exp-export",
            "name": "Export test",
            "status": "completed",
            "base_skill_version_id": "v1",
            "stop_reason": "MAX_EPOCHS",
        },
        "version_metadata": {
            "v1": {"version_number": 1},
            "v2": {"version_number": 2},
        },
        "epochs": [
            {
                "id": "epoch-1",
                "number": 1,
                "status": "completed",
                "parent_skill_version_id": "v1",
                "best_candidate_version_id": "v2",
                "decision": "promote",
                "candidates": [
                    {
                        "id": "candidate-1",
                        "candidate_skill_version_id": "v2",
                        "status": "accepted",
                        "rationale": "Improve | evidence\nwithout changing classification.",
                        "patch": {
                            "operations": [{"op": "append", "path": "SKILL.md", "content": "x"}]
                        },
                        "change_stats": {"tokens_added": 2, "tokens_removed": 0},
                        "comparisons": [
                            _comparison(
                                "comparison-1",
                                "paired_repeated_validation",
                                pairs=[
                                    {
                                        "case_path": "case-1",
                                        "case_family": "kernel",
                                        "baseline_score": 80,
                                        "candidate_score": 82.5,
                                        "delta": 2.5,
                                        "dimension_deltas": {"root_cause": 2.5},
                                    }
                                ],
                                verdict="pass",
                                active_level="provisional",
                            )
                        ],
                    }
                ],
            }
        ],
    }
    ledger = build_optimization_ledger(detail)

    encoded = serialize_optimization_ledger(ledger)
    assert encoded == serialize_optimization_ledger(ledger)
    assert json.loads(encoded) == ledger

    markdown = render_optimization_ledger_markdown(ledger)
    assert "# Skill 自优化账本" in markdown
    assert "80 → 82.5；本轮 +2.5；累计 +2.5" in markdown
    assert "Improve \\| evidence without changing classification." in markdown
    assert "Gate：pass；Active Level：provisional" in markdown
    assert "| case-1 | kernel | 80 | 82.5 | +2.5 |" in markdown

    rows = list(csv.DictReader(StringIO(render_optimization_ledger_csv(ledger))))
    assert len(rows) == 1
    assert rows[0]["epoch_number"] == "1"
    assert rows[0]["parent_version_id"] == "v1"
    assert rows[0]["selected_candidate_version_id"] == "v2"
    assert rows[0]["epoch_delta"] == "2.5"
    assert rows[0]["cumulative_delta"] == "2.5"
    assert rows[0]["changed_files"] == "SKILL.md"
    assert rows[0]["case_deltas_json"].startswith('[{"baseline_duration_ms"')


def test_running_ledger_keeps_missing_measurements_null() -> None:
    detail = {
        "experiment": {
            "id": "exp-running",
            "name": "Running",
            "status": "running",
            "stop_reason": None,
        },
        "epochs": [
            {
                "id": "epoch-running",
                "number": 1,
                "status": "generating_candidates",
                "parent_skill_version_id": "version-1",
                "selected_candidate_mutation_id": "candidate-running",
                "decision": None,
                "candidates": [
                    {
                        "id": "candidate-running",
                        "candidate_skill_version_id": None,
                        "status": "proposed",
                        "patch": {"operations": "not-a-list"},
                        "comparisons": [],
                    }
                ],
            }
        ],
    }

    ledger = build_optimization_ledger(detail)

    assert ledger["summary"]["initial_score"] is None
    assert ledger["summary"]["final_score"] is None
    assert ledger["summary"]["cumulative_delta"] is None
    epoch = ledger["epochs"][0]
    assert epoch["baseline_score"] is None
    assert epoch["candidate_score"] is None
    assert epoch["epoch_delta"] is None
    assert epoch["cumulative_delta"] is None
    assert epoch["gate"] is None
    assert epoch["decision"]["active_changed"] is None
    assert epoch["changes"]["operation_count"] == 0
    assert "— → —；本轮 —；累计 —" in render_optimization_ledger_markdown(ledger)


def test_no_screening_survivor_reports_best_attempt_without_active_delta() -> None:
    detail = {
        "experiment": {
            "id": "exp-screening-rejected",
            "name": "Rejected screening attempts",
            "status": "completed",
            "base_skill_version_id": "v1",
            "stop_reason": "NO_IMPROVEMENT",
        },
        "version_metadata": {"v1": {"version_number": 1}, "v2": {"version_number": 2}},
        "epochs": [
            {
                "id": "epoch-1",
                "number": 1,
                "status": "completed",
                "parent_skill_version_id": "v1",
                "best_candidate_version_id": None,
                "decision": "no_screening_survivor",
                "candidates": [
                    {
                        "id": "candidate-1",
                        "candidate_skill_version_id": "v2",
                        "status": "rejected",
                        "rationale": "Tighten evidence requirements.",
                        "change_stats": {"added_tokens": 8, "deleted_tokens": 2},
                        "patch": {"operations": [{"op": "append", "path": "SKILL.md"}]},
                        "rejection_code": "screening_rejected",
                        "comparisons": [
                            _comparison(
                                "screening-1",
                                "screening",
                                pairs=[
                                    {
                                        "case_path": "case-1",
                                        "case_family": "kernel",
                                        "baseline_score": 70,
                                        "candidate_score": 67,
                                        "delta": -3,
                                        "dimension_deltas": {"root_cause": -3},
                                    }
                                ],
                                verdict="reject",
                            )
                        ],
                    }
                ],
            }
        ],
    }

    ledger = build_optimization_ledger(detail)

    epoch = ledger["epochs"][0]
    assert epoch["selected_candidate"]["candidate_id"] == "candidate-1"
    assert epoch["baseline_score"] == 70
    assert epoch["candidate_score"] == 67
    assert epoch["epoch_delta"] == -3
    assert epoch["cumulative_delta"] == 0
    assert epoch["decision"]["active_changed"] is False
    assert epoch["changes"]["token_delta"] == 6
    assert ledger["summary"] == {
        "initial_score": 70,
        "final_score": 70,
        "active_path_score": 70,
        "score_semantics": "initial_score_plus_promoted_epoch_deltas",
        "cumulative_delta": 0,
        "promoted_epochs": 0,
        "retained_epochs": 1,
        "stop_reason": "NO_IMPROVEMENT",
    }
    markdown = render_optimization_ledger_markdown(ledger)
    assert "70 → 67；本轮 -3；累计 0" in markdown
    csv_row = next(csv.DictReader(StringIO(render_optimization_ledger_csv(ledger))))
    assert csv_row["epoch_delta"] == "-3.0"
    assert csv_row["cumulative_delta"] == "0.0"


def test_promoted_epoch_without_metrics_makes_cumulative_score_unknown() -> None:
    ledger = build_optimization_ledger(
        {
            "experiment": {"id": "exp-incomplete", "status": "completed"},
            "epochs": [
                {
                    "id": "epoch-1",
                    "number": 1,
                    "status": "completed",
                    "parent_skill_version_id": "v1",
                    "best_candidate_version_id": "v2",
                    "decision": "promote",
                    "candidates": [
                        {
                            "id": "candidate-1",
                            "candidate_skill_version_id": "v2",
                            "status": "accepted",
                            "patch": {"operations": []},
                            "comparisons": [],
                        }
                    ],
                }
            ],
        }
    )

    assert ledger["summary"]["promoted_epochs"] == 1
    assert ledger["summary"]["initial_score"] is None
    assert ledger["summary"]["final_score"] is None
    assert ledger["summary"]["cumulative_delta"] is None
