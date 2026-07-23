"""Direct-file evaluation result APIs — reads local JSON files from data/results/."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, status

from analystbench.config import Settings
from analystbench.errors import AnalystBenchError

router = APIRouter(tags=["direct-results"])


def results_dir(request: Request) -> Path:
    """Resolve the results directory from the app's configured data path."""
    settings: Settings = request.app.state.settings
    data_dir = settings.content_store_path.parent
    return data_dir / "results"


def _find_result_files(dir_path: Path, clean_id: str) -> list[Path]:
    """Find all files (json + md) belonging to a result by its short id."""
    matched: list[Path] = []
    for json_file in dir_path.glob("*.json"):
        file_stem = json_file.stem
        parts = file_stem.split("-", 1)
        if len(parts) == 2 and parts[1] == clean_id:
            matched.append(json_file)
            md_file = dir_path / f"{file_stem}.md"
            if md_file.is_file():
                matched.append(md_file)
            return matched
    # Also try direct filename
    for suffix in (".json", ".md"):
        direct_file = dir_path / f"{clean_id}{suffix}"
        if direct_file.is_file():
            matched.append(direct_file)
    return matched


def _migrate_report_fields(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy field names so the frontend always sees the new schema."""
    if "missing_critical" in report and "missing_chains" not in report:
        claims = report.get("claims", [])
        report["missing_chains"] = [
            c["statement"]
            for c in claims
            if c.get("type") == "analysis_chain" and c.get("overall_relation") == "missing"
        ]
    metrics = report.get("metrics")
    if isinstance(metrics, dict) and "missing_critical_count" in metrics and "missing_chain_count" not in metrics:
        claims = report.get("claims", [])
        metrics["missing_chain_count"] = sum(
            1 for c in claims if c.get("type") == "analysis_chain" and c.get("overall_relation") == "missing"
        )
    return report


class DirectResultListItem:
    """Lightweight summary extracted from a full result JSON."""

    __slots__ = ("id", "case_key", "status", "reports")

    def __init__(self, data: dict[str, Any]) -> None:
        self.id = data.get("id", "")
        self.case_key = data.get("case_key", "")
        self.status = data.get("status", "")
        self.reports = [
            {
                "candidate_name": r.get("candidate_name", ""),
                "score": r.get("score", ""),
                "passed": r.get("passed", False),
            }
            for r in data.get("summary", data).get("reports", data.get("reports", []))
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "case_key": self.case_key,
            "status": self.status,
            "reports": self.reports,
        }


@router.get("/direct-results")
def list_direct_results(request: Request) -> list[dict[str, Any]]:
    """List all direct_file evaluation results in data/results/."""
    dir_path = results_dir(request)
    if not dir_path.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for json_file in sorted(dir_path.glob("*.json"), key=lambda f: f.name):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("mode") != "direct_file":
            continue
        items.append(DirectResultListItem(data).to_dict())
    return items


@router.get("/direct-results/{result_id}")
def get_direct_result(result_id: str, request: Request) -> dict[str, Any]:
    """Return the full evaluation result JSON for a given result_id."""
    dir_path = results_dir(request)
    clean_id = result_id.removeprefix("direct-")
    files = _find_result_files(dir_path, clean_id)
    json_file = next((f for f in files if f.suffix == ".json"), None)
    if json_file:
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise AnalystBenchError("result_file_corrupt", f"评测结果文件 {json_file.name} 无法解析。")
        return _migrate_summary(data)
    raise AnalystBenchError(
        "result_not_found",
        f"找不到评测结果 {result_id}。",
        {"available_files": sorted(f.name for f in dir_path.glob("*.json")) if dir_path.is_dir() else []},
    )


@router.delete("/direct-results/{result_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_direct_result(result_id: str, request: Request) -> None:
    """Delete a direct_file evaluation result and its companion markdown file."""
    dir_path = results_dir(request)
    clean_id = result_id.removeprefix("direct-")
    files = _find_result_files(dir_path, clean_id)
    if not files:
        raise AnalystBenchError(
            "result_not_found",
            f"找不到评测结果 {result_id}。",
        )
    for f in files:
        f.unlink()


def _migrate_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Apply field-name migrations to the summary.reports of a result JSON."""
    summary = data.get("summary")
    if isinstance(summary, dict):
        for report in summary.get("reports", []):
            _migrate_report_fields(report)
    return data
