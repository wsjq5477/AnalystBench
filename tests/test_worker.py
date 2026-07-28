from pathlib import Path

from alembic.config import Config

from alembic import command
from analystbench.config import Settings
from analystbench.worker import LocalWorker


def test_worker_runs_dependency_check(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    try:
        assert worker.run_once() is False
    finally:
        worker.close()


def test_schedule_scan_failure_does_not_stop_worker(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'analystbench.db'}",
        content_store_path=tmp_path / "content",
    )
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    worker = LocalWorker(settings)
    try:
        def fail_scan() -> int:
            raise RuntimeError("competing scheduler")

        worker.evaluation_schedules.enqueue_due = fail_scan
        assert worker.run_once() is False
    finally:
        worker.close()
