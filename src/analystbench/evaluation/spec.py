"""Eval Spec v1 schema, semantic validation, and immutable draft/version service."""

import json
import re
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from analystbench.db.models import (
    CaseRevision,
    ContentBlob,
    EvalSpecDraft,
    EvalSpecVersion,
    ScoringPolicyVersion,
)
from analystbench.db.transaction import transaction
from analystbench.errors import AnalystBenchError, NotFoundError
from analystbench.storage.content import ContentRef, ContentStore, canonical_json


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)


class GoldClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^(?:root|category|chain-[1-9][0-9]*|claim-[1-9][0-9]*)$")
    type: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    importance: Literal["critical", "high", "normal", "low"]
    weight: float = Field(gt=0)
    source_ref: SourceRef
    review_required: bool = False
    notes: str | None = None
    evidence_keyword: str | None = None
    conclusion: str | None = None


class CausalEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^edge-[1-9][0-9]*$")
    from_claim: str = Field(
        alias="from", pattern=r"^(?:root|category|chain-[1-9][0-9]*|claim-[1-9][0-9]*)$"
    )
    to_claim: str = Field(
        alias="to", pattern=r"^(?:root|category|chain-[1-9][0-9]*|claim-[1-9][0-9]*)$"
    )
    relation: Literal["causes", "leads_to", "explains"]
    weight: int = Field(gt=0)
    review_required: bool = False


class ForbiddenClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^forbidden-[1-9][0-9]*$")
    statement: str = Field(min_length=1)
    severity: Literal["critical", "high", "medium", "low"]
    penalty: int = Field(ge=0)
    failure_gate: bool = False
    notes: str | None = None


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["draft", "approved"] = "draft"
    unresolved_items: list[str] = Field(default_factory=list)


class ScoringStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["weighted_sum", "root_category_chain"] = "weighted_sum"
    root_cause_score: int = Field(default=100, ge=0, le=100)
    category_score: int = Field(default=20, ge=0, le=100)
    chain_total_score: int = Field(default=60, ge=0, le=100)


class EvalSpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0"] = "1.0"
    case_revision_id: str
    suite: dict[str, str]
    claims: list[GoldClaim] = Field(min_length=1)
    causal_edges: list[CausalEdge] = Field(default_factory=list)
    forbidden_claims: list[ForbiddenClaim] = Field(default_factory=list)
    scoring_policy_version_id: str
    scoring_strategy: ScoringStrategy = Field(default_factory=ScoringStrategy)
    review: Review = Field(default_factory=Review)


def default_scoring_policy() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "pass_threshold": 70,
        "critical_root_cause_contradiction_penalty": 15,
        "penalty_cap": 100,
    }


class ReferenceAnswerDraftGenerator:
    """Deterministic local generator; drafts always require a human approval."""

    def generate(
        self, revision: CaseRevision, reference_answer: str, policy_id: str
    ) -> dict[str, Any]:
        spans = [
            (match.start(), match.end())
            for match in re.finditer(r"[^。！？.!?]+[。！？.!?]?", reference_answer)
        ]
        spans = [(start, end) for start, end in spans if reference_answer[start:end].strip()]
        if not spans:
            spans = [(0, len(reference_answer))]
        # The root-cause statement receives all positive weight by default. The
        # draft author can atomize it and redistribute weight before approval.
        start, end = spans[-1]
        quote = reference_answer[start:end]
        return {
            "schema_version": "1.0",
            "case_revision_id": revision.id,
            "suite": {"id": "analystbench", "version": "1.0.0"},
            "claims": [
                {
                    "id": "root",
                    "type": "root_cause",
                    "statement": quote.strip(),
                    "importance": "critical",
                    "weight": 100,
                    "source_ref": {
                        "content_hash": revision.reference_answer_content_hash,
                        "start": start,
                        "end": end,
                        "quote": quote,
                    },
                    "review_required": True,
                    "notes": "Generated deterministically from the reference answer.",
                }
            ],
            "causal_edges": [],
            "forbidden_claims": [],
            "scoring_policy_version_id": policy_id,
            "review": {"status": "draft", "unresolved_items": []},
        }


