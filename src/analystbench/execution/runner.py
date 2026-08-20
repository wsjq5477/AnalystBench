"""Controlled non-interactive claude and OpenCode subprocess runners."""

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analystbench.execution.isolation import isolated_process_environment
from analystbench.execution.resolver import resolve_executable


class AgentRunnerError(Exception):
    def __init__(self, code: str, message: str, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class ProbeResult:
    available: bool
    version_output: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    command: list[str]
    stdout: str
    stderr: str
    exit_code: int
    final_report: str


class CommandAgentRunner:
    """Builds argv arrays; it never invokes an operating-system shell."""

    runner_id: str

    def __init__(self, runner_id: str) -> None:
        self.runner_id = runner_id

    def probe(self, executable: str) -> ProbeResult:
        resolved_executable = resolve_executable(executable) or executable
        try:
            completed = subprocess.run(
                [resolved_executable, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
        except FileNotFoundError:
            return ProbeResult(False, "", "runner_unavailable")
        except subprocess.TimeoutExpired:
            return ProbeResult(False, "", "runner_probe_timeout")
        output = (completed.stdout or completed.stderr).strip()
        return ProbeResult(
            completed.returncode == 0, output, None if completed.returncode == 0 else output
        )

    def build_command(
        self, configuration: dict[str, Any], workspace: Path, prompt: str
    ) -> list[str]:
        requested_executable = str(configuration.get("executable") or self.default_executable)
        executable = resolve_executable(requested_executable) or requested_executable
        extra_args = [str(argument) for argument in configuration.get("extra_args", [])]
        if self.runner_id == "claude":
            # In print mode claude accepts the prompt on stdin, which avoids the
            # operating system command-line length limit.
            command = [executable, *extra_args, "-p", "--output-format", "json"]
            if configuration.get("environment_mode") == "bare":
                command.append("--bare")
            allowed_tools = configuration.get("allowed_tools", ["Read", "Grep", "Glob"])
            if allowed_tools:
                command.extend(["--allowedTools", ",".join(str(item) for item in allowed_tools)])
            return command
        command = [executable, *extra_args, "run", "--format", "json", "--dir", str(workspace)]
        if configuration.get("model"):
            command.extend(["--model", str(configuration["model"])])
        if configuration.get("agent"):
            command.extend(["--agent", str(configuration["agent"])])
        command.append(prompt)
        return command

    @property
    def default_executable(self) -> str:
        return "claude" if self.runner_id == "claude" else "opencode"

    def execute(
        self, configuration: dict[str, Any], workspace: Path, prompt: str
    ) -> AgentRunResult:
        command = self.build_command(configuration, workspace, prompt)
        timeout_seconds = int(configuration.get("timeout_seconds", 1800))
        max_output_bytes = int(configuration.get("max_output_bytes", 10 * 1024 * 1024))
        try:
            with tempfile.TemporaryDirectory(
                prefix="analystbench-agent-home-",
                dir=workspace.parent,
            ) as isolated_home:
                process = subprocess.run(
                    command,
                    cwd=workspace,
                    env=isolated_process_environment(Path(isolated_home)),
                    input=prompt if self.runner_id == "claude" else None,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    shell=False,
                )
        except FileNotFoundError as exc:
            raise AgentRunnerError("runner_unavailable", str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            stdout = self._as_text(exc.stdout)
            stderr = self._as_text(exc.stderr)
            raise AgentRunnerError(
                "agent_timeout", "agent execution timed out", stdout, stderr
            ) from exc
        stdout = process.stdout or ""
        stderr = process.stderr or ""
        if len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")) > max_output_bytes:
            raise AgentRunnerError(
                "output_limit_exceeded", "agent output exceeded configured limit", stdout, stderr
            )
        if process.returncode != 0:
            if self.runner_id == "claude" and self._claude_authentication_required(
                stdout, stderr
            ):
                raise AgentRunnerError(
                    "agent_authentication_required",
                    "Claude CLI 未登录。请为 AnalystBench 服务配置 "
                    "CLAUDE_CODE_OAUTH_TOKEN、ANTHROPIC_API_KEY 或 "
                    "ANTHROPIC_AUTH_TOKEN，然后重启服务。",
                    stdout,
                    stderr,
                )
            raise AgentRunnerError(
                "agent_exit_nonzero", f"agent exited with {process.returncode}", stdout, stderr
            )
        report = self.extract_final_report(stdout)
        if not report:
            stdout_excerpt = stdout[-2000:].strip()
            stderr_excerpt = stderr[-2000:].strip()
            raise AgentRunnerError(
                "final_report_missing",
                "agent produced no final report; "
                f"stdout_tail={stdout_excerpt!r}; stderr_tail={stderr_excerpt!r}",
                stdout,
                stderr,
            )
        return AgentRunResult(command, stdout, stderr, process.returncode, report)

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

    def extract_final_report(self, stdout: str) -> str:
        values: list[str] = []
        for raw_value in [stdout, *stdout.splitlines()]:
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                continue
            values.extend(self._report_values(parsed))
        return values[-1].strip() if values else ""

    @staticmethod
    def _claude_authentication_required(stdout: str, stderr: str) -> bool:
        output = f"{stdout}\n{stderr}".lower()
        return "not logged in" in output or "please run /login" in output

    def _report_values(self, value: Any) -> list[str]:
        if isinstance(value, dict):
            direct = value.get("result")
            if isinstance(direct, str) and direct.strip():
                return [direct]
            collected: list[str] = []
            for key in ("text", "content", "message", "part", "data"):
                nested = value.get(key)
                if isinstance(nested, str) and key in {"text", "content"} and nested.strip():
                    collected.append(nested)
                elif isinstance(nested, (dict, list)):
                    collected.extend(self._report_values(nested))
            return collected
        if isinstance(value, list):
            collected = []
            for item in value:
                collected.extend(self._report_values(item))
            return collected
        return []


def create_runner(runner_id: str) -> CommandAgentRunner:
    if runner_id not in {"claude", "opencode"}:
        raise AgentRunnerError("invalid_profile", f"unsupported runner '{runner_id}'")
    return CommandAgentRunner(runner_id)
