from analystbench.scoring import CandidateAnalyzer, evaluate


def spec() -> dict:
    return {
        "schema_version": "1.0",
        "case_revision_id": "case",
        "suite": {"id": "generic-analysis", "version": "1.0.0"},
        "claims": [
            {
                "id": "root",
                "type": "root_cause",
                "statement": "stale cache pointer caused failure",
                "importance": "critical",
                "weight": 70,
                "source_ref": {
                    "content_hash": "sha256:" + "a" * 64,
                    "start": 0,
                    "end": 1,
                    "quote": "x",
                },
            },
            {
                "id": "claim-1",
                "type": "impact",
                "statement": "service failure",
                "importance": "normal",
                "weight": 30,
                "source_ref": {
                    "content_hash": "sha256:" + "a" * 64,
                    "start": 0,
                    "end": 1,
                    "quote": "x",
                },
            },
        ],
        "causal_edges": [],
        "forbidden_claims": [],
        "scoring_policy_version_id": "policy",
        "review": {"status": "approved", "unresolved_items": []},
    }


def test_scoring_uses_one_to_one_alignment_and_preserves_process_score() -> None:
    result = evaluate(
        spec(),
        "The stale cache pointer caused failure. Service failure.",
        "sha256:" + "b" * 64,
    )
    matched = [
        item["candidate_claim_id"] for item in result["claim_results"] if item["candidate_claim_id"]
    ]
    assert len(matched) == len(set(matched))
    assert result["total_score"] == "100.00"


def test_root_cause_contradiction_is_penalized_but_not_direct_failure() -> None:
    result = evaluate(
        spec(),
        "The stale cache pointer did not cause failure. Service failure.",
        "sha256:" + "b" * 64,
    )
    root = result["claim_results"][0]
    assert root["relation"] == "contradiction"
    assert result["penalties"] == "15.00"
    assert result["total_score"] == "15.00"
    assert result["passed"] is False


def test_explicit_gold_text_inside_a_long_candidate_span_is_a_match() -> None:
    result = evaluate(
        spec(),
        "Timeline context: the stale cache pointer caused failure; unrelated recovery followed.",
        "sha256:" + "b" * 64,
    )
    assert result["claim_results"][0]["relation"] == "match"


def root_category_chain_spec(chain_count: int = 3) -> dict:
    payload = spec()
    source = payload["claims"][1]
    payload["claims"] = [
        {**payload["claims"][0], "statement": "scheduler repick root cause", "weight": 100},
        {
            **source,
            "id": "category",
            "type": "classification",
            "statement": "HM_PANIC_SYSMGR",
            "importance": "high",
            "weight": 20,
        },
    ]
    chains = [
        ("suspend to mem is timeout", "休眠超时"),
        ("cpuhp: listener devmgr.actv handling cpu1 event: 2 enter", "cpuhp卡主"),
        ("liblinux_remove_cpu", "卡在liblinux_remove_cpu的schedule，怀疑调度相关"),
        ("schedule_timeout", "调度超时"),
    ]
    for index, (keyword, conclusion) in enumerate(chains[:chain_count], 1):
        payload["claims"].append(
            {
                **source,
                "id": f"chain-{index}",
                "type": "analysis_chain",
                "statement": conclusion,
                "importance": "normal",
                "weight": 60 / chain_count,
                "evidence_keyword": keyword,
                "conclusion": conclusion,
            }
        )
    payload["scoring_strategy"] = {
        "mode": "root_category_chain",
        "root_cause_score": 100,
        "category_score": 20,
        "chain_total_score": 60,
    }
    return payload


def semantic_judge(relations: dict[str, tuple[str, float | None]]):
    def judge(spec_payload, candidates, report):
        alignments = []
        for claim in spec_payload.claims:
            relation, similarity = relations.get(claim.id, ("missing", None))
            candidate = candidates[0] if relation != "missing" and candidates else None
            alignments.append(
                {
                    "gold_claim_id": claim.id,
                    "candidate_claim_id": candidate.id if candidate else None,
                    "relation": relation,
                    "confidence": 1.0,
                    "reason": "test judge",
                    "candidate_ref": candidate.source_ref if candidate else None,
                    "certainty": candidate.certainty if candidate else None,
                    "semantic_details": {"conclusion_similarity": similarity},
                }
            )
        return {
            "alignments": alignments,
            "candidate_assessments": [],
            "supported_candidate_claim_ids": [],
        }

    return judge


