from pathlib import Path

from analystbench.agent_runner import CommandAgentRunner


def test_build_command_resolves_executable_from_path(monkeypatch) -> None:
    resolved = str(Path("/usr/local/bin/claude"))
    monkeypatch.setattr("analystbench.agent_runner.shutil.which", lambda value: resolved)

    command = CommandAgentRunner("claude-code").build_command({}, Path("workspace"), "prompt")

    assert command[0] == resolved
    assert command[1:4] == ["-p", "--output-format", "json"]
    assert "prompt" not in command
