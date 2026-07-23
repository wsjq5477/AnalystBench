"""Deterministic, explainable Eval Spec v1 analysis, alignment, and scoring."""

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from typing import Any, Literal

from analystbench.eval_spec import EvalSpecV1

ClaimRelation = Literal["match", "partial_match", "missing", "contradiction"]
EdgeRelation = Literal[
    "edge_match", "edge_partial", "edge_missing", "edge_reversed", "edge_conflict"
]

_RELATION_FACTOR = {
    "match": Decimal("1"),
    "partial_match": Decimal("0.5"),
    "missing": Decimal("0"),
    "contradiction": Decimal("0"),
}
_CERTAINTY_FACTOR = {
    "confirmed": Decimal("1"),
    "probable": Decimal("0.9"),
    "suspected": Decimal("0.7"),
    "possible": Decimal("0.5"),
}


@dataclass(frozen=True)
class CandidateClaim:
    id: str
    statement: str
    type: str
    certainty: Literal["confirmed", "probable", "suspected", "possible"]
    source_ref: dict[str, Any]


class CandidateAnalyzer:
    """A local, citation-preserving baseline analyzer with an in-memory cache."""

    def __init__(self) -> None:
        self._cache: dict[str, list[CandidateClaim]] = {}

    def analyze(
        self,
        report: str,
        report_content_hash: str,
        claim_hints: list[dict[str, Any]] | None = None,
    ) -> list[CandidateClaim]:
        if claim_hints:
            hinted = self._from_hints(report, report_content_hash, claim_hints)
            if hinted:
                return hinted
        if report_content_hash in self._cache:
            return self._cache[report_content_hash]
        spans = [
            (match.start(), match.end())
            for match in re.finditer(
                r".*?(?:[。！？]|(?<!\d)[.!?](?=\s|$)|\n{2,}|$)",
                report,
                flags=re.DOTALL,
            )
            if match.end() > match.start()
        ]
        claims: list[CandidateClaim] = []
        for index, (start, end) in enumerate(spans, 1):
            quote = report[start:end]
            statement = quote.strip()
            if not statement:
                continue
            lowered = statement.lower()
            certainty: Literal["confirmed", "probable", "suspected", "possible"] = "confirmed"
            if any(word in lowered for word in ("possible", "might", "may", "可能")):
                certainty = "possible"
            elif any(word in lowered for word in ("suspect", "likely", "怀疑", "疑似")):
                certainty = "suspected"
            elif any(word in lowered for word in ("probably", "probable", "大概率")):
                certainty = "probable"
            claim_type = (
                "root_cause"
                if any(
                    word in lowered
                    for word in ("cause", "caused", "root", "because", "原因", "根因")
                )
                else "evidence"
            )
            claims.append(
                CandidateClaim(
                    id=f"candidate-{index}",
                    statement=statement,
                    type=claim_type,
                    certainty=certainty,
                    source_ref={
                        "content_hash": report_content_hash,
                        "start": start,
                        "end": end,
                        "quote": quote,
                    },
                )
            )
        self._cache[report_content_hash] = claims
        return claims

    @staticmethod
    def _from_hints(
        report: str,
        report_content_hash: str,
        hints: list[dict[str, Any]],
    ) -> list[CandidateClaim]:
        claims: list[CandidateClaim] = []
        allowed_certainty = {"confirmed", "probable", "suspected", "possible"}
        for index, hint in enumerate(hints, 1):
            if not isinstance(hint, dict) or not isinstance(hint.get("statement"), str):
                continue
            statement = hint["statement"].strip()
            if not statement:
                continue
            quote = hint.get("quote")
            if isinstance(quote, str) and quote in report:
                start = report.index(quote)
                end = start + len(quote)
                cited_quote: str | None = quote
            elif statement in report:
                start = report.index(statement)
                end = start + len(statement)
                cited_quote = statement
            else:
                start = end = None
                cited_quote = None
            certainty = hint.get("certainty", "confirmed")
            if certainty not in allowed_certainty:
                certainty = "confirmed"
            claims.append(
                CandidateClaim(
                    id=f"candidate-{index}",
                    statement=statement,
                    type=str(hint.get("type") or "evidence"),
                    certainty=certainty,
                    source_ref={
                        "content_hash": report_content_hash,
                        "start": start,
                        "end": end,
                        "quote": cited_quote,
                        "source": "claim_hint",
                    },
                )
            )
        return claims


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.lower()))


