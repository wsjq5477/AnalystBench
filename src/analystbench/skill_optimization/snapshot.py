"""Content-addressed optimization data snapshots and split validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analystbench.config import Settings
from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.package import safe_relative_path
from analystbench.storage.content import canonical_json, content_hash


def _file_hash(path: Path) -> str:
    return content_hash(path.read_bytes())


def _case_directory(settings: Settings, case_path: str, dataset_key: str) -> Path:
    relative = safe_relative_path(case_path)
    if len(relative.parts) != 3 or relative.parts[0] != dataset_key:
        raise AnalystBenchError(
            "optimization_case_path_invalid",
            f"Case 路径不属于测试集 {dataset_key}：{case_path}",
        )
    root = settings.results_formal_path.resolve()
    directory = root.joinpath(*relative.parts).resolve()
    if directory == root or root not in directory.parents:
        raise AnalystBenchError(
            "optimization_case_path_invalid", f"Case 路径越界：{case_path}"
        )
    return directory


def inspect_case_snapshot(
    settings: Settings,
    *,
    dataset_key: str,
    case_path: str,
) -> dict[str, str]:
    """Return frozen input/spec hashes without exposing answer content."""

    directory = _case_directory(settings, case_path, dataset_key)
    case_file = directory / "case.json"
    if not case_file.is_file():
        raise AnalystBenchError(
            "optimization_case_not_found", f"找不到 Case：{case_path}"
        )
    try:
        payload = json.loads(case_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalystBenchError(
            "optimization_case_invalid", f"Case JSON 无效：{case_path}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("case"), dict):
        raise AnalystBenchError(
            "optimization_case_invalid", f"Case JSON 缺少 case 对象：{case_path}"
        )
    case = payload["case"]
    logs_directory = directory / "logs"
    log_files = sorted(
        path
        for path in logs_directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    ) if logs_directory.is_dir() else []
    if not log_files:
        raise AnalystBenchError(
            "optimization_case_logs_missing", f"Case 缺少日志：{case_path}"
        )
    logs = [
        {
            "path": path.relative_to(logs_directory).as_posix(),
            "content_hash": _file_hash(path),
        }
        for path in log_files
    ]
    input_manifest = {
        "case_path": case_path,
        "case_key": case.get("case_key") or directory.name,
        "category": case.get("category"),
        "problem_statement": case.get("problem_statement"),
        "logs": logs,
    }
    spec_manifest = {
        "reference_answer": case.get("reference_answer"),
        "eval_spec": payload.get("eval_spec") or payload.get("eval_spec_draft"),
    }
    source_group_key = (
        payload.get("source_group_key")
        or case.get("source_group_key")
        or case_path
    )
    return {
        "input_hash": content_hash(canonical_json(input_manifest).encode("utf-8")),
        "eval_spec_hash": content_hash(canonical_json(spec_manifest).encode("utf-8")),
        "source_group_key": str(source_group_key),
    }


def build_snapshot_manifest(
    settings: Settings,
    *,
    dataset_key: str,
    mode: str,
    train_cases: list[str],
    validation_cases: list[str],
    hidden_test_cases: list[str],
    prospective_holdout_cases: list[str],
) -> dict[str, Any]:
    splits = {
        "train": train_cases,
        "validation": validation_cases,
        "hidden_test": hidden_test_cases,
        "prospective_holdout": prospective_holdout_cases,
    }
    inspected: dict[str, dict[str, str]] = {}
    owners: dict[str, str] = {}
    group_owners: dict[str, str] = {}
    for split, paths in splits.items():
        for path in paths:
            if path in owners:
                raise AnalystBenchError(
                    "optimization_split_overlap",
                    f"Case 同时出现在 {owners[path]} 和 {split}：{path}",
                )
            item = inspect_case_snapshot(
                settings, dataset_key=dataset_key, case_path=path
            )
            group = item["source_group_key"]
            if group in group_owners and group_owners[group] != split:
                raise AnalystBenchError(
                    "optimization_source_group_overlap",
                    f"同源 Case 不能跨切分：{group}",
                )
            owners[path] = split
            group_owners[group] = split
            inspected[path] = item
    return {
        "dataset_key": dataset_key,
        "mode": mode,
        "train_cases": train_cases,
        "validation_cases": validation_cases,
        "hidden_test_cases": hidden_test_cases,
        "prospective_holdout_cases": prospective_holdout_cases,
        "case_input_hashes": {
            path: {
                "content_hash": item["input_hash"],
                "source_group_key": item["source_group_key"],
            }
            for path, item in sorted(inspected.items())
        },
        "eval_spec_hashes": {
            path: item["eval_spec_hash"] for path, item in sorted(inspected.items())
        },
    }
