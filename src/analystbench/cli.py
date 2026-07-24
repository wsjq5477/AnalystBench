"""AnalystBench command line interface."""

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from alembic.config import Config

from alembic import command
from analystbench import __version__
from analystbench.api.app import create_app
from analystbench.benchmark import BenchmarkService
from analystbench.case_library import (
    CaseLibraryService,
    EvaluationBatchService,
    report_payload_from_text,
)
from analystbench.comparison import ComparisonService
from analystbench.config import get_settings
from analystbench.content_store import ContentStore
from analystbench.db.models import DatasetVersion
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.direct_evaluation import (
    evaluate_direct,
    evaluate_direct_with_alignment,
    prepare_alignment_draft,
)
from analystbench.errors import AnalystBenchError
from analystbench.eval_spec import EvalSpecService
from analystbench.evaluation_session import EvaluationSessionService
from analystbench.reporting import render_markdown
from analystbench.services import CatalogService, NotFoundError
from analystbench.suites import list_suites
from analystbench.worker import LocalWorker

app = typer.Typer(no_args_is_help=True, add_completion=False)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def catalog_service() -> CatalogService:
    settings = get_settings()
    engine = create_database_engine(settings)
    return CatalogService(create_session_factory(engine), ContentStore(settings.content_store_path))


def benchmark_service() -> BenchmarkService:
    settings = get_settings()
    engine = create_database_engine(settings)
    return BenchmarkService(
        create_session_factory(engine), ContentStore(settings.content_store_path), settings
    )


def eval_spec_service() -> EvalSpecService:
    settings = get_settings()
    engine = create_database_engine(settings)
    return EvalSpecService(
        create_session_factory(engine), ContentStore(settings.content_store_path)
    )


def evaluation_session_service() -> EvaluationSessionService:
    settings = get_settings()
    engine = create_database_engine(settings)
    return EvaluationSessionService(
        create_session_factory(engine), ContentStore(settings.content_store_path)
    )


def case_library_service() -> CaseLibraryService:
    settings = get_settings()
    engine = create_database_engine(settings)
    return CaseLibraryService(
        create_session_factory(engine), ContentStore(settings.content_store_path)
    )


def evaluation_batch_service() -> EvaluationBatchService:
    settings = get_settings()
    engine = create_database_engine(settings)
    return EvaluationBatchService(
        create_session_factory(engine), ContentStore(settings.content_store_path), settings
    )


def _clear_sqlite_database(database: Path) -> None:
    with sqlite3.connect(database, timeout=10) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        ).fetchall()
        for (table_name,) in tables:
            quoted = str(table_name).replace('"', '""')
            connection.execute(f'DELETE FROM "{quoted}"')
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
        ).fetchone():
            connection.execute("DELETE FROM sqlite_sequence")


def _reset_local_data() -> list[tuple[Path, str]]:
    settings = get_settings()
    project_data = (Path.cwd() / "data").resolve()
    if not settings.database_url.startswith("sqlite:///"):
        raise typer.BadParameter("data-reset 只支持本地 SQLite 数据库")
    database = Path(settings.database_url.removeprefix("sqlite:///")).resolve()
    targets = [
        database,
        settings.content_store_path.resolve(),
        settings.workspace_root_path.resolve(),
        (database.parent / "results").resolve(),
    ]
    unique_targets = list(dict.fromkeys(targets))
    for target in unique_targets:
        if target == project_data or not target.is_relative_to(project_data):
            raise typer.BadParameter(f"拒绝清理 data 目录以外的路径：{target}")
    removed: list[tuple[Path, str]] = []
    for target in unique_targets:
        if target.is_dir():
            shutil.rmtree(target)
            removed.append((target, "deleted"))
        elif target.exists():
            try:
                target.unlink()
                removed.append((target, "deleted"))
            except PermissionError:
                if target != database:
                    raise
                _clear_sqlite_database(database)
                removed.append((target, "cleared_in_place"))
    return removed


@app.command()
def version() -> None:
    """Print the installed AnalystBench version."""
    typer.echo(__version__)


