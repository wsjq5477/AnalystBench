"""Deterministic static validation for immutable Skill package candidates.

The validator deliberately does not inspect benchmark Case files.  Callers may
provide opaque Case identifiers (or other forbidden tokens) that must not occur
in the package; findings only expose a token fingerprint, never the token.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from analystbench.errors import AnalystBenchError

CHECK_ORDER = (
    "content_security_scan",
    "case_leak_scan",
    "referenced_file_check",
    "script_syntax",
    "package_tests",
)

_MAX_FINDINGS = 50
_MAX_DIAGNOSTIC_CHARS = 2_000
_SHELL_META_RE = re.compile(r"[;&|`<>]|\$\(")
_NEGATION_RE = re.compile(
    r"(?:do\s+not|don't|never|must\s+not|禁止|不得|不要)\s*$", re.IGNORECASE
)
_FENCED_CODE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_MARKDOWN_LINK_RE = re.compile(
    r"!?\[[^\]\n]*\]\(\s*(?P<target><[^>\n]+>|[^\s)]+)", re.MULTILINE
)
_MARKDOWN_DEFINITION_RE = re.compile(
    r"^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*(?P<target><[^>\n]+>|\S+)",
    re.MULTILINE,
)
_HTML_REFERENCE_RE = re.compile(
    r"\b(?:href|src)\s*=\s*['\"](?P<target>[^'\"\n]+)['\"]", re.IGNORECASE
)

_SECURITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
    ),
    "openai_api_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "generic_credential": re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|password)\b"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}"
    ),
    "unix_user_path": re.compile(
        r"(?<![A-Za-z0-9_.-])/(?:home|Users)/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._~/-]+)?"
    ),
    "root_user_path": re.compile(r"(?<![A-Za-z0-9_.-])/root(?:/[A-Za-z0-9._~/-]+)+"),
    "windows_user_path": re.compile(
        r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9._ -]+(?:[\\/][^\s'\"`]+)?"
    ),
    "hidden_test_access": re.compile(
        r"(?is)\b(?:read|inspect|load|search|discover|open|access)\b.{0,40}"
        r"\b(?:hidden|private|holdout)\s+(?:tests?|cases?|answers?|ground[ -]?truth)\b"
    ),
    "hidden_test_access_zh": re.compile(
        r"(?:读取|查看|搜索|打开|访问).{0,24}(?:隐藏|私有|保留)(?:测试|案例|答案|标准答案)"
    ),
    "network_exfiltration": re.compile(
        r"(?i)(?:\b(?:curl|wget)\b[^\n]{0,160}\bhttps?://|"
        r"\brequests\.(?:post|put|patch)\s*\(|"
        r"\b(?:upload|exfiltrat(?:e|ion)|send)\b.{0,80}"
        r"\b(?:logs?|reports?|results?|answers?|secrets?)\b.{0,80}"
        r"\b(?:https?://|remote|server|endpoint)\b)"
    ),
    "system_prompt_override": re.compile(
        r"(?i)\b(?:modify|override|replace|rewrite|change)\b.{0,40}\bsystem\s+prompt\b"
    ),
}

_DEFAULT_ALLOWED_ARGV_PREFIXES = (
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("pytest",),
)
_FORBIDDEN_TEST_OPTIONS = (
    "-c",
    "-E",
    "-I",
    "-S",
    "-p",
    "-o",
    "--basetemp",
    "--confcutdir",
    "--override-ini",
    "--pyargs",
    "--rootdir",
)
_BUBBLEWRAP_UNAVAILABLE_MARKERS = (
    "creating new namespace failed",
    "failed to create new namespace",
    "no permissions to create new namespace",
    "failed to create netlink_route socket",
    "operation not permitted",
)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _result(name: str, status: str, **details: object) -> dict[str, object]:
    return {"name": name, "status": status, "details": details}


def _policy_error(field: str, message: str) -> AnalystBenchError:
    return AnalystBenchError(
        "skill_static_policy_invalid",
        message,
        [{"field": field}],
    )


def _as_string_list(value: object, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _policy_error(field, f"{field} 必须是字符串数组。")
    return value


def _coerce_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class StaticSkillValidator:
    """Run fixed-order, deterministic checks against one package directory."""

    def validate(
        self,
        root: Path,
        policy: Mapping[str, Any] | None,
        *,
        forbidden_case_tokens: Sequence[str] = (),
    ) -> dict[str, object]:
        package_root = root.expanduser().resolve()
        if not package_root.is_dir():
            raise AnalystBenchError(
                "skill_static_root_invalid",
                f"静态验证目录不存在：{root}",
            )
        resolved_policy = self._resolve_policy(policy)
        checks: list[dict[str, object]] = []

        for name in CHECK_ORDER:
            config = resolved_policy[name]
            if not bool(config["enabled"]):
                checks.append(_result(name, "disabled"))
                continue
            if name == "content_security_scan":
                checks.append(self._content_security_scan(package_root, config))
            elif name == "case_leak_scan":
                checks.append(
                    self._case_leak_scan(
                        package_root,
                        config,
                        forbidden_case_tokens=forbidden_case_tokens,
                    )
                )
            elif name == "referenced_file_check":
                checks.append(self._referenced_file_check(package_root, config))
            elif name == "script_syntax":
                checks.append(self._script_syntax(package_root, config))
            elif name == "package_tests":
                checks.append(
                    self._package_tests(
                        package_root,
                        config,
                        forbidden_case_tokens=forbidden_case_tokens,
                    )
                )

        return {"status": "passed", "checks": checks}

    @staticmethod
    def _resolve_policy(
        policy: Mapping[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        if policy is not None and not isinstance(policy, Mapping):
            raise _policy_error("policy", "Static policy 必须是对象。")
        supplied: Mapping[str, Any] = policy or {}
        nested = supplied.get("static_validation", supplied)
        if not isinstance(nested, Mapping):
            raise _policy_error("static_validation", "static_validation 必须是对象。")

        resolved: dict[str, dict[str, Any]] = {}
        for name in CHECK_ORDER:
            raw = nested.get(name, {})
            if isinstance(raw, bool):
                config: dict[str, Any] = {"enabled": raw}
            elif isinstance(raw, Mapping):
                config = dict(raw)
            else:
                raise _policy_error(name, f"{name} 必须是布尔值或对象。")
            enabled = config.get("enabled", True)
            if not isinstance(enabled, bool):
                raise _policy_error(f"{name}.enabled", "enabled 必须是布尔值。")
            config["enabled"] = enabled
            resolved[name] = config
        return resolved

    @staticmethod
    def _iter_text_files(root: Path, excluded: list[str]) -> list[tuple[Path, str]]:
        files: list[tuple[Path, str]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in excluded):
                continue
            data = path.read_bytes()
            if b"\x00" in data:
                continue
            files.append((path, data.decode("utf-8", errors="ignore")))
        return files

    @staticmethod
    def _is_negated(text: str, offset: int) -> bool:
        prefix = text[max(0, offset - 32) : offset]
        return _NEGATION_RE.search(prefix) is not None

    def _content_security_scan(
        self,
        root: Path,
        config: Mapping[str, Any],
    ) -> dict[str, object]:
        excluded = _as_string_list(
            config.get("exclude_paths"), field="content_security_scan.exclude_paths"
        )
        disabled_rules = set(
            _as_string_list(
                config.get("disabled_rules"),
                field="content_security_scan.disabled_rules",
            )
        )
        unknown_rules = sorted(disabled_rules - _SECURITY_PATTERNS.keys())
        if unknown_rules:
            raise AnalystBenchError(
                "skill_static_policy_invalid",
                "content_security_scan.disabled_rules 包含未知规则。",
                [
                    {
                        "field": "content_security_scan.disabled_rules",
                        "unknown_rules": unknown_rules,
                    }
                ],
            )

        files = self._iter_text_files(root, excluded)
        findings: list[dict[str, object]] = []
        total_findings = 0
        for path, text in files:
            relative = path.relative_to(root).as_posix()
            for rule, pattern in _SECURITY_PATTERNS.items():
                if rule in disabled_rules:
                    continue
                for match in pattern.finditer(text):
                    if rule in {
                        "hidden_test_access",
                        "hidden_test_access_zh",
                        "network_exfiltration",
                        "system_prompt_override",
                    } and self._is_negated(text, match.start()):
                        continue
                    total_findings += 1
                    if len(findings) < _MAX_FINDINGS:
                        findings.append(
                            {
                                "check": "content_security_scan",
                                "rule": rule,
                                "path": relative,
                                "line": _line_number(text, match.start()),
                            }
                        )
        if findings:
            details = findings
            if total_findings > len(findings):
                details.append(
                    {
                        "check": "content_security_scan",
                        "truncated": True,
                        "total_findings": total_findings,
                    }
                )
            raise AnalystBenchError(
                "skill_content_security_violation",
                "Skill 候选包含凭据、私有路径或危险指令。",
                details,
            )
        return _result(
            "content_security_scan",
            "passed",
            files_scanned=len(files),
            rules_checked=len(_SECURITY_PATTERNS) - len(disabled_rules),
        )

    def _case_leak_scan(
        self,
        root: Path,
        config: Mapping[str, Any],
        *,
        forbidden_case_tokens: Sequence[str],
    ) -> dict[str, object]:
        if isinstance(forbidden_case_tokens, (str, bytes)) or any(
            not isinstance(token, str) or not token
            for token in forbidden_case_tokens
        ):
            raise _policy_error(
                "forbidden_case_tokens",
                "forbidden_case_tokens 必须只包含非空字符串。",
            )
        excluded = _as_string_list(
            config.get("exclude_paths"), field="case_leak_scan.exclude_paths"
        )
        case_sensitive = config.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise _policy_error(
                "case_leak_scan.case_sensitive",
                "case_sensitive 必须是布尔值。",
            )
        unique_tokens = sorted(
            set(forbidden_case_tokens),
            key=lambda item: hashlib.sha256(item.encode("utf-8")).hexdigest(),
        )
        files = self._iter_text_files(root, excluded)
        findings: list[dict[str, object]] = []
        total_findings = 0
        for path, text in files:
            haystack = text if case_sensitive else text.casefold()
            relative = path.relative_to(root).as_posix()
            for token in unique_tokens:
                needle = token if case_sensitive else token.casefold()
                start = 0
                while True:
                    offset = haystack.find(needle, start)
                    if offset < 0:
                        break
                    total_findings += 1
                    if len(findings) < _MAX_FINDINGS:
                        findings.append(
                            {
                                "check": "case_leak_scan",
                                "rule": "forbidden_case_token",
                                "path": relative,
                                "line": _line_number(text, offset),
                                "token_fingerprint": hashlib.sha256(
                                    token.encode("utf-8")
                                ).hexdigest()[:16],
                            }
                        )
                    start = offset + max(len(needle), 1)
        if findings:
            details = findings
            if total_findings > len(findings):
                details.append(
                    {
                        "check": "case_leak_scan",
                        "truncated": True,
                        "total_findings": total_findings,
                    }
                )
            raise AnalystBenchError(
                "skill_case_leak_detected",
                "Skill 候选包含冻结 Case 的显式标识。",
                details,
            )
        return _result(
            "case_leak_scan",
            "passed",
            files_scanned=len(files),
            tokens_checked=len(unique_tokens),
        )

    @staticmethod
    def _markdown_references(text: str) -> list[tuple[str, int]]:
        scrubbed = _INLINE_CODE_RE.sub("", _FENCED_CODE_RE.sub("", text))
        references: list[tuple[str, int]] = []
        for pattern in (
            _MARKDOWN_LINK_RE,
            _MARKDOWN_DEFINITION_RE,
            _HTML_REFERENCE_RE,
        ):
            for match in pattern.finditer(scrubbed):
                target = match.group("target").strip()
                if target.startswith("<") and target.endswith(">"):
                    target = target[1:-1].strip()
                references.append((target, _line_number(scrubbed, match.start())))
        return references

    def _referenced_file_check(
        self,
        root: Path,
        config: Mapping[str, Any],
    ) -> dict[str, object]:
        excluded = _as_string_list(
            config.get("exclude_paths"),
            field="referenced_file_check.exclude_paths",
        )
        markdown_files = [
            path
            for path in sorted(root.rglob("*.md"))
            if path.is_file()
            and not path.is_symlink()
            and not any(
                fnmatch.fnmatchcase(path.relative_to(root).as_posix(), pattern)
                for pattern in excluded
            )
        ]
        checked = 0
        findings: list[dict[str, object]] = []
        for markdown in markdown_files:
            try:
                text = markdown.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append(
                    {
                        "check": "referenced_file_check",
                        "rule": "markdown_not_utf8",
                        "path": markdown.relative_to(root).as_posix(),
                    }
                )
                continue
            for target, line in self._markdown_references(text):
                parsed = urlsplit(target)
                if (
                    not target
                    or target.startswith("#")
                    or parsed.scheme.lower()
                    in {"data", "http", "https", "mailto", "tel"}
                    or target.startswith("//")
                ):
                    continue
                decoded = unquote(parsed.path).replace("\\", "/")
                checked += 1
                detail: dict[str, object] = {
                    "check": "referenced_file_check",
                    "path": markdown.relative_to(root).as_posix(),
                    "line": line,
                    "target": decoded,
                }
                if not decoded or Path(decoded).is_absolute() or re.match(
                    r"^[A-Za-z]:/", decoded
                ):
                    detail["rule"] = "reference_path_invalid"
                    findings.append(detail)
                    continue
                candidate = (markdown.parent / decoded).resolve()
                if root != candidate and root not in candidate.parents:
                    detail["rule"] = "reference_escapes_package"
                    findings.append(detail)
                elif not candidate.is_file():
                    detail["rule"] = "referenced_file_missing"
                    findings.append(detail)
                if len(findings) >= _MAX_FINDINGS:
                    break
            if len(findings) >= _MAX_FINDINGS:
                break
        if findings:
            raise AnalystBenchError(
                "skill_reference_check_failed",
                "Skill Markdown 包含无效或缺失的相对文件引用。",
                findings,
            )
        return _result(
            "referenced_file_check",
            "passed",
            markdown_files=len(markdown_files),
            relative_references_checked=checked,
        )

    def _script_syntax(
        self,
        root: Path,
        config: Mapping[str, Any],
    ) -> dict[str, object]:
        excluded = _as_string_list(
            config.get("exclude_paths"), field="script_syntax.exclude_paths"
        )
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:  # pragma: no cover - depends on optional environment
            yaml = None

        counts = {"python": 0, "json": 0, "yaml": 0}
        skipped_yaml_files = 0
        findings: list[dict[str, object]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in excluded):
                continue
            suffix = path.suffix.lower()
            if suffix not in {".py", ".json", ".yaml", ".yml"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append(
                    {
                        "check": "script_syntax",
                        "rule": "invalid_utf8",
                        "path": relative,
                    }
                )
                continue
            try:
                if suffix == ".py":
                    counts["python"] += 1
                    ast.parse(text, filename=relative)
                elif suffix == ".json":
                    counts["json"] += 1
                    json.loads(text)
                elif yaml is None:
                    skipped_yaml_files += 1
                else:
                    counts["yaml"] += 1
                    yaml.safe_load(text)
            except SyntaxError as exc:
                findings.append(
                    {
                        "check": "script_syntax",
                        "rule": "python_syntax",
                        "path": relative,
                        "line": exc.lineno,
                    }
                )
            except json.JSONDecodeError as exc:
                findings.append(
                    {
                        "check": "script_syntax",
                        "rule": "json_syntax",
                        "path": relative,
                        "line": exc.lineno,
                    }
                )
            except Exception as exc:
                if yaml is None or not isinstance(exc, yaml.YAMLError):
                    raise
                mark = getattr(exc, "problem_mark", None)
                findings.append(
                    {
                        "check": "script_syntax",
                        "rule": "yaml_syntax",
                        "path": relative,
                        "line": getattr(mark, "line", -1) + 1 if mark else None,
                    }
                )
            if len(findings) >= _MAX_FINDINGS:
                break
        if findings:
            raise AnalystBenchError(
                "skill_script_syntax_invalid",
                "Skill 候选包含无法解析的脚本或数据文件。",
                findings,
            )
        return _result(
            "script_syntax",
            "passed",
            files_checked=counts,
            yaml_parser_available=yaml is not None,
            skipped_yaml_files=skipped_yaml_files,
        )

    @staticmethod
    def _allowed_prefixes(config: Mapping[str, Any]) -> tuple[tuple[str, ...], ...]:
        raw = config.get("allowed_argv_prefixes")
        if raw is None:
            return _DEFAULT_ALLOWED_ARGV_PREFIXES
        if (
            not isinstance(raw, list)
            or not raw
            or any(
                not isinstance(prefix, list)
                or not prefix
                or any(not isinstance(item, str) or not item for item in prefix)
                for prefix in raw
            )
        ):
            raise _policy_error(
                "package_tests.allowed_argv_prefixes",
                "allowed_argv_prefixes 必须是非空 argv 数组列表。",
            )
        prefixes = tuple(tuple(prefix) for prefix in raw)
        for prefix in prefixes:
            executable = Path(prefix[0]).name
            if executable not in {"python", "python3", "pytest"}:
                raise _policy_error(
                    "package_tests.allowed_argv_prefixes",
                    "V1 包内测试只允许 Python 或 pytest 入口。",
                )
        return prefixes

    @staticmethod
    def _validate_test_argv(
        root: Path,
        argv: object,
        allowed_prefixes: tuple[tuple[str, ...], ...],
    ) -> list[str]:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            raise AnalystBenchError(
                "skill_package_test_config_invalid",
                "manifest.package_tests.argv 必须是非空字符串数组。",
                [{"check": "package_tests", "field": "argv"}],
            )
        command = tuple(argv)
        if not any(command[: len(prefix)] == prefix for prefix in allowed_prefixes):
            raise AnalystBenchError(
                "skill_package_test_command_forbidden",
                "包内测试 argv 不匹配 Verifier allowlist。",
                [
                    {
                        "check": "package_tests",
                        "rule": "argv_prefix_not_allowed",
                        "executable": Path(argv[0]).name,
                    }
                ],
            )
        for index, argument in enumerate(argv):
            if "\x00" in argument or "\n" in argument or _SHELL_META_RE.search(argument):
                raise AnalystBenchError(
                    "skill_package_test_command_forbidden",
                    "包内测试 argv 包含 shell 控制字符。",
                    [
                        {
                            "check": "package_tests",
                            "rule": "shell_control_character",
                            "argument_index": index,
                        }
                    ],
                )
            if index > 0 and any(
                argument == option or argument.startswith(f"{option}=")
                for option in _FORBIDDEN_TEST_OPTIONS
            ):
                raise AnalystBenchError(
                    "skill_package_test_command_forbidden",
                    "包内测试 argv 包含不允许的解释器或 pytest 选项。",
                    [
                        {
                            "check": "package_tests",
                            "rule": "forbidden_option",
                            "argument_index": index,
                        }
                    ],
                )
            if index > 0 and not argument.startswith("-"):
                path_part = argument.split("::", 1)[0].replace("\\", "/")
                if path_part in {"pytest", "unittest"}:
                    continue
                if path_part.startswith("tests/") or path_part == "tests":
                    resolved = (root / path_part).resolve()
                    if root != resolved and root not in resolved.parents:
                        raise AnalystBenchError(
                            "skill_package_test_command_forbidden",
                            "包内测试路径逃逸 Skill 包。",
                            [
                                {
                                    "check": "package_tests",
                                    "rule": "test_path_escape",
                                    "argument_index": index,
                                }
                            ],
                        )
                elif Path(path_part).is_absolute() or ".." in Path(path_part).parts:
                    raise AnalystBenchError(
                        "skill_package_test_command_forbidden",
                        "包内测试路径必须位于 Skill 包内。",
                        [
                            {
                                "check": "package_tests",
                                "rule": "test_path_escape",
                                "argument_index": index,
                            }
                        ],
                    )

        executable = Path(argv[0]).name
        if executable == "pytest":
            return [sys.executable, "-m", "pytest", *argv[1:]]
        return [sys.executable, *argv[1:]]

    @staticmethod
    def _redact_output(value: object, forbidden_case_tokens: Sequence[str]) -> str:
        text = _coerce_output(value)
        for pattern in _SECURITY_PATTERNS.values():
            text = pattern.sub("[REDACTED]", text)
        for token in forbidden_case_tokens:
            text = re.sub(re.escape(token), "[REDACTED_CASE_TOKEN]", text, flags=re.I)
        return text[-_MAX_DIAGNOSTIC_CHARS:]

    @staticmethod
    def _bubblewrap_command(
        *,
        executable: str,
        test_root: Path,
        guard_root: Path,
        argv: list[str],
    ) -> list[str]:
        """Build the package-test command inside an empty, no-network namespace."""

        runtime_prefix = Path(sys.prefix).resolve()
        runtime_executable = f"/venv/bin/{Path(sys.executable).name}"
        inner_argv = [runtime_executable, *argv[1:]]
        command = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--cap-drop",
            "ALL",
            "--clearenv",
        ]
        for source in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")):
            if source.exists():
                command.extend(["--ro-bind", str(source), str(source)])
        command.extend(
            [
                "--ro-bind",
                str(runtime_prefix),
                "/venv",
                "--bind",
                str(test_root),
                "/work",
                "--ro-bind",
                str(guard_root),
                "/guard",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/home",
                "--dir",
                "/home/analystbench",
                "--setenv",
                "HOME",
                "/home/analystbench",
                "--setenv",
                "USERPROFILE",
                "/home/analystbench",
                "--setenv",
                "XDG_CONFIG_HOME",
                "/home/analystbench/.config",
                "--setenv",
                "XDG_CACHE_HOME",
                "/home/analystbench/.cache",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--setenv",
                "PATH",
                "/venv/bin:/usr/bin:/bin",
                "--setenv",
                "PYTHONPATH",
                "/guard",
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--setenv",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
                "1",
                "--setenv",
                "ANALYSTBENCH_NETWORK_DISABLED",
                "1",
                "--setenv",
                "NO_PROXY",
                "*",
                "--chdir",
                "/work",
                *inner_argv,
            ]
        )
        return command

    def _package_tests(
        self,
        root: Path,
        config: Mapping[str, Any],
        *,
        forbidden_case_tokens: Sequence[str],
    ) -> dict[str, object]:
        tests_directory = root / "tests"
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            return _result(
                "package_tests",
                "not_configured",
                reason="manifest_missing",
                tests_directory_present=tests_directory.is_dir(),
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Normally reported by script_syntax, but keep this method safe if
            # called independently in a future composition.
            raise AnalystBenchError(
                "skill_package_test_config_invalid",
                "manifest.json 无法解析。",
                [{"check": "package_tests", "field": "manifest.json"}],
            ) from None
        if not isinstance(manifest, dict):
            raise AnalystBenchError(
                "skill_package_test_config_invalid",
                "manifest.json 顶层必须是对象。",
                [{"check": "package_tests", "field": "manifest.json"}],
            )
        test_config = manifest.get("package_tests")
        if test_config is None:
            legacy = manifest.get("tests")
            test_config = legacy if isinstance(legacy, dict) and "argv" in legacy else None
        if test_config is None:
            return _result(
                "package_tests",
                "not_configured",
                reason="argv_not_declared",
                tests_directory_present=tests_directory.is_dir(),
            )
        if not isinstance(test_config, dict):
            raise AnalystBenchError(
                "skill_package_test_config_invalid",
                "manifest.package_tests 必须是对象。",
                [{"check": "package_tests", "field": "package_tests"}],
            )
        if not tests_directory.is_dir():
            raise AnalystBenchError(
                "skill_package_test_config_invalid",
                "manifest 声明了包内测试，但 tests/ 不存在。",
                [{"check": "package_tests", "rule": "tests_directory_missing"}],
            )

        allowed_prefixes = self._allowed_prefixes(config)
        argv = self._validate_test_argv(root, test_config.get("argv"), allowed_prefixes)
        bubblewrap = shutil.which("bwrap")
        if os.name != "posix" or not bubblewrap:
            raise AnalystBenchError(
                "skill_package_test_sandbox_unavailable",
                "Skill 声明了包内测试，但当前 Worker 没有可用的 bubblewrap 沙箱。",
                [{"check": "package_tests", "required_executable": "bwrap"}],
            )
        maximum_timeout = config.get("max_timeout_seconds", 30)
        if type(maximum_timeout) is not int or not 1 <= maximum_timeout <= 300:
            raise _policy_error(
                "package_tests.max_timeout_seconds",
                "max_timeout_seconds 必须是 1 到 300 的整数。",
            )
        requested_timeout = test_config.get("timeout_seconds", maximum_timeout)
        if type(requested_timeout) is not int or not 1 <= requested_timeout <= maximum_timeout:
            raise AnalystBenchError(
                "skill_package_test_config_invalid",
                "manifest.package_tests.timeout_seconds 超出 Verifier 上限。",
                [
                    {
                        "check": "package_tests",
                        "field": "timeout_seconds",
                        "maximum": maximum_timeout,
                    }
                ],
            )

        with tempfile.TemporaryDirectory(prefix="analystbench-skill-test-") as temporary:
            sandbox_root = Path(temporary)
            guard_root = sandbox_root / "guard"
            guard_root.mkdir()
            test_root = sandbox_root / "skill"
            shutil.copytree(root, test_root, symlinks=True)
            (guard_root / "sitecustomize.py").write_text(
                """\
