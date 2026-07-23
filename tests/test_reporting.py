from analystbench.reporting import build_human_summary, render_markdown


def test_analysis_chain_uses_combined_keyword_and_conclusion_verdict() -> None:
    keyword = "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter"
    case_payload = {
        "eval_spec_draft": {
            "scoring_strategy": {"mode": "root_category_chain"},
            "claims": [
                {
                    "id": "chain-2",
                    "type": "analysis_chain",
                    "statement": "cpuhp卡主",
                    "importance": "normal",
                    "weight": 20,
                    "evidence_keyword": keyword,
                }
            ],
        }
    }
    reports = [
        {
            "candidate_name": "agent-1",
            "status": "completed",
            "score": "10.00",
            "passed": False,
            "warnings": [],
            "result": {
                "positive_score": "10.00",
                "penalties": "0.00",
                "metrics": {"root_cause_exact": False},
                "judge": {"kind": "semantic_llm", "runner": "claude-code"},
                "claim_results": [
                    {
                        "gold_claim_id": "chain-2",
                        "relation": "match",
                        "score": "10.00",
                        "keyword_match": False,
                        "keyword_score": "0.00",
                        "conclusion_similarity": 1.0,
                        "conclusion_score": "10.00",
                        "closest_keyword_line": {
                            "line_number": 32,
                            "quote": "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter",
                        },
                        "candidate_ref": {"quote": "cpuhp卡主"},
                    }
                ],
            },
        }
    ]

    summary = build_human_summary(
        "case-1",
        case_payload,
        reports,
        [],
        {
            "mode": "database",
            "case_version": 1,
            "eval_spec_version_id": "spec-1",
            "source_filename": "case-1.json",
        },
    )

    chain = summary["reports"][0]["claims"][0]
    assert chain["relation_label"] == "部分命中"
    assert chain["conclusion_relation_label"] == "完全命中"
    markdown = render_markdown(summary)
    assert "| chain-2：cpuhp卡主 | 20 | 部分命中 | 未命中（0.00）" in markdown
    assert "完全命中 / 1.00（10.00）" in markdown
    assert f"要求连续原文：`{keyword}`" in markdown
    assert "最接近的报告第 32 行" in markdown
    assert "评分模式：数据库已发布 Case" in markdown