def test_exact_root_cause_short_circuits_to_100_even_with_extra_claims() -> None:
    result = evaluate(
        root_category_chain_spec(),
        "Scheduler repick root cause. Extra one. Extra two. Extra three.",
        "sha256:" + "c" * 64,
        alignment_judge=semantic_judge({"root": ("match", None)}),
    )
    assert result["metrics"]["root_cause_exact"] is True
    assert result["total_score"] == "100.00"


def test_category_and_chain_components_use_keyword_and_semantic_similarity() -> None:
    report = (
        "HM_PANIC_SYSMGR。suspend to mem is timeout：休眠超时。"
        "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter：cpuhp卡主。"
        "liblinux_remove_cpu。"
    )
    result = evaluate(
        root_category_chain_spec(),
        report,
        "sha256:" + "d" * 64,
        alignment_judge=semantic_judge(
            {
                "category": ("match", None),
                "chain-1": ("match", 1.0),
                "chain-2": ("partial_match", 0.5),
                "chain-3": ("missing", 0.0),
            }
        ),
    )
    # 20 category + (10 + 10) + (10 + 5) + (10 + 0) = 65.
    assert result["positive_score"] == "65.00"
    assert result["total_score"] == "65.00"
    chain_2 = next(item for item in result["claim_results"] if item["gold_claim_id"] == "chain-2")
    assert chain_2["keyword_score"] == "10.00"
    assert chain_2["conclusion_score"] == "5.00"


def test_four_chains_split_60_into_15_each_and_each_half_is_7_5() -> None:
    spec_payload = root_category_chain_spec(chain_count=4)
    report = (
        "suspend to mem is timeout "
        "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter "
        "liblinux_remove_cpu schedule_timeout"
    )
    result = evaluate(
        spec_payload,
        report,
        "sha256:" + "e" * 64,
        alignment_judge=semantic_judge(
            {f"chain-{index}": ("match", 1.0) for index in range(1, 5)}
        ),
    )
    assert result["total_score"] == "60.00"
    first_chain = next(
        item for item in result["claim_results"] if item["gold_claim_id"] == "chain-1"
    )
    assert first_chain["keyword_score"] == "7.50"
    assert first_chain["conclusion_score"] == "7.50"


def test_technical_identifier_dot_does_not_split_candidate_claim() -> None:
    report = (
        "CPU 热插拔挂起点：\n"
        "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter → 永不返回。"
    )

    candidates = CandidateAnalyzer().analyze(report, "sha256:" + "f" * 64)

    assert any(
        "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter" in claim.statement
        for claim in candidates
    )


def test_keyword_miss_records_python_nearest_line_diagnostic() -> None:
    spec_payload = root_category_chain_spec()
    chain_2 = next(claim for claim in spec_payload["claims"] if claim["id"] == "chain-2")
    chain_2["evidence_keyword"] = (
        "[cpuhp_notifier_handle:108] "
        "cpuhp: listener devmgr.actv handling cpu1 event: 2 enter"
    )
    report = (
        "前置日志\n"
        "[233.074152s] cpuhp: listener devmgr.actv handling cpu1 event: 2 enter → 永不返回\n"
    )

    result = evaluate(
        spec_payload,
        report,
        "sha256:" + "1" * 64,
        alignment_judge=semantic_judge({"chain-2": ("match", 1.0)}),
    )

    scored = next(item for item in result["claim_results"] if item["gold_claim_id"] == "chain-2")
    assert scored["keyword_match"] is False
    assert scored["keyword_score"] == "0.00"
    assert scored["evidence_keyword"].startswith("[cpuhp_notifier_handle:108]")
    assert scored["closest_keyword_line"]["line_number"] == 2
    assert "cpuhp: listener devmgr.actv" in scored["closest_keyword_line"]["quote"]
