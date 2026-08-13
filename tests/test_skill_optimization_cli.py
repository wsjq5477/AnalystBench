from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from analystbench import cli
from analystbench.errors import AnalystBenchError


class Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def components(*, registry: object | None = None, experiments: object | None = None):
    return (
        Engine(),
        SimpleNamespace(),
        SimpleNamespace(),
        registry or SimpleNamespace(),
        experiments or SimpleNamespace(),
    )


def test_skill_opt_ledger_writes_deterministic_artifact(
    tmp_path: Path, monkeypatch: object
) -> None:
    experiment = SimpleNamespace(
        id="experiment-1",
        name="demo",
        status="completed",
        base_skill_version_id="version-1",
        stop_reason="MAX_EPOCHS",
    )
    service = SimpleNamespace(detail=lambda _: {"experiment": experiment, "epochs": []})
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli,
        "_skill_optimization_components",
        lambda: components(experiments=service),
    )
    output = tmp_path / "ledger.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "skill-opt",
            "ledger",
            "experiment-1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["experiment"]["id"] == "experiment-1"


def test_skill_opt_version_export_refuses_implicit_overwrite(
    tmp_path: Path, monkeypatch: object
) -> None:
    calls: list[dict[str, object]] = []

    def export_version_archive(**values: object) -> dict[str, object]:
        calls.append(values)
        return {"content": b"immutable-zip"}

    registry = SimpleNamespace(export_version_archive=export_version_archive)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli,
        "_skill_optimization_components",
        lambda: components(registry=registry),
    )
    output = tmp_path / "skill.zip"
    output.write_bytes(b"owned")

    rejected = CliRunner().invoke(
        cli.app,
        [
            "skill-opt",
            "version-export",
            "skill-1",
            "version-1",
            "--output",
            str(output),
        ],
    )
    assert rejected.exit_code != 0
    assert output.read_bytes() == b"owned"

    accepted = CliRunner().invoke(
        cli.app,
        [
            "skill-opt",
            "version-export",
            "skill-1",
            "version-1",
            "--output",
            str(output),
            "--overwrite",
        ],
    )

    assert output.read_bytes() == b"immutable-zip"
    assert accepted.exit_code == 0, accepted.output
    assert calls == [{"skill_id": "skill-1", "version_id": "version-1"}]


def test_skill_opt_rollback_requires_explicit_confirmation(monkeypatch: object) -> None:
    calls: list[dict[str, object]] = []

    def rollback(**values: object) -> SimpleNamespace:
        calls.append(values)
        return SimpleNamespace(
            skill_id="skill-1",
            evaluation_target_id="target-1",
            active_version_id="version-1",
            active_level="provisional",
            lock_version=3,
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        cli,
        "_skill_optimization_components",
        lambda: components(registry=SimpleNamespace(rollback=rollback)),
    )
    arguments = [
        "skill-opt",
        "rollback",
        "skill-1",
        "target-1",
        "version-1",
        "--expected-lock-version",
        "2",
    ]

    rejected = CliRunner().invoke(cli.app, arguments)
    blank_reason = CliRunner().invoke(
        cli.app,
        [*arguments, "--reason", "   ", "--yes"],
    )
    accepted = CliRunner().invoke(cli.app, [*arguments, "--yes"])

    assert rejected.exit_code != 0
    assert blank_reason.exit_code != 0
    assert "--reason 不能为空" in blank_reason.output
    assert accepted.exit_code == 0, accepted.output
    assert calls == [
        {
            "skill_id": "skill-1",
            "evaluation_target_id": "target-1",
            "version_id": "version-1",
            "expected_lock_version": 2,
            "reason": "manual_cli_rollback",
        }
    ]


def test_skill_opt_preflight_forwards_context_and_strict_warn_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = Engine()
    calls: list[tuple[object, object, dict[str, object]]] = []

    class Preflight:
        def __init__(self, session_factory: object, settings: object) -> None:
            self.session_factory = session_factory
            self.settings = settings

        def run(self, **kwargs: object) -> dict[str, object]:
            calls.append((self.session_factory, self.settings, kwargs))
            return {
                "status": "WARN",
                "checks": [{"code": "runner_version", "status": "WARN"}],
            }

    monkeypatch.setattr(cli, "SkillOptimizationPreflightService", Preflight)
    monkeypatch.setattr(
        cli,
        "_skill_optimization_components",
        lambda: (
            engine,
            "settings",
            "sessions",
            SimpleNamespace(),
            SimpleNamespace(),
        ),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "skill-opt",
            "preflight",
            "--skill-key",
            "demo",
            "--target-id",
            "target-1",
            "--profile-id",
            "profile-1",
            "--policy-id",
            "policy-1",
            "--verifier-id",
            "verifier-1",
            "--snapshot-id",
            "snapshot-1",
            "--case-path",
            "dataset/family/case-1",
            "--case-path",
            "dataset/family/case-2",
            "--strict",
        ],
    )

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["status"] == "WARN"
    assert engine.disposed is True
    assert calls == [
        (
            "sessions",
            "settings",
            {
                "skill_key": "demo",
                "evaluation_target_id": "target-1",
                "execution_profile_id": "profile-1",
                "optimizer_policy_version_id": "policy-1",
                "verifier_bundle_version_id": "verifier-1",
                "case_paths": [
                    "dataset/family/case-1",
                    "dataset/family/case-2",
                ],
                "data_snapshot_id": "snapshot-1",
            },
        )
    ]


def test_skill_opt_cli_surfaces_stable_application_error_and_disposes_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = Engine()

    class Registry:
        def export_version_archive(self, **_: object) -> dict[str, object]:
            raise AnalystBenchError(
                "skill_export_version_mismatch",
                "版本不属于指定 Skill。",
            )

    monkeypatch.setattr(
        cli,
        "_skill_optimization_components",
        lambda: (
            engine,
            SimpleNamespace(),
            SimpleNamespace(),
            Registry(),
            SimpleNamespace(),
        ),
    )
    output = tmp_path / "must-not-exist.zip"

    result = CliRunner().invoke(
        cli.app,
        [
            "skill-opt",
            "version-export",
            "skill-1",
            "version-2",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code != 0
    assert "skill_export_version_mismatch" in result.output
    assert "版本不属于指定 Skill" in result.output
    assert engine.disposed is True
    assert not output.exists()


def test_skill_opt_ledger_rejects_unknown_format_before_opening_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden_components() -> tuple[object, ...]:
        nonlocal called
        called = True
        raise AssertionError("database should not be opened")

    monkeypatch.setattr(cli, "_skill_optimization_components", forbidden_components)

    result = CliRunner().invoke(
        cli.app,
        ["skill-opt", "ledger", "experiment-1", "--format", "xml"],
    )

    assert result.exit_code != 0
    assert "--format 只支持 json、markdown 或 csv" in result.output
    assert called is False
