"""Direct-file evaluation result APIs — reads local JSON from tmp and formal directories."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from pydantic import BaseModel

from analystbench.catalog.case_library import report_payload_from_text
from analystbench.config import Settings
from analystbench.errors import AnalystBenchError
from analystbench.evaluation.direct import evaluate_direct
from analystbench.scoring.reporting import render_markdown

router = APIRouter(tags=["direct-results"])


def _safe_result_directory(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise AnalystBenchError(
            "result_path_invalid", "评测结果路径越界。", status_code=400
        ) from exc
    return candidate


def _safe_result_segment(value: str, field: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or "\x00" in normalized
    ):
        raise AnalystBenchError(
            "result_path_invalid", f"评测结果目标字段无效：{field}。", status_code=400
        )
    return normalized


def _candidate_name_aliases(data: dict[str, Any]) -> dict[str, str]:
    """Return reliable internal-key to display-name aliases persisted in a result."""

    generation = data.get("generation") or {}
    if not isinstance(generation, dict):
        return {}
    methods = generation.get("methods") or []
    if not isinstance(methods, list):
        return {}
    aliases: dict[str, str] = {}
    for method in methods:
        if not isinstance(method, dict):
            continue
        method_key = method.get("key")
        method_name = method.get("name")
        if (
            isinstance(method_key, str)
            and method_key.startswith("sv-")
            and isinstance(method_name, str)
            and method_name
        ):
            aliases[method_key] = method_name
    return aliases


def _normalize_candidate_names(data: dict[str, Any]) -> dict[str, Any]:
    """Hide historical internal Variant keys when a persisted mapping is available."""

    aliases = _candidate_name_aliases(data)
    if not aliases:
        return data
    containers = [data]
    summary = data.get("summary")
    if isinstance(summary, dict):
        containers.append(summary)
    for container in containers:
        reports = container.get("reports") or []
        if isinstance(reports, list):
            for report in reports:
                if isinstance(report, dict):
                    candidate_name = report.get("candidate_name")
                    if isinstance(candidate_name, str) and candidate_name in aliases:
                        report["candidate_name"] = aliases[candidate_name]
        ranking = container.get("ranking") or []
        if isinstance(ranking, list):
            container["ranking"] = [
                aliases.get(item, item) if isinstance(item, str) else item
                for item in ranking
            ]
        comparisons = container.get("comparisons") or []
        if isinstance(comparisons, list):
            for comparison in comparisons:
                if not isinstance(comparison, dict):
                    continue
                for field in ("baseline", "candidate"):
                    value = comparison.get(field)
                    if isinstance(value, str) and value in aliases:
                        comparison[field] = aliases[value]
    return data


def results_dirs(request: Request) -> tuple[Path, Path]:
    """Resolve the tmp and formal results directories from app settings."""
    settings: Settings = request.app.state.settings
    return settings.results_tmp_path, settings.results_formal_path


def _extract_result_meta(data: dict[str, Any], rel_path: Path, source: str) -> dict[str, Any]:
    """Extract metadata from a result JSON and its relative path for listing."""
    _normalize_candidate_names(data)
    if source == "tmp":
        # tmp format: {case_key}/{timestamp}/result.json
        if len(rel_path.parts) >= 2:
            case_dir = rel_path.parts[0]
            timestamp = rel_path.parts[1]
            result_id = f"tmp/{case_dir}/{timestamp}"
        else:
            result_id = str(rel_path.parent) if rel_path.parent != Path(".") else data.get("id", "")
            case_dir = ""
            timestamp = ""
        test_set = ""
        category = ""
    elif (
        rel_path.name == "result.json" and len(rel_path.parts) >= 6 and rel_path.parts[3] == "runs"
    ):
        # P15 format: {test_set}/{category}/{case_dir}/runs/{timestamp}/result.json
        result_id = str(Path(*rel_path.parts[:-1]))
        test_set = rel_path.parts[0]
        category = rel_path.parts[1]
        case_dir = rel_path.parts[2]
        timestamp = rel_path.parts[4]
    elif rel_path.name == "result.json" and len(rel_path.parts) >= 4:
        # Legacy formal format: {test_set}/{category}/{case_dir}/{timestamp}/result.json
        result_id = str(Path(*rel_path.parts[:-1]))
        test_set = rel_path.parts[0]
        category = rel_path.parts[1]
        case_dir = rel_path.parts[2]
        timestamp = rel_path.parts[3]
    else:
        # Legacy flat format
        result_id = data.get("id", rel_path.stem)
        test_set = ""
        category = ""
        case_dir = data.get("case_key", "") or ""
        timestamp = ""

    summary = data.get("summary", data)
    reports_data = summary.get("reports", data.get("reports", []))

    return {
        "id": result_id,
        "case_key": data.get("case_key", case_dir),
        "status": data.get("status", ""),
        "source": source,
        "included_in_statistics": data.get("included_in_statistics", True) is not False,
        "test_set": test_set,
        "category": category,
        "case_dir": case_dir,
        "timestamp": timestamp,
        "reports": [
            {
                "candidate_name": r.get("candidate_name", ""),
                "score": r.get("score", ""),
                "passed": r.get("passed", False),
            }
            for r in reports_data
        ],
    }


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
    if (
        isinstance(metrics, dict)
        and "missing_critical_count" in metrics
        and "missing_chain_count" not in metrics
    ):
        claims = report.get("claims", [])
        metrics["missing_chain_count"] = sum(
            1
            for c in claims
            if c.get("type") == "analysis_chain" and c.get("overall_relation") == "missing"
        )
    return report


def _result_date(data: dict[str, Any], rel_path: Path) -> str:
    """Return an ISO date for a structured result, preferring its run directory."""
    timestamp = ""
    if rel_path.name == "result.json" and len(rel_path.parts) >= 6 and rel_path.parts[3] == "runs":
        timestamp = rel_path.parts[4]
    elif rel_path.name == "result.json" and len(rel_path.parts) >= 4:
        timestamp = rel_path.parts[3]

    digits = "".join(char for char in timestamp if char.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"

    for field in ("completed_at", "created_at", "started_at"):
        value = data.get(field)
        if isinstance(value, str) and len(value) >= 10:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                continue
    return ""


def _result_sort_key(data: dict[str, Any], rel_path: Path) -> str:
    """Return a stable, second-or-better precision key for latest-run comparisons."""
    timestamp = ""
    if rel_path.name == "result.json" and len(rel_path.parts) >= 6 and rel_path.parts[3] == "runs":
        timestamp = rel_path.parts[4]
    elif rel_path.name == "result.json" and len(rel_path.parts) >= 4:
        timestamp = rel_path.parts[3]

    digits = "".join(char for char in timestamp if char.isdigit())
    if len(digits) >= 8:
        return digits[:20].ljust(20, "0")

    for field in ("completed_at", "created_at", "started_at"):
        value = data.get(field)
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed.strftime("%Y%m%d%H%M%S%f")
    return ""


def _generation_durations(data: dict[str, Any]) -> dict[str, float]:
    durations: dict[str, float] = {}
    generation = data.get("generation") or {}
    if not isinstance(generation, dict):
        return durations
    for field, key_fields in (
        ("methods", ("key", "name")),
        ("targets", ("target_key", "display_name")),
    ):
        items = generation.get(field) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            duration = item.get("duration_ms")
            if not (
                isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and duration >= 0
            ):
                continue
            for key_field in key_fields:
                candidate_name = item.get(key_field)
                if isinstance(candidate_name, str) and candidate_name:
                    durations[candidate_name] = float(duration)
    return durations


@router.get("/direct-results/stats")
def get_direct_result_stats(request: Request) -> dict[str, Any]:
    """Aggregate average scores per test_set, category, and case_dir from formal results."""
    tmp_dir, formal_dir = results_dirs(request)
    tmp_prefix = str(tmp_dir.resolve()) if tmp_dir.is_dir() else ""

    # Collect: test_set > category > case_dir > candidate > [scores]
    ts_data: dict[str, dict[str, str]] = {}  # key -> name
    cat_data: dict[str, dict[str, str]] = {}  # key -> name
    scores: dict[
        str, dict[str, dict[str, dict[str, list[float]]]]
    ] = {}  # ts > cat > case_dir > candidate -> [scores]
    durations: dict[
        str, dict[str, dict[str, dict[str, list[float]]]]
    ] = {}  # ts > cat > case_dir > candidate -> [duration_ms]
    daily_case_scores: dict[
        str, dict[str, dict[str, dict[str, list[float]]]]
    ] = {}  # date > ts > cat/case_dir > candidate -> [scores]
    daily_case_durations: dict[
        str, dict[str, dict[str, dict[str, list[float]]]]
    ] = {}  # date > ts > cat/case_dir > candidate -> [duration_ms]
    latest_results: dict[
        str,
        dict[str, dict[str, dict[str, tuple[str, float, float | None]]]],
    ] = {}  # ts > cat > case_dir > candidate -> (sort_key, score, duration_ms)
    global_latest_run = ""

    if formal_dir.is_dir():
        for json_file in formal_dir.rglob("result.json"):
            if tmp_prefix and str(json_file.resolve()).startswith(tmp_prefix):
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            _normalize_candidate_names(data)
            if data.get("mode") != "direct_file":
                continue
            if data.get("result_purpose") == "skill_optimization":
                continue
            if data.get("included_in_statistics", True) is False:
                continue
            if data.get("status") == "running":
                continue

            rel_path = json_file.relative_to(formal_dir)
            if len(rel_path.parts) < 4:
                continue  # Not structured format

            ts_key = rel_path.parts[0]
            cat_key = rel_path.parts[1]
            case_dir = rel_path.parts[2]
            result_date = _result_date(data, rel_path)
            result_sort_key = _result_sort_key(data, rel_path)
            if result_sort_key > global_latest_run:
                global_latest_run = result_sort_key

            # Extract test_set/category names from case.json or result data
            case_obj = data.get("case") or data.get("case_source") or {}
            if isinstance(case_obj, dict):
                ts_obj = case_obj.get("test_set") or {}
                ts_name = ts_obj.get("name", ts_key) if isinstance(ts_obj, dict) else str(ts_obj)
                cat_obj = case_obj.get("category") or {}
                cat_name = (
                    cat_obj.get("name", cat_key) if isinstance(cat_obj, dict) else str(cat_obj)
                )
            else:
                ts_name, cat_name = ts_key, cat_key

            ts_data[ts_key] = {"key": ts_key, "name": ts_name}
            cat_data[f"{ts_key}/{cat_key}"] = {"key": cat_key, "name": cat_name}

            summary = data.get("summary") or data
            reports = (
                summary.get("reports") if isinstance(summary, dict) else data.get("reports", [])
            )
            generation_durations = _generation_durations(data)
            for report in reports:
                candidate_name = report.get("candidate_name", "")
                score = float(report.get("score", 0))
                duration = report.get("duration_ms", generation_durations.get(candidate_name))
                if ts_key not in scores:
                    scores[ts_key] = {}
                if cat_key not in scores[ts_key]:
                    scores[ts_key][cat_key] = {}
                if case_dir not in scores[ts_key][cat_key]:
                    scores[ts_key][cat_key][case_dir] = {}
                if candidate_name not in scores[ts_key][cat_key][case_dir]:
                    scores[ts_key][cat_key][case_dir][candidate_name] = []
                scores[ts_key][cat_key][case_dir][candidate_name].append(score)
                if (
                    isinstance(duration, (int, float))
                    and not isinstance(duration, bool)
                    and duration >= 0
                ):
                    durations.setdefault(ts_key, {}).setdefault(cat_key, {}).setdefault(
                        case_dir, {}
                    ).setdefault(candidate_name, []).append(float(duration))
                normalized_duration = (
                    float(duration)
                    if isinstance(duration, (int, float))
                    and not isinstance(duration, bool)
                    and duration >= 0
                    else None
                )
                if result_sort_key:
                    latest_for_candidate = (
                        latest_results.setdefault(ts_key, {})
                        .setdefault(cat_key, {})
                        .setdefault(case_dir, {})
                    )
                    current_latest = latest_for_candidate.get(candidate_name)
                    if current_latest is None or result_sort_key >= current_latest[0]:
                        latest_for_candidate[candidate_name] = (
                            result_sort_key,
                            score,
                            normalized_duration,
                        )
                if result_date:
                    case_key = f"{cat_key}/{case_dir}"
                    daily_case_scores.setdefault(result_date, {}).setdefault(ts_key, {}).setdefault(
                        case_key, {}
                    ).setdefault(candidate_name, []).append(score)
                    if (
                        isinstance(duration, (int, float))
                        and not isinstance(duration, bool)
                        and duration >= 0
                    ):
                        daily_case_durations.setdefault(result_date, {}).setdefault(
                            ts_key, {}
                        ).setdefault(case_key, {}).setdefault(candidate_name, []).append(
                            float(duration)
                        )

    # Try to also read names from case.json files
    if formal_dir.is_dir():
        for case_file in formal_dir.rglob("case.json"):
            if tmp_prefix and str(case_file.resolve()).startswith(tmp_prefix):
                continue
            try:
                data = json.loads(case_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            rel_path = case_file.relative_to(formal_dir)
            if len(rel_path.parts) < 3:
                continue
            ts_key = rel_path.parts[0]
            cat_key = rel_path.parts[1]
            case_obj = data.get("case") or {}
            if isinstance(case_obj, dict):
                ts_obj = case_obj.get("test_set") or {}
                if isinstance(ts_obj, dict) and ts_obj.get("name"):
                    ts_data[ts_key] = {"key": ts_key, "name": ts_obj.get("name", ts_key)}
                cat_obj = case_obj.get("category") or {}
                if isinstance(cat_obj, dict) and cat_obj.get("name"):
                    cat_data[f"{ts_key}/{cat_key}"] = {
                        "key": cat_key,
                        "name": cat_obj.get("name", cat_key),
                    }

    # Build result structure
    def _avg(lst: list[float]) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    def _latest_case_metrics(
        ts_key: str,
        cat_key: str,
        case_dir: str,
        candidate_name: str,
    ) -> tuple[float, float | None, str]:
        latest = (
            latest_results.get(ts_key, {})
            .get(cat_key, {})
            .get(case_dir, {})
            .get(candidate_name)
        )
        if latest is not None:
            return latest[1], latest[2], latest[0]
        return (
            _avg(scores[ts_key][cat_key][case_dir][candidate_name]),
            None,
            "",
        )

    def _candidate_view(
        candidate_name: str,
        average_scores: list[float],
        average_durations: list[float],
        latest_scores: list[float],
        latest_durations: list[float],
        latest_run_keys: list[str],
    ) -> dict[str, Any]:
        return {
            "name": candidate_name,
            "avg_score": round(_avg(average_scores), 2),
            "avg_duration_ms": (
                round(_avg(average_durations)) if average_durations else None
            ),
            "latest_score": round(_avg(latest_scores or average_scores), 2),
            "latest_duration_ms": (
                round(_avg(latest_durations)) if latest_durations else None
            ),
            "latest_run_key": max(latest_run_keys, default=""),
        }

    def _daily_rows(test_set: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for result_date in sorted(daily_case_scores):
            candidate_case_scores: dict[str, list[float]] = {}
            candidate_case_durations: dict[str, list[float]] = {}
            for ts_key, cases in daily_case_scores[result_date].items():
                if test_set is not None and ts_key != test_set:
                    continue
                for case_key, case_scores in cases.items():
                    for candidate_name, values in case_scores.items():
                        candidate_case_scores.setdefault(candidate_name, []).append(_avg(values))
                        duration_values = (
                            daily_case_durations.get(result_date, {})
                            .get(ts_key, {})
                            .get(case_key, {})
                            .get(candidate_name, [])
                        )
                        if duration_values:
                            candidate_case_durations.setdefault(candidate_name, []).append(
                                _avg(duration_values)
                            )
            candidates = [
                {
                    "name": candidate_name,
                    "avg_score": round(_avg(candidate_case_scores[candidate_name]), 2),
                    "avg_duration_ms": (
                        round(_avg(candidate_case_durations[candidate_name]))
                        if candidate_name in candidate_case_durations
                        else None
                    ),
                }
                for candidate_name in all_candidate_names
                if candidate_name in candidate_case_scores
            ]
            if candidates:
                rows.append({"date": result_date, "candidates": candidates})
        return rows

    # Collect all candidate names across all results
    all_candidate_names: list[str] = []
    candidate_scores_global: dict[str, list[float]] = {}
    candidate_durations_global: dict[str, list[float]] = {}
    for ts_key in sorted(scores.keys()):
        for cat_key in sorted(scores[ts_key].keys()):
            for case_dir in sorted(scores[ts_key][cat_key].keys()):
                for c_name in scores[ts_key][cat_key][case_dir]:
                    if c_name not in candidate_scores_global:
                        candidate_scores_global[c_name] = []
                        all_candidate_names.append(c_name)
                    candidate_scores_global[c_name].extend(
                        scores[ts_key][cat_key][case_dir][c_name]
                    )
                    duration_values = (
                        durations.get(ts_key, {})
                        .get(cat_key, {})
                        .get(case_dir, {})
                        .get(c_name, [])
                    )
                    if duration_values:
                        candidate_durations_global.setdefault(c_name, []).extend(
                            duration_values
                        )

    # Sort candidates by global avg score descending
    all_candidate_names.sort(key=lambda n: _avg(candidate_scores_global[n]), reverse=True)

    result_test_sets: list[dict[str, Any]] = []
    for ts_key in sorted(scores.keys()):
        ts_info = ts_data.get(ts_key, {"key": ts_key, "name": ts_key})
        categories: list[dict[str, Any]] = []
        ts_candidate_scores: dict[str, list[float]] = {}
        ts_candidate_durations: dict[str, list[float]] = {}
        ts_candidate_latest_scores: dict[str, list[float]] = {}
        ts_candidate_latest_durations: dict[str, list[float]] = {}
        ts_candidate_latest_runs: dict[str, list[str]] = {}
        for cat_key in sorted(scores[ts_key].keys()):
            cat_info = cat_data.get(f"{ts_key}/{cat_key}", {"key": cat_key, "name": cat_key})
            case_dirs = scores[ts_key][cat_key]
            # Category-level: average across all case_dirs for each candidate
            cat_candidate_scores: dict[str, list[float]] = {}
            cat_candidate_durations: dict[str, list[float]] = {}
            cat_candidate_latest_scores: dict[str, list[float]] = {}
            cat_candidate_latest_durations: dict[str, list[float]] = {}
            cat_candidate_latest_runs: dict[str, list[str]] = {}
            cases: list[dict[str, Any]] = []
            case_count = len(case_dirs)
            for case_dir in case_dirs:
                case_candidates: list[dict[str, Any]] = []
                for c_name in case_dirs[case_dir]:
                    score_avg = _avg(case_dirs[case_dir][c_name])
                    if c_name not in cat_candidate_scores:
                        cat_candidate_scores[c_name] = []
                    cat_candidate_scores[c_name].append(score_avg)
                    if c_name not in ts_candidate_scores:
                        ts_candidate_scores[c_name] = []
                    ts_candidate_scores[c_name].append(score_avg)
                    duration_values = (
                        durations.get(ts_key, {})
                        .get(cat_key, {})
                        .get(case_dir, {})
                        .get(c_name, [])
                    )
                    if duration_values:
                        duration_avg = _avg(duration_values)
                        cat_candidate_durations.setdefault(c_name, []).append(duration_avg)
                        ts_candidate_durations.setdefault(c_name, []).append(duration_avg)

                    latest_score, latest_duration, latest_run_key = (
                        _latest_case_metrics(ts_key, cat_key, case_dir, c_name)
                    )
                    cat_candidate_latest_scores.setdefault(c_name, []).append(
                        latest_score
                    )
                    ts_candidate_latest_scores.setdefault(c_name, []).append(
                        latest_score
                    )
                    if latest_duration is not None:
                        cat_candidate_latest_durations.setdefault(c_name, []).append(
                            latest_duration
                        )
                        ts_candidate_latest_durations.setdefault(c_name, []).append(
                            latest_duration
                        )
                    if latest_run_key:
                        cat_candidate_latest_runs.setdefault(c_name, []).append(
                            latest_run_key
                        )
                        ts_candidate_latest_runs.setdefault(c_name, []).append(
                            latest_run_key
                        )
                    case_candidates.append(
                        _candidate_view(
                            c_name,
                            case_dirs[case_dir][c_name],
                            duration_values,
                            [latest_score],
                            [latest_duration] if latest_duration is not None else [],
                            [latest_run_key] if latest_run_key else [],
                        )
                    )
                case_candidates.sort(
                    key=lambda item: all_candidate_names.index(item["name"])
                )
                cases.append({"key": case_dir, "candidates": case_candidates})

            cat_candidates = [
                _candidate_view(
                    c_name,
                    cat_candidate_scores.get(c_name, []),
                    cat_candidate_durations.get(c_name, []),
                    cat_candidate_latest_scores.get(c_name, []),
                    cat_candidate_latest_durations.get(c_name, []),
                    cat_candidate_latest_runs.get(c_name, []),
                )
                for c_name in all_candidate_names
                if c_name in cat_candidate_scores
            ]
            categories.append(
                {
                    "key": cat_info["key"],
                    "name": cat_info["name"],
                    "case_count": case_count,
                    "cases": cases,
                    "candidates": cat_candidates,
                }
            )

        ts_candidates = [
            _candidate_view(
                c_name,
                ts_candidate_scores.get(c_name, []),
                ts_candidate_durations.get(c_name, []),
                ts_candidate_latest_scores.get(c_name, []),
                ts_candidate_latest_durations.get(c_name, []),
                ts_candidate_latest_runs.get(c_name, []),
            )
            for c_name in all_candidate_names
            if c_name in ts_candidate_scores
        ]
        result_test_sets.append(
            {
                "key": ts_info["key"],
                "name": ts_info["name"],
                "categories": categories,
                "candidates": ts_candidates,
                "daily_scores": _daily_rows(ts_key),
            }
        )

    global_latest_scores: dict[str, list[float]] = {}
    global_latest_durations: dict[str, list[float]] = {}
    global_candidate_latest_runs: dict[str, list[str]] = {}
    for ts_key in scores:
        for cat_key in scores[ts_key]:
            for case_dir, case_scores in scores[ts_key][cat_key].items():
                for candidate_name in case_scores:
                    latest_score, latest_duration, latest_run_key = (
                        _latest_case_metrics(
                            ts_key, cat_key, case_dir, candidate_name
                        )
                    )
                    global_latest_scores.setdefault(candidate_name, []).append(
                        latest_score
                    )
                    if latest_duration is not None:
                        global_latest_durations.setdefault(candidate_name, []).append(
                            latest_duration
                        )
                    if latest_run_key:
                        global_candidate_latest_runs.setdefault(
                            candidate_name, []
                        ).append(latest_run_key)

    global_candidates = [
        _candidate_view(
            c_name,
            candidate_scores_global[c_name],
            candidate_durations_global.get(c_name, []),
            global_latest_scores.get(c_name, []),
            global_latest_durations.get(c_name, []),
            global_candidate_latest_runs.get(c_name, []),
        )
        for c_name in all_candidate_names
    ]

    return {
        "test_sets": result_test_sets,
        "candidates": global_candidates,
        "daily_scores": _daily_rows(),
        "global_latest_run": global_latest_run,
    }


@router.get("/direct-results")
def list_direct_results(request: Request) -> list[dict[str, Any]]:
    """List all direct_file evaluation results from both formal and tmp directories."""
    tmp_dir, formal_dir = results_dirs(request)
    items: list[dict[str, Any]] = []

    # Scan formal results (structured directory format)
    # Exclude tmp_dir if it's a subdirectory of formal_dir
    tmp_prefix = str(tmp_dir.resolve()) if tmp_dir.is_dir() else ""
    if formal_dir.is_dir():
        for json_file in sorted(formal_dir.rglob("result.json"), key=lambda f: str(f)):
            # Skip files inside tmp directory
            if tmp_prefix and str(json_file.resolve()).startswith(tmp_prefix):
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("mode") != "direct_file":
                continue
            if data.get("result_purpose") == "skill_optimization":
                continue
            rel_path = json_file.relative_to(formal_dir)
            items.append(_extract_result_meta(data, rel_path, "formal"))

    # Also scan legacy flat files in formal dir (backward compat)
    seen_ids = {item["id"] for item in items}
    if formal_dir.is_dir():
        for json_file in sorted(formal_dir.glob("*.json"), key=lambda f: f.name):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("mode") != "direct_file":
                continue
            if data.get("result_purpose") == "skill_optimization":
                continue
            legacy_id = data.get("id", json_file.stem)
            if legacy_id not in seen_ids:
                rel_path = json_file.relative_to(formal_dir)
                items.append(_extract_result_meta(data, rel_path, "formal"))

    # Scan tmp results
    if tmp_dir.is_dir():
        for json_file in sorted(tmp_dir.rglob("result.json"), key=lambda f: str(f)):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("mode") != "direct_file":
                continue
            if data.get("result_purpose") == "skill_optimization":
                continue
            rel_path = json_file.relative_to(tmp_dir)
            items.append(_extract_result_meta(data, rel_path, "tmp"))

    # Sort by timestamp descending, then formal before tmp
    items.sort(
        key=lambda x: (x.get("timestamp", "") or "", 0 if x["source"] == "formal" else 1),
        reverse=True,
    )
    return items


@router.get("/direct-results/{result_id:path}")
def get_direct_result(result_id: str, request: Request) -> dict[str, Any]:
    """Return the full evaluation result JSON for a given result_id."""
    tmp_dir, formal_dir = results_dirs(request)

    # Determine which directory to look in based on result_id prefix
    if result_id.startswith("tmp/"):
        # Tmp result: result_id = "tmp/{case_key}/{timestamp}"
        clean_id = result_id.removeprefix("tmp/")
        candidate = _safe_result_directory(tmp_dir, clean_id) / "result.json"
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raise AnalystBenchError("result_file_corrupt", "评测结果文件无法解析。") from None
            return _migrate_summary(data)
    else:
        # Formal result: result_id = "{test_set}/{category}/{case_dir}/{timestamp}"
        candidate = _safe_result_directory(formal_dir, result_id) / "result.json"
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raise AnalystBenchError("result_file_corrupt", "评测结果文件无法解析。") from None
            return _migrate_summary(data)

    # Fallback: try both directories
    for base_dir in [formal_dir, tmp_dir]:
        for json_file in base_dir.rglob("result.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("id") == result_id:
                return _migrate_summary(data)

    raise AnalystBenchError(
        "result_not_found",
        f"找不到评测结果 {result_id}。",
    )


class PromotePayload(BaseModel):
    """Payload for promote/move specifying destination path."""

    test_set: str
    category: str
    case_dir: str


class ResultVisibilityPayload(BaseModel):
    """Whether a formal result is displayed in benchmark statistics."""

    included_in_statistics: bool


def _reject_optimization_result(result_json: Path) -> None:
    try:
        data = json.loads(result_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if data.get("result_purpose") == "skill_optimization":
        raise AnalystBenchError(
            "optimization_result_managed_by_experiment",
            "Skill 优化结果是 Experiment 证据，不能通过普通结果接口移动或删除。",
            status_code=409,
        )


@router.post("/direct-results/{result_id:path}/promote")
def promote_direct_result(
    result_id: str, payload: PromotePayload, request: Request
) -> dict[str, Any]:
    """Move a tmp result to the formal results directory. Only moves result files, not case.json."""
    tmp_dir, formal_dir = results_dirs(request)

    if not result_id.startswith("tmp/"):
        raise AnalystBenchError("result_not_tmp", "只能归档临时结果（ID 以 tmp/ 开头）。")

    clean_id = result_id.removeprefix("tmp/")
    src_dir = _safe_result_directory(tmp_dir, clean_id)
    result_json = src_dir / "result.json"

    if not result_json.is_file():
        raise AnalystBenchError("result_not_found", f"找不到临时评测结果 {result_id}。")
    _reject_optimization_result(result_json)

    test_set = _safe_result_segment(payload.test_set, "test_set")
    category = _safe_result_segment(payload.category, "category")
    case_dir = _safe_result_segment(payload.case_dir, "case_dir")
    timestamp = clean_id.split("/")[-1] if "/" in clean_id else ""

    dest_dir = formal_dir / test_set / category / case_dir / "runs" / timestamp
    if dest_dir.exists():
        raise AnalystBenchError(
            "dest_conflict",
            f"目标目录已存在：{test_set}/{category}/{case_dir}/runs/{timestamp}",
        )

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Move all files from src timestamp dir to dest (NOT case.json)
    for item in src_dir.iterdir():
        if item.name == "case.json":
            continue  # Skip case.json
        shutil.move(str(item), str(dest_dir / item.name))

    # Clean up empty directories in tmp
    if src_dir.is_dir() and not any(src_dir.iterdir()):
        src_dir.rmdir()
    parent = src_dir.parent
    while parent != tmp_dir and parent.is_dir():
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            else:
                break
        except OSError:
            break

    # Update result id
    new_result_id = f"{test_set}/{category}/{case_dir}/runs/{timestamp}"
    result_data = json.loads((dest_dir / "result.json").read_text(encoding="utf-8"))
    result_data["id"] = new_result_id
    (dest_dir / "result.json").write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    return {
        "old_id": result_id,
        "new_id": new_result_id,
        "dest_path": str(dest_dir),
    }


@router.post("/direct-results/{result_id:path}/move")
def move_direct_result(result_id: str, payload: PromotePayload, request: Request) -> dict[str, Any]:
    """Move formal result files to a different test_set/category/case_dir."""
    tmp_dir, formal_dir = results_dirs(request)

    src_dir = _safe_result_directory(formal_dir, result_id)
    if not (src_dir / "result.json").is_file():
        raise AnalystBenchError("result_not_found", f"找不到正式评测结果 {result_id}。")
    _reject_optimization_result(src_dir / "result.json")

    test_set = _safe_result_segment(payload.test_set, "test_set")
    category = _safe_result_segment(payload.category, "category")
    case_dir = _safe_result_segment(payload.case_dir, "case_dir")
    timestamp = src_dir.name  # Last part of result_id is the timestamp

    dest_parent = formal_dir / test_set / category / case_dir
    dest_dir = dest_parent / "runs" / timestamp
    if dest_dir.exists():
        raise AnalystBenchError(
            "dest_conflict",
            f"目标目录已存在：{test_set}/{category}/{case_dir}/runs/{timestamp}",
        )

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Move all files from src to dest (NOT case.json)
    for item in src_dir.iterdir():
        if item.name == "case.json":
            continue
        shutil.move(str(item), str(dest_dir / item.name))

    # Clean up empty src dir and parents
    if src_dir.is_dir() and not any(src_dir.iterdir()):
        src_dir.rmdir()
    parent = src_dir.parent
    while parent != formal_dir and parent.is_dir():
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            else:
                break
        except OSError:
            break

    # Update result id
    new_result_id = f"{test_set}/{category}/{case_dir}/runs/{timestamp}"
    result_data = json.loads((dest_dir / "result.json").read_text(encoding="utf-8"))
    result_data["id"] = new_result_id
    (dest_dir / "result.json").write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    return {
        "old_id": result_id,
        "new_id": new_result_id,
        "dest_path": str(dest_dir),
    }


@router.patch("/direct-results/{result_id:path}/visibility")
def set_direct_result_visibility(
    result_id: str,
    payload: ResultVisibilityPayload,
    request: Request,
) -> dict[str, Any]:
    """Show or hide a formal result in benchmark statistics."""
    _, formal_dir = results_dirs(request)
    result_json: Path | None = None
    direct_candidate = _safe_result_directory(formal_dir, result_id) / "result.json"
    try:
        direct_candidate.resolve().relative_to(formal_dir.resolve())
    except ValueError:
        pass
    else:
        if direct_candidate.is_file():
            result_json = direct_candidate

    if result_json is None:
        if formal_dir.is_dir():
            candidates = [
                *formal_dir.rglob("result.json"),
                *formal_dir.glob("*.json"),
            ]
            for candidate in candidates:
                try:
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if data.get("mode") == "direct_file" and data.get("id") == result_id:
                    result_json = candidate
                    break

    if result_json is None:
        raise AnalystBenchError("result_not_found", f"找不到正式评测结果 {result_id}。")

    try:
        data = json.loads(result_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise AnalystBenchError("result_file_corrupt", "评测结果文件无法解析。") from None

    if data.get("result_purpose") == "skill_optimization":
        raise AnalystBenchError(
            "optimization_result_managed_by_experiment",
            "Skill 优化结果由 Experiment 管理，不能修改普通统计可见性。",
            status_code=409,
        )

    data["included_in_statistics"] = payload.included_in_statistics
    pending_json = result_json.with_name(f".{result_json.name}.visibility.tmp")
    pending_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    pending_json.replace(result_json)
    return {
        "id": result_id,
        "included_in_statistics": payload.included_in_statistics,
    }


@router.delete("/direct-results/{result_id:path}", status_code=status.HTTP_204_NO_CONTENT)
def delete_direct_result(result_id: str, request: Request) -> None:
    """Delete a direct_file evaluation result."""
    tmp_dir, formal_dir = results_dirs(request)

    if result_id.startswith("tmp/"):
        # Delete tmp result
        clean_id = result_id.removeprefix("tmp/")
        target_dir = _safe_result_directory(tmp_dir, clean_id)
        if target_dir.is_dir():
            _reject_optimization_result(target_dir / "result.json")
            shutil.rmtree(target_dir)
            # Clean up empty parent dirs
            parent = target_dir.parent
            while parent != tmp_dir and parent.is_dir():
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                        parent = parent.parent
                    else:
                        break
                except OSError:
                    break
            return

    # Delete formal result (timestamp directory)
    target_dir = _safe_result_directory(formal_dir, result_id)
    if target_dir.is_dir():
        _reject_optimization_result(target_dir / "result.json")
        shutil.rmtree(target_dir)
        # Clean up empty parent dirs
        parent = target_dir.parent
        while parent != formal_dir and parent.is_dir():
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
                else:
                    break
            except OSError:
                break
        return

    # Legacy flat file fallback
    clean_id = result_id.removeprefix("direct-")
    for json_file in formal_dir.glob("*.json"):
        file_stem = json_file.stem
        parts = file_stem.split("-", 1)
        if len(parts) == 2 and parts[1] == clean_id:
            md_file = formal_dir / f"{file_stem}.md"
            if md_file.is_file():
                md_file.unlink()
            json_file.unlink()
            return


def _migrate_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Apply field-name migrations to the summary.reports of a result JSON."""
    _normalize_candidate_names(data)
    summary = data.get("summary")
    if isinstance(summary, dict):
        for report in summary.get("reports", []):
            _migrate_report_fields(report)
    return data


