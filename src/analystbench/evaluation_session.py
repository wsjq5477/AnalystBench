"""Single-entry draft review and Benchmark orchestration."""

import copy
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from analystbench.benchmark import BenchmarkService
from analystbench.content_store import ContentStore, canonical_json
from analystbench.db.models import EvaluationSession
from analystbench.errors import AnalystBenchError
from analystbench.eval_spec import EvalSpecService
from analystbench.services import CatalogService, NotFoundError, transaction

CORE_TYPES = {
    "trigger",
    "symptom",
    "localization",
    "root_cause",
    "mechanism",
    "impact",
    "evidence",
    "classification",
    "analysis_chain",
    "action",
}
IMPORTANCE = {"critical", "high", "normal", "low"}
IMPORTANCE_SUGGESTIONS = {"important": "normal", "supporting": "low"}


class EvaluationSessionService:
    def __init__(self, session_factory: sessionmaker[Session], content_store: ContentStore) -> None:
        self.session_factory = session_factory
        self.content_store = content_store
        self.catalog = CatalogService(session_factory, content_store)
        self.eval_specs = EvalSpecService(session_factory, content_store)
        self.benchmarks = BenchmarkService(session_factory, content_store)

    def create_session(
        self, case_draft: dict[str, Any], report_drafts: list[dict[str, Any]]
    ) -> EvaluationSession:
        if not isinstance(case_draft, dict) or not isinstance(report_drafts, list):
            raise AnalystBenchError(
                "draft_invalid", "case_draft must be an object and report_drafts must be an array"
            )
        if not report_drafts or not all(isinstance(item, dict) for item in report_drafts):
            raise AnalystBenchError(
                "draft_invalid", "at least one AI report draft object is required"
            )
        working = {
            "case_draft": copy.deepcopy(case_draft),
            "report_drafts": copy.deepcopy(report_drafts),
        }
        questions = self._questions(working)
        item = EvaluationSession(
            id=str(uuid4()),
            status="needs_confirmation" if self._required(questions) else "preparing",
            case_draft_json=canonical_json(case_draft),
            report_drafts_json=canonical_json(report_drafts),
            working_json=canonical_json(working),
            questions_json=canonical_json(questions),
        )
        with transaction(self.session_factory) as session:
            session.add(item)
            session.flush()
            item_id = item.id
        if not self._required(questions):
            self._finalize(item_id)
        return self.get_session(item_id)

    def get_session(self, session_id: str, refresh: bool = True) -> EvaluationSession:
        if refresh:
            self._refresh_status(session_id)
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSession, session_id)
            if item is None:
                raise NotFoundError("evaluation_session", session_id)
            session.expunge(item)
            return item

    def submit_answers(self, session_id: str, submitted: list[dict[str, Any]]) -> EvaluationSession:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSession, session_id)
            if item is None:
                raise NotFoundError("evaluation_session", session_id)
            if item.status != "needs_confirmation":
                raise AnalystBenchError(
                    "invalid_state", "evaluation session is not waiting for confirmation"
                )
            working = json.loads(item.working_json)
            questions = {question["id"]: question for question in json.loads(item.questions_json)}
            audit = json.loads(item.answers_json)
            for answer in submitted:
                question_id = str(answer.get("question_id", ""))
                question = questions.get(question_id)
                if question is None or not question.get("required"):
                    raise AnalystBenchError(
                        "answer_invalid",
                        "answer references an unknown or non-required question",
                        [{"question_id": question_id}],
                    )
                self._apply_answer(working, question, answer.get("value"))
                audit.append({"question_id": question_id, "value": answer.get("value")})
            next_questions = self._questions(working)
            item.working_json = canonical_json(working)
            item.questions_json = canonical_json(next_questions)
            item.answers_json = canonical_json(audit)
            item.status = "needs_confirmation" if self._required(next_questions) else "preparing"
        if not self._required(next_questions):
            self._finalize(session_id)
        return self.get_session(session_id)

    def result(self, session_id: str) -> dict[str, Any]:
        item = self.get_session(session_id)
        resources = json.loads(item.resources_json)
        runs = []
        for entry in resources.get("runs", []):
            run = self.benchmarks.get_run(entry["run_id"])
            exported = (
                self.benchmarks.export_run(run.id)
                if run.status in {"completed", "completed_with_errors", "failed", "cancelled"}
                else None
            )
            runs.append(
                {
                    **entry,
                    "status": run.status,
                    "summary": json.loads(run.summary_json),
                    "result": exported,
                }
            )
        return {"id": item.id, "status": item.status, "runs": runs}

    def process_pending(self, session_id: str) -> dict[str, Any]:
        """Synchronously process only this session's pending Case Runs for local clients."""
        item = self.get_session(session_id)
        if item.status not in {"queued", "running"}:
            return self.result(session_id)
        resources = json.loads(item.resources_json)
        for entry in resources.get("runs", []):
            for case_run in self.benchmarks.list_case_runs(entry["run_id"]):
                if case_run.status == "pending":
                    self.benchmarks.execute_case_run(case_run.id)
        return self.result(session_id)

    @staticmethod
    def view(item: EvaluationSession) -> dict[str, Any]:
        questions = json.loads(item.questions_json)
        return {
            "id": item.id,
            "status": item.status,
            "questions": questions,
            "required_questions": [q for q in questions if q.get("required")],
            "warnings": [q for q in questions if not q.get("required")],
            "resources": json.loads(item.resources_json),
            "error": json.loads(item.error_json),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def _questions(self, working: dict[str, Any]) -> list[dict[str, Any]]:
        questions: list[dict[str, Any]] = []
        wrapper = working.get("case_draft")
        if not isinstance(wrapper, dict):
            return [self._question("case_draft", "missing_field", "请提供 Case 草稿对象。")]
        case = wrapper.get("case")
        draft = wrapper.get("eval_spec_draft")
        if not isinstance(case, dict):
            questions.append(self._question("case", "missing_field", "请确认 Case 基本信息。"))
            case = {}
        if not isinstance(draft, dict):
            questions.append(
                self._question("eval_spec_draft", "missing_field", "请确认评分规范草稿。")
            )
            draft = {}
        for field, label in (
            ("case_key", "用例编号"),
            ("problem_statement", "问题描述"),
            ("reference_answer", "人工标准答案"),
        ):
            if not isinstance(case.get(field), str) or not case.get(field, "").strip():
                questions.append(
                    self._question(f"case.{field}", "missing_field", f"请确认{label}。")
                )
        reference = case.get("reference_answer", "")
        claims = draft.get("claims")
        if not isinstance(claims, list) or not claims:
            questions.append(
                self._question(
                    "eval_spec_draft.claims", "missing_field", "至少需要一个评分 Claim。"
                )
            )
            claims = []
        weights_valid = True
        chain_index = 0
        claim_index = 0
        for index, claim in enumerate(claims):
            base = f"eval_spec_draft.claims[{index}]"
            if not isinstance(claim, dict):
                questions.append(self._question(base, "invalid_type", "Claim 必须是对象。"))
                weights_valid = False
                continue
            claim_type = claim.get("type")
            if claim_type == "root_cause":
                expected_id = "root"
            elif claim_type == "classification":
                expected_id = "category"
            elif claim_type == "analysis_chain":
                chain_index += 1
                expected_id = f"chain-{chain_index}"
            else:
                claim_index += 1
                expected_id = f"claim-{claim_index}"
            if claim.get("id") != expected_id:
                questions.append(
                    self._question(
                        f"{base}.id",
                        "invalid_id",
                        f"Claim ID 应为 {expected_id}，是否接受？",
                        claim.get("id"),
                        expected_id,
                        [expected_id],
                    )
                )
            if claim.get("type") not in CORE_TYPES:
                questions.append(
                    self._question(
                        f"{base}.type",
                        "invalid_enum",
                        "请选择该 Claim 的类型。",
                        claim.get("type"),
                        None,
                        sorted(CORE_TYPES),
                    )
                )
            if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
                questions.append(
                    self._question(f"{base}.statement", "missing_field", "请确认 Claim 结论。")
                )
            if claim_type == "analysis_chain":
                if not isinstance(claim.get("evidence_keyword"), str) or not claim[
                    "evidence_keyword"
                ].strip():
                    questions.append(
                        self._question(
                            f"{base}.evidence_keyword",
                            "missing_field",
                            "请提供该分析链的完整日志关键字；评分时只做强匹配。",
                        )
                    )
                if not isinstance(claim.get("conclusion"), str) or not claim["conclusion"].strip():
                    questions.append(
                        self._question(
                            f"{base}.conclusion",
                            "missing_field",
                            "请提供该分析链的标准结论；评分时由语义 Judge 比较。",
                        )
                    )
            importance = claim.get("importance")
            if importance not in IMPORTANCE:
                suggestion = IMPORTANCE_SUGGESTIONS.get(str(importance))
                questions.append(
                    self._question(
                        f"{base}.importance",
                        "invalid_enum",
                        "请选择该 Claim 的重要性。",
                        importance,
                        suggestion,
                        ["critical", "high", "normal", "low"],
                    )
                )
            weight = claim.get("weight")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                weights_valid = False
                questions.append(
                    self._question(f"{base}.weight", "invalid_weight", "请输入正整数权重。")
                )
            quote = claim.get("quote")
            if not isinstance(quote, str) or not quote or quote not in reference:
                questions.append(
                    self._question(
                        f"{base}.quote",
                        "quote_mismatch",
                        "该引用不在人工标准答案原文中，请提供连续原文。",
                        quote,
                    )
                )
            if claim.get("review_required") is not False:
                review_text = (
                    f"请确认评分项 {claim.get('id', expected_id)}：{claim.get('statement', '')}"
                )
                questions.append(
                    self._question(
                        f"{base}.review_required",
                        "review_required",
                        review_text,
                        True,
                        "confirmed",
                        ["confirmed"],
                    )
                )
        edges = draft.get("causal_edges", [])
        if isinstance(edges, list):
            for index, edge in enumerate(edges):
                if isinstance(edge, dict) and edge.get("review_required") is not False:
                    questions.append(
                        self._question(
                            f"eval_spec_draft.causal_edges[{index}].review_required",
                            "review_required",
                            f"请确认因果边 {edge.get('id', f'e{index + 1}')}。",
                            True,
                            "confirmed",
                            ["confirmed"],
                        )
                    )
        strategy = draft.get("scoring_strategy", {"mode": "weighted_sum"})
        root_category_chain = (
            isinstance(strategy, dict) and strategy.get("mode") == "root_category_chain"
        )
        if root_category_chain:
            expected = {"mode": "root_category_chain", "root_cause_score": 100,
                        "category_score": 20, "chain_total_score": 60}
            roots = [claim for claim in claims if claim.get("type") == "root_cause"]
            categories = [claim for claim in claims if claim.get("type") == "classification"]
            chains = [claim for claim in claims if claim.get("type") == "analysis_chain"]
            valid = (
                strategy == expected
                and len(roots) == 1
                and roots[0].get("weight") == 100
                and len(categories) == 1
                and categories[0].get("weight") == 20
                and bool(chains)
                and abs(sum(float(item.get("weight", 0)) for item in chains) - 60) <= 0.01
            )
            if not valid:
                questions.append(
                    self._question(
                        "eval_spec_draft.scoring_strategy",
                        "root_category_chain_spec",
                        "根因/分类/分析链规则要求：根因100分直通；否则分类20分，"
                        "分析链总分60按条数等分。",
                        strategy,
                        expected,
                        [expected],
                    )
                )
        elif weights_valid and claims:
            edge_weight = (
                sum(
                    edge.get("weight", 0)
                    for edge in edges
                    if isinstance(edge, dict) and isinstance(edge.get("weight"), int)
                )
                if isinstance(edges, list)
                else 0
            )
            claim_weight = sum(claim["weight"] for claim in claims if isinstance(claim, dict))
            if claim_weight + edge_weight != 100:
                questions.append(
                    self._question(
                        "eval_spec_draft.claims",
                        "weights_total",
                        "Claim 与因果边权重合计必须为 100，请按 Claim ID 提供新权重。",
                    )
                )
        root_claims = [
            claim
            for claim in claims
            if isinstance(claim, dict) and claim.get("type") == "root_cause"
        ]
        if claims and not any(claim.get("importance") == "critical" for claim in root_claims):
            root_ids = [claim.get("id") for claim in root_claims if claim.get("id")]
            questions.append(
                self._question(
                    "eval_spec_draft.claims",
                    "critical_root_cause",
                    "请选择一个 Claim 作为 critical root_cause。",
                    None,
                    root_ids[0] if root_ids else None,
                    root_ids,
                )
            )
        unresolved = draft.get("unresolved_items", [])
        if isinstance(unresolved, list):
            for index, text in enumerate(unresolved):
                questions.append(
                    self._question(
                        f"eval_spec_draft.unresolved_items[{index}]",
                        "unresolved_item",
                        f"该未决项如何处理：{text}",
                        text,
                        "exclude_from_spec",
                        ["resolved", "exclude_from_spec"],
                    )
                )
        reports = working.get("report_drafts", [])
        for report_index, report in enumerate(reports):
            report_path = f"report_drafts[{report_index}]"
            candidate = report.get("candidate") if isinstance(report, dict) else None
            if not isinstance(candidate, dict) or not isinstance(candidate.get("name"), str):
                questions.append(
                    self._question(
                        f"{report_path}.candidate.name",
                        "missing_field",
                        "请确认候选模型、Agent 或 Prompt 版本名称。",
                        None,
                        f"candidate-{report_index + 1}",
                    )
                )
            text = report.get("candidate_report") if isinstance(report, dict) else None
            if not isinstance(text, str) or not text.strip():
                questions.append(
                    self._question(
                        f"{report_path}.candidate_report",
                        "missing_field",
                        "请提供 AI 报告原文。",
                    )
                )
                continue
            hints = report.get("claim_hints", [])
            if isinstance(hints, list):
                for hint_index, hint in enumerate(hints):
                    quote = hint.get("quote") if isinstance(hint, dict) else None
                    if not isinstance(quote, str) or quote not in text:
                        questions.append(
                            self._question(
                                f"{report_path}.claim_hints[{hint_index}].quote",
                                "candidate_hint_quote_mismatch",
                                "候选 Claim 提示引用不是报告连续原文；评分会从报告原文重新抽取。",
                                quote,
                                required=False,
                            )
                        )
        return questions

    @staticmethod
    def _question(
        field_path: str,
        code: str,
        question: str,
        current_value: Any = None,
        suggested_value: Any = None,
        options: list[Any] | None = None,
        required: bool = True,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(f"{code}:{field_path}:{question}".encode()).hexdigest()[:12]
        return {
            "id": f"q-{digest}",
            "field_path": field_path,
            "code": code,
            "question": question,
            "current_value": current_value,
            "suggested_value": suggested_value,
            "options": options or [],
            "required": required,
        }

    @staticmethod
    def _required(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [question for question in questions if question.get("required")]

    def _apply_answer(self, working: dict[str, Any], question: dict[str, Any], value: Any) -> None:
        options = question.get("options", [])
        if options and value not in options:
            raise AnalystBenchError(
                "answer_invalid",
                "answer is not one of the allowed options",
                [{"field_path": question["field_path"], "options": options}],
            )
        code = question["code"]
        if code == "review_required":
            self._set_path(working["case_draft"], question["field_path"], False)
            return
        if code == "unresolved_item":
            items = working["case_draft"]["eval_spec_draft"]["unresolved_items"]
            current = question["current_value"]
            if current in items:
                items.remove(current)
            return
        if code == "weights_total":
            if not isinstance(value, dict):
                raise AnalystBenchError(
                    "answer_invalid", "weight answer must map Claim IDs to weights"
                )
            claims = working["case_draft"]["eval_spec_draft"]["claims"]
            for claim in claims:
                if claim.get("id") in value:
                    claim["weight"] = value[claim["id"]]
            return
        if code == "critical_root_cause":
            claims = working["case_draft"]["eval_spec_draft"]["claims"]
            selected = next((claim for claim in claims if claim.get("id") == value), None)
            if selected is None:
                raise AnalystBenchError("answer_invalid", "selected Claim ID does not exist")
            selected["type"] = "root_cause"
            selected["importance"] = "critical"
            return
        target = (
            working if question["field_path"].startswith("report_drafts") else working["case_draft"]
        )
        self._set_path(target, question["field_path"], value)

    @staticmethod
    def _set_path(root: Any, path: str, value: Any) -> None:
        parts: list[str | int] = []
        for name, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path):
            parts.append(int(index) if index else name)
        node = root
        for position, part in enumerate(parts[:-1]):
            if isinstance(part, int):
                node = node[part]
                continue
            if part not in node or node[part] is None:
                next_part = parts[position + 1]
                node[part] = [] if isinstance(next_part, int) else {}
            node = node[part]
        node[parts[-1]] = value

    def _finalize(self, session_id: str) -> None:
        item = self.get_session(session_id, refresh=False)
        working = json.loads(item.working_json)
        try:
            case = working["case_draft"]["case"]
            draft = working["case_draft"]["eval_spec_draft"]
            prefix = session_id[:8]
            dataset = self.catalog.create_dataset(
                f"evaluation-{prefix}-{case['case_key']}"[:255],
                "Created by an AnalystBench Evaluation Session.",
            )
            revision = self.catalog.create_case_revision(
                dataset.id,
                case["case_key"],
                case["problem_statement"],
                case["reference_answer"],
            )
            dataset_version = self.catalog.freeze_dataset_version(dataset.id, [revision.id])
            policy = self.eval_specs.create_scoring_policy(f"evaluation-{prefix}-v1")
            reference = case["reference_answer"]
            formal_claims = []
            for claim in draft["claims"]:
                start = reference.index(claim["quote"])
                formal_claims.append(
                    {
                        "id": claim["id"],
                        "type": claim["type"],
                        "statement": claim["statement"],
                        "importance": claim["importance"],
                        "weight": claim["weight"],
                        "source_ref": {
                            "content_hash": revision.reference_answer_content_hash,
                            "start": start,
                            "end": start + len(claim["quote"]),
                            "quote": claim["quote"],
                        },
                        "review_required": False,
                        "notes": claim.get("notes"),
                        "evidence_keyword": claim.get("evidence_keyword"),
                        "conclusion": claim.get("conclusion"),
                    }
                )
            formal_edges = [
                {**edge, "review_required": False} for edge in draft.get("causal_edges", [])
            ]
            spec_payload = {
                "schema_version": "1.0",
                "case_revision_id": revision.id,
                "suite": {"id": dataset.dataset_key, "version": "1.0.0"},
                "claims": formal_claims,
                "causal_edges": formal_edges,
                "forbidden_claims": draft.get("forbidden_claims", []),
                "scoring_policy_version_id": policy.id,
                "review": {"status": "approved", "unresolved_items": []},
            }
            spec_draft = self.eval_specs.create_draft(revision.id, spec_payload)
            spec_version = self.eval_specs.freeze_draft(spec_draft.id)
            runs = []
            for index, report in enumerate(working["report_drafts"], 1):
                candidate_payload = report.get("candidate", {})
                candidate_name = str(candidate_payload.get("name") or f"candidate-{index}")
                candidate = self.catalog.create_candidate(
                    f"{candidate_name} [{prefix}-{index}]"[:255],
                    str(candidate_payload.get("description", "")),
                )
                version = self.catalog.create_candidate_version(
                    candidate.id, dict(candidate_payload.get("metadata", {}))
                )
                self.catalog.import_candidate_reports(
                    version.id,
                    [{"case_revision_id": revision.id, "report": report["candidate_report"]}],
                )
                run = self.benchmarks.create_run(dataset_version.id, version.id, policy.id)
                runs.append(
                    {
                        "candidate_name": candidate_name,
                        "candidate_version_id": version.id,
                        "run_id": run.id,
                    }
                )
            resources = {
                "dataset_version_id": dataset_version.id,
                "case_revision_id": revision.id,
                "scoring_policy_version_id": policy.id,
                "eval_spec_version_id": spec_version.id,
                "runs": runs,
            }
            with transaction(self.session_factory) as session:
                stored = session.get(EvaluationSession, session_id)
                assert stored is not None
                stored.resources_json = canonical_json(resources)
                stored.status = "queued"
                stored.error_json = "{}"
        except Exception as exc:
            with transaction(self.session_factory) as session:
                stored = session.get(EvaluationSession, session_id)
                assert stored is not None
                stored.status = "failed"
                stored.error_json = canonical_json(
                    {"code": getattr(exc, "code", "preparation_failed"), "message": str(exc)}
                )

    def _refresh_status(self, session_id: str) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationSession, session_id)
            if item is None or item.status not in {"queued", "running"}:
                return
            resources = json.loads(item.resources_json)
        statuses = [
            self.benchmarks.get_run(run["run_id"]).status for run in resources.get("runs", [])
        ]
        if not statuses:
            return
        if all(status == "completed" for status in statuses):
            status = "completed"
        elif all(
            status in {"completed", "completed_with_errors", "failed", "cancelled"}
            for status in statuses
        ):
            status = "failed" if all(value == "failed" for value in statuses) else "completed"
        elif any(status == "running" for status in statuses):
            status = "running"
        else:
            status = "queued"
        with transaction(self.session_factory) as session:
            stored = session.get(EvaluationSession, session_id)
            if stored is not None:
                stored.status = status
