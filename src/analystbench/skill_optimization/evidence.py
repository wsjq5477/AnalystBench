"""Build optimizer-safe failure-family and scoring-dimension evidence."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from typing import Any

MAX_LABEL_LENGTH = 128
SAFE_NUMERIC_METRICS = frozenset(
    {
        "candidate_claim_count",
        "causal_chain_score",
        "claim_coverage",
        "contradiction_count",
        "core_conclusion_score",
        "exact_claim_coverage",
        "forbidden_hit_count",
        "missing_chain_count",
    }
)
SAFE_CLAIM_DIMENSIONS = frozenset(
    {
        "action",
        "analysis_chain",
        "classification",
        "evidence",
        "impact",
        "localization",
        "mechanism",
        "root_cause",
        "symptom",
        "trigger",
    }
)
SAFE_RELATIONS = frozenset({"contradiction", "match", "missing", "partial_match"})


def _label(value: object, *, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip()[:MAX_LABEL_LENGTH]
    return text or fallback


def _claim_dimension(value: object) -> str:
    dimension = _label(value, fallback="other")
    return dimension if dimension in SAFE_CLAIM_DIMENSIONS else "other"


def _claim_relation(value: object) -> str:
    relation = _label(value, fallback="unknown")
    return relation if relation in SAFE_RELATIONS else "unknown"


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded_numeric_metrics(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    metrics: dict[str, float] = {}
    for raw_name in sorted(set(value).intersection(SAFE_NUMERIC_METRICS)):
        number = _finite_number(value[raw_name])
        if number is not None:
            metrics[_label(raw_name)] = number
    return metrics


def extract_report_evidence(
    result: dict[str, Any],
    candidate_name: str,
) -> dict[str, Any]:
    reports = (result.get("summary") or {}).get("reports") or []
    report = next(
        (
            item
            for item in reports
            if isinstance(item, dict) and item.get("candidate_name") == candidate_name
        ),
        {},
    )
    dimensions: dict[str, float] = defaultdict(float)
    failure_tags: set[str] = set()
    claim_findings: list[dict[str, Any]] = []
    success_patterns: set[str] = set()
    claims = report.get("claims") if isinstance(report.get("claims"), list) else []
    for claim_index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        dimension = _claim_dimension(claim.get("type"))
        try:
            dimensions[dimension] += float(claim.get("score") or 0)
        except (TypeError, ValueError):
            pass
        relation = _claim_relation(
            claim.get("overall_relation") or claim.get("relation"),
        )
        if relation in {"missing", "contradiction"}:
            failure_tags.add(f"{dimension}:{relation}")
        elif relation in {"match", "partial_match"}:
            success_patterns.add(f"{dimension}:{relation}")
        finding: dict[str, Any] = {
            "claim_index": claim_index,
            "dimension": dimension,
            "relation": relation,
        }
        for source_key, target_key in (
            ("score", "score"),
            ("weight", "weight"),
            ("conclusion_similarity", "conclusion_similarity"),
        ):
            number = _finite_number(claim.get(source_key))
            if number is not None:
                finding[target_key] = number
        if isinstance(claim.get("keyword_match"), bool):
            finding["keyword_match"] = claim["keyword_match"]
            success_patterns.add(
                f"{dimension}:keyword_"
                f"{'match' if claim['keyword_match'] else 'missing'}"
            )
        claim_findings.append(finding)
    raw_metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    metrics = _bounded_numeric_metrics(raw_metrics)
    # In root-category-chain scoring, an exact root cause deliberately short
    # circuits the lower-value chain path. Treating the unvisited chain count
    # as a new optimizer failure would reject a genuine 100-point improvement.
    if raw_metrics.get("root_cause_exact") is True:
        metrics["missing_chain_count"] = 0.0
        success_patterns.add("root_cause:exact")
    if int(metrics.get("forbidden_hit_count") or 0) > 0:
        failure_tags.add("unsupported_claim")
    if int(metrics.get("missing_chain_count") or 0) > 0:
        failure_tags.add("analysis_chain:missing")
    try:
        score = float(report["score"])
    except (KeyError, TypeError, ValueError):
        score = None
    return {
        "score": score,
        "dimensions": dict(sorted(dimensions.items())),
        "failure_tags": sorted(failure_tags),
        "metrics": metrics,
        "claim_findings": claim_findings,
        "success_patterns": sorted(success_patterns),
    }


def build_evidence_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only supplied visible-run signals; callers exclude hidden splits."""

    family_scores: dict[str, list[float]] = defaultdict(list)
    dimension_scores: dict[str, list[float]] = defaultdict(list)
    failure_tags: Counter[str] = Counter()
    success_patterns: Counter[str] = Counter()
    metric_values: dict[str, list[float]] = defaultdict(list)
    claim_findings: list[dict[str, Any]] = []
    failed_cases: list[dict[str, Any]] = []
    for signal in signals:
        family = _label(signal.get("case_family"))
        score = signal.get("score")
        if isinstance(score, (int, float)):
            family_scores[family].append(float(score))
        for name, value in (signal.get("dimensions") or {}).items():
            if isinstance(value, (int, float)):
                dimension_scores[_label(name)].append(float(value))
        for tag in signal.get("failure_tags") or []:
            failure_tags[_label(tag)] += 1
        for pattern in signal.get("success_patterns") or []:
            success_patterns[_label(pattern)] += 1
        for name, value in _bounded_numeric_metrics(signal.get("metrics")).items():
            metric_values[name].append(value)
        for raw_finding in signal.get("claim_findings") or []:
            if not isinstance(raw_finding, dict):
                continue
            # Deliberately copy only structural scoring fields. In
            # particular, statement/quote/evidence text from the Case or
            # candidate report can never enter optimizer evidence.
            finding: dict[str, Any] = {
                "case_path": str(signal.get("case_path") or "")[:1024],
                "case_family": family,
                "claim_index": int(raw_finding.get("claim_index") or 0),
                "dimension": _claim_dimension(raw_finding.get("dimension")),
                "relation": _claim_relation(raw_finding.get("relation")),
            }
            for key in ("score", "weight", "conclusion_similarity"):
                number = _finite_number(raw_finding.get(key))
                if number is not None:
                    finding[key] = number
            if isinstance(raw_finding.get("keyword_match"), bool):
                finding["keyword_match"] = raw_finding["keyword_match"]
            claim_findings.append(finding)
        if (
            not signal.get("succeeded", False)
            or bool(signal.get("failure_tags"))
            or (isinstance(score, (int, float)) and score < 100)
        ):
            failed_cases.append(
                {
                    "case_path": signal.get("case_path"),
                    "case_family": family,
                    "score": score,
                    "failure_tags": list(signal.get("failure_tags") or []),
                }
            )
    return {
        "evidence_scope": "train_only",
        "schema_version": "optimizer_evidence.v1",
        "case_count": len({item.get("case_path") for item in signals}),
        "failure_families": {
            name: {
                "sample_count": len(values),
                "median_score": float(statistics.median(values)) if values else None,
            }
            for name, values in sorted(family_scores.items())
        },
        "dimensions": {
            name: {
                "sample_count": len(values),
                "median_score": float(statistics.median(values)) if values else None,
            }
            for name, values in sorted(dimension_scores.items())
        },
        "failure_tags": dict(
            sorted(failure_tags.items(), key=lambda item: (-item[1], item[0]))
        ),
        "success_patterns": dict(
            sorted(success_patterns.items(), key=lambda item: (-item[1], item[0]))
        ),
        "metrics": {
            name: {
                "sample_count": len(values),
                "median": float(statistics.median(values)) if values else None,
            }
            for name, values in sorted(metric_values.items())
        },
        "claim_findings": claim_findings,
        "failed_cases": sorted(
            failed_cases,
            key=lambda item: (
                item["score"] is not None,
                item["score"] if item["score"] is not None else -1,
                str(item["case_path"]),
            ),
        ),
        "truncation": {
            "applied": False,
            "message": "Optimizer evidence is complete; no claims or cases were dropped.",
        },
    }
