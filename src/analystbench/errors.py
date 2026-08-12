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


class NotFoundError(AnalystBenchError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            code="not_found", message=f"{resource} '{resource_id}' was not found", status_code=404
        )


class ConflictError(AnalystBenchError):
    def __init__(self, message: str) -> None:
        super().__init__(code="conflict", message=message, status_code=409)
