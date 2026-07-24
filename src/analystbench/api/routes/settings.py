"""Settings API — expose configurable paths to the frontend."""

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from analystbench.config import Settings

router = APIRouter(tags=["settings"])


class SettingsResponse(BaseModel):
    results_tmp_path: str
    results_formal_path: str


class SettingsUpdate(BaseModel):
    results_tmp_path: str | None = None
    results_formal_path: str | None = None


@router.get("/settings", response_model=SettingsResponse)
def get_settings(request: Request) -> SettingsResponse:
    """Return the current configurable settings."""
    settings: Settings = request.app.state.settings
    return SettingsResponse(
        results_tmp_path=str(settings.results_tmp_path),
        results_formal_path=str(settings.results_formal_path),
    )


@router.put("/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate, request: Request) -> SettingsResponse:
    """Update configurable settings (persisted to .env.local)."""
    settings: Settings = request.app.state.settings

    if payload.results_tmp_path is not None:
        new_path = Path(payload.results_tmp_path)
        new_path.mkdir(parents=True, exist_ok=True)
        settings.results_tmp_path = new_path

    if payload.results_formal_path is not None:
        new_path = Path(payload.results_formal_path)
        new_path.mkdir(parents=True, exist_ok=True)
        settings.results_formal_path = new_path

    # Persist to .env.local
    env_lines = []
    env_file = Path(".env.local")
    if env_file.is_file():
        env_lines = env_file.read_text(encoding="utf-8").splitlines()

    updated_keys = {"ANALYSTBENCH_RESULTS_TMP_PATH", "ANALYSTBENCH_RESULTS_FORMAL_PATH"}
    new_lines = [line for line in env_lines if not any(line.startswith(k) for k in updated_keys)]

    if payload.results_tmp_path is not None:
        new_lines.append(f"ANALYSTBENCH_RESULTS_TMP_PATH={settings.results_tmp_path}")
    if payload.results_formal_path is not None:
        new_lines.append(f"ANALYSTBENCH_RESULTS_FORMAL_PATH={settings.results_formal_path}")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return SettingsResponse(
        results_tmp_path=str(settings.results_tmp_path),
        results_formal_path=str(settings.results_formal_path),
    )
