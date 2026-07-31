"""Promotion gate with explicit hard guardrails and validation levels."""

from __future__ import annotations

import statistics
from typing import Any


def _growth(candidate: list[float], baseline: list[float]) -> float | None:
    if not candidate or not baseline:
        return None
    base = statistics.mean(baseline)
    return None if base <= 0 else statistics.mean(candidate) / base - 1


def evaluate_gate(
    comparison: dict[str, Any],
    *,
    min_overall_delta: float,
    minimum_independent_validation_cases: int,
    max_latency_growth: float,
    max_token_growth: float,
    mode: str,
    current_repeats: int | None = None,
    max_repeats: int = 7,
    critical_dimension_min_delta: float = 0.0,
    critical_family_max_regression: float = -2.0,
) -> dict[str, Any]:
    hard_reasons: list[dict[str, Any]] = []
    quality_reasons: list[dict[str, Any]] = []
    overall_delta = comparison.get("overall_delta")
    pairs = comparison.get("pairs", [])
    if not isinstance(pairs, list) or not pairs:
        hard_reasons.append({"code": "no_paired_results"})
    if overall_delta is None:
        hard_reasons.append({"code": "overall_delta_missing"})
    elif float(overall_delta) < min_overall_delta:
        quality_reasons.append(
            {
                "code": "minimum_delta_not_met",
                "observed": overall_delta,
                "required": min_overall_delta,
            }
        )
    if int(comparison.get("candidate_failure_count", 0)) > int(
        comparison.get("baseline_failure_count", 0)
    ):
        hard_reasons.append({"code": "candidate_failures_increased"})
    for name, value in (comparison.get("dimension_deltas") or {}).items():
        if name in {"root_cause", "classification"} and float(value) < critical_dimension_min_delta:
            hard_reasons.append(
                {
                    "code": "critical_dimension_regressed",
                    "dimension": name,
                    "observed": value,
                    "minimum": critical_dimension_min_delta,
                }
            )
    for name, value in (comparison.get("family_deltas") or {}).items():
        if float(value) < critical_family_max_regression:
            hard_reasons.append(
                {
                    "code": "failure_family_regressed",
                    "family": name,
                    "observed": value,
                    "minimum": critical_family_max_regression,
                }
            )
    baseline_duration = [
        float(item["baseline_duration_ms"])
        for item in pairs
        if item.get("baseline_duration_ms") is not None
    ]
    candidate_duration = [
        float(item["candidate_duration_ms"])
        for item in pairs
        if item.get("candidate_duration_ms") is not None
    ]
    latency_growth = _growth(candidate_duration, baseline_duration)
    if latency_growth is not None and latency_growth > max_latency_growth:
        hard_reasons.append(
            {
                "code": "latency_growth_exceeded",
                "observed": latency_growth,
                "maximum": max_latency_growth,
            }
        )
    baseline_tokens = [
        float(item["baseline_tokens"])
        for item in pairs
        if item.get("baseline_tokens") is not None
    ]
    candidate_tokens = [
        float(item["candidate_tokens"])
        for item in pairs
        if item.get("candidate_tokens") is not None
    ]
    token_growth = _growth(candidate_tokens, baseline_tokens)
    if token_growth is not None and token_growth > max_token_growth:
        hard_reasons.append(
            {
                "code": "token_growth_exceeded",
                "observed": token_growth,
                "maximum": max_token_growth,
            }
        )
    repeats = int(current_repeats or comparison.get("repeat_count") or 0)
    interval = comparison.get("bootstrap_interval")
    win_probability = comparison.get("candidate_win_probability")
    statistical_gray = (
        mode == "independent_validation"
        and isinstance(interval, list)
        and len(interval) == 2
        and float(interval[0]) <= 0
        and win_probability is not None
        and float(win_probability) > 0.55
    )
    delta_gray = (
        overall_delta is not None
        and 0 <= float(overall_delta) < min_overall_delta
    )
    needs_more = not hard_reasons and (delta_gray or statistical_gray)
    if hard_reasons:
        verdict = "reject"
        active_level = None
        reasons = hard_reasons + quality_reasons
    elif needs_more and repeats < max_repeats:
        verdict = "needs_more_runs"
        active_level = None
        reasons = quality_reasons + [
            {
                "code": "gray_zone",
                "current_repeats": repeats,
                "next_repeats": min(max_repeats, 5 if repeats < 5 else 7),
            }
        ]
    elif needs_more:
        verdict = "reject"
        active_level = None
        reasons = quality_reasons + [
            {
                "code": "inconclusive_after_max_repeats",
                "current_repeats": repeats,
                "maximum": max_repeats,
            }
        ]
    elif quality_reasons:
        verdict = "reject"
        active_level = None
        reasons = quality_reasons
    elif mode == "independent_validation" and len(pairs) >= minimum_independent_validation_cases:
        if isinstance(interval, list) and len(interval) == 2 and float(interval[0]) > 0:
            verdict = "pass"
            active_level = "validated"
            reasons = []
        elif repeats < max_repeats:
            verdict = "needs_more_runs"
            active_level = None
            reasons = [
                {
                    "code": "independent_validation_not_confident",
                    "current_repeats": repeats,
                    "next_repeats": min(max_repeats, 5 if repeats < 5 else 7),
                }
            ]
        else:
            verdict = "reject"
            active_level = None
            reasons = [
                {
                    "code": "inconclusive_after_max_repeats",
                    "current_repeats": repeats,
                    "maximum": max_repeats,
                }
            ]
    else:
        verdict = "pass"
        active_level = "provisional"
        reasons = []
    return {
        "verdict": verdict,
        "active_level": active_level,
        "mode": mode,
        "reasons": reasons,
        "metrics": {
            "overall_delta": overall_delta,
            "case_count": len(pairs),
            "latency_growth": latency_growth,
            "token_growth": token_growth,
            "repeat_count": repeats,
            "candidate_win_probability": win_probability,
            "bootstrap_interval": interval,
            "dimension_deltas": comparison.get("dimension_deltas", {}),
            "family_deltas": comparison.get("family_deltas", {}),
        },
    }


