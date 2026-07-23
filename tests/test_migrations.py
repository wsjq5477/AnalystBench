from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_baseline_migration_creates_jobs_table(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        assert "jobs" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
