"""校验独立 AI 报告草稿。"""

import json
import sys
from pathlib import Path

CORE_TYPES = {
    "trigger",
    "symptom",
    "localization",
    "root_cause",
    "mechanism",
    "impact",
    "evidence",
    "action",
}
CERTAINTY = {"confirmed", "probable", "suspected", "possible"}


def fail(message: str) -> None:
    raise SystemExit(f"草稿无效：{message}")


def main(path: str) -> None:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(payload) != {"candidate", "candidate_report", "claim_hints", "unresolved_items"}:
        fail("顶层字段不符合 AI 报告草稿结构")
    candidate = payload["candidate"]
    if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str):
        fail("candidate.name 不能为空")
    report = payload["candidate_report"]
    if not isinstance(report, str) or not report.strip():
        fail("candidate_report 不能为空")
    if not isinstance(payload["claim_hints"], list):
        fail("claim_hints 必须是数组")
    for index, claim in enumerate(payload["claim_hints"], 1):
        if claim.get("id") != f"candidate-{index}" or claim.get("quote") not in report:
            fail("提示 Claim ID 或 quote 无效")
        if claim.get("type") not in CORE_TYPES:
            fail(f"{claim.get('id')} 的 type 无效")
        if claim.get("certainty") not in CERTAINTY:
            fail(f"{claim.get('id')} 的 certainty 无效")
    if not isinstance(payload["unresolved_items"], list) or not all(
        isinstance(item, str) for item in payload["unresolved_items"]
    ):
        fail("unresolved_items 必须是字符串数组")
    print("AI 报告草稿有效")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：校验AI报告草稿.py <草稿.json>")
    main(sys.argv[1])
