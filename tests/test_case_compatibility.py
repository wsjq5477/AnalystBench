import pytest

from analystbench.case_library import ensure_scoring_spec_supported
from analystbench.errors import AnalystBenchError


def test_obsolete_published_case_reports_actionable_error() -> None:
    with pytest.raises(AnalystBenchError) as captured:
        ensure_scoring_spec_supported(
            "HM_PANIC_SYSMGR-case1",
            {
                "scoring_strategy": {
                    "mode": "root_or_chain",
                    "root_cause_score": 100,
                    "chain_total_score": 80,
                    "chain_partial_factor": 0.5,
                    "hallucination_penalty_cap": 20,
                }
            },
        )

    assert captured.value.code == "case_scoring_strategy_obsolete"
    assert "重新导入并发布" in captured.value.message
