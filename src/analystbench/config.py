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
        if self.database_url.startswith("sqlite:///") and not self.database_url.endswith(
            ":memory:"
        ):
            database_path = Path(self.database_url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
