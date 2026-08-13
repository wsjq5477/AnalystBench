from pathlib import Path
from types import SimpleNamespace

import pytest

from analystbench.execution.runner import CommandAgentRunner


def test_build_command_resolves_executable_from_path(monkeypatch) -> None:
    resolved = str(Path("/usr/local/bin/claude"))
    monkeypatch.setattr(
        "analystbench.execution.resolver.shutil.which", lambda value: resolved
    )

    command = CommandAgentRunner("claude").build_command({}, Path("workspace"), "prompt")

    assert command[0] == resolved
    assert command[1:4] == ["-p", "--output-format", "json"]
    assert "prompt" not in command


def test_execute_uses_one_time_isolated_home_and_preserves_explicit_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"done"}',
            stderr="",
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "explicit-test-credential")
    monkeypatch.setenv("UNRELATED_SERVICE_SECRET", "must-not-leak")
    monkeypatch.setenv("HOME", str(tmp_path / "real-home"))
    monkeypatch.setattr(
        "analystbench.execution.runner.subprocess.run",
        fake_run,
    )

    result = CommandAgentRunner("claude").execute({}, workspace, "prompt")

    environment = captured["env"]
    assert isinstance(environment, dict)
    isolated_home = Path(environment["HOME"])
    assert isolated_home.parent == tmp_path
    assert isolated_home.name.startswith("analystbench-agent-home-")
    assert environment["CLAUDE_CONFIG_DIR"].startswith(str(isolated_home))
    assert environment["ANTHROPIC_API_KEY"] == "explicit-test-credential"
    assert "UNRELATED_SERVICE_SECRET" not in environment
    assert environment["ANALYSTBENCH_ISOLATED_HOME"] == "1"
    assert not isolated_home.exists()
    assert result.final_report == "done"