import os
import socket
import subprocess

def _analystbench_network_disabled(*args, **kwargs):
    raise RuntimeError("network disabled by AnalystBench package test sandbox")

socket.socket = _analystbench_network_disabled
socket.create_connection = _analystbench_network_disabled
socket.getaddrinfo = _analystbench_network_disabled

def _analystbench_process_disabled(*args, **kwargs):
    raise RuntimeError("process spawning disabled by AnalystBench package test sandbox")

subprocess.Popen = _analystbench_process_disabled
subprocess.call = _analystbench_process_disabled
subprocess.check_call = _analystbench_process_disabled
subprocess.check_output = _analystbench_process_disabled
subprocess.run = _analystbench_process_disabled
os.system = _analystbench_process_disabled
os.popen = _analystbench_process_disabled
""",
                encoding="utf-8",
            )
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            }
            sandbox_argv = self._bubblewrap_command(
                executable=bubblewrap,
                test_root=test_root,
                guard_root=guard_root,
                argv=argv,
            )
            try:
                completed = subprocess.run(
                    sandbox_argv,
                    env=environment,
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=requested_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise AnalystBenchError(
                    "skill_package_tests_timeout",
                    "Skill 包内测试超时。",
                    [
                        {
                            "check": "package_tests",
                            "timeout_seconds": requested_timeout,
                            "stdout_tail": self._redact_output(
                                exc.stdout, forbidden_case_tokens
                            ),
                            "stderr_tail": self._redact_output(
                                exc.stderr, forbidden_case_tokens
                            ),
                        }
                    ],
                ) from None

        unavailable_diagnostic = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
        if completed.returncode != 0 and any(
            marker in unavailable_diagnostic
            for marker in _BUBBLEWRAP_UNAVAILABLE_MARKERS
        ):
            raise AnalystBenchError(
                "skill_package_test_sandbox_unavailable",
                "bubblewrap 已安装，但当前 Worker 不允许创建隔离命名空间。",
                [
                    {
                        "check": "package_tests",
                        "stderr_tail": self._redact_output(
                            completed.stderr, forbidden_case_tokens
                        ),
                    }
                ],
            )

        stdout_tail = self._redact_output(completed.stdout, forbidden_case_tokens)
        stderr_tail = self._redact_output(completed.stderr, forbidden_case_tokens)
        if completed.returncode != 0:
            raise AnalystBenchError(
                "skill_package_tests_failed",
                "Skill 包内测试失败。",
                [
                    {
                        "check": "package_tests",
                        "return_code": completed.returncode,
                        "stdout_tail": stdout_tail,
                        "stderr_tail": stderr_tail,
                    }
                ],
            )
        return _result(
            "package_tests",
            "passed",
            return_code=completed.returncode,
            timeout_seconds=requested_timeout,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            network="disabled",
            sandbox="bubblewrap",
        )
