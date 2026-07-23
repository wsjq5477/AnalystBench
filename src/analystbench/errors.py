"""Application error types with stable machine-readable codes."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AnalystBenchError(Exception):
    code: str
    message: str
    details: list[dict[str, Any]] = field(default_factory=list)
    retryable: bool = False
    status_code: int = 400


class ReadinessError(AnalystBenchError):
    def __init__(self, message: str) -> None:
        super().__init__(code="service_not_ready", message=message, retryable=True)
