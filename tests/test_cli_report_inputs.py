import json
from pathlib import Path

from analystbench.cli import _read_report_input


def test_original_report_file_is_wrapped_internally(tmp_path: Path) -> None:
    path = tmp_path / "HM_PANIC_SYSMGR-test2-agent-3.md"
    path.write_text("完整 AI 报告原文", encoding="utf-8")

    payload = _read_report_input(path)

    assert payload["candidate_report"] == "完整 AI 报告原文"
    assert payload["candidate"]["name"] == "HM_PANIC_SYSMGR-test2-agent-3"
    assert payload["candidate"]["metadata"] == {
        "source_filename": "HM_PANIC_SYSMGR-test2-agent-3.md",
        "case_key_hint": "HM_PANIC_SYSMGR",
        "test_index": 2,
        "run_type": "agent",
        "attempt": 3,
    }
    assert payload["claim_hints"] == []


def test_optional_report_json_wrapper_remains_supported(tmp_path: Path) -> None:
    path = tmp_path / "wrapped-report.json"
    path.write_text(
        json.dumps(
            {
                "candidate": {"name": "ignored", "metadata": {"model": "claude"}},
                "candidate_report": "完整报告",
                "claim_hints": [],
                "unresolved_items": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = _read_report_input(path)

    assert payload["candidate"]["name"] == "wrapped-report"
    assert payload["candidate"]["metadata"] == {
        "model": "claude",
        "source_filename": "wrapped-report.json",
    }
    assert payload["candidate_report"] == "完整报告"
