"""Filesystem environment isolation for untrusted or model-driven commands."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

_INHERITED_ENVIRONMENT_KEYS = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "LANG",
    "LANGUAGE",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "CLI_INSTALL_DIR",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    # These credentials are explicitly task-relevant inputs for the supported
    # non-interactive claude runner. Other service secrets are not inherited.
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
}


def isolated_process_environment(
    home: Path,
    *,
    base: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
    preserve_user_home: bool = False,
) -> dict[str, str]:
    """Return a filtered environment with tool state roots redirected.

    Credentials intentionally supplied through environment variables remain
    available. By default HOME is isolated too; explicit local-runtime callers
    may preserve HOME for user-site packages while XDG/tool state stays
    redirected. This is environment isolation, not a filesystem namespace;
    callers requiring filesystem confinement must add a platform sandbox such
    as bubblewrap.
    """

    resolved_home = home.expanduser().resolve()
    config = resolved_home / ".config"
    cache = resolved_home / ".cache"
    data = resolved_home / ".local" / "share"
    state = resolved_home / ".local" / "state"
    temporary = resolved_home / "tmp"
    claude_config = config / "claude"
    opencode_config = config / "opencode"
    for directory in (
        resolved_home,
        config,
        cache,
        data,
        state,
        temporary,
        claude_config,
        opencode_config,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    source = base if base is not None else os.environ
    environment = {
        key: value
        for key, value in source.items()
        if key in _INHERITED_ENVIRONMENT_KEYS or key.startswith("LC_")
    }
    environment.update(
        {
            "XDG_CONFIG_HOME": str(config),
            "XDG_CACHE_HOME": str(cache),
            "XDG_DATA_HOME": str(data),
            "XDG_STATE_HOME": str(state),
            "TMPDIR": str(temporary),
            "TMP": str(temporary),
            "TEMP": str(temporary),
            "CLAUDE_CONFIG_DIR": str(claude_config),
            "OPENCODE_CONFIG_DIR": str(opencode_config),
        }
    )
    if preserve_user_home:
        # User-configured evaluation commands may rely on Python user-site
        # packages or wrapper install roots. Keep HOME only for this explicit
        # local-runtime path; XDG and tool config roots remain redirected and
        # unrelated credentials remain filtered.
        inherited_home = source.get("HOME") or source.get("USERPROFILE")
        environment["HOME"] = inherited_home or str(resolved_home)
        environment["USERPROFILE"] = inherited_home or str(resolved_home)
    else:
        environment["HOME"] = str(resolved_home)
        environment["USERPROFILE"] = str(resolved_home)
        environment["ANALYSTBENCH_ISOLATED_HOME"] = "1"
    if extra:
        environment.update(extra)
    return environment
