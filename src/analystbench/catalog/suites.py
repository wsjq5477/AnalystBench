"""Built-in, immutable Suite registry for the MVP."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Suite:
    id: str
    version: str
    display_name: str
    claim_types: tuple[str, ...]


_SUITES = (
    Suite(
        "generic-analysis",
        "1.0.0",
        "Generic Analysis",
        (
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
        ),
    ),
    Suite(
        "kdiag",
        "0.1.0",
        "KDiag v0",
        (
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
        ),
    ),
)


def list_suites() -> list[Suite]:
    return list(_SUITES)


def get_suite(suite_id: str, version: str) -> Suite | None:
    return next(
        (suite for suite in _SUITES if suite.id == suite_id and suite.version == version), None
    )
