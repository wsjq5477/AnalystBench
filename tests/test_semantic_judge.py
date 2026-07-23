import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from analystbench.config import Settings
from analystbench.eval_spec import EvalSpecV1
from analystbench.scoring import evaluate
from analystbench.semantic_alignment import SemanticAlignment
from analystbench.semantic_judge import SemanticJudge


def root_category_chain_spec() -> dict:
    source_ref = {"content_hash": "sha256:" + "a" * 64, "start": 0, "end": 1, "quote": "x"}
    return {
        "schema_version": "1.0",
        "case_revision_id": "case",
        "suite": {"id": "kdiag", "version": "1.0.0"},
        "claims": [
            {
                "id": "root",
                "type": "root_cause",
                "statement": "missing REPICK causes wrong CPU scheduling",
                "importance": "critical",
                "weight": 100,
                "source_ref": source_ref,
            },
            {
                "id": "category",
                "type": "classification",
                "statement": "HM_PANIC_SYSMGR",
                "importance": "high",
                "weight": 20,
                "source_ref": source_ref,
            },
            {
                "id": "chain-1",
                "type": "analysis_chain",
                "statement": "sleep timeout triggers panic",
                "importance": "normal",
                "weight": 20,
                "source_ref": source_ref,
                "evidence_keyword": "suspend to mem is timeout",
                "conclusion": "sleep timeout triggers panic",
            },
            {
                "id": "chain-2",
                "type": "analysis_chain",
                "statement": "the sh process is stuck",
                "importance": "normal",
                "weight": 20,
                "source_ref": source_ref,
                "evidence_keyword": "comm=sh",
                "conclusion": "the sh process is stuck",
            },
            {
                "id": "chain-3",
                "type": "analysis_chain",
                "statement": "liblinux_remove_cpu schedule is blocked",
                "importance": "normal",
                "weight": 20,
                "source_ref": source_ref,
                "evidence_keyword": "liblinux_remove_cpu",
                "conclusion": "liblinux_remove_cpu schedule is blocked",
            },
        ],
        "causal_edges": [],
        "forbidden_claims": [],
        "scoring_policy_version_id": "policy",
        "scoring_strategy": {
            "mode": "root_category_chain",
            "root_cause_score": 100,
            "category_score": 20,
            "chain_total_score": 60,
        },
        "review": {"status": "approved", "unresolved_items": []},
    }


def _judge(tmp_path: Path) -> SemanticJudge:
    fixture = Path(__file__).parent / "fixtures" / "fake_semantic_judge.py"
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
    )
    return SemanticJudge(
        settings, "claude-code", {"executable": sys.executable, "extra_args": [str(fixture)]}
    )


def _report() -> str:
    return (
        "suspend to mem is timeout; sleep timeout triggers panic. "
        "comm=sh; the sh process is stuck. "
        "liblinux_remove_cpu; schedule is blocked."
    )


def test_partial_match_rejects_a_different_subject() -> None:
    with pytest.raises(ValidationError):
        SemanticAlignment.model_validate(
            {
                "gold_claim_id": "chain-2",
                "relation": "partial_match",
                "confidence": 0.6,
                "reason": "different service",
                "subject_match": False,
                "predicate_match": True,
                "causal_direction_match": None,
                "missing_essential_facts": [],
                "conclusion_similarity": 0.5,
            }
        )


def test_semantic_judge_prompt_allows_category_aliases() -> None:
    prompt = SemanticJudge._prompt(EvalSpecV1.model_validate(root_category_chain_spec()), "report")

    assert "HM_PANIC_SYSMGR" in prompt
    assert "sysmgr panic" in prompt
    assert "不要求分类编码逐字相同" in prompt


def test_semantic_judge_reads_original_report_without_candidates(tmp_path: Path) -> None:
    judge = _judge(tmp_path)
    report = _report()
    judged = judge.align(EvalSpecV1.model_validate(root_category_chain_spec()), [], report)

    assert [item["gold_claim_id"] for item in judged["alignments"]] == [
        "root",
        "category",
        "chain-1",
        "chain-2",
        "chain-3",
    ]
    assert judged["alignments"][2]["candidate_claim_id"] is None
    assert judged["alignments"][2]["candidate_ref"] is None
    assert judged["supported_candidate_claim_ids"] == []
    assert "citation_mode" not in judge.audit


def test_python_applies_fixed_score_to_semantic_alignments(tmp_path: Path) -> None:
    judge = _judge(tmp_path)
    result = evaluate(
        root_category_chain_spec(), _report(), "sha256:" + "b" * 64, alignment_judge=judge.align
    )

    assert result["metrics"]["root_cause_exact"] is False
    assert result["positive_score"] == "60.00"
    assert result["total_score"] == "60.00"
    assert result["candidate_claims"] == []
