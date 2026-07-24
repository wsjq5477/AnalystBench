"""Database-free evaluation of one Case JSON against original report files."""

from decimal import Decimal
from typing import Any
from uuid import uuid4

from analystbench.config import Settings
from analystbench.content_store import canonical_json, content_hash
from analystbench.errors import AnalystBenchError
from analystbench.eval_spec import EvalSpecV1
from analystbench.reporting import build_human_summary
from analystbench.scoring import analysis_chain_keyword_audits, evaluate
from analystbench.semantic_judge import SemanticJudge
from analystbench.skill_align_adapter import make_skill_alignment_judge


def _direct_spec(case_payload: dict[str, Any], case_key: str) -> dict[str, Any]:
    case = case_payload.get("case")
    draft = case_payload.get("eval_spec_draft")
    if not isinstance(case, dict) or not isinstance(draft, dict):
        raise AnalystBenchError(
            "direct_case_invalid", "Case JSON 顶层必须包含 case 和 eval_spec_draft。"
        )
    reference = case.get("reference_answer")
    claims = draft.get("claims")
    if not isinstance(reference, str) or not reference.strip():
        raise AnalystBenchError("direct_case_invalid", "case.reference_answer 不能为空。")
    if not isinstance(claims, list) or not claims:
        raise AnalystBenchError("direct_case_invalid", "eval_spec_draft.claims 不能为空。")
    unresolved = draft.get("unresolved_items", [])
    if unresolved:
        raise AnalystBenchError(
            "direct_case_unresolved",
            "Case 仍包含 unresolved_items，请先审核解决后再进行本地评分。",
            [{"unresolved_items": unresolved}],
        )

    reference_hash = content_hash(reference.encode("utf-8"))
    formal_claims = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise AnalystBenchError(
                "direct_case_invalid", f"eval_spec_draft.claims[{index}] 必须是对象。"
            )
        quote = claim.get("quote")
        if not isinstance(quote, str) or quote not in reference:
            raise AnalystBenchError(
                "direct_case_invalid",
                f"eval_spec_draft.claims[{index}].quote 必须是标准答案中的连续原文。",
            )
        start = reference.index(quote)
        formal_claims.append(
            {
                "id": claim.get("id"),
                "type": claim.get("type"),
                "statement": claim.get("statement"),
                "importance": claim.get("importance"),
                "weight": claim.get("weight"),
                "source_ref": {
                    "content_hash": reference_hash,
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                },
                "review_required": False,
                "notes": claim.get("notes"),
                "evidence_keyword": claim.get("evidence_keyword"),
                "conclusion": claim.get("conclusion"),
            }
        )

    test_set = case.get("test_set")
    suite_id = test_set if isinstance(test_set, str) and test_set else "direct-file"
    payload = {
        "schema_version": "1.0",
        "case_revision_id": f"direct:{case_key}",
        "suite": {"id": suite_id, "version": "1.0.0"},
        "claims": formal_claims,
        "causal_edges": [
            {**edge, "review_required": False} for edge in draft.get("causal_edges", [])
        ],
        "forbidden_claims": draft.get("forbidden_claims", []),
        "scoring_policy_version_id": "direct-file",
        "scoring_strategy": draft.get("scoring_strategy", {"mode": "weighted_sum"}),
        "review": {"status": "approved", "unresolved_items": []},
    }
    try:
        spec = EvalSpecV1.model_validate(payload)
    except Exception as exc:
        raise AnalystBenchError(
            "direct_case_invalid", f"Case 评分规范校验失败：{exc}"
        ) from exc
    _validate_weights(spec)
    return payload


def _validate_weights(spec: EvalSpecV1) -> None:
    if spec.scoring_strategy.mode == "root_category_chain":
        roots = [claim for claim in spec.claims if claim.type == "root_cause"]
        categories = [claim for claim in spec.claims if claim.type == "classification"]
        chains = [claim for claim in spec.claims if claim.type == "analysis_chain"]
        if len(roots) != 1 or roots[0].id != "root" or roots[0].weight != 100:
            raise AnalystBenchError("direct_case_invalid", "根因必须是唯一的 root，权重为100。")
        if (
            len(categories) != 1
            or categories[0].id != "category"
            or categories[0].weight != 20
        ):
            raise AnalystBenchError(
                "direct_case_invalid", "问题分类必须是唯一的 category，权重为20。"
            )
        chain_weights = [Decimal(str(claim.weight)) for claim in chains]
        if not chains or abs(sum(chain_weights) - Decimal("60")) > Decimal("0.01"):
            raise AnalystBenchError("direct_case_invalid", "分析链权重必须等分并合计60。")
        if max(chain_weights) - min(chain_weights) > Decimal("0.01"):
            raise AnalystBenchError("direct_case_invalid", "分析链权重必须等分。")
        if any(not claim.evidence_keyword or not claim.conclusion for claim in chains):
            raise AnalystBenchError(
                "direct_case_invalid", "每条分析链都必须包含 evidence_keyword 和 conclusion。"
            )
        return
    total = sum(Decimal(str(claim.weight)) for claim in spec.claims) + sum(
        Decimal(edge.weight) for edge in spec.causal_edges
    )
    if total != Decimal("100"):
        raise AnalystBenchError("direct_case_invalid", "通用评分项与因果边权重必须合计100。")


