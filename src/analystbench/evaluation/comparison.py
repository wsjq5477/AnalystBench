"""Deterministic A/B comparison over frozen Benchmark Run results."""

from decimal import Decimal
from typing import Any

from analystbench.evaluation.benchmark import BenchmarkService


class ComparisonService:
    def __init__(self, benchmarks: BenchmarkService) -> None:
        self.benchmarks = benchmarks

    def compare(self, baseline_run_id: str, candidate_run_id: str) -> dict[str, Any]:
        baseline = self.benchmarks.export_run(baseline_run_id)
        candidate = self.benchmarks.export_run(candidate_run_id)
        left_manifest, right_manifest = baseline["manifest"], candidate["manifest"]
        direct = (
            left_manifest["dataset_version_hash"] == right_manifest["dataset_version_hash"]
            and left_manifest["scoring_policy_hash"] == right_manifest["scoring_policy_hash"]
            and {
                item["case_revision_id"]: item["eval_spec_hash"] for item in left_manifest["cases"]
            }
            == {
                item["case_revision_id"]: item["eval_spec_hash"] for item in right_manifest["cases"]
            }
        )
        left = {
            item["case_revision_id"]: item["result"]
            for item in baseline["case_runs"]
            if item["result"]
        }
        right = {
            item["case_revision_id"]: item["result"]
            for item in candidate["case_runs"]
            if item["result"]
        }
        shared = sorted(left.keys() & right.keys())
        cases = []
        for revision_id in shared:
            before, after = left[revision_id], right[revision_id]
            delta = Decimal(after["total_score"]) - Decimal(before["total_score"])
            cases.append(
                {
                    "case_revision_id": revision_id,
                    "baseline_score": before["total_score"],
                    "candidate_score": after["total_score"],
                    "delta": f"{delta:.2f}",
                    "classification": "improved"
                    if delta >= 5
                    else "degraded"
                    if delta <= -5
                    else "unchanged",
                    "pass_changed": before["passed"] != after["passed"],
                }
            )

        def average(values: list[Decimal]) -> Decimal | None:
            return sum(values, Decimal("0")) / len(values) if values else None

        before_scores = [Decimal(left[item]["total_score"]) for item in shared]
        after_scores = [Decimal(right[item]["total_score"]) for item in shared]
        return {
            "mode": "direct" if direct else "uncontrolled",
            "warnings": []
            if direct
            else ["Runs use different dataset, scoring policy, or Eval Spec manifests."],
            "intersection_case_revision_ids": shared,
            "baseline_only_case_revision_ids": sorted(left.keys() - right.keys()),
            "candidate_only_case_revision_ids": sorted(right.keys() - left.keys()),
            "aggregate": {
                "baseline_average_score": f"{average(before_scores):.2f}"
                if before_scores
                else None,
                "candidate_average_score": f"{average(after_scores):.2f}" if after_scores else None,
                "average_delta": f"{average(after_scores) - average(before_scores):.2f}"
                if shared
                else None,
                "baseline_pass_rate": float(
                    sum(left[item]["passed"] for item in shared) / len(shared)
                )
                if shared
                else None,
                "candidate_pass_rate": float(
                    sum(right[item]["passed"] for item in shared) / len(shared)
                )
                if shared
                else None,
            },
            "cases": cases,
        }
