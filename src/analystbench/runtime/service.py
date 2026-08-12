"""Lifecycle helpers for the combined local API and Worker service."""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from analystbench.config import Settings


@dataclass(frozen=True)
class ServiceRecord:
    pid: int
    token: str
    host: str
    port: int
    log_path: str
    started_at: str

    @classmethod
    def from_json(cls, payload: str) -> ServiceRecord:
        data = json.loads(payload)
        return cls(
            pid=int(data["pid"]),
            token=str(data["token"]),
            host=str(data["host"]),
            port=int(data["port"]),
            log_path=str(data["log_path"]),
            started_at=str(data["started_at"]),
        )


def service_pid_path(settings: Settings) -> Path:
    return settings.service_runtime_path / "analystbench.pid"


def read_service_record(settings: Settings) -> ServiceRecord | None:
    path = service_pid_path(settings)
    if not path.is_file():
        return None
    try:
        return ServiceRecord.from_json(path.read_text(encoding="utf-8"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_service_record(settings: Settings, record: ServiceRecord) -> None:
    path = service_pid_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(record), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def remove_service_record(settings: Settings, token: str | None = None) -> None:
    path = service_pid_path(settings)
    if token is not None:
        record = read_service_record(settings)
        if record is None or record.token != token:
            return
    path.unlink(missing_ok=True)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def service_is_running(record: ServiceRecord) -> bool:
    if not _pid_exists(record.pid):
        return False
    command_line = Path(f"/proc/{record.pid}/cmdline")
    if command_line.is_file():
        try:
            return record.token.encode() in command_line.read_bytes().split(b"\0")
        except OSError:
            return False
    return True


def _assert_port_available(host: str, port: int) -> None:
    try:
        address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
        with socket.socket(address[0], address[1], address[2]) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(address[4])
    except OSError as exc:
        raise RuntimeError(
            f"无法启动 API：{host}:{port} 无法监听（通常是端口已被占用）；"
            "请先停止现有 API 或通过 --port 指定其他端口"
        ) from exc


def _service_is_ready(host: str, port: int) -> bool:
    connect_host = host
    if host in {"0.0.0.0", ""}:
        connect_host = "127.0.0.1"
    elif host == "::":
        connect_host = "::1"
    connection = http.client.HTTPConnection(connect_host, port, timeout=0.5)
    try:
        connection.request("GET", "/api/v1/health/ready")
        response = connection.getresponse()
        if response.status != 200:
            return False
        payload = json.loads(response.read())
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()
    return payload.get("status") == "ok" and payload.get("database") == "ready"


def _terminate_started_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def start_detached_service(
    settings: Settings,
    host: str,
    port: int,
    startup_timeout_seconds: float = 60.0,
) -> ServiceRecord:
    existing = read_service_record(settings)
    if existing is not None and service_is_running(existing):
        raise RuntimeError(f"AnalystBench 已在后台运行（PID {existing.pid}）")
    if existing is not None:
        remove_service_record(settings)
    _assert_port_available(host, port)

    settings.ensure_local_directories()
    token = str(uuid4())
    command = [
        sys.executable,
        "-m",
        "analystbench.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--no-db-upgrade",
        "--service-token",
        token,
    ]
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": None,
        "stderr": subprocess.STDOUT,
        "cwd": str(Path.cwd()),
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )

    with settings.service_log_path.open("ab", buffering=0) as log_file:
        popen_kwargs["stdout"] = log_file
        process = subprocess.Popen(command, **popen_kwargs)

    record = ServiceRecord(
        pid=process.pid,
        token=token,
        host=host,
        port=port,
        log_path=str(settings.service_log_path.resolve()),
        started_at=datetime.now(UTC).isoformat(),
    )
    write_service_record(settings, record)
    deadline = time.monotonic() + startup_timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            remove_service_record(settings, token)
            raise RuntimeError(
                f"AnalystBench 后台服务启动失败，请查看日志：{record.log_path}"
            )
        if _service_is_ready(host, port):
            return record
        time.sleep(0.1)

    _terminate_started_process(process)
    remove_service_record(settings, token)
    raise RuntimeError(
        f"AnalystBench 后台服务在 {startup_timeout_seconds:g} 秒内未就绪，"
        f"请查看日志：{record.log_path}"
    )


def stop_detached_service(settings: Settings, timeout_seconds: float = 10.0) -> ServiceRecord:
    record = read_service_record(settings)
    if record is None:
        raise RuntimeError("没有找到 AnalystBench 后台服务记录")
    if not service_is_running(record):
        remove_service_record(settings)
        raise RuntimeError("AnalystBench 后台服务已停止，已清理过期 PID 记录")

    if os.name == "posix":
        os.killpg(record.pid, signal.SIGTERM)
    else:
        os.kill(record.pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while service_is_running(record) and time.monotonic() < deadline:
        time.sleep(0.1)
    if service_is_running(record):
        if os.name == "posix":
            os.killpg(record.pid, signal.SIGKILL)
        else:
            os.kill(record.pid, signal.SIGTERM)
    remove_service_record(settings, record.token)
    return record
