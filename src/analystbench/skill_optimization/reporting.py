"""Deterministic ledgers and exports for Skill optimization experiments.

The optimizer may describe why it proposed a mutation, but it never supplies the
scores in this module.  Score, change, gate, and decision fields are derived from
the persisted experiment detail returned by the optimization service.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from typing import Any

from analystbench.storage.content import canonical_json

LEDGER_SCHEMA_VERSION = "1"
_FINAL_COMPARISON_TYPES = (
    "paired_repeated_validation",
    "full_validation",
    "validation",
)
_VERSION_FIELDS = (
    "version_number",
    "package_hash",
    "parent_version_id",
    "source_type",
    "status",
    "created_at",
)


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: object) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _sequence(value: object) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _mapping(value: object) -> Mapping[Any, Any]:
    return value if isinstance(value, Mapping) else {}


def _stable_value(value: object) -> Any:
    """Return a JSON-safe value without retaining arbitrary object reprs."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        materialized = [_stable_value(item) for item in value]
        return sorted(materialized, key=canonical_json)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_stable_value(item) for item in value]
    return None


def _version_index(detail: Mapping[str, Any]) -> dict[str, object]:
    raw = detail.get("version_metadata")
    if raw is None:
        raw = detail.get("versions")
    result: dict[str, object] = {}
    if isinstance(raw, Mapping):
        for key, item in raw.items():
            version_id = _text(_get(item, "id")) or _text(_get(item, "version_id")) or _text(key)
            if version_id:
                result[version_id] = item
        return result
    for item in _sequence(raw):
        version_id = _text(_get(item, "id")) or _text(_get(item, "version_id"))
        if version_id:
            result[version_id] = item
    return result


def _version_summary(
    version_id: object,
    version_index: Mapping[str, object],
    *inline_sources: object,
) -> dict[str, Any] | None:
    identifier = _text(version_id)
    if identifier is None:
        return None
    sources = [version_index.get(identifier), *inline_sources]
    output: dict[str, Any] = {"version_id": identifier}
    for field in _VERSION_FIELDS:
        value = next(
            (
                _get(source, field)
                for source in sources
                if source is not None and _get(source, field) is not None
            ),
            None,
        )
        output[field] = _stable_value(value)
    return output


def _numeric_mapping(value: object) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, item in sorted(_mapping(value).items(), key=lambda pair: str(pair[0])):
        number = _number(item)
        if number is not None:
            result[str(key)] = number
    return result


