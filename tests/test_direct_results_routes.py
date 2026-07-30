import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from analystbench.api.routes.direct_results import (
    PromotePayload,
    ResultVisibilityPayload,
    get_direct_result_stats,
    list_direct_results,
    move_direct_result,
    promote_direct_result,
    router,
    set_direct_result_visibility,
)
from analystbench.config import Settings


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
        {"name": "agent-a", "avg_score": 85.0, "avg_duration_ms": 1500},
        {"name": "script", "avg_score": 55.0, "avg_duration_ms": 300},
    ]
    assert stats["daily_scores"][0]["candidates"][0]["avg_duration_ms"] == 1200
    assert stats["test_sets"][0]["categories"][0]["candidates"][0][
        "avg_duration_ms"
    ] == 1200


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
    assert get_direct_result_stats(request_for(settings))["candidates"] == [
        {"name": "agent-a", "avg_score": 80.0, "avg_duration_ms": None}
    ]

    set_direct_result_visibility(
        hidden_id,
        ResultVisibilityPayload(included_in_statistics=True),
        request_for(settings),
    )
    assert get_direct_result_stats(request_for(settings))["candidates"] == [
        {"name": "agent-a", "avg_score": 50.0, "avg_duration_ms": None}
    ]


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
