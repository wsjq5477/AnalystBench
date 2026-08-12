from decimal import Decimal

from analystbench.evaluation.comparison import ComparisonService


class FakeBenchmarks:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads

    def export_run(self, run_id: str) -> dict:
        return self.payloads[run_id]


def run(score: str, dataset_hash: str = "dataset") -> dict:
    return {
        "manifest": {
            "dataset_version_hash": dataset_hash,
            "scoring_policy_hash": "policy",
            "cases": [{"case_revision_id": "case", "eval_spec_hash": "spec"}],
        },
        "case_runs": [
            {
                "case_revision_id": "case",
                "status": "succeeded",
                "result": {"total_score": score, "passed": Decimal(score) >= 70},
            }
        ],
    }


def test_comparison_requires_identical_evaluation_manifest_for_direct_mode() -> None:
    service = ComparisonService(FakeBenchmarks({"a": run("60.00"), "b": run("80.00")}))
    result = service.compare("a", "b")
    assert result["mode"] == "direct"
    assert result["aggregate"]["average_delta"] == "20.00"
    assert result["cases"][0]["classification"] == "improved"

    uncontrolled = ComparisonService(
        FakeBenchmarks({"a": run("60.00"), "b": run("80.00", "other")})
    ).compare("a", "b")
    assert uncontrolled["mode"] == "uncontrolled"
    assert uncontrolled["warnings"]
