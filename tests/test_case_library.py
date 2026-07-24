import copy
import json
import sys
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from alembic import command
from analystbench.api.app import create_app
from analystbench.config import Settings
from analystbench.db.models import Case, CaseTrace, DatasetVersion
from analystbench.reporting import render_markdown
from analystbench.worker import LocalWorker


def migrated_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return Settings(
        database_url=database_url,
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
    )


def case_payload(importance: str = "normal") -> dict:
    reference = "系统因 suspend-to-mem 超时触发 panic。根因是未执行 REPICK，线程可能跑错 CPU 核。"
    return {
        "case": {
            "test_set": "kdiag",
            "category": "SYSMGR_PANIC",
            "problem_statement": "分析 suspend 超时的根因。",
            "reference_answer": reference,
        },
        "eval_spec_draft": {
            "claims": [
                {
                    "id": "claim-1",
                    "type": "symptom",
                    "statement": "系统因 suspend-to-mem 超时触发 panic",
                    "importance": importance,
                    "weight": 30,
                    "quote": "suspend-to-mem 超时触发 panic",
                    "review_required": True,
                },
                {
                    "id": "root",
                    "type": "root_cause",
                    "statement": "未执行 REPICK 导致线程可能跑错 CPU 核",
                    "importance": "critical",
                    "weight": 70,
                    "quote": "未执行 REPICK，线程可能跑错 CPU 核",
                    "review_required": True,
                },
            ],
            "causal_edges": [],
            "forbidden_claims": [],
            "unresolved_items": [],
        },
    }


def report_payload(name: str, report: str, bad_hint: bool = False) -> dict:
    return {
        "candidate": {"name": name, "metadata": {"model": "test"}},
        "candidate_report": report,
        "claim_hints": [
            {
                "id": "candidate-1",
                "type": "symptom",
                "statement": "发生挂起超时",
                "quote": "不存在的原文" if bad_hint else "suspend-to-mem 超时",
            }
        ],
        "unresolved_items": [],
    }


def test_labeled_reference_is_normalized_to_root_category_and_three_equal_chain_claims(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    payload = case_payload()
    payload["case"]["reference_answer"] = (
        "问题分类：HM_PANIC_SYSMGR\n"
        "问题根因：调度问题，开抢占未REPICK，导致线程跑错核\n"
        "分析链：\n"
        "证据1：suspend to mem is timeout\n"
        "结论1：休眠超时\n"
        "证据2：cpuhp: listener devmgr.actv handling cpu1 event: 2 enter\n"
        "结论2：cpuhp卡主\n"
        "证据3：liblinux_remove_cpu\n"
        "结论3：卡在liblinux_remove_cpu的schedule，怀疑调度相关"
    )
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/case-drafts", json={"payload": payload, "case_key": "1"}
        ).json()
        assert created["summary"]["claim_count"] == 5
        assert created["questions"][0]["code"] == "approve_case"
        stored = client.app.state.case_library_service.get_draft(created["id"])
        working = json.loads(stored.working_json)
        claims = working["eval_spec_draft"]["claims"]
        assert [claim["type"] for claim in claims] == [
            "root_cause",
            "classification",
            "analysis_chain",
            "analysis_chain",
            "analysis_chain",
        ]
        assert [claim["id"] for claim in claims] == [
            "root",
            "category",
            "chain-1",
            "chain-2",
            "chain-3",
        ]
        assert claims[0]["weight"] == 100
        assert claims[1]["weight"] == 20
        assert abs(sum(claim["weight"] for claim in claims[2:]) - 60) < 0.001
        assert (
            max(claim["weight"] for claim in claims[2:])
            - min(claim["weight"] for claim in claims[2:])
            < 0.01
        )
        assert claims[2]["evidence_keyword"] == "suspend to mem is timeout"


def test_case_field_question_explains_claim_and_then_asks_one_approval(
    tmp_path: Path,
) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/case-drafts",
            json={"payload": case_payload("important"), "case_key": "1"},
        )
        assert created.status_code == 201
        question = created.json()["questions"][0]
        assert question["field_path"] == "eval_spec_draft.claims[0].importance"
        assert "系统因 suspend-to-mem 超时触发 panic" in question["question"]
        assert "漏掉该结论的严重程度" in question["question"]

        corrected = client.post(
            f"/api/v1/case-drafts/{created.json()['id']}/answers",
            json={"answers": [{"question_id": question["id"], "value": "normal"}]},
        )
        assert corrected.status_code == 200
        questions = corrected.json()["questions"]
        assert len(questions) == 1
        assert questions[0]["code"] == "approve_case"


