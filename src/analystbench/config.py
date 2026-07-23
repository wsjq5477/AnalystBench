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
    log_level: str = "INFO"
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)

    def ensure_local_directories(self) -> None:
        """Create only configured local directories needed by this process."""
        self.content_store_path.mkdir(parents=True, exist_ok=True)
        self.workspace_root_path.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///") and not self.database_url.endswith(
            ":memory:"
        ):
            database_path = Path(self.database_url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
