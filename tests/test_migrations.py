from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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


def test_p16_upgrades_existing_p15_sqlite_database(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0010_p15_evaluation_submissions")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO evaluation_submissions
                    (
                        id, dataset_key, run_timestamp, status,
                        manifest_json, summary_json, error_json
                    )
                VALUES
                    (
                        'existing', 'kdiag', '20260728120000', 'completed',
                        '{}', '{}', '{}'
                    )
                """
            )
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "evaluation_schedules" in inspector.get_table_names()
        assert "evaluation_schedule_runs" in inspector.get_table_names()
        assert "schedule_run_id" in {
            column["name"]
            for column in inspector.get_columns("evaluation_submissions")
        }
        assert "ix_evaluation_submissions_schedule_run_id" in {
            index["name"]
            for index in inspector.get_indexes("evaluation_submissions")
        }
        method_run_columns = {
            column["name"]
            for column in inspector.get_columns(
                "evaluation_submission_method_runs"
            )
        }
        assert {"started_at", "finished_at", "duration_ms"} <= method_run_columns
        assert {
            "evaluation_harnesses",
            "evaluation_models",
            "evaluation_targets",
        } <= set(inspector.get_table_names())
        schedule_columns = {
            column["name"] for column in inspector.get_columns("evaluation_schedules")
        }
        assert "target_ids_json" in schedule_columns
    finally:
        engine.dispose()
