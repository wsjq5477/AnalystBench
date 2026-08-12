"""Tests for Skill semantic alignment."""

import pytest
from pydantic import ValidationError

from analystbench.evaluation.spec import EvalSpecV1
from analystbench.scoring.engine import evaluate
from analystbench.scoring.skill_adapter import make_skill_alignment_judge


def _spec_payload() -> dict:
    source_ref = {"content_hash": "sha256:" + "a" * 64, "start": 0, "end": 1, "quote": "x"}
    return {
        "schema_version": "1.0",
        "case_revision_id": "test",
        "suite": {"id": "test-suite", "version": "1.0.0"},
        "scoring_policy_version_id": "test-policy",
        "claims": [
            {
                "id": "root",
                "type": "root_cause",
                "statement": "Scheduler repick root cause",
                "importance": "critical",
                "weight": 70,
                "source_ref": source_ref,
            },
            {
                "id": "claim-1",
                "type": "impact",
                "statement": "service failure",
                "importance": "normal",
                "weight": 30,
                "source_ref": source_ref,
            },
        ],
        "causal_edges": [],
        "forbidden_claims": [],
        "scoring_strategy": {"mode": "weighted_sum"},
    }


def _alignment(report: str, matched: bool = True) -> dict:
    if not matched:
        return {
            "alignments": [
                {
                    "gold_claim_id": gold_id,
                    "relation": "missing",
                    "confidence": 0.0,
                    "reason": "report does not state this conclusion",
                    "subject_match": False,
                    "predicate_match": False,
                    "causal_direction_match": None,
                    "missing_essential_facts": [],
                    "conclusion_similarity": None,
                }
                for gold_id in ("root", "claim-1")
            ]
        }
    return {
        "alignments": [
            {
                "gold_claim_id": "root",
                "relation": "match",
                "confidence": 0.95,
                "reason": "the report gives the same root cause",
                "subject_match": True,
                "predicate_match": True,
                "causal_direction_match": True,
                "missing_essential_facts": [],
                "conclusion_similarity": None,
            },
            {
                "gold_claim_id": "claim-1",
                "relation": "match",
                "confidence": 0.95,
                "reason": "the report describes the same impact",
                "subject_match": True,
                "predicate_match": True,
                "causal_direction_match": None,
                "missing_essential_facts": [],
                "conclusion_similarity": None,
            },
        ]
    }


def test_skill_alignment_uses_full_report_without_extraction() -> None:
    report = "Scheduler repick root cause. Service failure occurred."
    spec_payload = _spec_payload()
    spec = EvalSpecV1.model_validate(spec_payload)
    judge = make_skill_alignment_judge(_alignment(report), spec)

    result = evaluate(spec_payload, report, "sha256:" + "b" * 64, alignment_judge=judge)

    root_result = next(item for item in result["claim_results"] if item["gold_claim_id"] == "root")
    assert root_result["relation"] == "match"
    assert root_result["candidate_ref"] is None
    assert result["candidate_claims"] == []


def test_skill_alignment_missing_yields_zero_score() -> None:
    report = "Something unrelated."
    spec_payload = _spec_payload()
    spec = EvalSpecV1.model_validate(spec_payload)
    judge = make_skill_alignment_judge(_alignment(report, matched=False), spec)

    result = evaluate(spec_payload, report, "sha256:" + "c" * 64, alignment_judge=judge)

    assert result["total_score"] == "0.00"


def test_skill_alignment_rejects_unknown_fields() -> None:
    spec = EvalSpecV1.model_validate(_spec_payload())
    bad_json = _alignment("Scheduler repick root cause. Service failure occurred.")
    bad_json["alignments"][0]["evidence_quote"] = "removed field"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        make_skill_alignment_judge(bad_json, spec)


def test_skill_alignment_rejects_incomplete_json() -> None:
    spec = EvalSpecV1.model_validate(_spec_payload())
    with pytest.raises(ValidationError):
        make_skill_alignment_judge({"alignments": [{"gold_claim_id": "root"}]}, spec)