def _similarity(left: str, right: str) -> Decimal:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return Decimal("0")
    overlap = Decimal(len(left_tokens & right_tokens))
    jaccard = overlap / Decimal(len(left_tokens | right_tokens))
    # Gold coverage is directional: a long candidate may contain a short Gold
    # conclusion, but a short generic candidate must not become an exact match
    # merely because all of its own words occur somewhere in a richer Gold claim.
    containment = overlap / Decimal(len(left_tokens))
    return max(jaccard, containment)


def _contradicts(gold: str, candidate: str) -> bool:
    gold_tokens, candidate_tokens = _tokens(gold), _tokens(candidate)
    overlap = gold_tokens & candidate_tokens
    if not overlap:
        return False
    candidate_lower = candidate.lower()
    gold_lower = gold.lower()
    english_negative = re.compile(r"\b(?:not|no|never|without)\b")
    chinese_markers = ("不是", "并非", "不存在", "没有", "无法", "不能", "未能")
    candidate_negative = bool(english_negative.search(candidate_lower)) or any(
        marker in candidate for marker in chinese_markers
    )
    gold_negative = bool(english_negative.search(gold_lower)) or any(
        marker in gold for marker in chinese_markers
    )
    return candidate_negative and not gold_negative


def judge_claim(gold_statement: str, candidate: CandidateClaim | None) -> ClaimRelation:
    if candidate is None:
        return "missing"
    similarity = _similarity(gold_statement, candidate.statement)
    if similarity >= Decimal("0.22") and _contradicts(gold_statement, candidate.statement):
        return "contradiction"
    # Preserve an explicit conclusion even when the analyzer's citation span
    # also contains surrounding timeline or rationale text.
    if gold_statement.lower() in candidate.statement.lower():
        return "match"
    if similarity >= Decimal("0.72"):
        return "match"
    if similarity >= Decimal("0.22"):
        return "partial_match"
    return "missing"


def _hungarian_max(weights: list[list[int]]) -> list[int]:
    """Return a maximum-weight assignment for a rectangular matrix (row -> column)."""
    if not weights:
        return []
    row_count, column_count = len(weights), len(weights[0])
    if row_count > column_count:
        raise ValueError("assignment matrix must include virtual columns")
    maximum = max(max(row) for row in weights)
    u = [0] * (row_count + 1)
    v = [0] * (column_count + 1)
    p = [0] * (column_count + 1)
    way = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        p[0] = row
        min_value = [10**30] * (column_count + 1)
        used = [False] * (column_count + 1)
        column = 0
        while True:
            used[column] = True
            current_row, delta, next_column = p[column], 10**30, 0
            for candidate_column in range(1, column_count + 1):
                if used[candidate_column]:
                    continue
                cost = maximum - weights[current_row - 1][candidate_column - 1]
                value = cost - u[current_row] - v[candidate_column]
                if value < min_value[candidate_column]:
                    min_value[candidate_column] = value
                    way[candidate_column] = column
                if min_value[candidate_column] < delta:
                    delta, next_column = min_value[candidate_column], candidate_column
            for candidate_column in range(column_count + 1):
                if used[candidate_column]:
                    u[p[candidate_column]] += delta
                    v[candidate_column] -= delta
                else:
                    min_value[candidate_column] -= delta
            column = next_column
            if p[column] == 0:
                break
        while True:
            previous = way[column]
            p[column] = p[previous]
            column = previous
            if column == 0:
                break
    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if p[column]:
            assignment[p[column] - 1] = column - 1
    return assignment


