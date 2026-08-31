"""Process configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-secret application settings.

    Agent and model credentials intentionally do not belong here. External CLIs
    own their credentials, while future model adapters receive secret references.
    """

    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env"),
        env_prefix="ANALYSTBENCH_",
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "sqlite:///./data/analystbench.db"
    content_store_path: Path = Field(default=Path("./data/content"))
    workspace_root_path: Path = Field(default=Path("./data/workspaces"))
    results_tmp_path: Path = Field(default=Path("./data/results/tmp"))
    results_formal_path: Path = Field(default=Path("./data/results"))
    service_runtime_path: Path = Field(default=Path("./data/run"))
    service_log_path: Path = Field(default=Path("./data/logs/analystbench.log"))
    skill_optimization_enabled: bool = False
    skill_optimization_managed_root: Path | None = None
    skill_optimization_max_files: int = Field(default=200, ge=1, le=10000)
    skill_optimization_max_total_bytes: int = Field(
        default=2 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024
    )
    skill_optimization_max_single_file_bytes: int = Field(
        default=256 * 1024, ge=1, le=100 * 1024 * 1024
    )
    skill_optimization_max_epochs: int = Field(default=5, ge=1, le=100)
    skill_optimization_candidate_count: int = Field(default=2, ge=1, le=4)
    skill_optimization_screening_case_count: int = Field(default=2, ge=1, le=1000)
    skill_optimization_validation_repeats: int = Field(default=3, ge=1, le=7)
    skill_optimization_max_repeats: int = Field(default=7, ge=1, le=15)
    skill_optimization_early_stop_patience: int = Field(default=2, ge=1, le=20)
    skill_optimization_min_overall_delta: float = 1.0
    skill_optimization_minimum_independent_validation_cases: int = Field(
        default=8, ge=1
    )
    skill_optimization_max_latency_growth: float = Field(default=0.20, ge=0)
    skill_optimization_max_token_growth: float = Field(default=0.20, ge=0)
    service_startup_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    log_level: str = "INFO"
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_concurrency_limit: int = Field(default=32, ge=1, le=32)
    worker_job_lease_seconds: int = Field(default=120, ge=3, le=3600)

    def ensure_local_directories(self) -> None:
        """Create only configured local directories needed by this process."""
        self.content_store_path.mkdir(parents=True, exist_ok=True)
        self.workspace_root_path.mkdir(parents=True, exist_ok=True)
        self.results_tmp_path.mkdir(parents=True, exist_ok=True)
        self.results_formal_path.mkdir(parents=True, exist_ok=True)
        self.service_runtime_path.mkdir(parents=True, exist_ok=True)
        self.service_log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.skill_optimization_enabled:
            self.skill_optimization_root_path.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///") and not self.database_url.endswith(
            ":memory:"
        ):
            database_path = Path(self.database_url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def skill_optimization_root_path(self) -> Path:
        configured = (
            self.skill_optimization_managed_root
            if self.skill_optimization_managed_root is not None
            else self.workspace_root_path / "skill-optimization"
        )
        return configured.expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
