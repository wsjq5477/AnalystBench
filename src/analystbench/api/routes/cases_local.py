"""Local case file APIs — scans case.json from formal results directory tree."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from analystbench.config import Settings

router = APIRouter(tags=["cases-local"])


class CaseTreeNode:
    """A node in the local case tree (test_set / category / case_dir)."""

    def __init__(self, key: str, name: str, node_type: str, children: list["CaseTreeNode"] | None = None, case_data: dict[str, Any] | None = None) -> None:
        self.key = key
        self.name = name
        self.node_type = node_type  # "test_set" | "category" | "case"
        self.children = children or []
        self.case_data = case_data

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "key": self.key,
            "name": self.name,
            "type": self.node_type,
        }
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        if self.case_data:
            result["case_data"] = self.case_data
        return result


@router.get("/local-cases/tree")
def list_local_cases_tree(request: Request) -> list[dict[str, Any]]:
    """Scan case.json files from the formal results directory and build a tree.

    Tree structure: test_set > category > case_dir (with case_data embedded).
    """
    settings: Settings = request.app.state.settings
    formal_dir = settings.results_formal_path
    tmp_dir = settings.results_tmp_path
    tmp_prefix = str(tmp_dir.resolve()) if tmp_dir.is_dir() else ""

    if not formal_dir.is_dir():
        return []

    # Find all case.json files
    test_sets: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for case_file in formal_dir.rglob("case.json"):
        # Skip files inside tmp directory
        if tmp_prefix and str(case_file.resolve()).startswith(tmp_prefix):
            continue

        try:
            data = json.loads(case_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        case_obj = data.get("case") or {}
        ts_key = str(case_obj.get("test_set") or "default")
        ts_name = ts_key
        cat_key = str(case_obj.get("category") or "uncategorized")
        cat_name = cat_key

        # case_dir from path: case.json is in .../test_set/category/case_dir/case.json
        rel = case_file.relative_to(formal_dir)
        parts = rel.parts
        # case.json should be at depth 3: test_set/category/case_dir/case.json
        if len(parts) >= 3:
            case_dir = parts[-2]  # Parent directory name
        else:
            case_dir = "unknown"

        # Count result timestamps under this case_dir
        case_parent = case_file.parent
        result_count = 0
        for timestamp_dir in case_parent.iterdir():
            if timestamp_dir.is_dir() and (timestamp_dir / "result.json").is_file():
                result_count += 1

        case_key = case_obj.get("case_key") or case_dir
        case_summary = {
            "case_key": case_key,
            "problem_statement": (case_obj.get("problem_statement") or "")[:200],
            "category": cat_key,
            "test_set": ts_key,
            "result_count": result_count,
            "claims_count": len((data.get("eval_spec_draft") or {}).get("claims", [])),
        }

        if ts_key not in test_sets:
            test_sets[ts_key] = {}
        if cat_key not in test_sets[ts_key]:
            test_sets[ts_key][cat_key] = {}
        test_sets[ts_key][cat_key][case_dir] = case_summary

    # Build tree
    result = []
    for ts_key, categories in sorted(test_sets.items()):
        ts_node = CaseTreeNode(ts_key, ts_key, "test_set")
        for cat_key, cases in sorted(categories.items()):
            cat_node = CaseTreeNode(cat_key, cat_key, "category")
            for case_dir, case_data in sorted(cases.items()):
                case_node = CaseTreeNode(case_dir, case_dir, "case", case_data=case_data)
                cat_node.children.append(case_node)
            ts_node.children.append(cat_node)
        result.append(ts_node.to_dict())

    return result


@router.get("/local-cases/{case_path:path}")
def get_local_case(case_path: str, request: Request) -> dict[str, Any]:
    """Return the full case.json data for a given path (test_set/category/case_dir)."""
    settings: Settings = request.app.state.settings
    formal_dir = settings.results_formal_path

    case_file = formal_dir / case_path / "case.json"
    if not case_file.is_file():
        # Try searching by case directory name or case_key stored in JSON
        for cf in formal_dir.rglob("case.json"):
            if cf.parent.name == case_path or cf.parent.parent.name == case_path:
                try:
                    return json.loads(cf.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
            try:
                data = json.loads(cf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if (data.get("case") or {}).get("case_key") == case_path:
                return data

        from analystbench.errors import AnalystBenchError
        raise AnalystBenchError("case_not_found", f"找不到 Case {case_path}。")

    try:
        return json.loads(case_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        from analystbench.errors import AnalystBenchError
        raise AnalystBenchError("case_file_corrupt", "Case 文件无法解析。")