def align_claims(spec: EvalSpecV1, candidates: list[CandidateClaim]) -> list[dict[str, Any]]:
    ordered_candidates = sorted(candidates, key=lambda claim: claim.id)
    virtual_count = len(spec.claims)
    columns = ordered_candidates + [None] * virtual_count
    weights: list[list[int]] = []
    relations: list[list[ClaimRelation]] = []
    for gold in spec.claims:
        row_relations = [judge_claim(gold.statement, candidate) for candidate in columns]
        relations.append(row_relations)
        row_weights: list[int] = []
        for column, relation in enumerate(row_relations):
            candidate = columns[column]
            certainty = _CERTAINTY_FACTOR[candidate.certainty] if candidate else Decimal("1")
            positive = Decimal(str(gold.weight)) * _RELATION_FACTOR[relation] * certainty
            # Positive score is primary. Semantic relation makes a contradiction
            # preferable to a virtual missing result, preserving root-cause penalties.
            semantic_rank = {"match": 4, "partial_match": 3, "contradiction": 2, "missing": 0}[
                relation
            ]
            row_weights.append(int(positive * 1_000_000) + semantic_rank * 1000 - column)
        weights.append(row_weights)
    assignment = _hungarian_max(weights)
    results: list[dict[str, Any]] = []
    for row, gold in enumerate(spec.claims):
        column = assignment[row]
        candidate = columns[column]
        relation = relations[row][column]
        if relation == "missing":
            candidate = None
        results.append(
            {
                "gold_claim_id": gold.id,
                "candidate_claim_id": candidate.id if candidate else None,
                "relation": relation,
                "confidence": 1.0,
                "reason": "deterministic lexical baseline",
                "candidate_ref": candidate.source_ref if candidate else None,
                "certainty": candidate.certainty if candidate else None,
            }
        )
    return results


