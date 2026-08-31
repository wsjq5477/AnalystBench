from pathlib import Path

from analystbench.config import Settings


def test_settings_creates_configured_local_directories(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'database' / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )

    settings.ensure_local_directories()

    assert settings.content_store_path.is_dir()
    assert (tmp_path / "database").is_dir()


def test_skill_optimization_root_resolves_relative_to_startup_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(skill_optimization_managed_root=Path("data/skill-optimization"))

    assert settings.skill_optimization_root_path == (
        tmp_path / "data" / "skill-optimization"
    )
