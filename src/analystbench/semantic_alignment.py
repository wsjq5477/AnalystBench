"""Structured semantic alignment contracts without report-fragment extraction."""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from analystbench.eval_spec import EvalSpecV1


class SemanticAlignment(BaseModel):
    """One model decision for one Gold Claim."""

    model_config = ConfigDict(extra="forbid")

    gold_claim_id: str
    relation: Literal["match", "partial_match", "missing", "contradiction"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    subject_match: bool
    predicate_match: bool
    causal_direction_match: bool | None = None
    missing_essential_facts: list[str] = Field(default_factory=list)
    conclusion_similarity: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def relation_consistency(self) -> "SemanticAlignment":
        if self.relation == "missing" and (self.subject_match or self.predicate_match):
            raise ValueError("missing relation requires false subject_match and predicate_match")
        if self.relation == "partial_match" and not (
            self.subject_match and self.predicate_match
        ):
            raise ValueError(
                "partial_match requires matching core subject and predicate; "
                "a different process or service is missing"
            )
        return self


class SemanticJudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alignments: list[SemanticAlignment]


def validate_semantic_alignment(
    payload: dict[str, Any], spec: EvalSpecV1
) -> SemanticJudgeOutput:
    """Validate model output has exactly one decision for every Gold Claim."""
    output = SemanticJudgeOutput.model_validate(payload)
    expected_gold = [claim.id for claim in spec.claims]
    actual_gold = [item.gold_claim_id for item in output.alignments]
    if len(actual_gold) != len(set(actual_gold)) or set(actual_gold) != set(expected_gold):
        raise ValueError("alignments must contain every Gold Claim exactly once")
    chain_ids = {claim.id for claim in spec.claims if claim.type == "analysis_chain"}
    missing_similarity = [
        item.gold_claim_id
        for item in output.alignments
        if item.gold_claim_id in chain_ids and item.conclusion_similarity is None
    ]
    if missing_similarity:
        raise ValueError(
            "analysis_chain alignments require conclusion_similarity: "
            f"{sorted(missing_similarity)}"
        )
    return output


def make_semantic_alignment_judge(
    payload: dict[str, Any],
) -> Callable[[EvalSpecV1, list[Any], str], dict[str, Any]]:
    """Adapt model semantic JSON to the scorer's alignment callback."""

    def _judge(spec: EvalSpecV1, _candidates: list[Any], _report: str) -> dict[str, Any]:
        output = validate_semantic_alignment(payload, spec)
        alignments = [
            {
                "gold_claim_id": item.gold_claim_id,
                "candidate_claim_id": None,
                "relation": item.relation,
                "confidence": item.confidence,
                "reason": item.reason,
                "candidate_ref": None,
                "certainty": None,
                "semantic_details": {
                    "subject_match": item.subject_match,
                    "predicate_match": item.predicate_match,
                    "causal_direction_match": item.causal_direction_match,
                    "missing_essential_facts": item.missing_essential_facts,
                    "conclusion_similarity": item.conclusion_similarity,
                },
            }
            for item in output.alignments
        ]
        return {
            "alignments": alignments,
            "supported_candidate_claim_ids": [],
            "candidate_assessments": [],
        }

    return _judge


def semantic_alignment_errors(payload: dict[str, Any], spec: EvalSpecV1) -> list[str]:
    """Expose Pydantic and contract errors in a user-facing form."""
    try:
        validate_semantic_alignment(payload, spec)
    except (ValidationError, ValueError) as exc:
        return [str(exc)]
    return []
