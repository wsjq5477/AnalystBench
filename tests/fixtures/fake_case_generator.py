import json

reference = "系统因 suspend-to-mem 超时触发 panic。根因是未执行 REPICK，线程可能跑错 CPU 核。"
payload = {
    "case": {
        "case_key": "1",
        "problem_statement": "分析 suspend 超时的根因。",
        "reference_answer": reference,
    },
    "eval_spec_draft": {
        "claims": [
            {
                "id": "root",
                "type": "root_cause",
                "statement": "未执行 REPICK 导致线程可能跑错 CPU 核",
                "importance": "critical",
                "weight": 100,
                "quote": "未执行 REPICK，线程可能跑错 CPU 核",
                "review_required": True,
            }
        ],
        "causal_edges": [],
        "forbidden_claims": [],
        "unresolved_items": [],
    },
}
print(json.dumps({"result": json.dumps(payload)}))