def test_published_case_is_reused_for_multi_report_batch(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/case-drafts",
            json={"payload": case_payload(), "case_key": "1"},
        ).json()
        assert len(created["questions"]) == 1
        assert created["questions"][0]["code"] == "approve_case"
        ready = client.post(
            f"/api/v1/case-drafts/{created['id']}/answers",
            json={
                "answers": [
                    {
                        "question_id": created["questions"][0]["id"],
                        "value": "approved",
                    }
                ]
            },
        ).json()
        assert ready["status"] == "ready"
        published = client.post(f"/api/v1/case-drafts/{created['id']}:publish")
        assert published.status_code == 200
        assert published.json()["status"] == "published"
        assert client.get("/api/v1/benchmark-cases").json()[0]["case_key"] == "1"

        batch = client.post(
            "/api/v1/evaluation-batches",
            json={
                "case_key": "1",
                "judge_runner": "lexical",
                "raw_reports": [
                    {
                        "filename": "HM_PANIC_SYSMGR-test1-agent-1.md",
                        "content": (
                            "系统因 suspend-to-mem 超时触发 panic，根因是未执行 REPICK，"
                            "线程可能跑错 CPU 核。"
                        ),
                    },
                    {
                        "filename": "HM_PANIC_SYSMGR-test1-skill-1.txt",
                        "content": "系统发生 suspend-to-mem 超时触发 panic。",
                    },
                ],
            },
        )
        assert batch.status_code == 202
        assert batch.json()["report_count"] == 2

        worker = LocalWorker(settings)
        try:
            assert worker.run_once() is True
            assert worker.run_once() is True
        finally:
            worker.close()

        result = client.get(f"/api/v1/evaluation-batches/{batch.json()['id']}/result").json()
        assert result["status"] == "completed"
        assert result["mode"] == "database"
        assert [item["candidate_name"] for item in result["reports"]] == [
            "HM_PANIC_SYSMGR-test1-agent-1",
            "HM_PANIC_SYSMGR-test1-skill-1",
        ]
        assert all(item["score"] is not None for item in result["reports"])
        assert result["reports"][1]["warnings"] == []
        assert len(result["comparisons"]) == 1
        assert result["comparisons"][0]["baseline"] == "HM_PANIC_SYSMGR-test1-agent-1"
        assert result["comparisons"][0]["candidate"] == "HM_PANIC_SYSMGR-test1-skill-1"
        assert set(result["summary"]["ranking"]) == {
            "HM_PANIC_SYSMGR-test1-agent-1",
            "HM_PANIC_SYSMGR-test1-skill-1",
        }
        ranked_scores = [
            next(
                item["score"]
                for item in result["summary"]["reports"]
                if item["candidate_name"] == name
            )
            for name in result["summary"]["ranking"]
        ]
        assert ranked_scores == sorted(ranked_scores, key=float, reverse=True)
        assert result["summary"]["reports"][0]["claim_count"] == 2
        markdown = render_markdown(result["summary"])
        assert "## 总览" in markdown
        assert "评分模式：数据库已发布 Case" in markdown
        assert "Case 版本：1" in markdown
        assert "Eval Spec：" in markdown
        assert "candidate_claims" not in markdown


