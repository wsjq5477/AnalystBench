"""Reusable Case publishing and multi-report evaluation workflows."""

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from analystbench.agent_runner import AgentRunnerError, create_runner
from analystbench.benchmark import BenchmarkService
from analystbench.comparison import ComparisonService
from analystbench.config import Settings
from analystbench.content_store import ContentStore, canonical_json
from analystbench.db.models import (
    AgentCaseRun,
    BenchmarkCaseRun,
    BenchmarkRun,
    CandidateGenerationRun,
    CandidateReport,
    CaseDraft,
    CaseRevision,
    CaseTrace,
    DatasetVersion,
    EvalSpecDraft,
    EvalSpecVersion,
    EvaluationBatch,
    ReportDraft,
)
from analystbench.errors import AnalystBenchError
from analystbench.eval_spec import EvalSpecService, EvalSpecV1
from analystbench.jobs import JobQueue
from analystbench.reporting import build_human_summary
from analystbench.services import CatalogService, ConflictError, NotFoundError, transaction

CORE_TYPES = {
    "trigger",
    "symptom",
    "localization",
    "root_cause",
    "mechanism",
    "impact",
    "evidence",
    "action",
    "classification",
    "analysis_chain",
}
IMPORTANCE = {"critical", "high", "normal", "low"}
IMPORTANCE_SUGGESTIONS = {"important": "normal", "supporting": "low"}
ROOT_CATEGORY_CHAIN_STRATEGY = {
    "mode": "root_category_chain",
    "root_cause_score": 100,
    "category_score": 20,
    "chain_total_score": 60,
}

REPORT_FILENAME_PATTERN = re.compile(
    r"^(?P<case_key>.+)-test(?P<test_index>\d+)-"
    r"(?P<run_type>native|skill|agent)-(?P<attempt>\d+)$",
    flags=re.IGNORECASE,
)


def report_metadata_from_filename(filename: str) -> dict[str, Any]:
    """Infer optional comparison metadata from the documented report filename."""
    stem = Path(filename).stem
    metadata: dict[str, Any] = {"source_filename": Path(filename).name}
    match = REPORT_FILENAME_PATTERN.fullmatch(stem)
    if match is not None:
        metadata.update(
            {
                "case_key_hint": match.group("case_key"),
                "test_index": int(match.group("test_index")),
                "run_type": match.group("run_type").lower(),
                "attempt": int(match.group("attempt")),
            }
        )
    return metadata


