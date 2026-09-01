import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from analystbench.api.routes.direct_results import (
    PromotePayload,
    ResultVisibilityPayload,
    delete_direct_result,
    get_direct_result,
    get_direct_result_stats,
    list_direct_results,
    move_direct_result,
    promote_direct_result,
    router,
    set_direct_result_visibility,
)
from analystbench.config import Settings
from analystbench.errors import AnalystBenchError


def request_for(settings: Settings) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
    )


def write_result(directory: Path, result_id: str) -> None:
    directory.mkdir(parents=True)
    (directory / "report.md").write_text("report", encoding="utf-8")
    (directory / "result.json").write_text(
        json.dumps(
            {
                "id": result_id,
                "mode": "direct_file",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )


def write_scored_result(
    directory: Path,
    result_id: str,
    reports: list[tuple[str, float]],
    durations: dict[str, int] | None = None,
) -> None:
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(
        json.dumps(
            {
                "id": result_id,
                "mode": "direct_file",
                "status": "completed",
                "reports": [
                    {
                        "candidate_name": candidate_name,
                        "score": str(score),
                        "passed": score >= 60,
                    }
                    for candidate_name, score in reports
                ],
                "generation": {
                    "targets": [
                        {
                            "target_key": candidate_name,
                            "duration_ms": duration_ms,
                        }
                        for candidate_name, duration_ms in (durations or {}).items()
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_stats_exposes_daily_candidate_scores_by_test_set(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    base = settings.results_formal_path
    write_scored_result(
        base / "kdiag" / "deadlock" / "case_a" / "runs" / "202607271000",
        "kdiag/deadlock/case_a/runs/202607271000",
        [("agent-a", 60), ("agent-b", 40)],
    )
    write_scored_result(
        base / "kdiag" / "deadlock" / "case_a" / "runs" / "202607271100",
        "kdiag/deadlock/case_a/runs/202607271100",
        [("agent-a", 100), ("agent-b", 60)],
    )
    write_scored_result(
        base / "kdiag" / "panic" / "case_b" / "runs" / "202607271200",
        "kdiag/panic/case_b/runs/202607271200",
        [("agent-a", 80)],
    )
    write_scored_result(
        base / "kdiag" / "deadlock" / "case_a" / "runs" / "202607281000",
        "kdiag/deadlock/case_a/runs/202607281000",
        [("agent-a", 90), ("agent-b", 70)],
    )
    write_scored_result(
        base / "other" / "deadlock" / "case_c" / "runs" / "202607281200",
        "other/deadlock/case_c/runs/202607281200",
        [("agent-a", 30)],
    )

    stats = get_direct_result_stats(request_for(settings))

    kdiag = next(item for item in stats["test_sets"] if item["key"] == "kdiag")
    assert kdiag["daily_scores"] == [
        {
            "date": "2026-07-27",
            "candidates": [
                {"name": "agent-a", "avg_score": 80.0, "avg_duration_ms": None},
                {"name": "agent-b", "avg_score": 50.0, "avg_duration_ms": None},
            ],
        },
        {
            "date": "2026-07-28",
            "candidates": [
                {"name": "agent-a", "avg_score": 90.0, "avg_duration_ms": None},
                {"name": "agent-b", "avg_score": 70.0, "avg_duration_ms": None},
            ],
        },
    ]
    assert stats["daily_scores"][-1] == {
        "date": "2026-07-28",
        "candidates": [
            {"name": "agent-a", "avg_score": 60.0, "avg_duration_ms": None},
            {"name": "agent-b", "avg_score": 70.0, "avg_duration_ms": None},
        ],
    }


def test_stats_exposes_average_generation_duration(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    base = settings.results_formal_path
    write_scored_result(
        base / "kdiag" / "deadlock" / "case_a" / "runs" / "202607271000",
        "kdiag/deadlock/case_a/runs/202607271000",
        [("agent-a", 80), ("script", 50)],
        {"agent-a": 1200, "script": 200},
    )
    write_scored_result(
        base / "kdiag" / "panic" / "case_b" / "runs" / "202607281000",
        "kdiag/panic/case_b/runs/202607281000",
        [("agent-a", 90), ("script", 60)],
        {"agent-a": 1800, "script": 400},
    )

    stats = get_direct_result_stats(request_for(settings))

    assert stats["candidates"] == [
        {
            "name": "agent-a",
            "avg_score": 85.0,
            "avg_duration_ms": 1500,
            "latest_score": 85.0,
            "latest_duration_ms": 1500,
            "latest_run_key": "20260728100000000000",
        },
        {
            "name": "script",
            "avg_score": 55.0,
            "avg_duration_ms": 300,
            "latest_score": 55.0,
            "latest_duration_ms": 300,
            "latest_run_key": "20260728100000000000",
        },
    ]
    assert stats["global_latest_run"] == "20260728100000000000"
    assert stats["daily_scores"][0]["candidates"][0]["avg_duration_ms"] == 1200
    assert stats["test_sets"][0]["categories"][0]["candidates"][0][
        "avg_duration_ms"
    ] == 1200


def test_variant_display_name_is_used_for_result_reads_and_stats(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    result_id = "kdiag/deadlock/case_a/runs/202607271000"
    result_directory = settings.results_formal_path / result_id
    result_directory.mkdir(parents=True)
    (result_directory / "result.json").write_text(
        json.dumps(
            {
                "id": result_id,
                "mode": "direct_file",
                "status": "completed",
                "summary": {
                    "reports": [
                        {
                            "candidate_name": "sv-1234567890abcdef",
                            "score": "80",
                            "passed": True,
                        }
                    ],
                    "ranking": ["sv-1234567890abcdef"],
                    "comparisons": [
                        {
                            "baseline": "script",
                            "candidate": "sv-1234567890abcdef",
                        }
                    ],
                },
                "generation": {
                    "methods": [
                        {
                            "key": "sv-1234567890abcdef",
                            "name": "codeagent-native@glm-5.2",
                            "duration_ms": 1200,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    request = request_for(settings)

    listed = list_direct_results(request)
    result = get_direct_result(result_id, request)
    stats = get_direct_result_stats(request)

    assert listed[0]["reports"][0]["candidate_name"] == "codeagent-native@glm-5.2"
    assert result["summary"]["reports"][0]["candidate_name"] == (
        "codeagent-native@glm-5.2"
    )
    assert result["summary"]["ranking"] == ["codeagent-native@glm-5.2"]
    assert result["summary"]["comparisons"][0]["candidate"] == (
        "codeagent-native@glm-5.2"
    )
    assert stats["candidates"][0]["name"] == "codeagent-native@glm-5.2"
    assert stats["candidates"][0]["avg_duration_ms"] == 1200


def test_stats_exposes_latest_metrics_at_every_scope(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    base = settings.results_formal_path
    write_scored_result(
        base / "kdiag" / "deadlock" / "case_a" / "runs" / "20260727_100000",
        "kdiag/deadlock/case_a/runs/20260727_100000",
        [("agent-a", 20), ("old-agent", 90)],
        {"agent-a": 100, "old-agent": 500},
    )
    write_scored_result(
        base / "kdiag" / "deadlock" / "case_a" / "runs" / "20260728_110000",
        "kdiag/deadlock/case_a/runs/20260728_110000",
        [("agent-a", 80)],
        {"agent-a": 200},
    )
    write_scored_result(
        base / "kdiag" / "deadlock" / "case_b" / "runs" / "20260728_120000",
        "kdiag/deadlock/case_b/runs/20260728_120000",
        [("agent-a", 40)],
        {"agent-a": 300},
    )

    stats = get_direct_result_stats(request_for(settings))

    candidates = {item["name"]: item for item in stats["candidates"]}
    assert candidates["agent-a"] == {
        "name": "agent-a",
        "avg_score": 46.67,
        "avg_duration_ms": 200,
        "latest_score": 60.0,
        "latest_duration_ms": 250,
        "latest_run_key": "20260728120000000000",
    }
    assert candidates["old-agent"]["latest_run_key"] == "20260727100000000000"
    assert stats["global_latest_run"] == "20260728120000000000"

    test_set = stats["test_sets"][0]
    category = test_set["categories"][0]
    case_a = next(item for item in category["cases"] if item["key"] == "case_a")
    case_a_agent = next(
        item for item in case_a["candidates"] if item["name"] == "agent-a"
    )
    assert case_a_agent["avg_score"] == 50.0
    assert case_a_agent["latest_score"] == 80.0
    category_agent = next(
        item for item in category["candidates"] if item["name"] == "agent-a"
    )
    test_set_agent = next(
        item for item in test_set["candidates"] if item["name"] == "agent-a"
    )
    assert category_agent["latest_score"] == 60.0
    assert test_set_agent["latest_score"] == 60.0


def test_running_formal_result_is_excluded_from_stats(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    base = settings.results_formal_path
    completed_id = "kdiag/deadlock/case_a/runs/202607271000"
    running_id = "kdiag/deadlock/case_a/runs/202607271100"
    write_scored_result(base / completed_id, completed_id, [("agent-a", 80)])
    write_scored_result(base / running_id, running_id, [("agent-a", 0)])
    running_path = base / running_id / "result.json"
    running = json.loads(running_path.read_text(encoding="utf-8"))
    running["status"] = "running"
    running["summary"] = {"reports": running.pop("reports")}
    running_path.write_text(json.dumps(running), encoding="utf-8")

    assert get_direct_result_stats(request_for(settings))["candidates"] == [
        {
            "name": "agent-a",
            "avg_score": 80.0,
            "avg_duration_ms": None,
            "latest_score": 80.0,
            "latest_duration_ms": None,
            "latest_run_key": "20260727100000000000",
        }
    ]


def test_hidden_formal_result_is_listed_but_excluded_from_stats(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    base = settings.results_formal_path
    visible_id = "kdiag/deadlock/case_a/runs/202607271000"
    hidden_id = "kdiag/deadlock/case_a/runs/202607281000"
    write_scored_result(base / visible_id, visible_id, [("agent-a", 80)])
    write_scored_result(base / hidden_id, hidden_id, [("agent-a", 20)])

    app = FastAPI()
    app.state.settings = settings
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/direct-results/{quote(hidden_id, safe='')}/visibility",
            json={"included_in_statistics": False},
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": hidden_id,
        "included_in_statistics": False,
    }
    listed = list_direct_results(request_for(settings))
    assert {item["id"]: item["included_in_statistics"] for item in listed} == {
        visible_id: True,
        hidden_id: False,
    }
    assert get_direct_result_stats(request_for(settings))["candidates"][0][
        "avg_score"
    ] == 80.0

    set_direct_result_visibility(
        hidden_id,
        ResultVisibilityPayload(included_in_statistics=True),
        request_for(settings),
    )
    visible_stats = get_direct_result_stats(request_for(settings))
    assert visible_stats["candidates"][0]["avg_score"] == 50.0
    assert visible_stats["candidates"][0]["latest_score"] == 20.0


def test_skill_optimization_results_are_isolated_and_operator_guarded(
    tmp_path: Path,
) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    normal_id = "kdiag/deadlock/case_a/runs/202607271000"
    optimization_id = "kdiag/deadlock/case_a/runs/202607271100"
    write_scored_result(
        settings.results_formal_path / normal_id,
        normal_id,
        [("agent-a", 80)],
    )
    optimization_dir = settings.results_formal_path / optimization_id
    write_scored_result(
        optimization_dir,
        optimization_id,
        [("agent-a", 0)],
    )
    result_json = optimization_dir / "result.json"
    payload = json.loads(result_json.read_text(encoding="utf-8"))
    payload.update(
        {
            "included_in_statistics": False,
            "result_purpose": "skill_optimization",
            "optimization_context": {"experiment_id": "experiment-1"},
        }
    )
    result_json.write_text(json.dumps(payload), encoding="utf-8")

    assert [item["id"] for item in list_direct_results(request_for(settings))] == [
        normal_id
    ]
    assert get_direct_result_stats(request_for(settings))["candidates"][0][
        "avg_score"
    ] == 80.0
    with pytest.raises(AnalystBenchError) as visibility:
        set_direct_result_visibility(
            optimization_id,
            ResultVisibilityPayload(included_in_statistics=True),
            request_for(settings),
        )
    with pytest.raises(AnalystBenchError) as deletion:
        delete_direct_result(optimization_id, request_for(settings))
    assert visibility.value.code == "optimization_result_managed_by_experiment"
    assert deletion.value.code == "optimization_result_managed_by_experiment"
    assert result_json.is_file()


def test_promote_tmp_result_uses_runs_directory(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    source = settings.results_tmp_path / "chmod_hung" / "202607281200"
    write_result(source, "tmp/chmod_hung/202607281200")

    moved = promote_direct_result(
        "tmp/chmod_hung/202607281200",
        PromotePayload(
            test_set="kdiag",
            category="SYSTEM_DEADLOCK",
            case_dir="chmod_hung",
        ),
        request_for(settings),
    )

    expected = (
        settings.results_formal_path
        / "kdiag"
        / "SYSTEM_DEADLOCK"
        / "chmod_hung"
        / "runs"
        / "202607281200"
    )
    assert Path(moved["dest_path"]) == expected
    assert moved["new_id"] == "kdiag/SYSTEM_DEADLOCK/chmod_hung/runs/202607281200"
    assert json.loads((expected / "result.json").read_text(encoding="utf-8"))["id"] == moved[
        "new_id"
    ]
    assert not source.exists()


def test_move_legacy_formal_result_converts_to_runs_directory(tmp_path: Path) -> None:
    settings = Settings(
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
    )
    source_id = "kdiag/SYSTEM_DEADLOCK/chmod_hung/202607281200"
    source = settings.results_formal_path / source_id
    write_result(source, source_id)

    moved = move_direct_result(
        source_id,
        PromotePayload(
            test_set="kdiag",
            category="SYSTEM_DEADLOCK",
            case_dir="chmod_hung_moved",
        ),
        request_for(settings),
    )

    expected = (
        settings.results_formal_path
        / "kdiag"
        / "SYSTEM_DEADLOCK"
        / "chmod_hung_moved"
        / "runs"
        / "202607281200"
    )
    assert Path(moved["dest_path"]) == expected
    assert moved["new_id"] == (
        "kdiag/SYSTEM_DEADLOCK/chmod_hung_moved/runs/202607281200"
    )
    assert json.loads((expected / "result.json").read_text(encoding="utf-8"))["id"] == moved[
        "new_id"
    ]
    assert not source.exists()