class EvalSpecService:
    def __init__(self, session_factory: sessionmaker[Session], content_store: ContentStore) -> None:
        self.session_factory = session_factory
        self.content_store = content_store
        self.generator = ReferenceAnswerDraftGenerator()

    @staticmethod
    def _store_ref(session: Session, ref: ContentRef) -> None:
        if session.get(ContentBlob, ref.content_hash) is None:
            session.add(ContentBlob(**asdict(ref)))

    def create_scoring_policy(
        self, name: str, policy: dict[str, Any] | None = None
    ) -> ScoringPolicyVersion:
        payload = policy or default_scoring_policy()
        ref = self.content_store.put_json(payload)
        with transaction(self.session_factory) as session:
            existing = session.scalar(
                select(ScoringPolicyVersion).where(
                    ScoringPolicyVersion.content_hash == ref.content_hash
                )
            )
            if existing is not None:
                session.expunge(existing)
                return existing
            current = session.scalar(
                select(func.max(ScoringPolicyVersion.version_number)).where(
                    ScoringPolicyVersion.name == name
                )
            )
            self._store_ref(session, ref)
            session.flush()
            version = ScoringPolicyVersion(
                id=str(uuid4()),
                name=name,
                version_number=int(current or 0) + 1,
                policy_json=canonical_json(payload),
                content_hash=ref.content_hash,
            )
            session.add(version)
            session.flush()
            session.expunge(version)
            return version

    def generate_draft(
        self, case_revision_id: str, scoring_policy_version_id: str
    ) -> EvalSpecDraft:
        with transaction(self.session_factory) as session:
            revision = session.get(CaseRevision, case_revision_id)
            if revision is None:
                raise NotFoundError("case_revision", case_revision_id)
            if session.get(ScoringPolicyVersion, scoring_policy_version_id) is None:
                raise NotFoundError("scoring_policy_version", scoring_policy_version_id)
            reference_answer = self.content_store.read_text(revision.reference_answer_content_hash)
            payload = self.generator.generate(revision, reference_answer, scoring_policy_version_id)
            draft = EvalSpecDraft(
                id=str(uuid4()),
                case_revision_id=case_revision_id,
                payload_json=canonical_json(payload),
                status="draft",
            )
            session.add(draft)
            session.flush()
            session.expunge(draft)
            return draft

    def create_draft(self, case_revision_id: str, payload: dict[str, Any]) -> EvalSpecDraft:
        with transaction(self.session_factory) as session:
            if session.get(CaseRevision, case_revision_id) is None:
                raise NotFoundError("case_revision", case_revision_id)
            draft = EvalSpecDraft(
                id=str(uuid4()),
                case_revision_id=case_revision_id,
                payload_json=canonical_json(payload),
                status="draft",
            )
            session.add(draft)
            session.flush()
            session.expunge(draft)
            return draft

    def get_draft(self, draft_id: str) -> EvalSpecDraft:
        with transaction(self.session_factory) as session:
            draft = session.get(EvalSpecDraft, draft_id)
            if draft is None:
                raise NotFoundError("eval_spec_draft", draft_id)
            session.expunge(draft)
            return draft

    def validate_draft(self, draft_id: str, for_freeze: bool = False) -> list[str]:
        draft = self.get_draft(draft_id)
        return self.validate_payload(
            draft.case_revision_id, json.loads(draft.payload_json), for_freeze
        )

    def validate_payload(
        self, case_revision_id: str, payload: dict[str, Any], for_freeze: bool = False
    ) -> list[str]:
        try:
            spec = EvalSpecV1.model_validate(payload)
        except ValidationError as exc:
            return [item["msg"] for item in exc.errors()]
        errors: list[str] = []
        if spec.case_revision_id != case_revision_id:
            errors.append("case_revision_id must match the draft binding")
        if set(spec.suite) != {"id", "version"} or not all(spec.suite.values()):
            errors.append("suite must contain non-empty id and version")
        claim_ids = [claim.id for claim in spec.claims]
        if len(claim_ids) != len(set(claim_ids)):
            errors.append("claim ids must be unique")
        edge_ids = [edge.id for edge in spec.causal_edges]
        if len(edge_ids) != len(set(edge_ids)):
            errors.append("edge ids must be unique")
        forbidden_ids = [claim.id for claim in spec.forbidden_claims]
        if len(forbidden_ids) != len(set(forbidden_ids)):
            errors.append("forbidden claim ids must be unique")
        with transaction(self.session_factory) as session:
            revision = session.get(CaseRevision, case_revision_id)
            if revision is None:
                return ["bound case revision no longer exists"]
            if session.get(ScoringPolicyVersion, spec.scoring_policy_version_id) is None:
                errors.append("scoring_policy_version_id does not exist")
            reference = self.content_store.read_text(revision.reference_answer_content_hash)
        for claim in spec.claims:
            source = claim.source_ref
            if source.content_hash != revision.reference_answer_content_hash:
                errors.append(f"{claim.id}: source_ref must cite the reference answer")
            elif (
                source.end > len(reference) or reference[source.start : source.end] != source.quote
            ):
                errors.append(f"{claim.id}: source_ref quote does not match the declared range")
        errors.extend(self._edge_errors(spec))
        if for_freeze:
            if spec.review.status != "approved" or spec.review.unresolved_items:
                errors.append("freeze requires approved review with no unresolved items")
            if any(claim.review_required for claim in spec.claims):
                errors.append("freeze requires all claims to be reviewed")
            if any(edge.review_required for edge in spec.causal_edges):
                errors.append("freeze requires all causal edges to be reviewed")
            if not any(
                claim.type == "root_cause" and claim.importance == "critical"
                for claim in spec.claims
            ):
                errors.append("freeze requires one critical root_cause claim")
            if spec.scoring_strategy.mode == "root_category_chain":
                roots = [claim for claim in spec.claims if claim.type == "root_cause"]
                categories = [claim for claim in spec.claims if claim.type == "classification"]
                chain = [claim for claim in spec.claims if claim.type == "analysis_chain"]
                strategy = spec.scoring_strategy
                if (
                    strategy.root_cause_score != 100
                    or strategy.category_score != 20
                    or strategy.chain_total_score != 60
                ):
                    errors.append("root_category_chain scoring strategy must use 100/20/60")
                if len(roots) != 1 or roots[0].weight != 100:
                    errors.append("root_category_chain requires exactly one 100-point root cause")
                elif roots[0].id != "root":
                    errors.append("root_category_chain root cause id must be 'root'")
                if (
                    len(categories) != 1
                    or categories[0].id != "category"
                    or categories[0].weight != 20
                ):
                    errors.append("root_category_chain requires one 20-point category claim")
                if not chain:
                    errors.append("root_category_chain requires one or more analysis chain claims")
                expected_chain_ids = [f"chain-{index}" for index in range(1, len(chain) + 1)]
                if [claim.id for claim in chain] != expected_chain_ids:
                    errors.append(
                        "root_category_chain analysis chain ids must be sequential chain-N values"
                    )
                chain_weights = [Decimal(str(claim.weight)) for claim in chain]
                if abs(sum(chain_weights) - Decimal("60")) > Decimal("0.01") or (
                    chain_weights and max(chain_weights) - min(chain_weights) > Decimal("0.01")
                ):
                    errors.append("root_category_chain chain weights must be equal and total 60")
                if any(not claim.evidence_keyword or not claim.conclusion for claim in chain):
                    errors.append(
                        "root_category_chain chains require evidence_keyword and conclusion"
                    )
                if spec.causal_edges:
                    errors.append("root_category_chain does not use causal edges")
            elif (
                sum(claim.weight for claim in spec.claims)
                + sum(edge.weight for edge in spec.causal_edges)
                != 100
            ):
                errors.append("freeze requires positive claim and edge weights to total 100")
        return errors

    @staticmethod
    def _edge_errors(spec: EvalSpecV1) -> list[str]:
        claim_ids = {claim.id for claim in spec.claims}
        seen: set[tuple[str, str, str]] = set()
        graph: dict[str, list[str]] = defaultdict(list)
        errors: list[str] = []
        for edge in spec.causal_edges:
            signature = (edge.from_claim, edge.to_claim, edge.relation)
            if (
                edge.from_claim == edge.to_claim
                or edge.from_claim not in claim_ids
                or edge.to_claim not in claim_ids
            ):
                errors.append(f"{edge.id}: endpoints must reference distinct claims")
            if signature in seen:
                errors.append(f"{edge.id}: duplicate causal edge")
            seen.add(signature)
            graph[edge.from_claim].append(edge.to_claim)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            cyclic = any(visit(target) for target in graph[node])
            visiting.remove(node)
            visited.add(node)
            return cyclic

        if any(visit(claim_id) for claim_id in claim_ids):
            errors.append("causal_edges must not contain a directed cycle")
        return errors

    def freeze_draft(self, draft_id: str) -> EvalSpecVersion:
        draft = self.get_draft(draft_id)
        errors = self.validate_payload(
            draft.case_revision_id, json.loads(draft.payload_json), for_freeze=True
        )
        if errors:
            raise AnalystBenchError(
                "validation_failed", "eval spec cannot be frozen", {"errors": errors}
            )
        with transaction(self.session_factory) as session:
            draft = session.get(EvalSpecDraft, draft_id)
            assert draft is not None
            current = session.scalar(
                select(func.max(EvalSpecVersion.version_number)).where(
                    EvalSpecVersion.case_revision_id == draft.case_revision_id
                )
            )
            payload = json.loads(draft.payload_json)
            ref = self.content_store.put_json(payload)
            self._store_ref(session, ref)
            session.flush()
            version = EvalSpecVersion(
                id=str(uuid4()),
                case_revision_id=draft.case_revision_id,
                version_number=int(current or 0) + 1,
                payload_json=canonical_json(payload),
                content_hash=ref.content_hash,
            )
            draft.status = "frozen"
            session.add(version)
            session.flush()
            session.expunge(version)
            return version
