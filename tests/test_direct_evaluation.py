from pathlib import Path

import pytest

from analystbench.config import Settings
from analystbench.errors import AnalystBenchError
from analystbench.evaluation.direct import (
    evaluate_direct,
    evaluate_direct_with_alignment,
    prepare_alignment_draft,
)
from analystbench.scoring.reporting import render_markdown


def _case_payload() -> dict:
    reference = (
        "问题分类：HM_PANIC_SYSMGR\n"
        "问题根因：调度问题，开抢占未REPICK\n"
        "分析链：\n"
        "证据1：suspend to mem is timeout\n"
        "结论1：休眠超时"
    )
    return {
        "case": {
            "case_key": "case-1",
            "reference_answer": reference,
            "test_set": {"key": "kernel", "name": "Kernel"},
        },
        "eval_spec_draft": {
            "claims": [
                {
                    "id": "root",
                    "type": "root_cause",
                    "statement": "调度问题，开抢占未REPICK",
                    "importance": "critical",
                    "weight": 100,
                    "quote": "调度问题，开抢占未REPICK",
                },
                {
                    "id": "category",
                    "type": "classification",
                    "statement": "HM_PANIC_SYSMGR",
                    "importance": "high",
                    "weight": 20,
                    "quote": "问题分类：HM_PANIC_SYSMGR",
                },
                {
                    "id": "chain-1",
                    "type": "analysis_chain",
                    "statement": "休眠超时",
                    "importance": "normal",
                    "weight": 60,
                    "evidence_keyword": "suspend to mem is timeout",
                    "conclusion": "休眠超时",
                    "quote": "证据1：suspend to mem is timeout\n结论1：休眠超时",
                },
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


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'must-not-be-created.db'}",
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
    )


def test_direct_evaluation_scores_without_creating_database(tmp_path: Path) -> None:
    report = "最终根因：调度问题，开抢占未REPICK"
    result = evaluate_direct(
        _case_payload(),
        "case-1",
        [{"candidate": {"name": "test-1-agent-1"}, "candidate_report": report, "claim_hints": []}],
        _settings(tmp_path),
        "lexical",
        str(tmp_path / "case-1.json"),
    )

    assert result["mode"] == "direct_file"
    assert result["reports"][0]["score"] == "100.00"
    assert result["reports"][0]["result"]["metrics"]["root_cause_exact"] is True
    assert result["case_source"]["source_path"] == str(tmp_path / "case-1.json")
    assert "本地 Case JSON 直接评分（不使用数据库）" in render_markdown(result["summary"])
    assert not (tmp_path / "must-not-be-created.db").exists()


def test_direct_evaluation_requires_resolved_case(tmp_path: Path) -> None:
    payload = _case_payload()
    payload["eval_spec_draft"]["unresolved_items"] = ["请确认根因"]

    with pytest.raises(AnalystBenchError) as captured:
        evaluate_direct(
            payload,
            "case-1",
            [{"candidate": {"name": "report-1"}, "candidate_report": "报告", "claim_hints": []}],
            _settings(tmp_path),
            "lexical",
        )

    assert captured.value.code == "direct_case_unresolved"


def test_prepare_and_score_semantic_alignment_without_report_splitting(tmp_path: Path) -> None:
    report = (
        "问题分类是 HM_PANIC_SYSMGR。根因：调度问题，开抢占未REPICK。"
        "证据：suspend to mem is timeout；结论：休眠超时。"
    )
    reports = [
        {"candidate": {"name": "test-1-agent-1"}, "candidate_report": report, "claim_hints": []}
    ]
    draft = prepare_alignment_draft(
        _case_payload(), "case-1", reports, str(tmp_path / "case-1.json")
    )
    entry = draft["reports"]["test-1-agent-1"]

    assert "candidate_claims" not in draft
    assert entry["python_keyword_audits"]["chain-1"]["keyword_match"] is True

    alignments = entry["semantic_alignment"]["alignments"]
    for item in alignments:
        if item["gold_claim_id"] == "root":
            item.update(
                {
                    "relation": "match",
                    "confidence": 0.95,
                    "reason": "明确给出同一根因",
                    "subject_match": True,
                    "predicate_match": True,
                    "causal_direction_match": True,
                }
            )
        elif item["gold_claim_id"] == "category":
            item.update(
                {
                    "relation": "match",
                    "confidence": 0.95,
                    "reason": "明确给出同一分类",
                    "subject_match": True,
                    "predicate_match": True,
                    "causal_direction_match": None,
                }
            )
        else:
            item.update(
                {
                    "relation": "match",
                    "confidence": 0.95,
                    "reason": "结论等价",
                    "subject_match": True,
                    "predicate_match": True,
                    "causal_direction_match": None,
                    "conclusion_similarity": 1.0,
                }
            )

    result = evaluate_direct_with_alignment(
        _case_payload(), "case-1", reports, draft, str(tmp_path / "case-1.json")
    )

    assert result["mode"] == "direct_file"
    assert result["reports"][0]["score"] == "100.00"
    assert result["reports"][0]["result"]["candidate_claims"] == []


def test_direct_evaluation_with_alignment_missing_report() -> None:
    with pytest.raises(AnalystBenchError) as captured:
        evaluate_direct_with_alignment(
            _case_payload(),
            "case-1",
            [
                {
                    "candidate": {"name": "missing-report"},
                    "candidate_report": "报告",
                    "claim_hints": [],
                }
            ],
            {
                "schema_version": "1.0",
                "kind": "analystbench_semantic_alignment_draft",
                "case": {"case_content_hash": "bad"},
                "reports": {},
            },
            None,
        )

    assert captured.value.code in {"alignment_case_mismatch", "alignment_missing"}