def test_frontend_raw_conversion_uses_background_runner(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    fake = Path(__file__).parent / "fixtures" / "fake_case_generator.py"
    with TestClient(create_app(settings)) as client:
        generated = client.post(
            "/api/v1/case-drafts:generate",
            json={
                "reference_answer": (
                    "系统因 suspend-to-mem 超时触发 panic。"
                    "根因是未执行 REPICK，线程可能跑错 CPU 核。"
                ),
                "runner": "claude-code",
                "runner_configuration": {
                    "executable": sys.executable,
                    "extra_args": [str(fake)],
                },
                "case_key": "1",
                "test_set": "kernel-log-analysis",
                "category": "panic",
            },
        )
        assert generated.status_code == 202
        assert generated.json()["status"] == "generating"

        worker = LocalWorker(settings)
        try:
            assert worker.run_once() is True
        finally:
            worker.close()

        draft = client.get(f"/api/v1/case-drafts/{generated.json()['id']}").json()
        assert draft["status"] == "needs_confirmation"
        assert draft["case_key"] == "1"
        assert draft["questions"][0]["code"] == "approve_case"

        converted = client.post(
            "/api/v1/report-drafts:convert",
            json={
                "candidate_name": "raw-report",
                "candidate_report": "完整 AI 报告原文",
            },
        )
        assert converted.status_code == 201
        assert converted.json()["status"] == "ready"
        assert converted.json()["issues"] == []


def test_case_hierarchy_is_persisted_in_one_test_set(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        first_payload = case_payload()
        first_payload["case"]["traces"] = [
            {
                "trace_key": "panic-log",
                "filename": "panic.log",
                "content": "suspend to mem is timeout",
                "metadata": {"kind": "kernel-log"},
            }
        ]
        first = client.post(
            "/api/v1/case-drafts",
            json={
                "payload": first_payload,
                "case_key": "HM_PANIC_SYSMGR-case1",
                "source_filename": "HM_PANIC_SYSMGR-case1.json",
                "test_set": "kernel-log-analysis",
                "category": "panic",
            },
        ).json()
        assert first["case_key"] == "HM_PANIC_SYSMGR-case1"
        ready = client.post(
            f"/api/v1/case-drafts/{first['id']}/answers",
            json={"answers": [{"question_id": first["questions"][0]["id"], "value": "approved"}]},
        ).json()
        first_published = client.post(f"/api/v1/case-drafts/{ready['id']}:publish").json()

        second_payload = copy.deepcopy(case_payload())
        second = client.post(
            "/api/v1/case-drafts",
            json={
                "payload": second_payload,
                "case_key": "HM_LOWDOG-case2",
                "source_filename": "HM_LOWDOG-case2.json",
                "test_set": "kernel-log-analysis",
                "category": "lowdog",
            },
        ).json()
        ready = client.post(
            f"/api/v1/case-drafts/{second['id']}/answers",
            json={"answers": [{"question_id": second["questions"][0]["id"], "value": "approved"}]},
        ).json()
        second_published = client.post(f"/api/v1/case-drafts/{ready['id']}:publish").json()

        assert (
            first_published["resources"]["test_set"]["id"]
            == (second_published["resources"]["test_set"]["id"])
        )
        assert second_published["case_key"] == "HM_LOWDOG-case2"
        session_factory = client.app.state.case_library_service.session_factory
        with session_factory() as session:
            version = session.get(
                DatasetVersion, second_published["resources"]["dataset_version_id"]
            )
            assert version is not None
            assert len(json.loads(version.case_revision_ids_json)) == 2
            stored_case = session.scalar(
                select(Case).where(Case.case_key == "HM_PANIC_SYSMGR-case1")
            )
            assert stored_case is not None
            assert stored_case.source_filename == "HM_PANIC_SYSMGR-case1.json"
            traces = list(
                session.scalars(
                    select(CaseTrace).where(
                        CaseTrace.case_revision_id
                        == first_published["resources"]["case_revision_id"]
                    )
                )
            )
            assert [trace.trace_key for trace in traces] == ["panic-log"]


def test_published_case_can_be_reorganized_without_rechecking_claims(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/case-drafts",
            json={"payload": case_payload(), "case_key": "1"},
        ).json()
        ready = client.post(
            f"/api/v1/case-drafts/{created['id']}/answers",
            json={"answers": [{"question_id": created["questions"][0]["id"], "value": "approved"}]},
        ).json()
        assert client.post(f"/api/v1/case-drafts/{ready['id']}:publish").status_code == 200

        organized = client.post(
            "/api/v1/benchmark-cases/1:organize",
            json={
                "source_filename": "panic-repick.json",
                "case_key": "panic-repick",
                "test_set": "kernel-log-analysis-v2",
                "category": "panic",
            },
        )
        assert organized.status_code == 200
        assert organized.json()["case_key"] == "panic-repick"
        published = client.get("/api/v1/benchmark-cases").json()
        assert [item["case_key"] for item in published] == ["panic-repick"]


def test_same_case_can_publish_a_new_revision_from_an_approved_draft(tmp_path: Path) -> None:
    settings = migrated_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        first = client.post(
            "/api/v1/case-drafts",
            json={"payload": case_payload(), "case_key": "1"},
        ).json()
        first_ready = client.post(
            f"/api/v1/case-drafts/{first['id']}/answers",
            json={"answers": [{"question_id": first["questions"][0]["id"], "value": "approved"}]},
        ).json()
        first_published = client.post(f"/api/v1/case-drafts/{first_ready['id']}:publish").json()

        changed = case_payload()
        changed["case"]["problem_statement"] = "分析更新后的 suspend 超时问题。"
        second = client.post(
            "/api/v1/case-drafts",
            json={"payload": changed, "case_key": "1"},
        ).json()
        second_ready = client.post(
            f"/api/v1/case-drafts/{second['id']}/answers",
            json={"answers": [{"question_id": second["questions"][0]["id"], "value": "approved"}]},
        ).json()
        replaced = client.post(f"/api/v1/case-drafts/{second_ready['id']}:publish")
        assert replaced.status_code == 200
        view = replaced.json()
        assert view["resources"]["case_version"] == 2
        assert view["resources"]["case_id"] == first_published["resources"]["case_id"]
        service = client.app.state.case_library_service
        assert [item.case_key for item in service.list_published()] == ["1"]