def _average(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _case_deltas(metrics: Mapping[Any, Any]) -> list[dict[str, Any]]:
    raw_pairs = metrics.get("pairs")
    if raw_pairs is None:
        raw_pairs = metrics.get("case_deltas")
    output: list[dict[str, Any]] = []
    for pair in _sequence(raw_pairs):
        if not isinstance(pair, Mapping):
            continue
        baseline = _number(pair.get("baseline_score"))
        candidate = _number(pair.get("candidate_score"))
        delta = _number(pair.get("delta"))
        if delta is None and baseline is not None and candidate is not None:
            delta = candidate - baseline
        output.append(
            {
                "case_path": _text(pair.get("case_path")) or _text(pair.get("case_key")),
                "case_family": _text(pair.get("case_family")) or _text(pair.get("family")),
                "baseline_score": baseline,
                "candidate_score": candidate,
                "delta": delta,
                "baseline_duration_ms": _number(pair.get("baseline_duration_ms")),
                "candidate_duration_ms": _number(pair.get("candidate_duration_ms")),
                "baseline_tokens": _number(pair.get("baseline_tokens")),
                "candidate_tokens": _number(pair.get("candidate_tokens")),
                "dimension_deltas": _numeric_mapping(pair.get("dimension_deltas")),
                "baseline_failure_tags": sorted(
                    filter(
                        None, (_text(item) for item in _sequence(pair.get("baseline_failure_tags")))
                    )
                ),
                "candidate_failure_tags": sorted(
                    filter(
                        None,
                        (_text(item) for item in _sequence(pair.get("candidate_failure_tags"))),
                    )
                ),
            }
        )
    return sorted(
        output,
        key=lambda item: (
            item["case_path"] is None,
            item["case_path"] or "",
            item["case_family"] or "",
        ),
    )


def _score_from_metrics(
    metrics: Mapping[Any, Any], arm: str, pairs: Sequence[Mapping[str, Any]]
) -> float | None:
    for key in (
        f"{arm}_score",
        f"{arm}_mean_score",
        f"{arm}_average_score",
        f"{arm}_avg_score",
    ):
        value = _number(metrics.get(key))
        if value is not None:
            return value
    scores = _mapping(metrics.get("scores"))
    value = _number(scores.get(arm))
    if value is not None:
        return value
    arm_metrics = _mapping(metrics.get(arm))
    for key in ("score", "mean_score", "average_score", "avg_score"):
        value = _number(arm_metrics.get(key))
        if value is not None:
            return value
    values = [value for pair in pairs if (value := _number(pair.get(f"{arm}_score"))) is not None]
    return _average(values)


def _derived_group_deltas(pairs: Sequence[Mapping[str, Any]], key: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for pair in pairs:
        name = _text(pair.get(key))
        delta = _number(pair.get("delta"))
        if name is not None and delta is not None:
            grouped.setdefault(name, []).append(delta)
    return {name: _average(grouped[name]) or 0.0 for name in sorted(grouped)}


def _derived_dimension_deltas(
    pairs: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for pair in pairs:
        for name, value in _numeric_mapping(pair.get("dimension_deltas")).items():
            grouped.setdefault(name, []).append(value)
    return {name: _average(grouped[name]) or 0.0 for name in sorted(grouped)}


def _comparison_summary(comparison: object) -> dict[str, Any] | None:
    if comparison is None:
        return None
    metrics = _mapping(_get(comparison, "metrics"))
    pairs = _case_deltas(metrics)
    baseline = _score_from_metrics(metrics, "baseline", pairs)
    candidate = _score_from_metrics(metrics, "candidate", pairs)
    delta = _number(metrics.get("overall_delta"))
    if delta is None and baseline is not None and candidate is not None:
        delta = candidate - baseline
    family_deltas = _numeric_mapping(metrics.get("family_deltas"))
    if not family_deltas:
        family_deltas = _derived_group_deltas(pairs, "case_family")
    dimension_deltas = _numeric_mapping(metrics.get("dimension_deltas"))
    if not dimension_deltas:
        dimension_deltas = _derived_dimension_deltas(pairs)
    improved = _integer(metrics.get("improved_case_count"))
    unchanged = _integer(metrics.get("unchanged_case_count"))
    regressed = _integer(metrics.get("regressed_case_count"))
    deltas = [_number(pair.get("delta")) for pair in pairs]
    valid_deltas = [value for value in deltas if value is not None]
    gate = _mapping(_get(comparison, "gate"))
    return {
        "comparison_id": _text(_get(comparison, "id")),
        "comparison_type": _text(_get(comparison, "type"))
        or _text(_get(comparison, "comparison_type")),
        "baseline_score": baseline,
        "candidate_score": candidate,
        "delta": delta,
        "case_count": _integer(metrics.get("case_count"))
        if _integer(metrics.get("case_count")) is not None
        else len(pairs),
        "improved_case_count": improved
        if improved is not None
        else sum(value > 0 for value in valid_deltas),
        "unchanged_case_count": unchanged
        if unchanged is not None
        else sum(value == 0 for value in valid_deltas),
        "regressed_case_count": regressed
        if regressed is not None
        else sum(value < 0 for value in valid_deltas),
        "baseline_failure_count": _integer(metrics.get("baseline_failure_count")),
        "candidate_failure_count": _integer(metrics.get("candidate_failure_count")),
        "repeat_count": _integer(metrics.get("repeat_count")),
        "bootstrap_confidence": _number(metrics.get("bootstrap_confidence")),
        "bootstrap_interval": _stable_value(metrics.get("bootstrap_interval")),
        "candidate_win_probability": _number(metrics.get("candidate_win_probability")),
        "case_deltas": pairs,
        "family_deltas": family_deltas,
        "dimension_deltas": dimension_deltas,
        "gate": {
            "verdict": _text(gate.get("verdict")),
            "active_level": _text(gate.get("active_level")),
            "reasons": _stable_value(gate.get("reasons")) or [],
            "metrics": _stable_value(gate.get("metrics")) or {},
        }
        if gate
        else None,
    }


def _comparison_type(value: object) -> str:
    return _text(_get(value, "type")) or _text(_get(value, "comparison_type")) or ""


def _latest_comparison(candidate: object, types: Sequence[str]) -> object | None:
    accepted = [
        item
        for item in _sequence(_get(candidate, "comparisons"))
        if _comparison_type(item) in types
    ]
    if not accepted:
        return None
    type_rank = {name: index for index, name in enumerate(types)}
    best_rank = min(type_rank[_comparison_type(item)] for item in accepted)
    same_type = [item for item in accepted if type_rank[_comparison_type(item)] == best_rank]
    return sorted(
        same_type,
        key=lambda item: (
            _text(_get(item, "created_at")) or "",
            _text(_get(item, "id")) or "",
        ),
    )[-1]


def _change_stat(stats: Mapping[Any, Any], *names: str) -> int | None:
    for name in names:
        value = _integer(stats.get(name))
        if value is not None:
            return value
    return None


def _candidate_change_stats(
    candidate: object, version_index: Mapping[str, object]
) -> dict[str, Any]:
    patch = _mapping(_get(candidate, "patch"))
    operations = [item for item in _sequence(patch.get("operations")) if isinstance(item, Mapping)]
    operation_summaries = sorted(
        (
            {
                "op": _text(item.get("op")),
                "path": _text(item.get("path")),
            }
            for item in operations
        ),
        key=lambda item: (item["path"] or "", item["op"] or ""),
    )
    operation_types = Counter(item["op"] for item in operation_summaries if item["op"] is not None)
    files = sorted({item["path"] for item in operation_summaries if item["path"] is not None})
    version_id = _text(_get(candidate, "candidate_skill_version_id"))
    version_metadata = version_index.get(version_id or "")
    stats = _mapping(_get(candidate, "change_stats"))
    if not stats:
        stats = _mapping(_get(version_metadata, "change_stats"))
    supplied_files = _sequence(stats.get("files"))
    if not supplied_files:
        supplied_files = _sequence(stats.get("changed_files"))
    if not supplied_files:
        supplied_files = _sequence(stats.get("modified_files"))
    normalized_supplied_files = sorted(filter(None, (_text(item) for item in supplied_files)))
    if normalized_supplied_files:
        files = normalized_supplied_files
    operation_count = len(operations)
    supplied_operation_count = _change_stat(stats, "operation_count", "operations_count")
    if supplied_operation_count is not None:
        operation_count = supplied_operation_count
    tokens_added = _change_stat(stats, "tokens_added", "added_tokens", "token_additions")
    tokens_removed = _change_stat(
        stats,
        "tokens_removed",
        "removed_tokens",
        "deleted_tokens",
        "token_deletions",
    )
    token_delta = _change_stat(stats, "token_delta", "tokens_delta")
    if token_delta is None and tokens_added is not None and tokens_removed is not None:
        token_delta = tokens_added - tokens_removed
    supplied_file_count = _change_stat(
        stats, "file_count", "changed_file_count", "modified_file_count"
    )
    return {
        "operation_count": operation_count,
        "operation_types": {name: operation_types[name] for name in sorted(operation_types)},
        "file_count": supplied_file_count if supplied_file_count is not None else len(files),
        "files": files,
        "tokens_added": tokens_added,
        "tokens_removed": tokens_removed,
        "token_delta": token_delta,
        "characters_added": _change_stat(
            stats, "characters_added", "added_characters", "character_additions"
        ),
        "characters_removed": _change_stat(
            stats, "characters_removed", "removed_characters", "character_deletions"
        ),
        "lines_added": _change_stat(stats, "lines_added", "added_lines"),
        "lines_removed": _change_stat(stats, "lines_removed", "removed_lines"),
        "per_file": _stable_value(stats.get("per_file")) or {},
        "operations": operation_summaries,
    }


def _intent(candidate: object) -> dict[str, Any]:
    patch = _mapping(_get(candidate, "patch"))
    raw_intent = _mapping(_get(candidate, "intent"))
    if not raw_intent:
        raw_intent = _mapping(patch.get("intent"))

    def values(*names: str) -> list[str]:
        for source in (candidate, raw_intent, patch):
            for name in names:
                raw = _get(source, name)
                if raw is not None:
                    return sorted(filter(None, (_text(item) for item in _sequence(raw))))
        return []

    return {
        "rationale": _text(_get(candidate, "rationale"))
        or _text(raw_intent.get("rationale"))
        or _text(patch.get("rationale")),
        "change_type": _text(raw_intent.get("change_type"))
        or _text(_get(candidate, "change_type"))
        or _text(patch.get("change_type")),
        "failure_clusters": values(
            "intended_failure_clusters", "failure_clusters", "target_failure_clusters"
        ),
        "expected_dimensions": values(
            "expected_dimensions", "target_dimensions", "intended_dimensions"
        ),
        "protected_behaviors": values("protected_behaviors", "preserve", "guardrails"),
    }


def _candidate_summary(candidate: object, version_index: Mapping[str, object]) -> dict[str, Any]:
    version_id = _text(_get(candidate, "candidate_skill_version_id"))
    validation = _comparison_summary(_latest_comparison(candidate, _FINAL_COMPARISON_TYPES))
    screening = _comparison_summary(_latest_comparison(candidate, ("screening",)))
    rejection_detail = _stable_value(_get(candidate, "rejection_detail")) or {}
    rejection_code = _text(_get(candidate, "rejection_code"))
    if rejection_code is None and validation and validation.get("gate"):
        gate = _mapping(validation["gate"])
        if gate.get("verdict") == "reject":
            reasons = _sequence(gate.get("reasons"))
            if reasons:
                rejection_code = _text(_get(reasons[-1], "code"))
    return {
        "candidate_id": _text(_get(candidate, "id")),
        "version": _version_summary(
            version_id,
            version_index,
            _get(candidate, "version_metadata"),
            candidate,
        ),
        "candidate_type": _text(_get(candidate, "candidate_type")),
        "status": _text(_get(candidate, "status")),
        "patch_hash": _text(_get(candidate, "patch_hash")),
        "intent": _intent(candidate),
        "changes": _candidate_change_stats(candidate, version_index),
        "screening": screening,
        "validation": validation,
        "rejection": {
            "code": rejection_code,
            "detail": rejection_detail,
        }
        if rejection_code is not None or rejection_detail
        else None,
    }


def _select_candidate(epoch: object, candidates: Sequence[object]) -> object | None:
    selected_id = _text(_get(epoch, "selected_candidate_mutation_id"))
    if selected_id:
        match = next(
            (item for item in candidates if _text(_get(item, "id")) == selected_id),
            None,
        )
        if match is not None:
            return match
    best_version_id = _text(_get(epoch, "best_candidate_version_id"))
    if best_version_id:
        match = next(
            (
                item
                for item in candidates
                if _text(_get(item, "candidate_skill_version_id")) == best_version_id
            ),
            None,
        )
        if match is not None:
            return match
    status_rank = {
        "accepted": 0,
        "needs_more_runs": 1,
        "validating": 2,
        "screening_selected": 3,
    }
    ranked_statuses = [item for item in candidates if _text(_get(item, "status")) in status_rank]
    if ranked_statuses:
        return sorted(
            ranked_statuses,
            key=lambda item: (
                status_rank[_text(_get(item, "status")) or ""],
                _text(_get(item, "id")) or "",
            ),
        )[0]
    validated = [
        item for item in candidates if _latest_comparison(item, _FINAL_COMPARISON_TYPES) is not None
    ]
    if validated:
        return sorted(validated, key=lambda item: _text(_get(item, "id")) or "")[0]
    # A completed Epoch may legitimately have no screening survivor. Still
    # report the best measured attempt so operators can answer both "what was
    # changed" and "how much did it help or hurt". This is reporting only: the
    # decision below remains retain and cumulative Active delta remains zero.
    screened = [
        item
        for item in candidates
        if _latest_comparison(item, ("screening",)) is not None
    ]
    if screened:
        def screening_rank(item: object) -> tuple[bool, float, str]:
            delta = _number(
                _mapping(
                    _comparison_summary(_latest_comparison(item, ("screening",)))
                ).get("delta")
            )
            return (
                delta is None,
                -(delta if delta is not None else 0.0),
                _text(_get(item, "id")) or "",
            )

        return sorted(
            screened,
            key=screening_rank,
        )[0]
    return None


def _experiment_summary(experiment: object) -> dict[str, Any]:
    return {
        "id": _text(_get(experiment, "id")),
        "name": _text(_get(experiment, "name")),
        "status": _text(_get(experiment, "status")),
        "skill_id": _text(_get(experiment, "skill_id")),
        "base_skill_version_id": _text(_get(experiment, "base_skill_version_id")),
        "evaluation_target_id": _text(_get(experiment, "evaluation_target_id")),
        "data_snapshot_id": _text(_get(experiment, "data_snapshot_id")),
        "optimizer_policy_version_id": _text(_get(experiment, "optimizer_policy_version_id")),
        "verifier_bundle_version_id": _text(_get(experiment, "verifier_bundle_version_id")),
        "current_epoch_number": _integer(_get(experiment, "current_epoch_number")),
        "max_epochs": _integer(_get(experiment, "max_epochs")),
        "stop_reason": _text(_get(experiment, "stop_reason")),
        "started_at": _stable_value(_get(experiment, "started_at")),
        "finished_at": _stable_value(_get(experiment, "finished_at")),
    }


def build_optimization_ledger(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic, JSON-safe optimization ledger from service detail.

    Missing or in-progress comparison data stays ``None``.  In particular, this
    function never asks a model to infer a score or fabricates a zero delta.
    """

    experiment = detail.get("experiment")
    experiment_summary = _experiment_summary(experiment)
    versions = _version_index(detail)
    raw_epochs = sorted(
        _sequence(detail.get("epochs")),
        key=lambda item: (
            _integer(_get(item, "number")) is None,
            _integer(_get(item, "number")) or 0,
            _text(_get(item, "id")) or "",
        ),
    )
    epochs: list[dict[str, Any]] = []
    initial_score: float | None = None
    cumulative_delta: float | None = None
    cumulative_known = True
    promoted_epochs = 0
    retained_epochs = 0

    for index, epoch in enumerate(raw_epochs):
        raw_candidates = sorted(
            _sequence(_get(epoch, "candidates")),
            key=lambda item: _text(_get(item, "id")) or "",
        )
        selected_raw = _select_candidate(epoch, raw_candidates)
        candidate_summaries = [_candidate_summary(item, versions) for item in raw_candidates]
        selected_summary = (
            _candidate_summary(selected_raw, versions) if selected_raw is not None else None
        )
        comparison = selected_summary.get("validation") if selected_summary is not None else None
        if comparison is None and selected_summary is not None:
            comparison = selected_summary.get("screening")
        comparison_map = _mapping(comparison)
        baseline_score = _number(comparison_map.get("baseline_score"))
        candidate_score = _number(comparison_map.get("candidate_score"))
        epoch_delta = _number(comparison_map.get("delta"))
        decision_value = _text(_get(epoch, "decision"))
        if initial_score is None and baseline_score is not None and cumulative_known:
            initial_score = baseline_score
            cumulative_delta = 0.0
        if index == 0 and initial_score is None:
            cumulative_delta = None
        if decision_value == "promote":
            promoted_epochs += 1
            if initial_score is not None and cumulative_known and epoch_delta is not None:
                assert cumulative_delta is not None
                cumulative_delta += epoch_delta
            else:
                cumulative_known = False
                cumulative_delta = None
        elif decision_value in {"retain", "no_screening_survivor"}:
            retained_epochs += 1
        parent_version_id = _text(_get(epoch, "parent_skill_version_id"))
        selected_version = _mapping(selected_summary.get("version")) if selected_summary else {}
        selected_version_id = _text(selected_version.get("version_id"))
        action_known = decision_value is not None
        new_active_version_id = None
        if decision_value == "promote":
            new_active_version_id = selected_version_id
        elif action_known:
            new_active_version_id = parent_version_id
        gate = comparison_map.get("gate") if comparison else None
        rejections = [
            {
                "candidate_id": item["candidate_id"],
                "candidate_version_id": _get(item.get("version"), "version_id"),
                "code": _get(item.get("rejection"), "code"),
                "detail": _get(item.get("rejection"), "detail") or {},
            }
            for item in candidate_summaries
            if item.get("rejection") is not None or item.get("status") == "rejected"
        ]
        epochs.append(
            {
                "epoch_id": _text(_get(epoch, "id")),
                "epoch_number": _integer(_get(epoch, "number")),
                "status": _text(_get(epoch, "status")),
                "finished_at": _stable_value(_get(epoch, "finished_at")),
                "parent": _version_summary(
                    parent_version_id,
                    versions,
                    _get(epoch, "parent_version_metadata"),
                ),
                "selected_candidate": selected_summary,
                "baseline_score": baseline_score,
                "candidate_score": candidate_score,
                "epoch_delta": epoch_delta,
                "cumulative_delta": cumulative_delta if cumulative_known else None,
                "case_deltas": comparison_map.get("case_deltas", []),
                "family_deltas": comparison_map.get("family_deltas", {}),
                "dimension_deltas": comparison_map.get("dimension_deltas", {}),
                "changes": selected_summary.get("changes") if selected_summary else None,
                "gate": gate,
                "decision": {
                    "action": decision_value,
                    "active_changed": (
                        decision_value == "promote"
                        and selected_version_id is not None
                        and selected_version_id != parent_version_id
                    )
                    if action_known
                    else None,
                    "previous_active_version_id": parent_version_id,
                    "new_active_version_id": new_active_version_id,
                },
                "rejections": rejections,
                "candidates": candidate_summaries,
            }
        )

    final_score = (
        initial_score + cumulative_delta
        if initial_score is not None and cumulative_known and cumulative_delta is not None
        else None
    )
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "experiment": experiment_summary,
        "summary": {
            "initial_score": initial_score,
            "final_score": final_score,
            "active_path_score": final_score,
            "score_semantics": "initial_score_plus_promoted_epoch_deltas",
            "cumulative_delta": cumulative_delta if cumulative_known else None,
            "promoted_epochs": promoted_epochs,
            "retained_epochs": retained_epochs,
            "stop_reason": experiment_summary["stop_reason"],
        },
        "epochs": epochs,
    }


def serialize_optimization_ledger(ledger: Mapping[str, Any]) -> str:
    """Serialize a ledger as canonical JSON for stable downloads and hashing."""

    return canonical_json(ledger)


def _format_number(value: object, *, signed: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if abs(number) < 0.0000005:
        number = 0.0
    rendered = f"{abs(number):.4f}".rstrip("0").rstrip(".")
    if signed and number > 0:
        return f"+{rendered}"
    if number < 0:
        return f"-{rendered}"
    return rendered


def _markdown_cell(value: object) -> str:
    text = _text(value) or "—"
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _version_label(version: object) -> str:
    data = _mapping(version)
    identifier = _text(data.get("version_id"))
    number = _integer(data.get("version_number"))
    if number is not None and identifier:
        return f"v{number} (`{identifier}`)"
    if number is not None:
        return f"v{number}"
    return f"`{identifier}`" if identifier else "—"


def _reason_label(reason: object) -> str:
    code = _text(_get(reason, "code"))
    if code:
        return code
    stable = _stable_value(reason)
    return canonical_json(stable) if stable is not None else "unknown"


def render_optimization_ledger_markdown(ledger: Mapping[str, Any]) -> str:
    """Render a human-readable Chinese Markdown optimization ledger."""

    experiment = _mapping(ledger.get("experiment"))
    summary = _mapping(ledger.get("summary"))
    lines = [
        "# Skill 自优化账本",
        "",
        f"- 实验：{_markdown_cell(experiment.get('name'))}",
        f"- 实验 ID：`{_markdown_cell(experiment.get('id'))}`",
        f"- 状态：{_markdown_cell(experiment.get('status'))}",
        f"- 停止原因：{_markdown_cell(summary.get('stop_reason'))}",
        "",
        "## 总结",
        "",
        "| 初始分 | Active 路径分 | 累计晋升变化 | 晋升轮数 | 保留轮数 |",
        "|---:|---:|---:|---:|---:|",
        "| "
        f"{_format_number(summary.get('initial_score'))} | "
        f"{_format_number(summary.get('active_path_score', summary.get('final_score')))} | "
        f"{_format_number(summary.get('cumulative_delta'), signed=True)} | "
        f"{_format_number(summary.get('promoted_epochs'))} | "
        f"{_format_number(summary.get('retained_epochs'))} |",
    ]
    epochs = _sequence(ledger.get("epochs"))
    if not epochs:
        lines.extend(["", "尚无 Epoch 数据。"])
        return "\n".join(lines).rstrip() + "\n"

    for epoch in epochs:
        epoch_map = _mapping(epoch)
        selected = _mapping(epoch_map.get("selected_candidate"))
        intent = _mapping(selected.get("intent"))
        changes = _mapping(epoch_map.get("changes"))
        gate = _mapping(epoch_map.get("gate"))
        decision = _mapping(epoch_map.get("decision"))
        lines.extend(
            [
                "",
                f"## Epoch {_markdown_cell(epoch_map.get('epoch_number'))}",
                "",
                f"- 状态：{_markdown_cell(epoch_map.get('status'))}",
                f"- 父版本：{_version_label(epoch_map.get('parent'))}",
                f"- 入选候选：{_version_label(selected.get('version'))}",
                f"- 决策：{_markdown_cell(decision.get('action'))}",
                "- 分数："
                f"{_format_number(epoch_map.get('baseline_score'))} → "
                f"{_format_number(epoch_map.get('candidate_score'))}；"
                f"本轮 {_format_number(epoch_map.get('epoch_delta'), signed=True)}；"
                f"累计 {_format_number(epoch_map.get('cumulative_delta'), signed=True)}",
            ]
        )
        if intent.get("rationale"):
            lines.append(f"- 修改理由：{_markdown_cell(intent.get('rationale'))}")
        if intent.get("change_type"):
            lines.append(f"- 修改类型：{_markdown_cell(intent.get('change_type'))}")
        if intent.get("failure_clusters"):
            lines.append(
                "- 针对问题："
                + "、".join(_markdown_cell(item) for item in intent["failure_clusters"])
            )
        if changes:
            files = (
                "、".join(f"`{_markdown_cell(item)}`" for item in _sequence(changes.get("files")))
                or "—"
            )
            token_text = (
                f"+{_format_number(changes.get('tokens_added'))} / "
                f"-{_format_number(changes.get('tokens_removed'))}"
                if changes.get("tokens_added") is not None
                or changes.get("tokens_removed") is not None
                else "—"
            )
            lines.append(
                f"- 实际修改：{_format_number(changes.get('operation_count'))} 个操作，"
                f"{_format_number(changes.get('file_count'))} 个文件（{files}），"
                f"Token 增/删：{token_text}"
            )
        if gate:
            lines.append(
                f"- Gate：{_markdown_cell(gate.get('verdict'))}；"
                f"Active Level：{_markdown_cell(gate.get('active_level'))}"
            )
            reasons = _sequence(gate.get("reasons"))
            if reasons:
                lines.append("- Gate 原因：" + "、".join(_reason_label(item) for item in reasons))

        case_deltas = _sequence(epoch_map.get("case_deltas"))
        if case_deltas:
            lines.extend(
                [
                    "",
                    "### Case 变化",
                    "",
                    "| Case | Family | 基线 | 候选 | Delta |",
                    "|---|---|---:|---:|---:|",
                ]
            )
            for item in case_deltas:
                lines.append(
                    f"| {_markdown_cell(_get(item, 'case_path'))} | "
                    f"{_markdown_cell(_get(item, 'case_family'))} | "
                    f"{_format_number(_get(item, 'baseline_score'))} | "
                    f"{_format_number(_get(item, 'candidate_score'))} | "
                    f"{_format_number(_get(item, 'delta'), signed=True)} |"
                )

        for title, key in (
            ("Family 变化", "family_deltas"),
            ("Dimension 变化", "dimension_deltas"),
        ):
            values = _mapping(epoch_map.get(key))
            if values:
                lines.extend(["", f"### {title}", "", "| 名称 | Delta |", "|---|---:|"])
                for name, value in sorted(values.items(), key=lambda pair: str(pair[0])):
                    lines.append(
                        f"| {_markdown_cell(name)} | {_format_number(value, signed=True)} |"
                    )

        rejections = _sequence(epoch_map.get("rejections"))
        if rejections:
            lines.extend(["", "### 拒绝记录", ""])
            for rejection in rejections:
                lines.append(
                    "- "
                    f"`{_markdown_cell(_get(rejection, 'candidate_id'))}`："
                    f"{_markdown_cell(_get(rejection, 'code'))}"
                )
    return "\n".join(lines).rstrip() + "\n"


_CSV_FIELDS = (
    "experiment_id",
    "experiment_name",
    "experiment_status",
    "epoch_number",
    "epoch_status",
    "parent_version_id",
    "parent_version_number",
    "selected_candidate_id",
    "selected_candidate_version_id",
    "selected_candidate_version_number",
    "decision",
    "gate_verdict",
    "active_level",
    "baseline_score",
    "candidate_score",
    "epoch_delta",
    "cumulative_delta",
    "case_count",
    "improved_case_count",
    "unchanged_case_count",
    "regressed_case_count",
    "changed_files",
    "operation_count",
    "tokens_added",
    "tokens_removed",
    "family_deltas_json",
    "dimension_deltas_json",
    "case_deltas_json",
    "rejection_count",
    "rejection_codes",
)


def render_optimization_ledger_csv(ledger: Mapping[str, Any]) -> str:
    """Render one stable CSV row per Epoch."""

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    experiment = _mapping(ledger.get("experiment"))
    for epoch in _sequence(ledger.get("epochs")):
        epoch_map = _mapping(epoch)
        parent = _mapping(epoch_map.get("parent"))
        selected = _mapping(epoch_map.get("selected_candidate"))
        selected_version = _mapping(selected.get("version"))
        validation = _mapping(selected.get("validation"))
        if not validation:
            validation = _mapping(selected.get("screening"))
        gate = _mapping(epoch_map.get("gate"))
        decision = _mapping(epoch_map.get("decision"))
        changes = _mapping(epoch_map.get("changes"))
        rejections = _sequence(epoch_map.get("rejections"))
        writer.writerow(
            {
                "experiment_id": experiment.get("id"),
                "experiment_name": experiment.get("name"),
                "experiment_status": experiment.get("status"),
                "epoch_number": epoch_map.get("epoch_number"),
                "epoch_status": epoch_map.get("status"),
                "parent_version_id": parent.get("version_id"),
                "parent_version_number": parent.get("version_number"),
                "selected_candidate_id": selected.get("candidate_id"),
                "selected_candidate_version_id": selected_version.get("version_id"),
                "selected_candidate_version_number": selected_version.get("version_number"),
                "decision": decision.get("action"),
                "gate_verdict": gate.get("verdict"),
                "active_level": gate.get("active_level"),
                "baseline_score": epoch_map.get("baseline_score"),
                "candidate_score": epoch_map.get("candidate_score"),
                "epoch_delta": epoch_map.get("epoch_delta"),
                "cumulative_delta": epoch_map.get("cumulative_delta"),
                "case_count": validation.get("case_count"),
                "improved_case_count": validation.get("improved_case_count"),
                "unchanged_case_count": validation.get("unchanged_case_count"),
                "regressed_case_count": validation.get("regressed_case_count"),
                "changed_files": ";".join(str(item) for item in _sequence(changes.get("files"))),
                "operation_count": changes.get("operation_count"),
                "tokens_added": changes.get("tokens_added"),
                "tokens_removed": changes.get("tokens_removed"),
                "family_deltas_json": canonical_json(epoch_map.get("family_deltas") or {}),
                "dimension_deltas_json": canonical_json(epoch_map.get("dimension_deltas") or {}),
                "case_deltas_json": canonical_json(epoch_map.get("case_deltas") or []),
                "rejection_count": len(rejections),
                "rejection_codes": ";".join(
                    filter(None, (_text(_get(item, "code")) for item in rejections))
                ),
            }
        )
    return output.getvalue()
