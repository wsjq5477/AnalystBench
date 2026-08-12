"""Resolve configured command executables consistently across AnalystBench."""

import os
import re
import shutil
from pathlib import Path


def _claude_extension_version(path: Path) -> tuple[int, ...]:
    match = re.search(
        r"anthropic\.claude-(?:[^/]+-)?(\d+(?:\.\d+)*)-",
        path.as_posix(),
    )
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def resolve_executable(executable: str) -> str | None:
    """Resolve PATH commands and the WSL VS Code claude native binary."""
    resolved = shutil.which(executable)
    if resolved is not None:
        return resolved
    candidate = Path(executable).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    if executable != "claude":
        return None

    configured = os.environ.get("ANALYSTBENCH_CLAUDE_EXECUTABLE")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    extension_root = Path.home() / ".vscode-server" / "extensions"
    candidates = [
        path
        for path in extension_root.glob(
            "anthropic.claude-*-linux-*/resources/native-binary/claude"
        )
        if path.is_file() and os.access(path, os.X_OK)
    ]
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda path: (_claude_extension_version(path), path.stat().st_mtime_ns),
    )
    return str(selected.resolve())
