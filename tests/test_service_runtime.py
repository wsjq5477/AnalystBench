import socket
from pathlib import Path
from unittest.mock import Mock

from typer.testing import CliRunner

from analystbench import cli, service_runtime
from analystbench.cli import app
from analystbench.config import Settings
from analystbench.service_runtime import ServiceRecord


def service_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'analystbench.db').as_posix()}",
        content_store_path=tmp_path / "content",
        workspace_root_path=tmp_path / "workspaces",
        results_tmp_path=tmp_path / "results" / "tmp",
        results_formal_path=tmp_path / "results",
        service_runtime_path=tmp_path / "run",
        service_log_path=tmp_path / "logs" / "analystbench.log",
    )


def test_serve_detach_upgrades_database_before_starting_service(
    tmp_path: Path, monkeypatch
) -> None:
    settings = service_settings(tmp_path)
    calls: list[str] = []
    record = ServiceRecord(
        pid=123,
        token="token",
        host="127.0.0.1",
        port=8123,
        log_path=str(settings.service_log_path),
        started_at="2026-07-28T00:00:00+00:00",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_upgrade_database", lambda: calls.append("upgrade"))

    def start(_settings: Settings, host: str, port: int) -> ServiceRecord:
        assert _settings is settings
        assert host == "127.0.0.1"
        assert port == 8123
        calls.append("start")
        return record

    monkeypatch.setattr(cli, "start_detached_service", start)

    result = CliRunner().invoke(app, ["serve", "--detach", "--port", "8123"])

    assert result.exit_code == 0
    assert calls == ["upgrade", "start"]
    assert "PID 123" in result.stdout
    assert "analystbench.log" in result.stdout


def test_start_detached_service_writes_log_and_pid_record(
    tmp_path: Path, monkeypatch
) -> None:
    settings = service_settings(tmp_path)
    process = Mock()
    process.pid = 456
    process.poll.return_value = None
    monkeypatch.setattr(service_runtime.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(service_runtime, "_assert_port_available", lambda _host, _port: None)
    monkeypatch.setattr(service_runtime, "_service_is_ready", lambda _host, _port: True)
    monkeypatch.setattr(service_runtime.time, "sleep", lambda _seconds: None)

    record = service_runtime.start_detached_service(settings, "127.0.0.1", 8000)

    assert record.pid == 456
    assert record.log_path == str(settings.service_log_path.resolve())
    assert service_runtime.read_service_record(settings) == record
    command = service_runtime.subprocess.Popen.call_args.args[0]
    assert command[:4] == [
        service_runtime.sys.executable,
        "-m",
        "analystbench.cli",
        "serve",
    ]
    assert "--no-db-upgrade" in command
    assert settings.service_log_path.is_file()


def test_start_detached_service_rejects_an_occupied_port(tmp_path: Path) -> None:
    settings = service_settings(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        try:
            service_runtime.start_detached_service(settings, "127.0.0.1", port)
        except RuntimeError as exc:
            assert "端口已被占用" in str(exc)
        else:
            raise AssertionError("an occupied API port must be rejected")

    assert service_runtime.read_service_record(settings) is None


def test_start_detached_service_waits_for_api_readiness(
    tmp_path: Path, monkeypatch
) -> None:
    settings = service_settings(tmp_path)
    process = Mock()
    process.pid = 457
    process.poll.return_value = None
    readiness = iter([False, False, True])
    monkeypatch.setattr(service_runtime.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(service_runtime, "_assert_port_available", lambda _host, _port: None)
    monkeypatch.setattr(
        service_runtime,
        "_service_is_ready",
        lambda _host, _port: next(readiness),
    )
    monkeypatch.setattr(service_runtime.time, "sleep", lambda _seconds: None)

    record = service_runtime.start_detached_service(settings, "127.0.0.1", 8000)

    assert record.pid == 457
    assert process.poll.call_count == 3


def test_start_detached_service_does_not_report_success_when_child_exits(
    tmp_path: Path, monkeypatch
) -> None:
    settings = service_settings(tmp_path)
    process = Mock()
    process.pid = 458
    process.poll.return_value = 1
    monkeypatch.setattr(service_runtime.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(service_runtime, "_assert_port_available", lambda _host, _port: None)

    try:
        service_runtime.start_detached_service(settings, "127.0.0.1", 8000)
    except RuntimeError as exc:
        assert "启动失败" in str(exc)
    else:
        raise AssertionError("an exited child must not be reported as started")

    assert service_runtime.read_service_record(settings) is None


def test_service_status_cleans_stale_record(tmp_path: Path, monkeypatch) -> None:
    settings = service_settings(tmp_path)
    record = ServiceRecord(
        pid=789,
        token="stale-token",
        host="127.0.0.1",
        port=8000,
        log_path=str(settings.service_log_path),
        started_at="2026-07-28T00:00:00+00:00",
    )
    service_runtime.write_service_record(settings, record)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "service_is_running", lambda _record: False)

    result = CliRunner().invoke(app, ["service", "status"])

    assert result.exit_code == 0
    assert "未运行" in result.stdout
    assert service_runtime.read_service_record(settings) is None