def _score_edge(edge: Any, alignments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    left, right = alignments[edge.from_claim], alignments[edge.to_claim]
    if left["relation"] in {"missing", "contradiction"} or right["relation"] in {
        "missing",
        "contradiction",
    }:
        relation: EdgeRelation = "edge_missing"
    else:
        relation = "edge_partial"
    factor = Decimal("0.5") if relation == "edge_partial" else Decimal("0")
    return {"edge_id": edge.id, "relation": relation, "score": Decimal(edge.weight) * factor}


def _forbidden_hits(spec: EvalSpecV1, candidates: list[CandidateClaim]) -> list[dict[str, Any]]:
    hits = []
    for forbidden in spec.forbidden_claims:
        candidate = next(
            (
                claim
                for claim in candidates
                if _similarity(forbidden.statement, claim.statement) >= Decimal("0.5")
            ),
            None,
        )
        if candidate:
            hits.append(
                {
                    "forbidden_claim_id": forbidden.id,
                    "candidate_claim_id": candidate.id,
                    "candidate_ref": candidate.source_ref,
                    "penalty": forbidden.penalty,
                    "failure_gate": forbidden.failure_gate,
                    "reason": "deterministic lexical baseline",
                }
            )
    return hits


def _strong_keyword_match(keyword: str, report: str) -> bool:
    """Match a required log keyword as one normalized, contiguous report fragment."""
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    normalized_keyword = normalize(keyword)
    return bool(normalized_keyword) and normalized_keyword in normalize(report)


def _closest_keyword_line(keyword: str, report: str) -> dict[str, Any] | None:
    """Return an explanatory nearest line without affecting the deterministic score."""

    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    normalized_keyword = normalize(keyword)
    lines = [(number, line.strip()) for number, line in enumerate(report.splitlines(), 1)]
    candidates = [(number, line) for number, line in lines if line]
    if not normalized_keyword or not candidates:
        return None
    number, line = max(
        candidates,
        key=lambda item: SequenceMatcher(
            None,
            normalized_keyword,
            normalize(item[1]),
            autojunk=False,
        ).ratio(),
    )
    similarity = SequenceMatcher(
        None,
        normalized_keyword,
        normalize(line),
        autojunk=False,
    ).ratio()
    return {
        "line_number": number,
        "quote": line[:500],
        "diagnostic_similarity": round(similarity, 4),
    }


def analysis_chain_keyword_audits(spec: EvalSpecV1, report: str) -> dict[str, dict[str, Any]]:
    """Compute every analysis-chain strong-match result once, without LLM input."""
    chains = [claim for claim in spec.claims if claim.type == "analysis_chain"]
    if not chains:
        return {}
    chain_unit = Decimal(spec.scoring_strategy.chain_total_score) / Decimal(len(chains))
    audits: dict[str, dict[str, Any]] = {}
    for claim in chains:
        matched = _strong_keyword_match(claim.evidence_keyword or "", report)
        audit: dict[str, Any] = {
            "evidence_keyword": claim.evidence_keyword,
            "keyword_match": matched,
            "keyword_score": chain_unit / Decimal("2") if matched else Decimal("0"),
        }
        if not matched:
            audit["closest_keyword_line"] = _closest_keyword_line(
                claim.evidence_keyword or "", report
            )
        audits[claim.id] = audit
    return audits


def evaluate(
    spec_payload: dict[str, Any],
    report: str,
    report_content_hash: str,
    claim_hints: list[dict[str, Any]] | None = None,
    alignment_judge: Callable[[EvalSpecV1, list[CandidateClaim], str], dict[str, Any]]
    | None = None,
) -> dict[str, Any]:
    """Evaluate one report; result is deterministic and stores every intermediate value."""
    spec = EvalSpecV1.model_validate(spec_payload)
    candidate_assessments: list[dict[str, Any]] = []
    if alignment_judge is not None:
        # Semantic Judge cites the original report directly. Do not create
        # sentence-level Candidate Claims merely to give it reference IDs.
        candidates: list[CandidateClaim] = []
        judged = alignment_judge(spec, candidates, report)
        claim_results = judged["alignments"]
        candidate_assessments = judged["candidate_assessments"]
        forbidden_candidates = [
            CandidateClaim(
                id="report-original",
                statement=report,
                type="report",
                certainty="confirmed",
                source_ref={
                    "content_hash": report_content_hash,
                    "start": 0,
                    "end": len(report),
                    "quote": report,
                },
            )
        ]
    else:
        analyzer = CandidateAnalyzer()
        candidates = analyzer.analyze(report, report_content_hash, claim_hints)
        claim_results = align_claims(spec, candidates)
        forbidden_candidates = candidates
    by_gold = {item["gold_claim_id"]: item for item in claim_results}
    strategy = spec.scoring_strategy
    root_category_chain = strategy.mode == "root_category_chain"
    keyword_audits = analysis_chain_keyword_audits(spec, report)
    claim_scores: list[Decimal] = []
    for gold in spec.claims:
        result = by_gold[gold.id]
        relation = result["relation"]
        certainty = result["certainty"] or "confirmed"
        if root_category_chain:
            if gold.type == "root_cause":
                factor = Decimal("1") if relation == "match" else Decimal("0")
                score = Decimal(strategy.root_cause_score) * factor
            elif gold.type == "classification":
                factor = Decimal("1") if relation == "match" else Decimal("0")
                score = Decimal(strategy.category_score) * factor
            elif gold.type == "analysis_chain":
                audit = keyword_audits[gold.id]
                chain_count = sum(claim.type == "analysis_chain" for claim in spec.claims)
                chain_unit = Decimal(strategy.chain_total_score) / Decimal(chain_count)
                keyword_score = audit["keyword_score"]
                semantic_details = result.get("semantic_details") or {}
                similarity = semantic_details.get("conclusion_similarity")
                if similarity is None:
                    similarity = _RELATION_FACTOR[relation]
                similarity = min(Decimal("1"), max(Decimal("0"), Decimal(str(similarity))))
                conclusion_score = chain_unit / Decimal("2") * similarity
                result["keyword_match"] = audit["keyword_match"]
                result["keyword_score"] = keyword_score
                result["evidence_keyword"] = audit["evidence_keyword"]
                if audit.get("closest_keyword_line"):
                    result["closest_keyword_line"] = audit["closest_keyword_line"]
                result["conclusion_similarity"] = similarity
                result["conclusion_score"] = conclusion_score
                score = keyword_score + conclusion_score
            else:
                score = Decimal("0")
        else:
            score = (
                Decimal(str(gold.weight))
                * _RELATION_FACTOR[relation]
                * _CERTAINTY_FACTOR[certainty]
            )
        result["score"] = score
        claim_scores.append(score)
    edge_results = [_score_edge(edge, by_gold) for edge in spec.causal_edges]
    forbidden_hits = _forbidden_hits(spec, forbidden_candidates)
    contradiction_penalty = Decimal("0")
    for gold in spec.claims:
        if (
            not root_category_chain
            and gold.type == "root_cause"
            and gold.importance == "critical"
            and by_gold[gold.id]["relation"] == "contradiction"
        ):
            contradiction_penalty += Decimal("15")
    forbidden_penalty = sum(
        (Decimal(hit["penalty"]) for hit in forbidden_hits), Decimal("0")
    )
    root_exact = root_category_chain and any(
        claim.type == "root_cause" and by_gold[claim.id]["relation"] == "match"
        for claim in spec.claims
    )
    if root_exact:
        positive_score = Decimal(strategy.root_cause_score)
        penalties = Decimal("0")
        total_score = positive_score
        failure_gate = False
    elif root_category_chain:
        positive_score = sum(claim_scores)
        penalties = min(Decimal("100"), forbidden_penalty)
        total_score = max(Decimal("0"), positive_score - penalties)
        failure_gate = any(hit["failure_gate"] for hit in forbidden_hits)
    else:
        penalties = min(Decimal("100"), contradiction_penalty + forbidden_penalty)
        positive_score = sum(claim_scores) + sum(Decimal(item["score"]) for item in edge_results)
        total_score = max(Decimal("0"), positive_score - penalties)
        failure_gate = any(hit["failure_gate"] for hit in forbidden_hits)
    passed = not failure_gate and total_score >= Decimal(spec_payload.get("pass_threshold", 70))
    claim_weight = sum(Decimal(str(claim.weight)) for claim in spec.claims)
    edge_weight = sum(Decimal(edge.weight) for edge in spec.causal_edges)
    critical_root = [
        claim
        for claim in spec.claims
        if claim.type == "root_cause" and claim.importance == "critical"
    ]

    def quantize(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if root_category_chain:
        coverage_denominator = Decimal(strategy.category_score + strategy.chain_total_score)
        claim_coverage = Decimal("1") if root_exact else positive_score / coverage_denominator
        exact_chain_score = sum(
            by_gold[claim.id]["score"]
            for claim in spec.claims
            if claim.type != "root_cause"
        )
        exact_claim_coverage = (
            Decimal("1") if root_exact else exact_chain_score / coverage_denominator
        )
    else:
        claim_coverage = (
            sum(
                Decimal(str(claim.weight))
                for claim in spec.claims
                if by_gold[claim.id]["score"] > 0
            )
            / claim_weight
        )
        exact_claim_coverage = (
            sum(
                Decimal(str(claim.weight))
                for claim in spec.claims
                if by_gold[claim.id]["relation"] == "match"
            )
            / claim_weight
        )

    return {
        "candidate_claims": [asdict(claim) for claim in candidates],
        "candidate_assessments": candidate_assessments,
        "claim_results": [
            {
                **item,
                "score": str(quantize(item["score"])),
                **{
                    key: str(quantize(item[key]))
                    for key in ("keyword_score", "conclusion_score")
                    if key in item
                },
                **{
                    key: float(item[key])
                    for key in ("conclusion_similarity",)
                    if key in item
                },
            }
            for item in claim_results
        ],
        "edge_results": [{**item, "score": str(quantize(item["score"]))} for item in edge_results],
        "forbidden_hits": forbidden_hits,
        "positive_score": str(quantize(positive_score)),
        "penalties": str(quantize(penalties)),
        "total_score": str(quantize(total_score)),
        "passed": passed,
        "metrics": {
            "claim_coverage": float(claim_coverage),
            "exact_claim_coverage": float(exact_claim_coverage),
            "causal_chain_score": float(
                sum(Decimal(item["score"]) for item in edge_results) / edge_weight
            )
            if edge_weight
            else None,
            "core_conclusion_score": float(
                sum(by_gold[claim.id]["score"] for claim in critical_root)
                / sum(Decimal(str(claim.weight)) for claim in critical_root)
            )
            if critical_root
            else None,
            "contradiction_count": sum(
                item["relation"] == "contradiction" for item in claim_results
            ),
            "forbidden_hit_count": len(forbidden_hits),
            "missing_chain_count": sum(
                item["relation"] in {"missing", "contradiction"}
                and next(
                    claim for claim in spec.claims if claim.id == item["gold_claim_id"]
                ).type
                == "analysis_chain"
                for item in claim_results
            ),
            "root_cause_exact": root_exact,
            "candidate_claim_count": len(candidates),
        },
    }
