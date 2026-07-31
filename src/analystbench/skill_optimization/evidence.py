"""Build optimizer-safe failure-family and scoring-dimension evidence."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any


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
    for claim in report.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        dimension = str(claim.get("type") or "other")
        try:
            dimensions[dimension] += float(claim.get("score") or 0)
        except (TypeError, ValueError):
            pass
        relation = str(claim.get("overall_relation") or claim.get("relation") or "")
        if relation in {"missing", "contradiction"}:
            failure_tags.add(f"{dimension}:{relation}")
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
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
    }


def build_evidence_summary(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only supplied visible-run signals; callers exclude hidden splits."""

    family_scores: dict[str, list[float]] = defaultdict(list)
    dimension_scores: dict[str, list[float]] = defaultdict(list)
    failure_tags: Counter[str] = Counter()
    failed_cases: list[dict[str, Any]] = []
    for signal in signals:
        family = str(signal.get("case_family") or "unknown")
        score = signal.get("score")
        if isinstance(score, (int, float)):
            family_scores[family].append(float(score))
        for name, value in (signal.get("dimensions") or {}).items():
            if isinstance(value, (int, float)):
                dimension_scores[str(name)].append(float(value))
        for tag in signal.get("failure_tags") or []:
            failure_tags[str(tag)] += 1
        if not signal.get("succeeded", False) or (isinstance(score, (int, float)) and score < 70):
            failed_cases.append(
                {
                    "case_path": signal.get("case_path"),
                    "case_family": family,
                    "score": score,
                    "failure_tags": list(signal.get("failure_tags") or []),
                }
            )
    return {
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
        "failure_tags": dict(sorted(failure_tags.items())),
        "failed_cases": sorted(
            failed_cases,
            key=lambda item: (
                item["score"] is not None,
                item["score"] if item["score"] is not None else -1,
                str(item["case_path"]),
            ),
        ),
    }