def report_payload_from_text(
    filename: str,
    report: str,
    candidate_name: str | None = None,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap report text internally without asking the user to convert it to JSON."""
    inferred = report_metadata_from_filename(filename)
    inferred.update(metadata or {})
    return {
        "candidate": {
            "name": candidate_name or Path(filename).stem,
            "description": description,
            "metadata": inferred,
        },
        "candidate_report": report,
        "claim_hints": [],
        "unresolved_items": [],
    }


def ensure_scoring_spec_supported(case_key: str, payload: dict[str, Any]) -> None:
    """Fail early with an actionable message for an obsolete published Case."""
    try:
        EvalSpecV1.model_validate(payload)
    except ValidationError as exc:
        strategy = payload.get("scoring_strategy")
        mode = strategy.get("mode") if isinstance(strategy, dict) else None
        if mode == "root_or_chain":
            raise AnalystBenchError(
                "case_scoring_strategy_obsolete",
                f"已发布 Case「{case_key}」仍使用旧评分策略 root_or_chain，"
                "不能用当前评分器执行。请用新格式 Case JSON 重新导入并发布，"
                "然后使用新发布的 case_key 评分。",
                [{"case_key": case_key, "current_mode": mode}],
            ) from exc
        raise AnalystBenchError(
            "case_scoring_spec_invalid",
            f"已发布 Case「{case_key}」的评分规范与当前程序不兼容，请重新导入并发布。",
            [{"case_key": case_key, "validation_errors": exc.errors()}],
        ) from exc


def _question(
    field_path: str,
    code: str,
    question: str,
    current_value: Any = None,
    suggested_value: Any = None,
    options: list[Any] | None = None,
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
        "required": True,
    }


def _set_path(root: Any, path: str, value: Any) -> None:
    parts: list[str | int] = []
    for name, index in re.findall(r"([^.[\]]+)|\[(\d+)\]", path):
        parts.append(int(index) if index else name)
    node = root
    for position, part in enumerate(parts[:-1]):
        if isinstance(part, int):
            node = node[part]
            continue
        if part not in node or node[part] is None:
            node[part] = [] if isinstance(parts[position + 1], int) else {}
        node = node[part]
    node[parts[-1]] = value


def _normalize_structured_reference(payload: dict[str, Any]) -> None:
    """Normalize `分类 + 根因 + 证据N/结论N` into fixed scoring components."""
    case = payload.get("case")
    draft = payload.get("eval_spec_draft")
    if not isinstance(case, dict) or not isinstance(draft, dict):
        return
    reference = case.get("reference_answer")
    if not isinstance(reference, str):
        return
    category_match = re.search(r"^问题分类[：:]\s*(.+)$", reference, flags=re.MULTILINE)
    root_match = re.search(r"^(?:问题)?根因[：:]\s*(.+)$", reference, flags=re.MULTILINE)
    pair_pattern = re.compile(
        r"^(?:日志|证据)(?P<number>\d+)[：:](?P<log>.*?)\r?\n"
        r"结论(?P=number)[：:](?P<conclusion>.*?)"
        r"(?=\r?\n(?:日志|证据)\d+[：:]|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    pairs = list(pair_pattern.finditer(reference))
    if category_match is None or root_match is None or not pairs:
        return
    category = category_match.group(1).strip()
    if not isinstance(case.get("category"), str):
        case["category"] = category
    claims: list[dict[str, Any]] = [
        {
            "id": "root",
            "type": "root_cause",
            "statement": root_match.group(1).strip(),
            "importance": "critical",
            "weight": 100,
            "quote": root_match.group(1).strip(),
            "review_required": True,
        },
        {
            "id": "category",
            "type": "classification",
            "statement": category,
            "importance": "high",
            "weight": 20,
            "quote": category,
            "review_required": True,
        },
    ]
    chain_weight = round(ROOT_CATEGORY_CHAIN_STRATEGY["chain_total_score"] / len(pairs), 6)
    for index, match in enumerate(pairs, 1):
        keyword = match.group("log").strip()
        conclusion = match.group("conclusion").strip()
        weight = (
            round(
                ROOT_CATEGORY_CHAIN_STRATEGY["chain_total_score"]
                - chain_weight * (len(pairs) - 1),
                6,
            )
            if index == len(pairs)
            else chain_weight
        )
        claims.append(
            {
                "id": f"chain-{index}",
                "type": "analysis_chain",
                "statement": conclusion,
                "importance": "normal",
                "weight": weight,
                "quote": match.group(0).strip(),
                "review_required": True,
                "evidence_keyword": keyword,
                "conclusion": conclusion,
            }
        )
    draft["claims"] = claims
    draft["causal_edges"] = []
    draft["scoring_strategy"] = copy.deepcopy(ROOT_CATEGORY_CHAIN_STRATEGY)


class CaseLibraryService:
    """Validate a Case draft once and publish it as immutable benchmark resources."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        content_store: ContentStore,
        settings: Settings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.content_store = content_store
        self.settings = settings
        self.catalog = CatalogService(session_factory, content_store)
        self.eval_specs = EvalSpecService(session_factory, content_store)
        self.jobs = JobQueue(session_factory)

    def create_generation(
        self,
        reference_answer: str,
        problem_statement: str = "",
        case_key: str | None = None,
        runner_id: str = "claude-code",
        runner_configuration: dict[str, Any] | None = None,
        source_filename: str | None = None,
        test_set: str | None = None,
        category: str | None = None,
    ) -> CaseDraft:
        """Queue raw reference-answer conversion for a frontend or agent client."""
        if self.settings is None:
            raise AnalystBenchError("configuration_error", "generation settings are unavailable")
        if not reference_answer.strip():
            raise AnalystBenchError("draft_invalid", "reference_answer cannot be empty")
        source = {
            "reference_answer": reference_answer,
            "problem_statement": problem_statement,
            "case_key": case_key,
            "runner_id": runner_id,
            "runner_configuration": runner_configuration or {},
            "source_filename": source_filename,
            "test_set": test_set,
            "category": category,
        }
        item = CaseDraft(
            id=str(uuid4()),
            case_key=case_key,
            source_filename=source_filename,
            dataset_key=test_set,
            category_key=category,
            status="generating",
            original_json=canonical_json(source),
            working_json="{}",
            questions_json="[]",
        )
        with transaction(self.session_factory) as session:
            session.add(item)
            session.flush()
            self.jobs.enqueue(session, "case_draft_generate", {"case_draft_id": item.id})
            session.expunge(item)
        return item

    def execute_generation(self, draft_id: str) -> None:
        """Run one queued Claude/OpenCode conversion job."""
        if self.settings is None:
            raise AnalystBenchError("configuration_error", "generation settings are unavailable")
        item = self.get_draft(draft_id)
        if item.status != "generating":
            return
        source = json.loads(item.original_json)
        workspace = self.settings.workspace_root_path / f"case-draft-{draft_id}"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "reference-answer.txt").write_text(
            source["reference_answer"], encoding="utf-8"
        )
        prompt = self._generation_prompt(source, workspace)
        try:
            result = create_runner(source["runner_id"]).execute(
                source.get("runner_configuration", {}), workspace, prompt
            )
            payload = self._parse_generated_json(result.final_report)
            case_payload = payload.setdefault("case", {})
            if isinstance(case_payload, dict):
                case_payload["reference_answer"] = source["reference_answer"]
                # Force-fill case_key and problem_statement from user input (required fields)
                case_payload["case_key"] = source.get("case_key") or case_payload.get("case_key") or ""
                case_payload["problem_statement"] = source.get("problem_statement") or case_payload.get("problem_statement") or ""
                if source.get("test_set"):
                    case_payload["test_set"] = source["test_set"]
                if source.get("category"):
                    case_payload["category"] = source["category"]
            _normalize_structured_reference(payload)
            questions = self._questions(payload)
            case = payload.get("case", {})
            with transaction(self.session_factory) as session:
                stored = session.get(CaseDraft, draft_id)
                assert stored is not None
                stored.case_key = source.get("case_key") or stored.case_key
                stored.source_filename = source.get("source_filename")
                stored.dataset_key = source.get("test_set") if isinstance(source.get("test_set"), str) else None
                stored.category_key = source.get("category") if isinstance(source.get("category"), str) else None
                stored.working_json = canonical_json(payload)
                stored.questions_json = canonical_json(questions)
                stored.status = "needs_confirmation"
                stored.error_json = "{}"
        except Exception as exc:
            with transaction(self.session_factory) as session:
                stored = session.get(CaseDraft, draft_id)
                assert stored is not None
                stored.status = "failed"
                stored.error_json = canonical_json(
                    {"code": getattr(exc, "code", "generation_failed"), "message": str(exc)}
                )
            raise

    @staticmethod
    def _generation_prompt(source: dict[str, Any], workspace: Path) -> str:
        problem = source.get("problem_statement") or "请从标准答案提炼中性问题描述"
        return f"""读取 {workspace / "reference-answer.txt"}，把其中唯一一份人工标准答案
转换为 AnalystBench Case JSON。
只输出一个 JSON 对象，不要 Markdown 或解释。顶层只能有 case 和 eval_spec_draft。
不要在 case 中包含 case_key 字段。
case.problem_statement：{problem}
case.reference_answer 必须逐字保留文件全文。
不要包含 domain 或 tags 字段。
eval_spec_draft.claims 中每项包含 id、type、statement、importance、weight、quote、review_required。
type 只能是 trigger、symptom、localization、root_cause、mechanism、impact、
classification、analysis_chain、action。
importance 只能是 critical、high、normal、low。
若标准答案包含“问题分类”“问题根因”和编号的“证据N/结论N”，必须生成且只生成：
id=root 的 critical root_cause，id=category 的 classification，
以及每组“证据N+结论N”对应的 analysis_chain。
不得把证据和结论拆成两个 Claim，也不得生成 direct_cause。
analysis_chain ID 必须依次为 chain-1、chain-2、chain-3；
每个 analysis_chain 还必须包含 evidence_keyword（证据原文）和 conclusion（结论原文）；
其他通用 Claim 使用 claim-N。
根因 weight=100；分类 weight=20；所有 analysis_chain 的 weight 等分且合计60；causal_edges=[]；
scoring_strategy 固定为 {{"mode":"root_category_chain","root_cause_score":100,
"category_score":20,"chain_total_score":60}}。
quote 必须是标准答案中的连续原文。
同时输出 forbidden_claims、unresolved_items 数组；不确定内容写入 unresolved_items。
所有 Claim 的 review_required 均为 true。"""

    @staticmethod
    def _parse_generated_json(text: str) -> dict[str, Any]:
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, count=1)
            candidate = re.sub(r"\s*```$", "", candidate, count=1)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            if start < 0:
                raise AgentRunnerError(
                    "generated_json_invalid", "agent output contains no JSON object"
                ) from None
            try:
                payload, _ = json.JSONDecoder().raw_decode(candidate[start:])
            except json.JSONDecodeError as exc:
                raise AgentRunnerError(
                    "generated_json_invalid", "agent output is not valid JSON"
                ) from exc
        if not isinstance(payload, dict):
            raise AgentRunnerError("generated_json_invalid", "generated JSON must be an object")
        return payload

    def create_draft(
        self,
        payload: dict[str, Any],
        *,
        case_key: str | None = None,
        source_filename: str | None = None,
        test_set: str | None = None,
        category: str | None = None,
    ) -> CaseDraft:
        if not isinstance(payload, dict):
            raise AnalystBenchError("draft_invalid", "Case draft must be a JSON object")
        working = copy.deepcopy(payload)
        case = working.get("case") if isinstance(working.get("case"), dict) else {}
        effective_case_key = case_key or (
            case.get("case_key") if isinstance(case.get("case_key"), str) else None
        )
        if effective_case_key:
            case["case_key"] = effective_case_key
        if test_set:
            case["test_set"] = test_set
        if category:
            case["category"] = category
        _normalize_structured_reference(working)
        questions = self._questions(working)
        test_set_value = case.get("test_set") if isinstance(case.get("test_set"), str) else None
        category_value = case.get("category") if isinstance(case.get("category"), str) else None
        item = CaseDraft(
            id=str(uuid4()),
            case_key=effective_case_key,
            source_filename=source_filename,
            dataset_key=test_set_value,
            category_key=category_value,
            status="needs_confirmation",
            original_json=canonical_json(payload),
            working_json=canonical_json(working),
            questions_json=canonical_json(questions),
        )
        with transaction(self.session_factory) as session:
            session.add(item)
            session.flush()
            session.expunge(item)
        return item

    def get_draft(self, draft_id: str) -> CaseDraft:
        with transaction(self.session_factory) as session:
            item = session.get(CaseDraft, draft_id)
            if item is None:
                raise NotFoundError("case_draft", draft_id)
            session.expunge(item)
            return item

    def submit_answers(self, draft_id: str, submitted: list[dict[str, Any]]) -> CaseDraft:
        with transaction(self.session_factory) as session:
            item = session.get(CaseDraft, draft_id)
            if item is None:
                raise NotFoundError("case_draft", draft_id)
            if item.status != "needs_confirmation":
                raise AnalystBenchError("invalid_state", "Case draft is not awaiting confirmation")
            working = json.loads(item.working_json)
            questions = {entry["id"]: entry for entry in json.loads(item.questions_json)}
            audit = json.loads(item.answers_json)
            approved = False
            for answer in submitted:
                question_id = str(answer.get("question_id", ""))
                question = questions.get(question_id)
                if question is None:
                    raise AnalystBenchError(
                        "answer_invalid",
                        "answer references an unknown question",
                        [{"question_id": question_id}],
                    )
                value = answer.get("value")
                self._apply_answer(working, question, value)
                audit.append({"question_id": question_id, "value": value})
                approved = approved or question["code"] == "approve_case"
            next_questions = [] if approved else self._questions(working)
            case = working.get("case", {})
            test_set = case.get("test_set") if isinstance(case, dict) else None
            category = case.get("category") if isinstance(case, dict) else None
            item.dataset_key = test_set if isinstance(test_set, str) else None
            item.category_key = category if isinstance(category, str) else None
            item.working_json = canonical_json(working)
            item.questions_json = canonical_json(next_questions)
            item.answers_json = canonical_json(audit)
            item.status = "ready" if approved else "needs_confirmation"
            session.flush()
        return self.get_draft(draft_id)

    def publish(self, draft_id: str, supersedes_draft_id: str | None = None) -> CaseDraft:
        item = self.get_draft(draft_id)
        if item.status == "published":
            return item
        if item.status != "ready":
            raise AnalystBenchError(
                "invalid_state", "Case must pass field checks and receive overall approval first"
            )
        working = json.loads(item.working_json)
        case = working["case"]
        draft = working["eval_spec_draft"]
        case_key = item.case_key or ""
        if supersedes_draft_id is None:
            try:
                previous = self.get_published(case_key)
            except NotFoundError:
                previous = None
            if previous is not None and previous.id != draft_id:
                return self.replace_published(draft_id, previous.id)
        with transaction(self.session_factory) as session:
            duplicate = session.scalar(
                select(CaseDraft).where(
                    CaseDraft.case_key == case_key,
                    CaseDraft.status == "published",
                    CaseDraft.id != supersedes_draft_id,
                )
            )
            if duplicate is not None and duplicate.id != draft_id:
                raise ConflictError(f"published case_key '{case_key}' already exists")
        revision_id: str | None = None
        dataset_version_id: str | None = None
        try:
            test_set = case["test_set"]
            category_key = case["category"]
            dataset = self.catalog.get_or_create_dataset(
                test_set, test_set, "AnalystBench 测试集"
            )
            category = self.catalog.get_or_create_category(
                dataset.id,
                category_key,
                category_key,
            )
            existing_case_id: str | None = None
            if supersedes_draft_id is not None:
                previous = self.get_draft(supersedes_draft_id)
                previous_resources = json.loads(previous.resources_json)
                previous_test_set = previous_resources.get("test_set", {})
                previous_category = previous_resources.get("category", {})
                if (
                    previous.case_key == case_key
                    and previous_test_set.get("id") == dataset.id
                    and previous_category.get("id") == category.id
                ):
                    existing_case_id = previous_resources.get("case_id")
            revision = self.catalog.create_case_revision(
                dataset.id,
                case_key,
                case["problem_statement"],
                case["reference_answer"],
                category_id=category.id,
                source_filename=item.source_filename,
                traces=list(case.get("traces", [])),
                case_id=existing_case_id,
            )
            revision_id = revision.id
            dataset_version = self.catalog.freeze_dataset_version(
                dataset.id, self.catalog.latest_case_revision_ids(dataset.id)
            )
            dataset_version_id = dataset_version.id
            prefix = draft_id[:8]
            policy = self.eval_specs.create_scoring_policy(f"case-{prefix}-policy")
            reference = case["reference_answer"]
            claims = []
            for claim in draft["claims"]:
                start = reference.index(claim["quote"])
                claims.append(
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
            spec_payload = {
                "schema_version": "1.0",
                "case_revision_id": revision.id,
                "suite": {"id": test_set, "version": "1.0.0"},
                "claims": claims,
                "causal_edges": [
                    {**edge, "review_required": False} for edge in draft.get("causal_edges", [])
                ],
                "forbidden_claims": draft.get("forbidden_claims", []),
                "scoring_policy_version_id": policy.id,
                "review": {"status": "approved", "unresolved_items": []},
            }
            if draft.get("scoring_strategy"):
                spec_payload["scoring_strategy"] = draft["scoring_strategy"]
            spec_draft = self.eval_specs.create_draft(revision.id, spec_payload)
            spec_version = self.eval_specs.freeze_draft(spec_draft.id)
            resources = {
                "dataset_version_id": dataset_version.id,
                "case_revision_id": revision.id,
                "scoring_policy_version_id": policy.id,
                "eval_spec_version_id": spec_version.id,
                "case_version": revision.revision_number,
                "case_id": revision.case_id,
                "test_set": {
                    "id": dataset.id,
                    "key": dataset.dataset_key,
                },
                "category": {
                    "id": category.id,
                    "key": category.category_key,
                },
                "source_filename": item.source_filename,
            }
        except Exception as exc:
            self._discard_publish_artifacts(
                revision_id=revision_id,
                dataset_version_id=dataset_version_id,
            )
            with transaction(self.session_factory) as session:
                stored = session.get(CaseDraft, draft_id)
                assert stored is not None
                stored.status = "failed"
                stored.error_json = canonical_json(
                    {"code": getattr(exc, "code", "publish_failed"), "message": str(exc)}
                )
            raise
        with transaction(self.session_factory) as session:
            stored = session.get(CaseDraft, draft_id)
            assert stored is not None
            stored.status = "published"
            stored.resources_json = canonical_json(resources)
            stored.error_json = "{}"
            session.flush()
        return self.get_draft(draft_id)

    def _discard_publish_artifacts(
        self,
        *,
        revision_id: str | None,
        dataset_version_id: str | None,
    ) -> None:
        """Remove only unreferenced artifacts created by one failed publish attempt."""
        if revision_id is None:
            return
        with transaction(self.session_factory) as session:
            if session.scalar(
                select(EvalSpecVersion.id).where(EvalSpecVersion.case_revision_id == revision_id)
            ):
                return
            referenced = any(
                session.scalar(select(model.id).where(model.case_revision_id == revision_id))
                for model in (CandidateReport, AgentCaseRun, BenchmarkCaseRun)
            )
            if referenced:
                return
            if dataset_version_id is not None:
                version_referenced = session.scalar(
                    select(BenchmarkRun.id).where(
                        BenchmarkRun.dataset_version_id == dataset_version_id
                    )
                ) or session.scalar(
                    select(CandidateGenerationRun.id).where(
                        CandidateGenerationRun.dataset_version_id == dataset_version_id
                    )
                )
                if version_referenced:
                    return
                version = session.get(DatasetVersion, dataset_version_id)
                if version is not None:
                    session.delete(version)
            drafts = list(
                session.scalars(
                    select(EvalSpecDraft).where(EvalSpecDraft.case_revision_id == revision_id)
                )
            )
            for draft in drafts:
                session.delete(draft)
            traces = list(
                session.scalars(select(CaseTrace).where(CaseTrace.case_revision_id == revision_id))
            )
            for trace in traces:
                session.delete(trace)
            session.flush()
            revision = session.get(CaseRevision, revision_id)
            if revision is not None:
                session.delete(revision)

    def replace_published(self, draft_id: str, published_draft_id: str) -> CaseDraft:
        """Publish an approved replacement and preserve the previous immutable version."""
        replacement = self.get_draft(draft_id)
        previous = self.get_draft(published_draft_id)
        if replacement.status != "ready":
            raise AnalystBenchError("invalid_state", "replacement Case draft is not ready")
        if previous.status != "published":
            raise AnalystBenchError("invalid_state", "Case being replaced is not published")
        with transaction(self.session_factory) as session:
            stored = session.get(CaseDraft, previous.id)
            assert stored is not None
            stored.status = "superseding"
        try:
            published = self.publish(replacement.id, supersedes_draft_id=previous.id)
        except Exception:
            with transaction(self.session_factory) as session:
                stored = session.get(CaseDraft, previous.id)
                assert stored is not None
                stored.status = "published"
            raise
        with transaction(self.session_factory) as session:
            stored = session.get(CaseDraft, previous.id)
            assert stored is not None
            stored.status = "superseded"
        return published

    def list_published(self) -> list[CaseDraft]:
        with transaction(self.session_factory) as session:
            return list(
                session.scalars(
                    select(CaseDraft)
                    .where(CaseDraft.status == "published")
                    .order_by(CaseDraft.case_key)
                )
            )

    def get_published(self, case_key: str) -> CaseDraft:
        with transaction(self.session_factory) as session:
            item = session.scalar(
                select(CaseDraft).where(
                    CaseDraft.case_key == case_key, CaseDraft.status == "published"
                )
            )
            if item is None:
                raise NotFoundError("published_case", case_key)
            session.expunge(item)
            return item

    def organize_published(
        self,
        case_key: str,
        source_filename: str,
        test_set: str,
        category: str,
        new_case_key: str | None = None,
    ) -> CaseDraft:
        """Republish approved content under the formal hierarchy without re-reviewing it."""
        previous = self.get_published(case_key)
        working = json.loads(previous.working_json)
        case = working.get("case")
        if not isinstance(case, dict):
            raise AnalystBenchError("draft_invalid", "published Case has no case object")
        case["test_set"] = test_set
        case["category"] = category
        effective_case_key = new_case_key or case_key
        case["case_key"] = effective_case_key
        issues = [
            question for question in self._questions(working) if question["code"] != "approve_case"
        ]
        if issues:
            raise AnalystBenchError(
                "draft_invalid",
                "published Case cannot be reorganized until its stored fields are valid",
                issues,
            )
        replacement = CaseDraft(
            id=str(uuid4()),
            case_key=effective_case_key,
            source_filename=source_filename,
            dataset_key=test_set,
            category_key=category,
            status="ready",
            original_json=previous.original_json,
            working_json=canonical_json(working),
            questions_json="[]",
            answers_json=canonical_json(
                json.loads(previous.answers_json)
                + [
                    {
                        "action": "organize_published",
                        "supersedes_case_draft_id": previous.id,
                    }
                ]
            ),
        )
        with transaction(self.session_factory) as session:
            session.add(replacement)
            session.flush()
        return self.replace_published(replacement.id, previous.id)

    @staticmethod
    def view(item: CaseDraft) -> dict[str, Any]:
        questions = json.loads(item.questions_json)
        working = json.loads(item.working_json)
        draft = working.get("eval_spec_draft", {})
        return {
            "id": item.id,
            "case_key": item.case_key,
            "source_filename": item.source_filename,
            "test_set": item.dataset_key,
            "category": item.category_key,
            "status": item.status,
            "questions": questions,
            "summary": {
                "problem_statement": working.get("case", {}).get("problem_statement"),
                "claim_count": len(draft.get("claims", []))
                if isinstance(draft.get("claims", []), list)
                else 0,
                "root_cause_claims": [
                    claim.get("statement")
                    for claim in draft.get("claims", [])
                    if isinstance(claim, dict) and claim.get("type") == "root_cause"
                ],
            },
            "resources": json.loads(item.resources_json),
            "error": json.loads(item.error_json),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def _questions(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        case = payload.get("case")
        draft = payload.get("eval_spec_draft")
        if not isinstance(case, dict):
            return [_question("case", "missing_field", "缺少 case 对象，请提供 Case 基本信息。")]
        if not isinstance(draft, dict):
            return [
                _question(
                    "eval_spec_draft", "missing_field", "缺少 eval_spec_draft 对象，请提供评分项。"
                )
            ]
        for field, label in (
            ("case_key", "用例标识"),
            ("problem_statement", "问题描述"),
            ("reference_answer", "人工标准答案原文"),
        ):
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    _question(f"case.{field}", "missing_field", f"{label}不能为空。", value)
                )
        for field, label in (("test_set", "测试集"), ("category", "用例分类")):
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    _question(
                        f"case.{field}",
                        "missing_field",
                        f"缺少{label}标识。",
                        value,
                    )
                )
        reference = case.get("reference_answer", "")
        claims = draft.get("claims")
        if not isinstance(claims, list) or not claims:
            issues.append(
                _question(
                    "eval_spec_draft.claims",
                    "missing_field",
                    "至少需要一个可评分的 Claim。",
                    claims,
                )
            )
            claims = []
        weights_valid = True
        chain_number = 0
        claim_number = 0
        for index, claim in enumerate(claims):
            base = f"eval_spec_draft.claims[{index}]"
            if not isinstance(claim, dict):
                issues.append(_question(base, "invalid_type", "Claim 必须是对象。", claim))
                weights_valid = False
                continue
            claim_type = claim.get("type")
            if claim_type == "root_cause":
                expected_id = "root"
            elif claim_type == "classification":
                expected_id = "category"
            elif claim_type == "analysis_chain":
                chain_number += 1
                expected_id = f"chain-{chain_number}"
            else:
                claim_number += 1
                expected_id = f"claim-{claim_number}"
            statement = str(claim.get("statement") or "")
            context = f"评分项 {expected_id}「{statement or '未填写结论'}」"
            if claim.get("id") != expected_id:
                issues.append(
                    _question(
                        f"{base}.id",
                        "invalid_id",
                        f"{context}的 id 应按顺序为 {expected_id}。",
                        claim.get("id"),
                        expected_id,
                        [expected_id],
                    )
                )
            if claim_type not in CORE_TYPES:
                issues.append(
                    _question(
                        f"{base}.type",
                        "invalid_enum",
                        f"{context}的 type 表示该结论在分析链中的角色，请选择类型。",
                        claim.get("type"),
                        None,
                        sorted(CORE_TYPES),
                    )
                )
            if not statement.strip():
                issues.append(
                    _question(f"{base}.statement", "missing_field", f"{context}缺少结论文本。")
                )
            if claim_type == "analysis_chain":
                evidence_keyword = claim.get("evidence_keyword")
                if not isinstance(evidence_keyword, str) or not evidence_keyword.strip():
                    issues.append(
                        _question(
                            f"{base}.evidence_keyword",
                            "missing_field",
                            f"{context}缺少日志关键字；它必须是标准答案中的完整证据原文，"
                            "评分时只做强匹配。",
                            evidence_keyword,
                        )
                    )
                conclusion = claim.get("conclusion")
                if not isinstance(conclusion, str) or not conclusion.strip():
                    issues.append(
                        _question(
                            f"{base}.conclusion",
                            "missing_field",
                            f"{context}缺少分析链结论；它将由语义 Judge 计算相似度。",
                            conclusion,
                        )
                    )
            importance = claim.get("importance")
            if importance not in IMPORTANCE:
                issues.append(
                    _question(
                        f"{base}.importance",
                        "invalid_enum",
                        f"{context}的 importance 表示漏掉该结论的严重程度。",
                        importance,
                        IMPORTANCE_SUGGESTIONS.get(str(importance)),
                        ["critical", "high", "normal", "low"],
                    )
                )
            weight = claim.get("weight")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                weights_valid = False
                issues.append(
                    _question(
                        f"{base}.weight",
                        "invalid_weight",
                        f"{context}的 weight 是该项分值，必须是正数。",
                        weight,
                    )
                )
            quote = claim.get("quote")
            if not isinstance(quote, str) or not quote or quote not in reference:
                issues.append(
                    _question(
                        f"{base}.quote",
                        "quote_mismatch",
                        f"{context}的 quote 必须是人工标准答案中的连续原文。",
                        quote,
                    )
                )
        edges = draft.get("causal_edges", [])
        if not isinstance(edges, list):
            issues.append(
                _question(
                    "eval_spec_draft.causal_edges",
                    "invalid_type",
                    "causal_edges 必须是数组；没有因果边时填写空数组。",
                    edges,
                    [],
                )
            )
            edges = []
        strategy = draft.get("scoring_strategy", {"mode": "weighted_sum"})
        strategy_mode = strategy.get("mode") if isinstance(strategy, dict) else None
        if strategy_mode == "root_category_chain":
            expected = ROOT_CATEGORY_CHAIN_STRATEGY
            if strategy != expected:
                issues.append(
                    _question(
                        "eval_spec_draft.scoring_strategy",
                        "invalid_scoring_strategy",
                        "根因/分类/分析链模式固定为：根因完全命中直接100分；否则问题分类20分，"
                        "分析链总分60并按条数等分，每条关键字与结论各占一半。",
                        strategy,
                        expected,
                        [expected],
                    )
                )
            root_claims_for_weight = [
                claim
                for claim in claims
                if isinstance(claim, dict) and claim.get("type") == "root_cause"
            ]
            category_claims = [
                claim
                for claim in claims
                if isinstance(claim, dict) and claim.get("type") == "classification"
            ]
            chain_claims = [
                claim
                for claim in claims
                if isinstance(claim, dict) and claim.get("type") == "analysis_chain"
            ]
            root_valid = (
                len(root_claims_for_weight) == 1
                and float(root_claims_for_weight[0].get("weight", 0)) == 100
            )
            chain_weights = [
                float(claim.get("weight", 0))
                for claim in chain_claims
                if isinstance(claim.get("weight"), (int, float))
                and not isinstance(claim.get("weight"), bool)
            ]
            chain_weight = sum(chain_weights)
            equal_distribution = not chain_weights or (
                max(chain_weights) - min(chain_weights) <= 0.01
            )
            if (
                not root_valid
                or len(category_claims) != 1
                or float(category_claims[0].get("weight", 0)) != expected["category_score"]
                or abs(chain_weight - expected["chain_total_score"]) > 0.01
                or not equal_distribution
            ):
                issues.append(
                    _question(
                        "eval_spec_draft.claims",
                        "root_category_chain_weights",
                        "根因必须为100分；问题分类必须为20分；所有分析链必须等比分配并合计60分，"
                        "分析链必须等比分配并合计60分。",
                        {
                            "root_weights": [
                                claim.get("weight") for claim in root_claims_for_weight
                            ],
                            "category_weights": [
                                claim.get("weight") for claim in category_claims
                            ],
                            "chain_weights": [claim.get("weight") for claim in chain_claims],
                        },
                    )
                )
        elif weights_valid and claims:
            edge_weight = sum(
                edge.get("weight", 0)
                for edge in edges
                if isinstance(edge, dict) and isinstance(edge.get("weight"), int)
            )
            claim_weight = sum(
                claim.get("weight", 0)
                for claim in claims
                if isinstance(claim, dict) and isinstance(claim.get("weight"), int)
            )
            if claim_weight + edge_weight != 100:
                issues.append(
                    _question(
                        "eval_spec_draft.claims",
                        "weights_total",
                        "当前 Claim 与因果边权重合计为 "
                        f"{claim_weight + edge_weight}，必须为 100；"
                        "请按 Claim ID 提供新权重。",
                        {
                            claim.get("id"): claim.get("weight")
                            for claim in claims
                            if isinstance(claim, dict)
                        },
                    )
                )
        root_claims = [
            claim
            for claim in claims
            if isinstance(claim, dict) and claim.get("type") == "root_cause"
        ]
        if claims and not any(claim.get("importance") == "critical" for claim in root_claims):
            root_ids = [claim.get("id") for claim in root_claims if claim.get("id")]
            issues.append(
                _question(
                    "eval_spec_draft.claims",
                    "critical_root_cause",
                    "必须选择一个根因 Claim 作为 critical；在根因/分类/分析链模式中，"
                    "只有该根因完全命中才会直接得100分。",
                    None,
                    root_ids[0] if root_ids else None,
                    root_ids,
                )
            )
        unresolved = draft.get("unresolved_items", [])
        if not isinstance(unresolved, list):
            issues.append(
                _question(
                    "eval_spec_draft.unresolved_items",
                    "invalid_type",
                    "unresolved_items 必须是字符串数组。",
                    unresolved,
                    [],
                )
            )
        elif unresolved:
            issues.append(
                _question(
                    "eval_spec_draft.unresolved_items",
                    "unresolved_items",
                    f"评分规范仍有 {len(unresolved)} 个未决项。发布前需要解决或排除这些项目。",
                    unresolved,
                    "exclude_from_spec",
                    ["resolved", "exclude_from_spec"],
                )
            )
        if issues:
            return issues
        critical = next(
            (
                claim.get("statement")
                for claim in claims
                if isinstance(claim, dict)
                and claim.get("type") == "root_cause"
                and claim.get("importance") == "critical"
            ),
            "未识别",
        )
        return [
            _question(
                "$approval",
                "approve_case",
                f"字段检查已通过。请整体确认：共 {len(claims)} 个评分项，"
                f"关键根因为「{critical}」。"
                + (
                    "评分规则为根因完全命中100分；否则问题分类20分，分析链总分60并按节点数等分；"
                    "每条分析链的关键字与结论各占一半。"
                    if strategy_mode == "root_category_chain"
                    else ""
                )
                + "确认后可发布到基准库。",
                None,
                "approved",
                ["approved"],
            )
        ]

    @staticmethod
    def _apply_answer(payload: dict[str, Any], question: dict[str, Any], value: Any) -> None:
        options = question.get("options", [])
        if options and value not in options:
            raise AnalystBenchError(
                "answer_invalid",
                "answer is not one of the allowed options",
                [{"field_path": question["field_path"], "options": options}],
            )
        code = question["code"]
        if code == "approve_case":
            for claim in payload["eval_spec_draft"].get("claims", []):
                if isinstance(claim, dict):
                    claim["review_required"] = False
            for edge in payload["eval_spec_draft"].get("causal_edges", []):
                if isinstance(edge, dict):
                    edge["review_required"] = False
            return
        if code == "unresolved_items":
            payload["eval_spec_draft"]["unresolved_items"] = []
            return
        if code == "weights_total":
            if not isinstance(value, dict):
                raise AnalystBenchError(
                    "answer_invalid", "weight answer must map Claim IDs to integer weights"
                )
            for claim in payload["eval_spec_draft"]["claims"]:
                if claim.get("id") in value:
                    claim["weight"] = value[claim["id"]]
            return
        if code == "invalid_scoring_strategy":
            payload["eval_spec_draft"]["scoring_strategy"] = value
            return
        if code == "critical_root_cause":
            selected = next(
                (
                    claim
                    for claim in payload["eval_spec_draft"]["claims"]
                    if claim.get("id") == value
                ),
                None,
            )
            if selected is None:
                raise AnalystBenchError("answer_invalid", "selected Claim ID does not exist")
            selected["type"] = "root_cause"
            selected["importance"] = "critical"
            return
        _set_path(payload, question["field_path"], value)


class ReportDraftService:
    """Persist normalized candidate reports and expose non-blocking hint warnings."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_draft(self, payload: dict[str, Any]) -> ReportDraft:
        if not isinstance(payload, dict):
            raise AnalystBenchError("draft_invalid", "Report draft must be a JSON object")
        issues = self._issues(payload)
        errors = [issue for issue in issues if issue["severity"] == "error"]
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        item = ReportDraft(
            id=str(uuid4()),
            candidate_name=candidate.get("name")
            if isinstance(candidate.get("name"), str)
            else None,
            status="invalid" if errors else "ready",
            payload_json=canonical_json(payload),
            issues_json=canonical_json(issues),
        )
        with transaction(self.session_factory) as session:
            session.add(item)
            session.flush()
            session.expunge(item)
        return item

    def create_from_text(
        self,
        candidate_name: str,
        candidate_report: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
        filename: str | None = None,
    ) -> ReportDraft:
        """Deterministically wrap a raw report; no model call is needed."""
        return self.create_draft(
            report_payload_from_text(
                filename or candidate_name,
                candidate_report,
                candidate_name,
                description,
                metadata,
            )
        )

    def get_draft(self, draft_id: str) -> ReportDraft:
        with transaction(self.session_factory) as session:
            item = session.get(ReportDraft, draft_id)
            if item is None:
                raise NotFoundError("report_draft", draft_id)
            session.expunge(item)
            return item

    @staticmethod
    def view(item: ReportDraft) -> dict[str, Any]:
        return {
            "id": item.id,
            "candidate_name": item.candidate_name,
            "status": item.status,
            "issues": json.loads(item.issues_json),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    @staticmethod
    def _issues(payload: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        candidate = payload.get("candidate")
        if (
            not isinstance(candidate, dict)
            or not isinstance(candidate.get("name"), str)
            or not candidate.get("name", "").strip()
        ):
            issues.append(
                {
                    "field_path": "candidate.name",
                    "code": "missing_field",
                    "message": "请提供用于结果展示和对比的候选名称。",
                    "severity": "error",
                }
            )
        report = payload.get("candidate_report")
        if not isinstance(report, str) or not report.strip():
            issues.append(
                {
                    "field_path": "candidate_report",
                    "code": "missing_field",
                    "message": "请提供 AI 报告原文。",
                    "severity": "error",
                }
            )
            return issues
        hints = payload.get("claim_hints", [])
        if isinstance(hints, list):
            for index, hint in enumerate(hints):
                quote = hint.get("quote") if isinstance(hint, dict) else None
                if not isinstance(quote, str) or quote not in report:
                    issues.append(
                        {
                            "field_path": f"claim_hints[{index}].quote",
                            "code": "candidate_hint_quote_mismatch",
                            "message": (
                                "提示引用不是报告连续原文；评分仍使用 statement 作为"
                                "候选结论，但该结论没有可审计的连续原文引用。"
                            ),
                            "severity": "warning",
                        }
                    )
        return issues


class EvaluationBatchService:
    """Evaluate many reports against one previously published Case."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        content_store: ContentStore,
        settings: Settings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.content_store = content_store
        self.cases = CaseLibraryService(session_factory, content_store)
        self.reports = ReportDraftService(session_factory)
        self.catalog = CatalogService(session_factory, content_store)
        self.benchmarks = BenchmarkService(session_factory, content_store, settings)
        self.comparisons = ComparisonService(self.benchmarks)

    def create_batch(
        self,
        case_key: str,
        report_payloads: list[dict[str, Any]] | None = None,
        report_draft_ids: list[str] | None = None,
        judge_runner: str = "lexical",
        judge_configuration: dict[str, Any] | None = None,
    ) -> EvaluationBatch:
        if judge_runner not in {"claude-code", "opencode", "lexical"}:
            raise AnalystBenchError(
                "validation_failed", "judge_runner must be claude-code, opencode, or lexical"
            )
        case = self.cases.get_published(case_key)
        case_resources = json.loads(case.resources_json)
        with transaction(self.session_factory) as session:
            spec = session.get(EvalSpecVersion, case_resources["eval_spec_version_id"])
            assert spec is not None
            ensure_scoring_spec_supported(case_key, json.loads(spec.payload_json))
        ids = list(report_draft_ids or [])
        for payload in report_payloads or []:
            ids.append(self.reports.create_draft(payload).id)
        if not ids:
            raise AnalystBenchError("draft_invalid", "at least one report is required")
        report_drafts = [self.reports.get_draft(draft_id) for draft_id in ids]
        invalid = [draft.id for draft in report_drafts if draft.status != "ready"]
        if invalid:
            raise AnalystBenchError(
                "report_invalid",
                "one or more report drafts contain blocking field errors",
                {"report_draft_ids": invalid},
            )
        batch = EvaluationBatch(
            id=str(uuid4()),
            case_draft_id=case.id,
            status="preparing",
            report_draft_ids_json=canonical_json(ids),
        )
        with transaction(self.session_factory) as session:
            session.add(batch)
            session.flush()
            batch_id = batch.id
        self._prepare(
            batch_id,
            case,
            report_drafts,
            judge_runner,
            judge_configuration or {},
        )
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str, refresh: bool = True) -> EvaluationBatch:
        if refresh:
            self._refresh(batch_id)
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationBatch, batch_id)
            if item is None:
                raise NotFoundError("evaluation_batch", batch_id)
            session.expunge(item)
            return item

    def process_pending(self, batch_id: str) -> dict[str, Any]:
        item = self.get_batch(batch_id)
        if item.status not in {"queued", "running"}:
            return self.result(batch_id)
        for entry in json.loads(item.resources_json).get("runs", []):
            for case_run in self.benchmarks.list_case_runs(entry["run_id"]):
                if case_run.status == "pending":
                    self.benchmarks.execute_case_run(case_run.id)
        return self.result(batch_id)

    def result(self, batch_id: str) -> dict[str, Any]:
        item = self.get_batch(batch_id)
        resources = json.loads(item.resources_json)
        runs = []
        for entry in resources.get("runs", []):
            run = self.benchmarks.get_run(entry["run_id"])
            exported = (
                self.benchmarks.export_run(run.id)
                if run.status in {"completed", "completed_with_errors", "failed", "cancelled"}
                else None
            )
            case_result = exported["case_runs"][0]["result"] if exported else None
            runs.append(
                {
                    "candidate_name": entry["candidate_name"],
                    "report_draft_id": entry["report_draft_id"],
                    "status": run.status,
                    "score": case_result["total_score"] if case_result else None,
                    "passed": case_result["passed"] if case_result else None,
                    "result": case_result,
                    "warnings": entry.get("warnings", []),
                }
            )
        comparisons: list[dict[str, Any]] = []
        if item.status == "completed" and len(resources.get("runs", [])) > 1:
            baseline = resources["runs"][0]
            for candidate in resources["runs"][1:]:
                comparison = self.comparisons.compare(baseline["run_id"], candidate["run_id"])
                comparisons.append(
                    {
                        "baseline": baseline["candidate_name"],
                        "candidate": candidate["candidate_name"],
                        "average_delta": comparison["aggregate"]["average_delta"],
                        "classification": comparison["cases"][0]["classification"]
                        if comparison["cases"]
                        else None,
                        "details": comparison,
                    }
                )
            with transaction(self.session_factory) as session:
                stored = session.get(EvaluationBatch, batch_id)
                assert stored is not None
                stored.comparison_json = canonical_json(comparisons)
        comparison_results = comparisons or json.loads(item.comparison_json)
        case_draft = self.cases.get_draft(item.case_draft_id)
        case_source = resources.get("case_source") or {
            "mode": "database",
            "case_version": None,
            "eval_spec_version_id": None,
            "source_filename": case_draft.source_filename,
        }
        response = {
            "id": item.id,
            "mode": "database",
            "case_key": resources.get("case_key"),
            "case_source": case_source,
            "status": item.status,
            "reports": runs,
            "comparisons": comparison_results,
            "error": json.loads(item.error_json),
        }
        response["summary"] = build_human_summary(
            str(resources.get("case_key")),
            json.loads(case_draft.working_json),
            runs,
            comparison_results,
            case_source,
        )
        return response

    @staticmethod
    def view(item: EvaluationBatch) -> dict[str, Any]:
        resources = json.loads(item.resources_json)
        return {
            "id": item.id,
            "case_key": resources.get("case_key"),
            "status": item.status,
            "report_count": len(json.loads(item.report_draft_ids_json)),
            "resources": resources,
            "error": json.loads(item.error_json),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

    def _prepare(
        self,
        batch_id: str,
        case: CaseDraft,
        report_drafts: list[ReportDraft],
        judge_runner: str,
        judge_configuration: dict[str, Any],
    ) -> None:
        try:
            case_resources = json.loads(case.resources_json)
            runs = []
            for index, report_draft in enumerate(report_drafts, 1):
                payload = json.loads(report_draft.payload_json)
                candidate_payload = payload["candidate"]
                candidate_name = candidate_payload["name"].strip()
                candidate = self.catalog.create_candidate(
                    f"{candidate_name} [{batch_id[:8]}-{index}]"[:255],
                    str(candidate_payload.get("description", "")),
                )
                candidate_metadata = dict(candidate_payload.get("metadata", {}))
                candidate_metadata["analystbench_claim_hints"] = payload.get("claim_hints", [])
                candidate_metadata["analystbench_judge"] = {
                    "runner": judge_runner,
                    "configuration": judge_configuration,
                }
                version = self.catalog.create_candidate_version(candidate.id, candidate_metadata)
                self.catalog.import_candidate_reports(
                    version.id,
                    [
                        {
                            "case_revision_id": case_resources["case_revision_id"],
                            "report": payload["candidate_report"],
                        }
                    ],
                )
                run = self.benchmarks.create_run(
                    case_resources["dataset_version_id"],
                    version.id,
                    case_resources["scoring_policy_version_id"],
                )
                runs.append(
                    {
                        "candidate_name": candidate_name,
                        "report_draft_id": report_draft.id,
                        "run_id": run.id,
                        "warnings": [
                            issue
                            for issue in json.loads(report_draft.issues_json)
                            if issue["severity"] == "warning"
                        ],
                    }
                )
            resources = {
                "case_key": case.case_key,
                "case_source": {
                    "mode": "database",
                    "case_version": case_resources.get("case_version"),
                    "case_revision_id": case_resources.get("case_revision_id"),
                    "eval_spec_version_id": case_resources.get("eval_spec_version_id"),
                    "source_filename": case_resources.get("source_filename"),
                },
                "runs": runs,
            }
            with transaction(self.session_factory) as session:
                stored = session.get(EvaluationBatch, batch_id)
                assert stored is not None
                stored.resources_json = canonical_json(resources)
                stored.status = "queued"
        except Exception as exc:
            with transaction(self.session_factory) as session:
                stored = session.get(EvaluationBatch, batch_id)
                assert stored is not None
                stored.status = "failed"
                stored.error_json = canonical_json(
                    {"code": getattr(exc, "code", "preparation_failed"), "message": str(exc)}
                )
            raise

    def _refresh(self, batch_id: str) -> None:
        with transaction(self.session_factory) as session:
            item = session.get(EvaluationBatch, batch_id)
            if item is None or item.status not in {"queued", "running"}:
                return
            resources = json.loads(item.resources_json)
        statuses = [
            self.benchmarks.get_run(entry["run_id"]).status for entry in resources.get("runs", [])
        ]
        if not statuses:
            return
        terminal = {"completed", "completed_with_errors", "failed", "cancelled"}
        if all(status == "completed" for status in statuses):
            status = "completed"
        elif all(status in terminal for status in statuses):
            status = "failed" if all(value == "failed" for value in statuses) else "completed"
        elif any(status == "running" for status in statuses):
            status = "running"
        else:
            status = "queued"
        with transaction(self.session_factory) as session:
            stored = session.get(EvaluationBatch, batch_id)
            if stored is not None:
                stored.status = status