def _warnings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    report = payload["candidate_report"]
    warnings = []
    for index, hint in enumerate(payload.get("claim_hints") or []):
        quote = hint.get("quote") if isinstance(hint, dict) else None
        if not isinstance(quote, str) or quote not in report:
            warnings.append(
                {
                    "field_path": f"claim_hints[{index}].quote",
                    "code": "candidate_hint_quote_mismatch",
                    "message": "候选提示引用不是报告连续原文；评分仍以完整报告原文为准。",
                    "severity": "warning",
                }
            )
    return warnings


def prepare_alignment_draft(
    case_payload: dict[str, Any],
    case_key: str,
    reports: list[dict[str, Any]],
    source_path: str | None = None,
) -> dict[str, Any]:
    """Build the Python-owned part of a no-splitting Skill alignment document."""
    if not reports:
        raise AnalystBenchError("report_invalid", "至少需要一份 AI 报告。")
    spec_payload = _direct_spec(case_payload, case_key)
    spec = EvalSpecV1.model_validate(spec_payload)
    draft_reports: dict[str, dict[str, Any]] = {}
    for payload in reports:
        candidate = payload.get("candidate")
        report = payload.get("candidate_report")
        if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str):
            raise AnalystBenchError("report_invalid", "报告缺少 candidate.name。")
        if not isinstance(report, str) or not report.strip():
            raise AnalystBenchError("report_invalid", "报告原文不能为空。")
        audits = analysis_chain_keyword_audits(spec, report)
        draft_reports[candidate["name"]] = {
            "report_content_hash": content_hash(report.encode("utf-8")),
            "python_keyword_audits": {
                claim_id: {
                    **audit,
                    "keyword_score": str(audit["keyword_score"]),
                }
                for claim_id, audit in audits.items()
            },
            "semantic_alignment": {
                "alignments": [
                    {
                        "gold_claim_id": claim.id,
                        "relation": None,
                        "confidence": None,
                        "reason": None,
                        "subject_match": None,
                        "predicate_match": None,
                        "causal_direction_match": None,
                        "missing_essential_facts": [],
                        "conclusion_similarity": None,
                    }
                    for claim in spec.claims
                ]
            },
        }
    return {
        "schema_version": "1.0",
        "kind": "analystbench_semantic_alignment_draft",
        "case": {
            "case_key": case_key,
            "case_content_hash": content_hash(canonical_json(case_payload).encode("utf-8")),
            "source_path": source_path,
            "gold_claims": [
                {
                    "id": claim.id,
                    "type": claim.type,
                    "statement": claim.statement,
                    "importance": claim.importance,
                    "evidence_keyword": claim.evidence_keyword,
                    "conclusion": claim.conclusion,
                }
                for claim in spec.claims
            ],
        },
        "reports": draft_reports,
    }


def evaluate_direct(
    case_payload: dict[str, Any],
    case_key: str,
    reports: list[dict[str, Any]],
    settings: Settings,
    judge_runner: str,
    source_path: str | None = None,
) -> dict[str, Any]:
    """Evaluate original report texts entirely in memory and return export-ready results."""
    if not reports:
        raise AnalystBenchError("report_invalid", "至少需要一份 AI 报告。")
    if judge_runner not in {"claude-code", "opencode", "lexical"}:
        raise AnalystBenchError(
            "validation_failed", "judge must be claude-code, opencode, or lexical"
        )
    spec_payload = _direct_spec(case_payload, case_key)
    report_results = []
    for payload in reports:
        candidate = payload.get("candidate")
        report = payload.get("candidate_report")
        if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str):
            raise AnalystBenchError("report_invalid", "报告缺少 candidate.name。")
        if not isinstance(report, str) or not report.strip():
            raise AnalystBenchError("report_invalid", "报告原文不能为空。")
        report_hash = content_hash(report.encode("utf-8"))
        judge = (
            SemanticJudge(settings, judge_runner)
            if judge_runner != "lexical"
            else None
        )
        result = evaluate(
            spec_payload,
            report,
            report_hash,
            payload.get("claim_hints"),
            judge.align if judge else None,
        )
        result["judge"] = (
            judge.audit if judge else {"kind": "lexical_debug", "runner": "lexical"}
        )
        report_results.append(
            {
                "candidate_name": candidate["name"],
                "status": "completed",
                "score": result["total_score"],
                "passed": result["passed"],
                "result": result,
                "warnings": _warnings(payload),
            }
        )

    comparisons = []
    baseline = report_results[0]
    for candidate in report_results[1:]:
        delta = Decimal(candidate["score"]) - Decimal(baseline["score"])
        comparisons.append(
            {
                "baseline": baseline["candidate_name"],
                "candidate": candidate["candidate_name"],
                "average_delta": f"{delta:.2f}",
                "classification": (
                    "improved" if delta >= 5 else "degraded" if delta <= -5 else "unchanged"
                ),
            }
        )
    case_source = {
        "mode": "direct_file",
        "source_path": source_path,
        "case_version": None,
        "eval_spec_version_id": None,
    }
    response = {
        "id": f"direct-{uuid4().hex[:8]}",
        "mode": "direct_file",
        "case_key": case_key,
        "case_source": case_source,
        "status": "completed",
        "reports": report_results,
        "comparisons": comparisons,
        "error": {},
    }
    response["summary"] = build_human_summary(
        case_key,
        case_payload,
        report_results,
        comparisons,
        case_source,
    )
    return response