def evaluate_screening(
    comparison: dict[str, Any],
    *,
    minimum_delta: float = -1.0,
    max_latency_growth: float = 0.50,
    critical_dimension_min_delta: float = -5.0,
) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []
    overall_delta = comparison.get("overall_delta")
    if overall_delta is None:
        reasons.append({"code": "screening_results_missing"})
    elif float(overall_delta) < minimum_delta:
        reasons.append(
            {
                "code": "screening_delta_below_minimum",
                "observed": overall_delta,
                "minimum": minimum_delta,
            }
        )
    if int(comparison.get("candidate_failure_count", 0)) > int(
        comparison.get("baseline_failure_count", 0)
    ):
        reasons.append({"code": "candidate_failures_increased"})
    for name, value in (comparison.get("dimension_deltas") or {}).items():
        if name in {"root_cause", "classification"} and float(value) < critical_dimension_min_delta:
            reasons.append(
                {
                    "code": "critical_dimension_screening_regression",
                    "dimension": name,
                    "observed": value,
                    "minimum": critical_dimension_min_delta,
                }
            )
    pairs = comparison.get("pairs") or []
    baseline_duration = [
        float(item["baseline_duration_ms"])
        for item in pairs
        if item.get("baseline_duration_ms") is not None
    ]
    candidate_duration = [
        float(item["candidate_duration_ms"])
        for item in pairs
        if item.get("candidate_duration_ms") is not None
    ]
    latency_growth = _growth(candidate_duration, baseline_duration)
    if latency_growth is not None and latency_growth > max_latency_growth:
        reasons.append(
            {
                "code": "screening_latency_growth_exceeded",
                "observed": latency_growth,
                "maximum": max_latency_growth,
            }
        )
    return {
        "verdict": "reject" if reasons else "pass",
        "reasons": reasons,
        "metrics": {
            "overall_delta": overall_delta,
            "latency_growth": latency_growth,
            "dimension_deltas": comparison.get("dimension_deltas", {}),
            "family_deltas": comparison.get("family_deltas", {}),
        },
    }
