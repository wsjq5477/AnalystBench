"""Local case file APIs — scans case.json from formal results directory tree."""

import json
import shutil
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from analystbench.config import Settings
from analystbench.db.models import (
    EvaluationSchedule,
    EvaluationSubmission,
    EvaluationSubmissionCaseRun,
    EvaluationSubmissionMethodRun,
    OptimizationDataSnapshot,
    OptimizationExperiment,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError
from analystbench.evaluation.submission import (
    _atomic_json,
    _safe_case_directory,
    _safe_relative_path,
    inspect_case_logs,
)

router = APIRouter(tags=["cases-local"])


class CaseTreeNode:
    """A node in the local case tree (test_set / category / case_dir)."""

    def __init__(
        self,
        key: str,
        name: str,
        node_type: str,
        children: list["CaseTreeNode"] | None = None,
        case_data: dict[str, Any] | None = None,
    ) -> None:
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
        cat_key = str(case_obj.get("category") or "uncategorized")

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
        runs_dir = case_parent / "runs"
        if runs_dir.is_dir():
            result_count += sum(
                1
                for timestamp_dir in runs_dir.iterdir()
                if timestamp_dir.is_dir() and (timestamp_dir / "result.json").is_file()
            )
        log_info = inspect_case_logs(case_parent)

        case_key = case_obj.get("case_key") or case_dir
        case_summary = {
            "case_key": case_key,
            "problem_statement": (case_obj.get("problem_statement") or "")[:200],
            "category": cat_key,
            "test_set": ts_key,
            "result_count": result_count,
            "claims_count": len((data.get("eval_spec_draft") or {}).get("claims", [])),
            "log_count": log_info["log_count"],
            "primary_log": log_info["primary_log"],
            "submission_ready": log_info["submission_ready"],
            "blocking_issues": log_info["blocking_issues"],
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


class PrimaryLogUpdate(BaseModel):
    filename: str


def _local_case_directory(request: Request, test_set: str, category: str, case_key: str) -> Path:
    settings: Settings = request.app.state.settings
    case_path = f"{test_set}/{category}/{case_key}"
    directory = _safe_case_directory(settings.results_formal_path, case_path)
    if not (directory / "case.json").is_file():
        raise AnalystBenchError("case_not_found", f"找不到 Case {case_path}。", status_code=404)
    return directory


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _assert_case_can_be_deleted(request: Request, case_path: str) -> None:
    """Keep inputs available while durable work still expects to read them."""
    terminal_submission_states = {
        "completed",
        "completed_with_errors",
        "failed",
        "cancelled",
    }
    terminal_experiment_states = {"completed", "failed", "cancelled"}
    with transaction(request.app.state.session_factory) as session:
        active_submission = session.scalar(
            select(EvaluationSubmission.id)
            .join(
                EvaluationSubmissionCaseRun,
                EvaluationSubmissionCaseRun.submission_id == EvaluationSubmission.id,
            )
            .where(
                EvaluationSubmissionCaseRun.case_path == case_path,
                EvaluationSubmission.status.not_in(terminal_submission_states),
            )
            .limit(1)
        )
        active_method = session.scalar(
            select(EvaluationSubmissionMethodRun.id)
            .join(
                EvaluationSubmissionCaseRun,
                EvaluationSubmissionCaseRun.id
                == EvaluationSubmissionMethodRun.case_run_id,
            )
            .where(
                EvaluationSubmissionCaseRun.case_path == case_path,
                EvaluationSubmissionMethodRun.status.in_({"running", "cancelling"}),
            )
            .limit(1)
        )
        if active_submission or active_method:
            raise AnalystBenchError(
                "local_case_delete_running",
                "该 Case 仍有测评在排队或运行，请先取消并等待任务停止。",
                status_code=409,
            )

        for schedule in session.scalars(
            select(EvaluationSchedule).where(
                EvaluationSchedule.dataset_key == case_path.split("/", 1)[0],
                EvaluationSchedule.case_mode == "selected",
            )
        ):
            if case_path in _json_list(schedule.case_paths_json):
                raise AnalystBenchError(
                    "local_case_delete_scheduled",
                    f"定时计划“{schedule.name}”固定选择了该 Case，请先修改或删除计划。",
                    status_code=409,
                )

        active_experiments = session.execute(
            select(OptimizationExperiment, OptimizationDataSnapshot)
            .join(
                OptimizationDataSnapshot,
                OptimizationDataSnapshot.id == OptimizationExperiment.data_snapshot_id,
            )
            .where(OptimizationExperiment.status.not_in(terminal_experiment_states))
        )
        snapshot_fields = (
            "train_cases_json",
            "validation_cases_json",
            "hidden_test_cases_json",
            "prospective_holdout_cases_json",
        )
        for experiment, snapshot in active_experiments:
            if any(case_path in _json_list(getattr(snapshot, field)) for field in snapshot_fields):
                raise AnalystBenchError(
                    "local_case_delete_optimization_active",
                    f"Skill 自优化实验“{experiment.name}”仍在使用该 Case，请先结束实验。",
                    status_code=409,
                )


def _remove_empty_case_parents(case_directory: Path, formal_root: Path) -> None:
    parent = case_directory
    root = formal_root.resolve()
    while parent != root and root in parent.resolve().parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


@router.get("/local-cases/{test_set}/{category}/{case_key}/logs")
def list_local_case_logs(
    test_set: str, category: str, case_key: str, request: Request
) -> dict[str, Any]:
    return inspect_case_logs(_local_case_directory(request, test_set, category, case_key))


@router.post("/local-cases/{test_set}/{category}/{case_key}/logs")
async def upload_local_case_logs(
    test_set: str,
    category: str,
    case_key: str,
    request: Request,
    files: Annotated[list[UploadFile], File(...)],
    primary: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    if not files:
        raise AnalystBenchError("case_logs_missing", "至少上传一个日志文件。")
    case_directory = _local_case_directory(request, test_set, category, case_key)
    logs_directory = case_directory / "logs"
    logs_directory.mkdir(parents=True, exist_ok=True)
    uploaded_names: list[str] = []
    for upload in files:
        filename = Path(upload.filename or "").name
        if not filename or filename == "manifest.json":
            raise AnalystBenchError("case_log_invalid", "日志文件名无效。")
        raw = await upload.read()
        if not raw:
            raise AnalystBenchError("case_log_invalid", f"日志文件 {filename} 为空。")
        destination = logs_directory / filename
        destination.write_bytes(raw)
        uploaded_names.append(filename)
    if primary:
        relative = _safe_relative_path(primary).as_posix()
        if not (logs_directory / relative).is_file():
            raise AnalystBenchError("case_primary_log_missing", "指定的主日志不存在。")
        _atomic_json(logs_directory / "manifest.json", {"primary": relative})
    elif len(inspect_case_logs(case_directory)["files"]) == 1:
        only_file = inspect_case_logs(case_directory)["files"][0]
        _atomic_json(logs_directory / "manifest.json", {"primary": only_file})
    return {**inspect_case_logs(case_directory), "uploaded": uploaded_names}


@router.put("/local-cases/{test_set}/{category}/{case_key}/logs/primary")
def set_local_case_primary_log(
    test_set: str,
    category: str,
    case_key: str,
    payload: PrimaryLogUpdate,
    request: Request,
) -> dict[str, Any]:
    case_directory = _local_case_directory(request, test_set, category, case_key)
    relative = _safe_relative_path(payload.filename).as_posix()
    logs_directory = case_directory / "logs"
    if not (logs_directory / relative).is_file():
        raise AnalystBenchError("case_primary_log_missing", "指定的主日志不存在。")
    _atomic_json(logs_directory / "manifest.json", {"primary": relative})
    return inspect_case_logs(case_directory)


@router.delete(
    "/local-cases/{test_set}/{category}/{case_key}/logs",
    status_code=status.HTTP_200_OK,
)
def delete_local_case_log(
    test_set: str,
    category: str,
    case_key: str,
    request: Request,
    filename: str = Query(...),
) -> dict[str, Any]:
    case_directory = _local_case_directory(request, test_set, category, case_key)
    relative = _safe_relative_path(filename)
    logs_directory = case_directory / "logs"
    target = (logs_directory / relative).resolve()
    if logs_directory.resolve() not in target.parents or not target.is_file():
        raise AnalystBenchError("case_log_not_found", "找不到指定日志。", status_code=404)
    if target.is_symlink():
        raise AnalystBenchError("case_log_invalid", "不能操作符号链接日志。")
    target.unlink()
    info = inspect_case_logs(case_directory)
    manifest = logs_directory / "manifest.json"
    if info["log_count"] == 0:
        if manifest.is_file():
            manifest.unlink()
    elif info["log_count"] == 1:
        _atomic_json(manifest, {"primary": info["files"][0]})
    return inspect_case_logs(case_directory)


@router.delete("/local-cases/{test_set}/{category}/{case_key}")
def delete_local_case(
    test_set: str,
    category: str,
    case_key: str,
    request: Request,
) -> dict[str, int]:
    """Delete runnable Case inputs while preserving immutable evaluation history."""
    settings: Settings = request.app.state.settings
    case_path = f"{test_set}/{category}/{case_key}"
    relative = _safe_relative_path(case_path)
    if len(relative.parts) != 3:
        raise AnalystBenchError("path_invalid", f"Case 路径无效：{case_path}")

    unresolved = settings.results_formal_path / relative
    current = settings.results_formal_path
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise AnalystBenchError(
                "local_case_delete_path_invalid",
                "Case 路径包含符号链接，已拒绝自动删除。",
                status_code=409,
            )

    case_directory = _local_case_directory(request, test_set, category, case_key)
    case_file = case_directory / "case.json"
    logs_directory = case_directory / "logs"
    if case_file.is_symlink() or logs_directory.is_symlink():
        raise AnalystBenchError(
            "local_case_delete_path_invalid",
            "Case 输入或日志目录是符号链接，已拒绝自动删除。",
            status_code=409,
        )

    _assert_case_can_be_deleted(request, case_path)
    log_count = inspect_case_logs(case_directory)["log_count"]
    result_count = sum(
        1
        for result_file in case_directory.rglob("result.json")
        if not result_file.is_symlink()
    )

    quarantined: list[tuple[Path, Path]] = []
    try:
        for target in (case_file, logs_directory):
            if not target.exists():
                continue
            quarantine = target.with_name(f".{target.name}.delete-{uuid4().hex}")
            target.rename(quarantine)
            quarantined.append((target, quarantine))
    except Exception:
        for original, quarantine in reversed(quarantined):
            if quarantine.exists() and not original.exists():
                quarantine.rename(original)
        raise

    for _original, quarantine in quarantined:
        if quarantine.is_dir():
            shutil.rmtree(quarantine, ignore_errors=True)
        else:
            quarantine.unlink(missing_ok=True)
    _remove_empty_case_parents(unresolved, settings.results_formal_path)
    return {
        "case_files_deleted": 1,
        "log_files_deleted": log_count,
        "historical_results_preserved": result_count,
    }


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

        raise AnalystBenchError(
            "case_not_found", f"找不到 Case {case_path}。", status_code=404
        )

    try:
        return json.loads(case_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AnalystBenchError("case_file_corrupt", "Case 文件无法解析。") from exc