@router.post("/evaluate-direct")
async def evaluate_local_case(
    request: Request,
    reports: Annotated[list[UploadFile], File()],
    case_path: str = Form(...),
    judge: str = Form("lexical"),
) -> dict[str, Any]:
    """Evaluate uploaded report files against a local Case JSON asynchronously.

    Returns immediately with result_id and status='running'.
    The actual evaluation runs in a background thread.
    Poll GET /direct-results/{result_id} to check progress.
    """
    if not reports:
        raise AnalystBenchError("report_invalid", "至少需要一份 AI 报告文件。")
    if judge not in {"claude", "opencode", "lexical"}:
        raise AnalystBenchError(
            "validation_failed", "judge 必须是 claude、opencode 或 lexical。"
        )

    settings: Settings = request.app.state.settings
    formal_dir = settings.results_formal_path

    # Read case.json from formal directory
    case_file = formal_dir / case_path / "case.json"
    if not case_file.is_file():
        raise AnalystBenchError("case_not_found", f"找不到 Case {case_path}。")

    try:
        case_payload = json.loads(case_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise AnalystBenchError("case_file_corrupt", "Case 文件无法解析。") from None

    if not isinstance(case_payload, dict):
        raise AnalystBenchError("direct_case_invalid", "Case JSON 顶层必须是 JSON 对象。")

    # Determine case_key from case data or path
    case_obj = case_payload.get("case") or {}
    case_key = case_obj.get("case_key") if isinstance(case_obj, dict) else Path(case_path).name

    # Parse uploaded report files — read raw bytes once, reuse for parsing and disk write
    report_payloads: list[dict[str, Any]] = []
    report_raw: list[tuple[str, bytes]] = []  # (filename, raw_bytes) for disk write
    for upload in reports:
        filename = upload.filename or "report.md"
        raw = await upload.read()
        report_raw.append((filename, raw))
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise AnalystBenchError(
                "report_invalid", f"报告文件 {filename} 不是有效的 UTF-8 文本。"
            ) from None
        if not text.strip():
            raise AnalystBenchError("report_invalid", f"报告文件 {filename} 为空。")

        # Try JSON wrapper format first
        if filename.lower().endswith(".json"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("candidate_report"), str):
                candidate = payload.setdefault("candidate", {})
                if not isinstance(candidate, dict):
                    raise AnalystBenchError(
                        "report_invalid", f"{filename} 的 candidate 必须是 JSON 对象。"
                    )
                candidate["name"] = Path(filename).stem
                metadata = candidate.setdefault("metadata", {})
                if isinstance(metadata, dict):
                    metadata.setdefault("source_filename", filename)
                report_payloads.append(payload)
                continue

        # Plain text / markdown report
        report_payloads.append(report_payload_from_text(filename, text))

    # Create output directory and result_id upfront (before evaluation starts)
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output_dir = settings.results_tmp_path / str(case_key) / timestamp
    result_id = f"tmp/{case_key}/{timestamp}"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Ensure case.json exists in parent directory
    case_target = output_dir.parent / "case.json"
    if not case_target.exists():
        shutil.copy2(case_file, case_target)

    # Write report files into timestamp directory
    for filename, raw in report_raw:
        dest = output_dir / filename
        if not dest.exists():
            dest.write_bytes(raw)

    # Write initial result.json with status=running so it appears in the list
    running_result = {
        "id": result_id,
        "mode": "direct_file",
        "case_key": case_key,
        "status": "running",
        "reports": [],
        "comparisons": [],
        "summary": {
            "case_key": case_key,
            "engine_note": f"评分引擎 {judge}，评分进行中…",
            "ranking": [],
            "reports": [
                {
                    "candidate_name": r.get("candidate", {}).get("name", "unknown"),
                    "status": "running",
                    "score": "0",
                    "passed": False,
                    "claim_count": 0,
                    "hit_count": 0,
                    "missing_chains": [],
                    "metrics": {},
                    "claims": [],
                    "warnings": [],
                }
                for r in report_payloads
            ],
            "comparisons": [],
        },
        "error": {},
    }
    (output_dir / "result.json").write_text(
        json.dumps(running_result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # Launch evaluation in background thread
    source_path = str(case_file.resolve())

    def _run_evaluation() -> None:
        """Run evaluation in a background thread and update result.json on completion."""
        try:
            result = evaluate_direct(
                case_payload,
                case_key,
                report_payloads,
                settings,
                judge,
                source_path,
            )
            result["id"] = result_id
            # Write completed result.json
            (output_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            # Write result.md
            markdown = render_markdown(result["summary"])
            (output_dir / "result.md").write_text(markdown, encoding="utf-8")
        except Exception as exc:
            # Write failed result.json
            error_result = {
                "id": result_id,
                "mode": "direct_file",
                "case_key": case_key,
                "status": "failed",
                "reports": [],
                "comparisons": [],
                "summary": {
                    "case_key": case_key,
                    "engine_note": f"评分失败：{exc}",
                    "ranking": [],
                    "reports": [],
                    "comparisons": [],
                },
                "error": {"code": "evaluation_failed", "message": str(exc)},
            }
            (output_dir / "result.json").write_text(
                json.dumps(error_result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

    import asyncio

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_evaluation)

    return {
        "result_id": result_id,
        "status": "running",
    }
