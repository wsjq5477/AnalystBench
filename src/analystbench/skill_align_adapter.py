"""Adapt claude Skill semantic JSON to deterministic scoring."""

from collections.abc import Callable
from typing import Any

from analystbench.eval_spec import EvalSpecV1
from analystbench.semantic_alignment import (
    SemanticJudgeOutput,
    make_semantic_alignment_judge,
    validate_semantic_alignment,
)


def make_skill_alignment_judge(
    claude_json: dict[str, Any],
    spec: EvalSpecV1,
) -> Callable[[EvalSpecV1, list[Any], str], dict[str, Any]]:
    """Validate a Skill JSON and adapt it to deterministic scoring."""
    validate_semantic_alignment(claude_json, spec)
    return make_semantic_alignment_judge(claude_json)


def validate_alignment_json(
    claude_json: dict[str, Any], spec: EvalSpecV1
) -> SemanticJudgeOutput:
    """Validate a Skill semantic alignment."""
    return validate_semantic_alignment(claude_json, spec)
