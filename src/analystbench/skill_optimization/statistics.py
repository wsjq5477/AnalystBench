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


def _median(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return float(statistics.median(materialized)) if materialized else None


def compare_paired(
    observations: list[RunObservation],
    *,
    bootstrap_samples: int = 2000,
    confidence: float = 0.95,
) -> dict[str, Any]:
    arms = {
        arm: [item for item in observations if item.arm == arm]
        for arm in ("baseline", "candidate")
    }
    case_paths = sorted(
        {item.case_path for item in arms["baseline"]}
        & {item.case_path for item in arms["candidate"]}
    )
    pairs: list[dict[str, Any]] = []
    for case_path in case_paths:
        baseline = [
            item
            for item in arms["baseline"]
            if item.case_path == case_path and item.succeeded and item.score is not None
        ]
        candidate = [
            item
            for item in arms["candidate"]
            if item.case_path == case_path and item.succeeded and item.score is not None
        ]
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
                "baseline_failure_tags": sorted(
                    {tag for item in baseline for tag in item.failure_tags}
                ),
                "candidate_failure_tags": sorted(
                    {tag for item in candidate for tag in item.failure_tags}
                ),
            }
        )
    deltas = [float(item["delta"]) for item in pairs]
    overall_delta = float(statistics.mean(deltas)) if deltas else None
    confidence_interval: list[float] | None = None
    win_probability: float | None = None
    if deltas and bootstrap_samples > 0:
        seed_material = "|".join(
            f"{item['case_path']}:{item['delta']}" for item in pairs
        )
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
        "bootstrap_interval": confidence_interval,
        "candidate_win_probability": win_probability,
        "repeat_count": repeat_count,
        "regressed_case_count": sum(float(item["delta"]) < 0 for item in pairs),
        "improved_case_count": sum(float(item["delta"]) > 0 for item in pairs),
        "unchanged_case_count": sum(float(item["delta"]) == 0 for item in pairs),
        "baseline_failure_count": sum(not item.succeeded for item in arms["baseline"]),
        "candidate_failure_count": sum(not item.succeeded for item in arms["candidate"]),
        "dimension_deltas": dimension_deltas,
        "family_deltas": family_deltas,
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
