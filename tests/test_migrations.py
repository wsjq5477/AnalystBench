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
        assert "skill_base_dir" in {
            column["name"]
            for column in inspector.get_columns("evaluation_harnesses")
        }
        schedule_columns = {
            column["name"] for column in inspector.get_columns("evaluation_schedules")
        }
        assert {"target_ids_json", "target_selections_json"} <= schedule_columns
    finally:
        engine.dispose()


def test_runner_name_is_normalized_during_upgrade(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0014_skill_optimization")

    legacy_name = "-".join(("claude", "code"))
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO execution_profiles
                    (
                        id, name, version_number, runner,
                        configuration_json, status, content_hash
                    )
                VALUES
                    (
                        'legacy-profile', 'legacy', 1, :runner,
                        '{}', 'frozen', 'sha256:legacy'
                    )
                """
            ),
            {"runner": legacy_name},
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            runner = connection.scalar(
                text(
                    "SELECT runner FROM execution_profiles "
                    "WHERE id = 'legacy-profile'"
                )
            )
        assert runner == "claude"
    finally:
        engine.dispose()


def test_skill_optimization_idempotency_migration_round_trip(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0017_skill_optimization_ledger")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO evaluation_submissions
                    (
                        id, dataset_key, run_timestamp, status, purpose,
                        optimization_context_json, manifest_json,
                        summary_json, error_json
                    )
                VALUES
                    (
                        'existing', 'kdiag', '20260812010101', 'completed',
                        'normal', '{}', '{}', '{}', '{}'
                    )
                """
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "idempotency_key" in {
            column["name"]
            for column in inspector.get_columns("evaluation_submissions")
        }
        assert ["idempotency_key"] in [
            item["column_names"]
            for item in inspector.get_unique_constraints("evaluation_submissions")
        ]
        assert ["experiment_id", "run_config_hash"] in [
            item["column_names"]
            for item in inspector.get_unique_constraints("optimization_run_groups")
        ]
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT id FROM evaluation_submissions WHERE id = 'existing'"
                )
            ) == "existing"
    finally:
        engine.dispose()

    command.downgrade(config, "0017_skill_optimization_ledger")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "idempotency_key" not in {
            column["name"]
            for column in inspector.get_columns("evaluation_submissions")
        }
        assert ("experiment_id", "run_config_hash") not in {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("optimization_run_groups")
        }
    finally:
        engine.dispose()


def test_model_runtime_limits_migrate_from_referencing_harnesses(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0019_evaluation_skill_selections")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        # The historical 0002 migration calls current metadata.create_all(), so
        # remove the new columns to reproduce a real pre-0020 database.
        connection.execute(
            text("ALTER TABLE evaluation_models DROP COLUMN timeout_seconds")
        )
        connection.execute(
            text("ALTER TABLE evaluation_models DROP COLUMN concurrency_limit")
        )
        connection.execute(
            text(
                """
                INSERT INTO evaluation_harnesses
                    (id, harness_key, name, version_number, model_policy,
                     command_template, timeout_seconds, max_output_bytes,
                     concurrency_limit, status, content_hash, last_probe_json)
                VALUES
                    ('h1', 'h1', 'H1', 1, 'required', '{model}', 300,
                     10485760, 4, 'frozen', 'sha256:h1', '{}'),
                    ('h2', 'h2', 'H2', 1, 'required', '{model}', 500,
                     10485760, 2, 'frozen', 'sha256:h2', '{}')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO evaluation_models
                    (id, model_key, name, version_number, argument, status, content_hash)
                VALUES
                    ('m1', 'shared-model', 'Shared Model', 1, 'shared-model',
                     'frozen', 'sha256:m1')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO evaluation_targets
                     (id, target_key, version_number, harness_id, model_id,
                     model_argument, status, content_hash, last_probe_json)
                VALUES
                    ('t1', 'h1@shared-model', 1, 'h1', 'm1', 'shared-model',
                     'frozen', 'sha256:t1', '{}'),
                    ('t2', 'h2@shared-model', 1, 'h2', 'm1', 'shared-model',
                     'frozen', 'sha256:t2', '{}')
                """
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        model_columns = {
            column["name"]
            for column in inspector.get_columns("evaluation_models")
        }
        assert {"timeout_seconds", "concurrency_limit"} <= model_columns
        with engine.connect() as connection:
            limits = connection.execute(
                text(
                    "SELECT timeout_seconds, concurrency_limit "
                    "FROM evaluation_models WHERE id = 'm1'"
                )
            ).one()
        assert limits == (500, 2)
    finally:
        engine.dispose()