@app.command("data-reset")
def data_reset(
    yes: bool = typer.Option(False, "--yes", help="确认删除全部本地基准库和评分结果"),
) -> None:
    """删除本项目 data 下的数据库、内容、Worker 工作区和评分结果。"""
    if not yes:
        raise typer.BadParameter("这是不可恢复操作；确认后请增加 --yes")
    removed = _reset_local_data()
    typer.echo("已删除以下本地数据：")
    for path, action in removed:
        label = "已原地清空（文件被占用）" if action == "cleared_in_place" else "已删除"
        typer.echo(f"- {path}：{label}")


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the local API server."""
    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def worker(
    once: bool = typer.Option(False, "--once", help="Run one worker iteration then exit."),
) -> None:
    """Run the Local Worker."""
    local_worker = LocalWorker()
    try:
        if once:
            local_worker.run_once()
        else:
            local_worker.serve()
    finally:
        local_worker.close()


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Upgrade the configured database to the latest migration."""
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(config, "head")


@app.command("dataset-export")
def dataset_export(dataset_version_id: str, output: Path) -> None:
    """Write a frozen Dataset Version as portable JSON."""
    output.write_text(
        json.dumps(
            catalog_service().export_dataset_version(dataset_version_id),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@app.command("dataset-import")
def dataset_import(input_path: Path) -> None:
    """Import a portable Dataset JSON export and freeze its first version."""
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    version = catalog_service().import_dataset_export(payload)
    typer.echo(version.id)


@app.command("dataset-version-show")
def dataset_version_show(dataset_version_id: str) -> None:
    """Print frozen Dataset Version metadata and Case Revision IDs as JSON."""
    settings = get_settings()
    factory = create_session_factory(create_database_engine(settings))
    with factory() as session:
        version = session.get(DatasetVersion, dataset_version_id)
        if version is None:
            raise typer.BadParameter("dataset version was not found")
        typer.echo(
            json.dumps(
                {
                    "id": version.id,
                    "case_revision_ids": json.loads(version.case_revision_ids_json),
                    "content_hash": version.content_hash,
                }
            )
        )


@app.command("candidate-report-import")
def candidate_report_import(candidate_version_id: str, input_path: Path) -> None:
    """Import a JSON array of case_revision_id/report objects."""
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise typer.BadParameter("input JSON must be an array of reports")
    imported = catalog_service().import_candidate_reports(candidate_version_id, payload)
    typer.echo(json.dumps([report.id for report in imported]))


@app.command("candidate-create")
def candidate_create(name: str, description: str = "") -> None:
    """Create a Candidate and print its ID."""
    typer.echo(catalog_service().create_candidate(name, description).id)


@app.command("candidate-version-create")
def candidate_version_create(candidate_id: str, metadata_path: Path | None = None) -> None:
    """Create an immutable Candidate Version from optional metadata JSON."""
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path else {}
    typer.echo(catalog_service().create_candidate_version(candidate_id, metadata).id)


@app.command("scoring-policy-create")
def scoring_policy_create(name: str, policy_path: Path | None = None) -> None:
    """Create a scoring policy version from optional policy JSON."""
    policy = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path else None
    typer.echo(eval_spec_service().create_scoring_policy(name, policy).id)


@app.command("eval-spec-generate")
def eval_spec_generate(case_revision_id: str, scoring_policy_version_id: str) -> None:
    """Generate a review-required Eval Spec draft and print its ID."""
    typer.echo(eval_spec_service().generate_draft(case_revision_id, scoring_policy_version_id).id)


@app.command("eval-spec-draft-show")
def eval_spec_draft_show(draft_id: str, output: Path | None = None) -> None:
    """Print or write a generated Eval Spec draft for human review and editing."""
    draft = eval_spec_service().get_draft(draft_id)
    payload = draft.payload_json
    if output:
        output.write_text(payload, encoding="utf-8")
    else:
        typer.echo(payload)


@app.command("eval-spec-freeze")
def eval_spec_freeze(case_revision_id: str, payload_path: Path) -> None:
    """Create and freeze an approved Eval Spec payload JSON."""
    service = eval_spec_service()
    draft = service.create_draft(
        case_revision_id, json.loads(payload_path.read_text(encoding="utf-8"))
    )
    typer.echo(service.freeze_draft(draft.id).id)


@app.command("benchmark-run")
def benchmark_run(
    dataset_version_id: str, candidate_version_id: str, scoring_policy_version_id: str
) -> None:
    """Queue an immutable Benchmark Run."""
    typer.echo(
        benchmark_service()
        .create_run(dataset_version_id, candidate_version_id, scoring_policy_version_id)
        .id
    )


@app.command("benchmark-status")
def benchmark_status(run_id: str) -> None:
    """Print Benchmark Run state and summary as JSON."""
    run = benchmark_service().get_run(run_id)
    typer.echo(
        json.dumps({"id": run.id, "status": run.status, "summary": json.loads(run.summary_json)})
    )


@app.command("benchmark-export")
def benchmark_export(run_id: str, output: Path) -> None:
    """Write all immutable Benchmark artifacts and results as JSON."""
    output.write_text(
        json.dumps(benchmark_service().export_run(run_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _session_json(item: object) -> str:
    return json.dumps(
        EvaluationSessionService.view(item), ensure_ascii=False, indent=2, default=str
    )


def _load_drafts(case_path: Path, report_paths: list[Path]) -> tuple[dict, list[dict]]:
    case_draft = json.loads(case_path.read_text(encoding="utf-8"))
    report_drafts = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    return case_draft, report_drafts


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"{path} 的顶层必须是 JSON 对象")
    return payload


def _read_report_input(path: Path) -> dict:
    """Accept either an optional Report JSON wrapper or an original report file."""
    if not path.is_file():
        raise typer.BadParameter(f"报告文件不存在：{path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise typer.BadParameter(f"报告文件不是有效的 UTF-8 文本：{path}") from exc
    if not text.strip():
        raise typer.BadParameter(f"报告文件为空：{path}")
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("candidate_report"), str):
            candidate = payload.setdefault("candidate", {})
            if not isinstance(candidate, dict):
                raise typer.BadParameter(f"{path} 的 candidate 必须是 JSON 对象")
            candidate["name"] = path.stem
            metadata = candidate.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata.setdefault("source_filename", path.name)
            return payload
    return report_payload_from_text(path.name, text)


@app.command("case-import")
def case_import(
    case_path: Path,
    test_set: str | None = typer.Option(None, "--test-set", help="测试集稳定标识"),
    test_set_name: str | None = typer.Option(None, "--test-set-name", help="测试集显示名称"),
    category: str | None = typer.Option(None, "--category", help="用例分类稳定标识"),
    category_name: str | None = typer.Option(None, "--category-name", help="分类显示名称"),
    auto_approve: bool = typer.Option(False, "--yes", help="自动批准所有确认项并发布"),
) -> None:
    """审核一份 Case JSON，并在整体确认后发布到本地基准库。"""
    service = case_library_service()
    case_key = case_path.stem
    previous = None
    try:
        previous = service.get_published(case_key)
    except NotFoundError:
        pass
    payload = _read_json(case_path)
    case = payload.get("case", {}) if isinstance(payload.get("case"), dict) else {}
    embedded_test_set = case.get("test_set", {}) if isinstance(case.get("test_set"), dict) else {}
    embedded_category = case.get("category", {}) if isinstance(case.get("category"), dict) else {}
    test_set = test_set or embedded_test_set.get("key")
    category = category or embedded_category.get("key")
    if not test_set:
        test_set = typer.prompt("请输入测试集标识（例如 kernel-log-analysis）")
    if not category:
        category = typer.prompt("请输入用例分类（例如 panic、lowdog、highdog）")
    test_set_name = test_set_name or embedded_test_set.get("name") or test_set
    category_name = category_name or embedded_category.get("name") or category
    item = service.create_draft(
        payload,
        source_filename=case_path.name,
        test_set_key=test_set,
        test_set_name=test_set_name,
        category_key=category,
        category_name=category_name,
    )
    while item.status == "needs_confirmation":
        question = service.view(item)["questions"][0]
        if auto_approve:
            value = question.get("suggested_value", "approved")
            if question.get("options") and "approved" in question.get("options", []):
                value = "approved"
            typer.echo(f"自动确认 {question['field_path']}：{value}")
            item = service.submit_answers(item.id, [{"question_id": question["id"], "value": value}])
            continue
        typer.echo(f"需要确认 {question['field_path']}：")
        typer.echo(question["question"])
        if question["current_value"] is not None:
            typer.echo(f"当前值：{json.dumps(question['current_value'], ensure_ascii=False)}")
        if question["options"]:
            typer.echo(f"可选值：{json.dumps(question['options'], ensure_ascii=False)}")
        default = question["suggested_value"]
        raw = typer.prompt("请输入", default=default, show_default=default is not None)
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            value = raw
        item = service.submit_answers(item.id, [{"question_id": question["id"], "value": value}])
    if item.status != "ready":
        typer.echo(json.dumps(service.view(item), ensure_ascii=False, indent=2, default=str))
        raise typer.Exit(1)
    published = (
        service.replace_published(item.id, previous.id)
        if previous is not None
        else service.publish(item.id)
    )
    view = service.view(published)

    # Sync case.json to the formal results directory so the frontend can see it
    settings = get_settings()
    ts_key = view["resources"]["test_set"]["key"]
    cat_key = view["resources"]["category"]["key"]
    case_dir = case_path.parent.name  # directory containing the input file
    formal_case_dir = settings.results_formal_path / ts_key / cat_key / case_dir
    formal_case_dir.mkdir(parents=True, exist_ok=True)
    formal_case_file = formal_case_dir / "case.json"
    if not formal_case_file.exists():
        shutil.copy2(case_path, formal_case_file)
        typer.echo(f"已同步到 {formal_case_file}")
    else:
        typer.echo(f"文件已存在，跳过同步：{formal_case_file}")

    typer.echo(
        json.dumps(
            {
                "status": "published",
                "case_key": view["case_key"],
                "case_version": view["resources"]["case_version"],
                "test_set": view["resources"]["test_set"],
                "category": view["resources"]["category"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("case-list")
def case_list() -> None:
    """列出可直接用于评测的已发布 Case。"""
    cases = [
        {
            "case_key": item.case_key,
            "case_version": json.loads(item.resources_json).get("case_version"),
            "test_set": json.loads(item.resources_json).get("test_set"),
            "category": json.loads(item.resources_json).get("category"),
        }
        for item in case_library_service().list_published()
    ]
    typer.echo(json.dumps(cases, ensure_ascii=False, indent=2))


@app.command("case-organize")
def case_organize(
    case_key: str,
    case_path: Path,
    test_set: str = typer.Option(..., "--test-set", help="测试集稳定标识"),
    category: str = typer.Option(..., "--category", help="用例分类稳定标识"),
    test_set_name: str | None = typer.Option(None, "--test-set-name", help="测试集显示名称"),
    category_name: str | None = typer.Option(None, "--category-name", help="分类显示名称"),
) -> None:
    """把已发布的旧 Case 按源文件名、测试集和分类重新归档。"""
    item = case_library_service().organize_published(
        case_key,
        case_path.name,
        test_set,
        test_set_name or test_set,
        category,
        category_name or category,
    )
    view = CaseLibraryService.view(item)
    typer.echo(
        json.dumps(
            {
                "status": view["status"],
                "case_key": view["case_key"],
                "case_version": view["resources"]["case_version"],
                "test_set": view["resources"]["test_set"],
                "category": view["resources"]["category"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _tmp_result_dir(
    case_payload: dict[str, Any],
    case_path: Path,
) -> tuple[Path, str]:
    """Compute the temporary output directory and result_id for a single evaluation run.

    Returns (output_dir, result_id) where:
      output_dir = {results_tmp_path}/{case_key}/{timestamp}/
      result_id  = tmp/{case_key}/{timestamp}
    """
    case_key = case_path.stem
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    settings = get_settings()
    output_dir = settings.results_tmp_path / case_key / timestamp
    result_id = f"tmp/{case_key}/{timestamp}"
    return output_dir, result_id


def _write_structured_result(
    result: dict[str, Any],
    output_dir: Path,
    result_id: str,
    case_path: Path | None,
    case_payload: dict[str, Any] | None,
    report_paths: list[Path],
) -> None:
    """Write evaluation result into the structured directory layout.

    - Creates output_dir
    - Copies case.json to parent directory (if not already present)
    - Copies original report .md files into timestamp directory
    - Writes result.json and result.md
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy original case.json to the case directory (parent of timestamp dir)
    if case_path and case_path.is_file():
        case_target = output_dir.parent / "case.json"
        if not case_target.exists():
            shutil.copy2(case_path, case_target)

    # Copy original report files into the timestamp directory
    for report_path in report_paths:
        if report_path.is_file():
            dest = output_dir / report_path.name
            if not dest.exists():
                shutil.copy2(report_path, dest)

    # Write result.json
    json_path = output_dir / "result.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # Write result.md
    markdown_path = output_dir / "result.md"
    markdown = render_markdown(result["summary"])
    markdown_path.write_text(markdown, encoding="utf-8")

    typer.echo(markdown)
    typer.echo(f"人类可读报告：{markdown_path.resolve()}")
    typer.echo(f"完整审计 JSON：{json_path.resolve()}")


@app.command("evaluate")
def evaluate_reports(
    case_ref: Annotated[
        str,
        typer.Argument(help="本地 Case JSON 路径，或数据库中已发布的 case_key"),
    ],
    report_paths: Annotated[
        list[Path],
        typer.Argument(help="一份或多份 AI 报告原文路径"),
    ],
    judge: str = typer.Option(
        "claude-code", "--judge", help="语义 Judge：claude-code、opencode 或 lexical"
    ),
) -> None:
    """用本地 Case JSON 或数据库 Case 分别评分多份报告，并自动对比。"""
    if not report_paths:
        raise typer.BadParameter("至少需要一份 AI 报告文件")
    reports = [_read_report_input(path) for path in report_paths]
    case_path = Path(case_ref)
    direct_mode = case_path.suffix.casefold() == ".json"
    try:
        if direct_mode:
            if not case_path.is_file():
                raise AnalystBenchError(
                    "case_file_not_found",
                    f"找不到本地 Case JSON：{case_path}",
                )
            case_key = case_path.stem
            case_payload = _read_json(case_path)
            result = evaluate_direct(
                case_payload,
                case_key,
                reports,
                get_settings(),
                judge,
                str(case_path.resolve()),
            )
            output_dir, result_id = _tmp_result_dir(case_payload, case_path)
            # Override the id in result to use the tmp path
            result["id"] = result_id
            _write_structured_result(
                result, output_dir, result_id, case_path, case_payload, report_paths
            )
        else:
            case_key = case_ref
            service = evaluation_batch_service()
            batch = service.create_batch(case_key, report_payloads=reports, judge_runner=judge)
            result = service.process_pending(batch.id)
            # Database mode still uses flat output for now
            result_id = batch.id[:8]
            output_dir = Path("data/results")
            output_dir.mkdir(parents=True, exist_ok=True)
            base_name = f"{case_key}-{result_id}"
            json_path = output_dir / f"{base_name}.json"
            markdown_path = output_dir / f"{base_name}.md"
            json_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            markdown = render_markdown(result["summary"])
            markdown_path.write_text(markdown, encoding="utf-8")
            typer.echo(markdown)
            typer.echo(f"人类可读报告：{markdown_path.resolve()}")
            typer.echo(f"完整审计 JSON：{json_path.resolve()}")
    except AnalystBenchError as exc:
        details = f"；详情：{exc.details}" if exc.details else ""
        raise typer.BadParameter(f"{exc.code}：{exc.message}{details}") from exc


@app.command("score-with-alignment")
def score_with_alignment(
    case_path: Annotated[
        Path,
        typer.Argument(help="本地 Case JSON 路径"),
    ],
    alignment_path: Annotated[
        Path,
        typer.Argument(help="Claude Skill 语义对齐 JSON 路径"),
    ],
    report_paths: Annotated[
        list[Path],
        typer.Argument(help="一份或多份 AI 报告原文路径"),
    ],
) -> None:
    """使用已有的 Claude Skill 语义对齐 JSON 评分报告（不调用大模型，只做 Python 确定性计分）。"""
    if not report_paths:
        raise typer.BadParameter("至少需要一份 AI 报告文件")
    if not case_path.is_file():
        raise AnalystBenchError("case_file_not_found", f"找不到本地 Case JSON：{case_path}")
    if not alignment_path.is_file():
        raise AnalystBenchError(
            "alignment_file_not_found", f"找不到对齐 JSON：{alignment_path}"
        )
    reports = [_read_report_input(path) for path in report_paths]
    case_key = case_path.stem
    case_payload = _read_json(case_path)
    try:
        result = evaluate_direct_with_alignment(
            case_payload,
            case_key,
            reports,
            _read_json(alignment_path),
            str(case_path.resolve()),
        )
    except AnalystBenchError as exc:
        details = f"；详情：{exc.details}" if exc.details else ""
        raise typer.BadParameter(f"{exc.code}：{exc.message}{details}") from exc
    output_dir, result_id = _tmp_result_dir(case_payload, case_path)
    result["id"] = result_id
    _write_structured_result(
        result, output_dir, result_id, case_path, case_payload, report_paths
    )



@app.command("promote")
def promote_result(
    result_id: Annotated[
        str,
        typer.Argument(help="临时结果的 ID，格式如 tmp/{case_key}/{timestamp}"),
    ],
    dest: Annotated[
        str | None,
        typer.Option("--dest", help="指定目标路径，格式如 {test_set}/{category}/{case_dir}。不指定则从 result.json 自动读取。"),
    ] = None,
) -> None:
    """将临时评测结果归档到正式结果集目录。"""
    settings = get_settings()
    tmp_dir = settings.results_tmp_path
    formal_dir = settings.results_formal_path

    # Locate the tmp result
    result_path = tmp_dir / result_id / "result.json"
    if not result_path.is_file():
        # Try without "tmp/" prefix
        alt_path = tmp_dir / result_id.removeprefix("tmp/") / "result.json"
        if alt_path.is_file():
            result_path = alt_path
            result_id = result_id.removeprefix("tmp/") if not result_id.startswith("tmp/") else result_id
        else:
            raise typer.BadParameter(f"找不到临时评测结果：{result_id}")

    # Read result.json to extract metadata
    try:
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise typer.BadParameter(f"评测结果文件无法解析：{exc}") from exc

    # Determine destination path
    if dest:
        dest_parts = dest.split("/")
        if len(dest_parts) < 3:
            raise typer.BadParameter("目标路径格式应为 {test_set}/{category}/{case_dir}")
        test_set, category, case_dir = dest_parts[0], dest_parts[1], dest_parts[2]
    else:
        # Extract from case JSON embedded in result
        case_payload = result_data.get("case") or {}
        # Or from the original case source
        case_source = result_data.get("case_source") or {}
        source_path = case_source.get("source_path", "")

        test_set_obj = case_payload.get("test_set") or {}
        if isinstance(test_set_obj, dict):
            test_set = str(test_set_obj.get("key") or "default")
        else:
            test_set = str(test_set_obj) if test_set_obj else "default"

        category_obj = case_payload.get("category") or {}
        if isinstance(category_obj, dict):
            category = str(category_obj.get("key") or "uncategorized")
        else:
            category = str(category_obj) if category_obj else "uncategorized"

        # case_dir from source_path or case_key
        if source_path:
            case_dir = Path(source_path).parent.name
        else:
            case_dir = result_data.get("case_key", "case")

    # Extract timestamp from current tmp result_id
    timestamp = result_id.split("/")[-1] if "/" in result_id else ""

    if not timestamp:
        # Use current time if no timestamp in path
        timestamp = datetime.now().strftime("%Y%m%d%H%M")

    # Compute new path and result_id
    formal_dest = formal_dir / test_set / category / case_dir / timestamp
    new_result_id = f"{test_set}/{category}/{case_dir}/{timestamp}"

    if formal_dest.exists():
        raise typer.BadParameter(f"目标目录已存在：{new_result_id}")

    # Move the entire timestamp directory
    src_dir = result_path.parent
    formal_dest.mkdir(parents=True, exist_ok=True)

    for item in src_dir.iterdir():
        shutil.move(str(item), str(formal_dest / item.name))

    # Clean up empty parent directories in tmp
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

    # Copy case.json to formal parent directory if not already there
    # Try to find the original case.json from result source path
    source_path_str = (result_data.get("case_source") or {}).get("source_path", "")
    if source_path_str:
        original_case = Path(source_path_str)
        if original_case.is_file():
            case_target = formal_dest.parent / "case.json"
            if not case_target.exists():
                shutil.copy2(original_case, case_target)

    # Update the id in result.json
    result_data["id"] = new_result_id
    formal_dest / "result.json" .write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    typer.echo(f"已归档：{result_id} → {new_result_id}")
    typer.echo(f"目标目录：{formal_dest}")


@app.command("prepare-alignment")
def prepare_alignment(
    case_path: Annotated[
        Path,
        typer.Argument(help="本地 Case JSON 路径"),
    ],
    report_paths: Annotated[
        list[Path],
        typer.Argument(help="一份或多份 AI 报告原文路径"),
    ],
    output_path: Annotated[
        Path,
        typer.Option("--output", help="Python 生成的语义评分草稿 JSON 路径"),
    ] = Path("alignment-draft.json"),
) -> None:
    """生成不切分报告的语义评分草稿，并预先完成日志关键字强匹配。"""
    if not report_paths:
        raise typer.BadParameter("至少需要一份报告文件")
    if not case_path.is_file():
        raise typer.BadParameter(f"找不到本地 Case JSON：{case_path}")
    try:
        draft = prepare_alignment_draft(
            _read_json(case_path),
            case_path.stem,
            [_read_report_input(path) for path in report_paths],
            str(case_path.resolve()),
        )
    except AnalystBenchError as exc:
        raise typer.BadParameter(f"{exc.code}：{exc.message}") from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"语义评分草稿：{output_path.resolve()}")


@app.command("evaluation-report")
def evaluation_report(batch_id: str) -> None:
    """把已有评测批次导出为人类可读 Markdown 和完整审计 JSON。"""
    result = evaluation_batch_service().result(batch_id)
    output_dir = Path("data/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{result['case_key']}-{batch_id[:8]}"
    json_path = output_dir / f"{base_name}.json"
    markdown_path = output_dir / f"{base_name}.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    markdown = render_markdown(result["summary"])
    markdown_path.write_text(markdown, encoding="utf-8")
    typer.echo(markdown)
    typer.echo(f"人类可读报告：{markdown_path.resolve()}")
    typer.echo(f"完整审计 JSON：{json_path.resolve()}")


@app.command("case-draft-create", hidden=True)
def case_draft_create(
    case_path: Path,
    test_set: str = typer.Option(..., "--test-set"),
    category: str = typer.Option(..., "--category"),
    test_set_name: str | None = typer.Option(None, "--test-set-name"),
    category_name: str | None = typer.Option(None, "--category-name"),
) -> None:
    """Create a machine-readable Case Draft for an agent client."""
    item = case_library_service().create_draft(
        _read_json(case_path),
        source_filename=case_path.name,
        test_set_key=test_set,
        test_set_name=test_set_name or test_set,
        category_key=category,
        category_name=category_name or category,
    )
    typer.echo(json.dumps(CaseLibraryService.view(item), ensure_ascii=False, indent=2, default=str))


@app.command("case-draft-answer", hidden=True)
def case_draft_answer(draft_id: str, question_id: str, value_json: str) -> None:
    """Answer one Case Draft question for an agent client."""
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError:
        value = value_json
    service = case_library_service()
    item = service.submit_answers(draft_id, [{"question_id": question_id, "value": value}])
    typer.echo(json.dumps(CaseLibraryService.view(item), ensure_ascii=False, indent=2, default=str))


@app.command("case-draft-publish", hidden=True)
def case_draft_publish(draft_id: str) -> None:
    """Publish one approved Case Draft for an agent client."""
    item = case_library_service().publish(draft_id)
    typer.echo(json.dumps(CaseLibraryService.view(item), ensure_ascii=False, indent=2, default=str))


@app.command("case-draft-replace", hidden=True)
def case_draft_replace(draft_id: str, case_key: str) -> None:
    """Replace a published Case with one already approved draft."""
    service = case_library_service()
    previous = service.get_published(case_key)
    item = service.replace_published(draft_id, previous.id)
    typer.echo(json.dumps(CaseLibraryService.view(item), ensure_ascii=False, indent=2, default=str))


@app.command("evaluation-batch-create", hidden=True)
def evaluation_batch_create(case_key: str, report_paths: list[Path]) -> None:
    """Create a machine-readable multi-report Evaluation Batch."""
    reports = [_read_report_input(path) for path in report_paths]
    service = evaluation_batch_service()
    item = service.create_batch(case_key, report_payloads=reports)
    typer.echo(
        json.dumps(EvaluationBatchService.view(item), ensure_ascii=False, indent=2, default=str)
    )


@app.command("evaluation-batch-process", hidden=True)
def evaluation_batch_process(batch_id: str) -> None:
    """Synchronously process one Evaluation Batch for a local agent client."""
    typer.echo(
        json.dumps(
            evaluation_batch_service().process_pending(batch_id),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


@app.command("evaluation-batch-result", hidden=True)
def evaluation_batch_result(batch_id: str) -> None:
    """Read one Evaluation Batch result for an agent client."""
    typer.echo(
        json.dumps(
            evaluation_batch_service().result(batch_id),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


@app.command("score")
def score(case_path: Path, report_paths: list[Path]) -> None:
    """Review drafts interactively, then queue scoring as one operation."""
    case_draft, report_drafts = _load_drafts(case_path, report_paths)
    service = evaluation_session_service()
    item = service.create_session(case_draft, report_drafts)
    while item.status == "needs_confirmation":
        view = service.view(item)
        for warning in view["warnings"]:
            typer.echo(f"警告 {warning['field_path']}：{warning['question']}")
        question = view["required_questions"][0]
        typer.echo(f"需要确认 {question['field_path']}：{question['question']}")
        if question["current_value"] is not None:
            typer.echo(f"当前值：{json.dumps(question['current_value'], ensure_ascii=False)}")
        if question["options"]:
            typer.echo(f"可选值：{json.dumps(question['options'], ensure_ascii=False)}")
        default = question["suggested_value"]
        raw = typer.prompt("请输入", default=default, show_default=default is not None)
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            value = raw
        item = service.submit_answers(item.id, [{"question_id": question["id"], "value": value}])
    if item.status in {"queued", "running"}:
        typer.echo(
            json.dumps(
                service.process_pending(item.id),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    else:
        typer.echo(_session_json(item))


@app.command("evaluation-session-create", hidden=True)
def evaluation_session_create(case_path: Path, report_paths: list[Path]) -> None:
    """Create a machine-readable Evaluation Session for an agent client."""
    case_draft, report_drafts = _load_drafts(case_path, report_paths)
    typer.echo(
        _session_json(evaluation_session_service().create_session(case_draft, report_drafts))
    )


@app.command("evaluation-session-answer", hidden=True)
def evaluation_session_answer(session_id: str, question_id: str, value_json: str) -> None:
    """Submit one structured answer for an agent client."""
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError:
        value = value_json
    typer.echo(
        _session_json(
            evaluation_session_service().submit_answers(
                session_id, [{"question_id": question_id, "value": value}]
            )
        )
    )


@app.command("evaluation-session-accept-suggestions", hidden=True)
def evaluation_session_accept_suggestions(session_id: str) -> None:
    """Atomically accept every currently available backend suggestion."""
    service = evaluation_session_service()
    item = service.get_session(session_id)
    view = service.view(item)
    suggested = [
        {"question_id": question["id"], "value": question["suggested_value"]}
        for question in view["required_questions"]
        if question["suggested_value"] is not None
    ]
    if not suggested:
        raise typer.BadParameter("no required question currently has a suggested value")
    typer.echo(_session_json(service.submit_answers(session_id, suggested)))


@app.command("evaluation-session-status", hidden=True)
def evaluation_session_status(session_id: str) -> None:
    """Read a machine-readable Evaluation Session for an agent client."""
    typer.echo(_session_json(evaluation_session_service().get_session(session_id)))


@app.command("evaluation-session-result", hidden=True)
def evaluation_session_result(session_id: str) -> None:
    """Read Evaluation Session scoring results for an agent client."""
    typer.echo(
        json.dumps(
            evaluation_session_service().result(session_id),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


@app.command("evaluation-session-process", hidden=True)
def evaluation_session_process(session_id: str) -> None:
    """Process this session synchronously for an interactive local agent."""
    typer.echo(
        json.dumps(
            evaluation_session_service().process_pending(session_id),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


@app.command("compare")
def compare(baseline_run_id: str, candidate_run_id: str) -> None:
    """Compare two Benchmark Runs; output is machine-readable JSON."""
    typer.echo(
        json.dumps(
            ComparisonService(benchmark_service()).compare(baseline_run_id, candidate_run_id)
        )
    )


@app.command("suite-list")
def suite_list() -> None:
    """List built-in benchmark suites as JSON."""
    typer.echo(json.dumps([suite.__dict__ for suite in list_suites()]))


if __name__ == "__main__":
    app()