def evaluate_direct_with_alignment(
    case_payload: dict[str, Any],
    case_key: str,
    reports: list[dict[str, Any]],
    alignment_json: dict[str, Any],
    source_path: str | None = None,
) -> dict[str, Any]:
    """Score a Python-generated alignment draft after Claude fills semantic fields."""
    if not reports:
        raise AnalystBenchError("report_invalid", "至少需要一份 AI 报告。")
    spec_payload = _direct_spec(case_payload, case_key)
    spec = EvalSpecV1.model_validate(spec_payload)
    expected_case_hash = content_hash(canonical_json(case_payload).encode("utf-8"))
    if (
        alignment_json.get("kind") != "analystbench_semantic_alignment_draft"
        or alignment_json.get("schema_version") != "1.0"
    ):
        raise AnalystBenchError(
            "alignment_draft_invalid",
            "对齐 JSON 必须由 prepare-alignment 生成，且保留 schema_version=1.0。",
        )
    if alignment_json.get("case", {}).get("case_content_hash") != expected_case_hash:
        raise AnalystBenchError(
            "alignment_case_mismatch",
            "对齐 JSON 对应的 Case 与当前 Case JSON 不一致，请重新生成评分草稿。",
        )
    draft_reports = alignment_json.get("reports")
    if not isinstance(draft_reports, dict):
        raise AnalystBenchError("alignment_draft_invalid", "对齐 JSON 缺少 reports 对象。")
    report_results = []
    for payload in reports:
        candidate = payload.get("candidate")
        report = payload.get("candidate_report")
        if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str):
            raise AnalystBenchError("report_invalid", "报告缺少 candidate.name。")
        if not isinstance(report, str) or not report.strip():
            raise AnalystBenchError("report_invalid", "报告原文不能为空。")
        report_hash = content_hash(report.encode("utf-8"))
        draft_report = draft_reports.get(candidate["name"])
        if not isinstance(draft_report, dict):
            raise AnalystBenchError(
                "alignment_missing",
                f"对齐 JSON 中缺少报告 '{candidate['name']}' 的对齐结果。",
                {"available_keys": sorted(draft_reports.keys())},
            )
        if draft_report.get("report_content_hash") != report_hash:
            raise AnalystBenchError(
                "alignment_report_mismatch",
                f"报告 '{candidate['name']}' 已变化，请重新生成评分草稿。",
            )
        report_alignment = draft_report.get("semantic_alignment")
        if not isinstance(report_alignment, dict):
            raise AnalystBenchError(
                "alignment_draft_invalid",
                f"报告 '{candidate['name']}' 缺少 semantic_alignment。",
            )
        judge_callable = make_skill_alignment_judge(report_alignment, spec)
        result = evaluate(
            spec_payload,
            report,
            report_hash,
            payload.get("claim_hints"),
            judge_callable,
        )
        result["judge"] = {
            "kind": "skill_semantic",
            "runner": "claude-code",
        }
        report_results.append(
            {
                "candidate_name": candidate["name"],
                "status": "completed",
                "score": result["total_score"],
                "passed": result["passed"],
                "result": result,
                "warnings": _warnings(payload),
            }
        )

    comparisons = []
    baseline = report_results[0]
    for candidate in report_results[1:]:
        delta = Decimal(candidate["score"]) - Decimal(baseline["score"])
        comparisons.append(
            {
                "baseline": baseline["candidate_name"],
                "candidate": candidate["candidate_name"],
                "average_delta": f"{delta:.2f}",
                "classification": (
                    "improved" if delta >= 5 else "degraded" if delta <= -5 else "unchanged"
                ),
            }
        )
    case_source = {
        "mode": "direct_file",
        "source_path": source_path,
        "case_version": None,
        "eval_spec_version_id": None,
    }
    response = {
        "id": f"direct-{uuid4().hex[:8]}",
        "mode": "direct_file",
        "case_key": case_key,
        "case_source": case_source,
        "status": "completed",
        "reports": report_results,
        "comparisons": comparisons,
        "error": {},
    }
    response["summary"] = build_human_summary(
        case_key,
        case_payload,
        report_results,
        comparisons,
        case_source,
    )
    return response
