"""Human-readable evaluation summaries built from full audit results."""

from decimal import Decimal, InvalidOperation
from typing import Any

RELATION_LABELS = {
    "match": "完全命中",
    "partial_match": "部分命中",
    "missing": "未命中",
    "contradiction": "结论矛盾",
}
COMPARISON_LABELS = {
    "improved": "提升",
    "degraded": "下降",
    "unchanged": "基本不变",
}


def _score_number(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("-1")


def _short_quote(value: Any, limit: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def _chain_relation_label(
    conclusion_relation: str,
    keyword_match: bool,
    conclusion_similarity: Any,
) -> tuple[str, str]:
    if conclusion_relation == "contradiction":
        return "contradiction", "结论矛盾"
    similarity = _score_number(conclusion_similarity)
    if keyword_match and similarity >= Decimal("1"):
        return "match", "完全命中"
    if keyword_match or similarity > 0:
        return "partial_match", "部分命中"
    return "missing", "未命中"


def _inline_code(value: Any) -> str:
    return str(value or "").replace("`", "ˋ")


def build_human_summary(
    case_key: str,
    case_payload: dict[str, Any],
    reports: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    case_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eval_spec = case_payload.get("eval_spec_draft", {})
    claims = eval_spec.get("claims", [])
    strategy = eval_spec.get("scoring_strategy", {})
    root_category_chain = strategy.get("mode") == "root_category_chain"
    gold_by_id = {
        claim.get("id"): claim for claim in claims if isinstance(claim, dict) and claim.get("id")
    }
    report_summaries: list[dict[str, Any]] = []
    for report in reports:
        result = report.get("result") or {}
        claim_summaries = []
        for claim_result in result.get("claim_results", []):
            gold = gold_by_id.get(claim_result.get("gold_claim_id"), {})
            relation = claim_result.get("relation", "missing")
            candidate_ref = claim_result.get("candidate_ref") or {}
            relation_label = RELATION_LABELS.get(relation, relation)
            overall_relation = relation
            if (
                root_category_chain
                and gold.get("type") == "root_cause"
                and relation == "partial_match"
            ):
                relation_label = "部分命中（根因不计分）"
            if root_category_chain and gold.get("type") == "analysis_chain":
                overall_relation, relation_label = _chain_relation_label(
                    relation,
                    bool(claim_result.get("keyword_match")),
                    claim_result.get("conclusion_similarity"),
                )
            claim_summaries.append(
                {
                    "id": claim_result.get("gold_claim_id"),
                    "type": gold.get("type"),
                    "statement": gold.get("statement", "未找到评分项说明"),
                    "importance": gold.get("importance"),
                    "weight": gold.get("weight"),
                    "relation": relation,
                    "conclusion_relation_label": RELATION_LABELS.get(relation, relation),
                    "overall_relation": overall_relation,
                    "relation_label": relation_label,
                    "score": claim_result.get("score"),
                    "evidence_keyword": gold.get("evidence_keyword"),
                    "keyword_match": claim_result.get("keyword_match"),
                    "keyword_score": claim_result.get("keyword_score"),
                    "conclusion_similarity": claim_result.get("conclusion_similarity"),
                    "conclusion_score": claim_result.get("conclusion_score"),
                    "closest_keyword_line": claim_result.get("closest_keyword_line"),
                    "candidate_quote": _short_quote(candidate_ref.get("quote")),
                }
            )
        missing_chains = [
            claim
            for claim in claim_summaries
            if claim["type"] == "analysis_chain"
            and claim["overall_relation"] == "missing"
        ]
        hit_count = sum(
            claim["overall_relation"] in {"match", "partial_match"}
            for claim in claim_summaries
        )
        report_summaries.append(
            {
                "candidate_name": report.get("candidate_name"),
                "status": report.get("status"),
                "score": report.get("score"),
                "passed": report.get("passed"),
                "positive_score": result.get("positive_score"),
                "penalties": result.get("penalties"),
                "claim_count": len(claim_summaries),
                "hit_count": hit_count,
                "missing_chains": [claim["statement"] for claim in missing_chains],
                "metrics": result.get("metrics", {}),
                "judge": result.get("judge", {}),
                "claims": claim_summaries,
                "warnings": [warning.get("message", "") for warning in report.get("warnings", [])],
            }
        )
    ranking = sorted(
        report_summaries,
        key=lambda entry: _score_number(entry.get("score")),
        reverse=True,
    )
    return {
        "case_key": case_key,
        "case_source": case_source or {},
        "engine_note": (
            "根因完全命中直接得100分并停止后续评分；否则问题分类正确得20分，"
            "分析链总分60按条数等分，每条日志关键字强匹配与结论语义相似度各占一半。"
            if root_category_chain
            else "当前使用确定性词法基线评分；完整 JSON 保留所有审计细节。"
        ),
        "ranking": [entry["candidate_name"] for entry in ranking],
        "reports": report_summaries,
        "comparisons": [
            {
                "baseline": item.get("baseline"),
                "candidate": item.get("candidate"),
                "delta": item.get("average_delta"),
                "classification": item.get("classification"),
                "classification_label": COMPARISON_LABELS.get(
                    item.get("classification"), item.get("classification")
                ),
            }
            for item in comparisons
        ],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    reports_by_name = {item["candidate_name"]: item for item in summary["reports"]}
    lines = [
        "# AnalystBench 评分报告",
        "",
        f"- Case：`{summary['case_key']}`",
    ]
    source = summary.get("case_source") or {}
    if source.get("mode") == "direct_file":
        lines.append("- 评分模式：本地 Case JSON 直接评分（不使用数据库）")
        if source.get("source_path"):
            lines.append(f"- Case 来源：`{_inline_code(source['source_path'])}`")
    elif source.get("mode") == "database":
        lines.append("- 评分模式：数据库已发布 Case")
        if source.get("case_version") is not None:
            lines.append(f"- Case 版本：{source['case_version']}")
        if source.get("eval_spec_version_id"):
            lines.append(
                f"- Eval Spec：`{_inline_code(source['eval_spec_version_id'])}`"
            )
        if source.get("source_filename"):
            lines.append(f"- 发布源文件：`{_inline_code(source['source_filename'])}`")
    lines.extend(
        [
            f"- 说明：{summary['engine_note']}",
            "",
            "## 总览",
            "",
            "| 排名 | 报告 | 得分 | 结果 | 命中评分项 | 缺失链 | 警告 |",
            "|---:|---|---:|---|---:|---:|---:|",
        ]
    )
    for index, name in enumerate(summary["ranking"], 1):
        report = reports_by_name[name]
        lines.append(
            f"| {index} | {name} | {report['score'] or '-'} | "
            f"{'通过' if report['passed'] else '未通过'} | "
            f"{report['hit_count']}/{report['claim_count']} | "
            f"{len(report['missing_chains'])} | {len(report['warnings'])} |"
        )
    for name in summary["ranking"]:
        report = reports_by_name[name]
        lines.extend(
            [
                "",
                f"## {name}",
                "",
                f"- 总分：**{report['score'] or '-'} / 100**",
                f"- 结果：**{'通过' if report['passed'] else '未通过'}**",
            ]
        )
        metrics = report["metrics"]
        judge = report.get("judge", {})
        if judge:
            judge_label = (
                "大模型语义 Judge" if judge.get("kind") == "semantic_llm" else "词法调试 Judge"
            )
            lines.append(f"- 判定器：{judge_label}（{judge.get('runner', 'unknown')}）")
        if metrics.get("root_cause_exact"):
            lines.append("- 计分路径：根因完全命中，直接100分；未计算分类和分析链。")
        elif summary["engine_note"].startswith("根因完全命中"):
            lines.extend(
                [
                    f"- 分类与分析链得分：{report['positive_score']} / 80",
                    "- 分析链关键字为强匹配；结论由大模型给出 0～1 的语义相似度。",
                ]
            )
        if report["missing_chains"]:
            lines.append("- 缺失分析链：" + "；".join(report["missing_chains"]))
        lines.extend(
            [
                "",
                "| 评分项 | 分值 | 综合判定 | 关键字强匹配 | 结论语义 | 得分 |",
                "|---|---:|---|---|---|---:|",
            ]
        )
        for claim in report["claims"]:
            keyword = "-"
            semantic = "-"
            if claim["type"] == "analysis_chain":
                keyword = (
                    f"{'命中' if claim['keyword_match'] else '未命中'}"
                    f"（{claim['keyword_score']}）"
                )
                semantic = (
                    f"{claim['conclusion_relation_label']} / "
                    f"{claim['conclusion_similarity']:.2f}"
                    f"（{claim['conclusion_score']}）"
                )
            lines.append(
                f"| {claim['id']}：{claim['statement']} | {claim['weight']} | "
                f"{claim['relation_label']} | {keyword} | {semantic} | {claim['score']} |"
            )
        chain_claims = [
            claim for claim in report["claims"] if claim["type"] == "analysis_chain"
        ]
        if chain_claims:
            lines.extend(["", "关键字审计（由 Python 强匹配，不调用大模型）：", ""])
            for claim in chain_claims:
                lines.append(
                    f"- {claim['id']} 要求连续原文："
                    f"`{_inline_code(claim['evidence_keyword'])}`"
                )
                if not claim["keyword_match"]:
                    closest = claim.get("closest_keyword_line") or {}
                    if closest.get("quote"):
                        lines.append(
                            f"  - 最接近的报告第 {closest['line_number']} 行："
                            f"`{_inline_code(closest['quote'])}`"
                        )
        matched = [claim for claim in report["claims"] if claim["candidate_quote"]]
        if matched:
            lines.extend(["", "命中证据：", ""])
            for claim in matched:
                lines.append(f"- {claim['id']}：{claim['candidate_quote']}")
        if report["warnings"]:
            lines.extend(["", "警告：", ""])
            for warning in report["warnings"]:
                lines.append(f"- {warning}")
    if summary["comparisons"]:
        lines.extend(["", "## 报告对比", ""])
        for item in summary["comparisons"]:
            lines.append(
                f"- {item['baseline']} → {item['candidate']}："
                f"{item['delta']} 分，{item['classification_label']}。"
            )
    return "\n".join(lines) + "\n"
