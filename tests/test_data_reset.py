import sqlite3
from pathlib import Path

from analystbench import cli
from analystbench.config import Settings


def test_reset_local_data_only_removes_configured_project_data(
    tmp_path: Path, monkeypatch
) -> None:
    data = tmp_path / "data"
    database = data / "analystbench.db"
    content = data / "content"
    workspaces = data / "workspaces"
    results = data / "results"
    source = tmp_path / "case.json"
    for directory in (content, workspaces, results):
        directory.mkdir(parents=True)
        (directory / "old.txt").write_text("old", encoding="utf-8")
    database.write_bytes(b"old database")
    source.write_text("{}", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite:///{database.as_posix()}",
        content_store_path=content,
        workspace_root_path=workspaces,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    removed = cli._reset_local_data()

    assert {path for path, _action in removed} == {database, content, workspaces, results}
    assert source.exists()
    assert not database.exists()
    assert not content.exists()
    assert not workspaces.exists()
    assert not results.exists()


def test_reset_local_data_refuses_paths_outside_project_data(
    tmp_path: Path, monkeypatch
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    settings = Settings(
        database_url=f"sqlite:///{(data / 'analystbench.db').as_posix()}",
        content_store_path=tmp_path / "outside-content",
        workspace_root_path=data / "workspaces",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    try:
        cli._reset_local_data()
    except Exception as exc:
        assert "data 目录以外" in str(exc)
    else:
        raise AssertionError("outside path must be rejected")


def test_clear_sqlite_database_keeps_schema_and_removes_rows(tmp_path: Path) -> None:
    database = tmp_path / "test.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('head')")
        connection.execute("CREATE TABLE cases (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO cases (name) VALUES ('old')")

    cli._clear_sqlite_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "head"
