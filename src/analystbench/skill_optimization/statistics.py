"""Deterministic paired comparison for repeated benchmark runs."""

from __future__ import annotations

import hashlib
import random
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RunObservation:
    case_path: str
    arm: str
    repeat_index: int
    score: float | None
    duration_ms: int | None = None
    token_count: int | None = None
    succeeded: bool = True
    case_family: str | None = None
    dimensions: dict[str, float] | None = None
    failure_tags: tuple[str, ...] = ()
    guardrail_metrics: dict[str, float] | None = None


def _median(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return float(statistics.median(materialized)) if materialized else None


def compare_paired(
    observations: list[RunObservation],
    *,
    bootstrap_samples: int = 2000,
    confidence: float = 0.95,
    bootstrap_seed: str | int | None = None,
) -> dict[str, Any]:
    arms = {
        arm: [item for item in observations if item.arm == arm]
        for arm in ("baseline", "candidate")
    }
    case_paths = sorted(
        {item.case_path for item in arms["baseline"]}
        | {item.case_path for item in arms["candidate"]}
    )
    arm_repeat_counts = {
        arm: len({item.repeat_index for item in items})
        for arm, items in arms.items()
    }
    pairs: list[dict[str, Any]] = []
    case_outcomes: list[dict[str, Any]] = []
    for case_path in case_paths:
        baseline_all = [
            item for item in arms["baseline"] if item.case_path == case_path
        ]
        candidate_all = [
            item for item in arms["candidate"] if item.case_path == case_path
        ]
        baseline = [
            item
            for item in baseline_all
            if item.succeeded and item.score is not None
        ]
        candidate = [
            item
            for item in candidate_all
            if item.succeeded and item.score is not None
        ]
        baseline_missing = max(
            0, arm_repeat_counts["baseline"] - len(baseline_all)
        )
        candidate_missing = max(
            0, arm_repeat_counts["candidate"] - len(candidate_all)
        )
        baseline_failures = baseline_missing + sum(
            not item.succeeded or item.score is None for item in baseline_all
        )
        candidate_failures = candidate_missing + sum(
            not item.succeeded or item.score is None for item in candidate_all
        )
        baseline_tags = sorted(
            {tag for item in baseline_all for tag in item.failure_tags}
        )
        candidate_tags = sorted(
            {tag for item in candidate_all for tag in item.failure_tags}
        )
        case_outcomes.append(
            {
                "case_path": case_path,
                "baseline_failure_count": baseline_failures,
                "candidate_failure_count": candidate_failures,
                "baseline_success_count": len(baseline),
                "candidate_success_count": len(candidate),
                "baseline_missing_observation_count": baseline_missing,
                "candidate_missing_observation_count": candidate_missing,
                "baseline_failure_tags": baseline_tags,
                "candidate_failure_tags": candidate_tags,
                "new_failure_tags": sorted(set(candidate_tags) - set(baseline_tags)),
                "baseline_guardrail_metrics": _median_metrics(
                    baseline, "guardrail_metrics"
                ),
                "candidate_guardrail_metrics": _median_metrics(
                    candidate, "guardrail_metrics"
                ),
            }
        )
        baseline_guardrails = case_outcomes[-1]["baseline_guardrail_metrics"]
        candidate_guardrails = case_outcomes[-1]["candidate_guardrail_metrics"]
        case_outcomes[-1]["guardrail_metric_increases"] = {
            name: candidate_guardrails[name] - baseline_guardrails[name]
            for name in sorted(set(baseline_guardrails) & set(candidate_guardrails))
            if candidate_guardrails[name] > baseline_guardrails[name]
        }
        baseline_score = _median(float(item.score) for item in baseline)
        candidate_score = _median(float(item.score) for item in candidate)
        if baseline_score is None or candidate_score is None:
            continue
        pairs.append(
            {
                "case_path": case_path,
                "case_family": next(
                    (
                        item.case_family
                        for item in baseline + candidate
                        if item.case_family
                    ),
                    case_path.split("/")[-2] if "/" in case_path else "unknown",
                ),
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
                "delta": candidate_score - baseline_score,
                "baseline_duration_ms": _median(
                    float(item.duration_ms)
                    for item in baseline
                    if item.duration_ms is not None
                ),
                "candidate_duration_ms": _median(
                    float(item.duration_ms)
                    for item in candidate
                    if item.duration_ms is not None
                ),
                "baseline_tokens": _median(
                    float(item.token_count)
                    for item in baseline
                    if item.token_count is not None
                ),
                "candidate_tokens": _median(
                    float(item.token_count)
                    for item in candidate
                    if item.token_count is not None
                ),
                "dimension_deltas": _dimension_deltas(baseline, candidate),
                "guardrail_metric_deltas": _metric_deltas(
                    baseline, candidate, "guardrail_metrics"
                ),
                "baseline_failure_tags": baseline_tags,
                "candidate_failure_tags": candidate_tags,
            }
        )
    deltas = [float(item["delta"]) for item in pairs]
    overall_delta = float(statistics.mean(deltas)) if deltas else None
    confidence_interval: list[float] | None = None
    win_probability: float | None = None
    seed: int | None = None
    if deltas and bootstrap_samples > 0:
        seed_material = "|".join(
            f"{item['case_path']}:{item['delta']}" for item in pairs
        )
        if bootstrap_seed is not None:
            seed_material = f"{bootstrap_seed}|{seed_material}"
        seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
        generator = random.Random(seed)
        means = sorted(
            statistics.mean(generator.choice(deltas) for _ in deltas)
            for _ in range(bootstrap_samples)
        )
        tail = (1 - confidence) / 2
        low_index = max(0, int(tail * (len(means) - 1)))
        high_index = min(len(means) - 1, int((1 - tail) * (len(means) - 1)))
        confidence_interval = [float(means[low_index]), float(means[high_index])]
        win_probability = sum(value > 0 for value in means) / len(means)
    dimension_names = sorted(
        {
            name
            for pair in pairs
            for name in pair.get("dimension_deltas", {})
        }
    )
    dimension_deltas = {
        name: float(
            statistics.mean(
                float(pair["dimension_deltas"][name])
                for pair in pairs
                if name in pair.get("dimension_deltas", {})
            )
        )
        for name in dimension_names
    }
    family_names = sorted(
        {
            str(pair["case_family"])
            for pair in pairs
            if pair.get("case_family")
        }
    )
    family_deltas = {
        name: float(
            statistics.mean(
                float(pair["delta"])
                for pair in pairs
                if pair.get("case_family") == name
            )
        )
        for name in family_names
    }
    guardrail_names = sorted(
        {
            name
            for pair in pairs
            for name in pair.get("guardrail_metric_deltas", {})
        }
    )
    guardrail_metric_deltas = {
        name: float(
            statistics.mean(
                float(pair["guardrail_metric_deltas"][name])
                for pair in pairs
                if name in pair.get("guardrail_metric_deltas", {})
            )
        )
        for name in guardrail_names
    }
    repeat_count = min(
        (
            len(
                {
                    item.repeat_index
                    for item in observations
                    if item.arm == arm
                }
            )
            for arm in ("baseline", "candidate")
        ),
        default=0,
    )
    return {
        "case_count": len(pairs),
        "overall_delta": overall_delta,
        "bootstrap_confidence": confidence,
        "bootstrap_seed": seed,
        "bootstrap_interval": confidence_interval,
        "candidate_win_probability": win_probability,
        "repeat_count": repeat_count,
        "regressed_case_count": sum(float(item["delta"]) < 0 for item in pairs),
        "improved_case_count": sum(float(item["delta"]) > 0 for item in pairs),
        "unchanged_case_count": sum(float(item["delta"]) == 0 for item in pairs),
        "baseline_failure_count": sum(
            int(item["baseline_failure_count"]) for item in case_outcomes
        ),
        "candidate_failure_count": sum(
            int(item["candidate_failure_count"]) for item in case_outcomes
        ),
        "case_outcomes": case_outcomes,
        "dimension_deltas": dimension_deltas,
        "family_deltas": family_deltas,
        "guardrail_metric_deltas": guardrail_metric_deltas,
        "pairs": pairs,
    }


def _dimension_deltas(
    baseline: list[RunObservation],
    candidate: list[RunObservation],
) -> dict[str, float]:
    names = sorted(
        {
            name
            for item in baseline + candidate
            for name in (item.dimensions or {})
        }
    )
    output: dict[str, float] = {}
    for name in names:
        baseline_value = _median(
            float(item.dimensions[name])
            for item in baseline
            if item.dimensions and name in item.dimensions
        )
        candidate_value = _median(
            float(item.dimensions[name])
            for item in candidate
            if item.dimensions and name in item.dimensions
        )
        if baseline_value is not None and candidate_value is not None:
            output[name] = candidate_value - baseline_value
    return output


def _median_metrics(
    observations: list[RunObservation], field: str
) -> dict[str, float]:
    names = sorted(
        {
            name
            for item in observations
            for name in (getattr(item, field) or {})
        }
    )
    return {
        name: value
        for name in names
        if (
            value := _median(
                float((getattr(item, field) or {})[name])
                for item in observations
                if name in (getattr(item, field) or {})
            )
        )
        is not None
    }


def _metric_deltas(
    baseline: list[RunObservation],
    candidate: list[RunObservation],
    field: str,
) -> dict[str, float]:
    baseline_values = _median_metrics(baseline, field)
    candidate_values = _median_metrics(candidate, field)
    return {
        name: candidate_values[name] - baseline_values[name]
        for name in sorted(set(baseline_values) & set(candidate_values))
    }
