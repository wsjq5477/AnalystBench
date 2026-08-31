import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.patch import StructuredPatchApplier


class _Registry:
    def __init__(
        self,
        source: Path,
        *,
        editable_paths: list[str] | None = None,
    ) -> None:
        self.source = source
        self.parent = SimpleNamespace(id="parent-version", skill_id="skill")
        self.skill = SimpleNamespace(
            id="skill",
            editable_paths_json=json.dumps(
                ["SKILL.md"] if editable_paths is None else editable_paths
            ),
        )
        self.imported_files: dict[str, str] | None = None

    def get_version(self, version_id: str) -> SimpleNamespace:
        assert version_id == self.parent.id
        return self.parent

    def get(self, skill_id: str) -> SimpleNamespace:
        assert skill_id == self.skill.id
        return self.skill

    def materialize_version(self, version_id: str, destination: Path) -> None:
        assert version_id == self.parent.id
        shutil.copytree(self.source, destination)

    def import_version(self, skill_id: str, **kwargs: Any) -> SimpleNamespace:
        assert skill_id == self.skill.id
        root = Path(kwargs["source_path"])
        self.imported_files = {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in root.rglob("*")
            if path.is_file()
        }
        return SimpleNamespace(id="candidate-version", skill_id=skill_id)


def _applier(
    tmp_path: Path,
    *,
    files: dict[str, str] | None = None,
    editable_paths: list[str] | None = None,
) -> tuple[StructuredPatchApplier, _Registry, Path]:
    source = tmp_path / "source"
    source.mkdir()
    for relative, content in (files or {"SKILL.md": "# Demo\n\nInitial instructions.\n"}).items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    registry = _Registry(source, editable_paths=editable_paths)
    return StructuredPatchApplier(registry), registry, source  # type: ignore[arg-type]


def test_apply_reports_net_stats_and_keeps_legacy_unpacking(tmp_path: Path) -> None:
    applier, registry, source = _applier(tmp_path)
    original = (source / "SKILL.md").read_text(encoding="utf-8")

    result = applier.apply(
        parent_version_id="parent-version",
        structured_patch={
            "operations": [
                {
                    "op": "replace",
                    "path": "SKILL.md",
                    "old": "Initial instructions.",
                    "new": "Revised instructions.",
                }
            ]
        },
    )
    version, patch_hash = result

    assert version.id == "candidate-version"
    assert patch_hash.startswith("sha256:")
    assert result.stats.changed_files == ("SKILL.md",)
    assert result.stats.operation_count == 1
    assert result.stats.operation_types == {"replace": 1}
    assert result.stats.added_tokens == 2
    assert result.stats.deleted_tokens == 2
    assert result.stats.per_file["SKILL.md"].change_ratio == pytest.approx(0.2)
    assert result.stats.as_dict()["changed_files"] == ["SKILL.md"]
    assert registry.imported_files == {"SKILL.md": "# Demo\n\nRevised instructions.\n"}
    assert (source / "SKILL.md").read_text(encoding="utf-8") == original


def test_v1_rejects_create_even_when_a_policy_requests_it(
    tmp_path: Path,
) -> None:
    applier, registry, _source = _applier(
        tmp_path,
        editable_paths=["SKILL.md", "references/*.md"],
    )
    patch = {
        "operations": [
            {
                "op": "create",
                "path": "references/new.md",
                "content": "New guidance.\n",
            }
        ]
    }

    with pytest.raises(AnalystBenchError) as rejected:
        applier.apply(parent_version_id="parent-version", structured_patch=patch)
    assert rejected.value.code == "skill_patch_operation_invalid"
    assert rejected.value.details == [
        {
            "operation_index": 0,
            "operation": "create",
        }
    ]
    assert registry.imported_files is None

    with pytest.raises(AnalystBenchError) as unsupported:
        applier.apply(
            parent_version_id="parent-version",
            structured_patch=patch,
            policy={
                "allowed_operations": ["create"],
                "edit_budget_schedule": [1],
                "max_single_file_change_ratio": 1.0,
            },
        )
    assert unsupported.value.code == "skill_patch_policy_invalid"
    assert unsupported.value.details == [
        {
            "field": "allowed_operations",
            "unsupported_operations": ["create"],
        }
    ]


def test_epoch_budget_schedule_uses_current_then_last_budget(tmp_path: Path) -> None:
    applier, registry, _source = _applier(tmp_path)
    patch = {
        "operations": [
            {"op": "append", "path": "SKILL.md", "content": ""},
            {"op": "append", "path": "SKILL.md", "content": ""},
            {"op": "append", "path": "SKILL.md", "content": ""},
        ]
    }

    with pytest.raises(AnalystBenchError) as second_epoch:
        applier.apply(
            parent_version_id="parent-version",
            structured_patch=patch,
            policy={"edit_budget_schedule": [4, 2]},
            epoch_number=2,
        )
    assert second_epoch.value.code == "edit_budget_exceeded"
    assert second_epoch.value.details == [
        {"rule": "max_operations", "actual": 3, "limit": 2, "epoch_number": 2}
    ]

    with pytest.raises(AnalystBenchError) as later_epoch:
        applier.apply(
            parent_version_id="parent-version",
            structured_patch=patch,
            policy={"edit_budget_schedule": [4, 2]},
            epoch_number=9,
        )
    assert later_epoch.value.details[0]["limit"] == 2
    assert registry.imported_files is None


def test_legacy_changed_file_budget_is_ignored(tmp_path: Path) -> None:
    applier, registry, _source = _applier(
        tmp_path,
        files={
            "SKILL.md": "# Demo\n\n" + ("primary guidance " * 20),
            "references/guide.md": "# Guide\n\n" + ("reference guidance " * 20),
        },
        editable_paths=["SKILL.md", "references/*.md"],
    )

    result = applier.apply(
        parent_version_id="parent-version",
        structured_patch={
            "operations": [
                {"op": "append", "path": "SKILL.md", "content": "x"},
                {"op": "append", "path": "references/guide.md", "content": "y"},
            ]
        },
        policy={"max_changed_files": 1},
    )
    assert result.stats.changed_files == ("SKILL.md", "references/guide.md")
    assert registry.imported_files is not None


@pytest.mark.parametrize(
    ("operation", "policy", "expected_rule"),
    [
        (
            {"op": "append", "path": "SKILL.md", "content": "abcdefgh"},
            {"max_added_tokens": 1, "max_single_file_change_ratio": 1.0},
            "max_added_tokens",
        ),
        (
            {
                "op": "replace",
                "path": "SKILL.md",
                "old": "Initial instructions.",
                "new": "",
            },
            {"max_deleted_tokens": 1, "max_single_file_change_ratio": 1.0},
            "max_deleted_tokens",
        ),
    ],
)
def test_legacy_added_and_deleted_token_budgets_are_ignored(
    tmp_path: Path,
    operation: dict[str, str],
    policy: dict[str, object],
    expected_rule: str,
) -> None:
    applier, registry, _source = _applier(tmp_path)

    result = applier.apply(
        parent_version_id="parent-version",
        structured_patch={"operations": [operation]},
        policy=policy,
        epoch_number=3,
    )
    assert result.version.id == "candidate-version"
    assert result.stats.operation_count == 1
    assert expected_rule in {"max_added_tokens", "max_deleted_tokens"}
    assert registry.imported_files is not None


def test_single_file_change_ratio_no_longer_rejects_large_rewrite(tmp_path: Path) -> None:
    before = "a" * 100
    applier, registry, _source = _applier(tmp_path, files={"SKILL.md": before})

    result = applier.apply(
        parent_version_id="parent-version",
        structured_patch={
            "operations": [
                {
                    "op": "replace",
                    "path": "SKILL.md",
                    "old": before,
                    "new": "b" * 100,
                }
            ]
        },
    )
    assert result.stats.per_file["SKILL.md"].change_ratio == 1.0
    assert registry.imported_files == {"SKILL.md": "b" * 100}


def test_append_cannot_create_a_missing_file(tmp_path: Path) -> None:
    applier, registry, _source = _applier(
        tmp_path,
        editable_paths=["SKILL.md", "references/*.md"],
    )

    with pytest.raises(AnalystBenchError) as error:
        applier.apply(
            parent_version_id="parent-version",
            structured_patch={
                "operations": [
                    {
                        "op": "append",
                        "path": "references/missing.md",
                        "content": "must not create",
                    }
                ]
            },
        )
    assert error.value.code == "skill_patch_target_missing"
    assert error.value.details[0]["operation_index"] == 0
    assert registry.imported_files is None


def test_invalid_policy_has_a_stable_field_detail(tmp_path: Path) -> None:
    applier, registry, _source = _applier(tmp_path)

    with pytest.raises(AnalystBenchError) as error:
        applier.apply(
            parent_version_id="parent-version",
            structured_patch={"operations": [{"op": "append", "path": "SKILL.md", "content": "x"}]},
            policy={"edit_budget_schedule": [0]},
        )
    assert error.value.code == "skill_patch_policy_invalid"
    assert error.value.details == [{"field": "edit_budget_schedule"}]
    assert registry.imported_files is None


@pytest.mark.parametrize(
    ("policy", "epoch_number", "expected_field"),
    [
        ([], None, "policy"),
        ({"edit_budget": 1}, None, "edit_budget"),
        ({"allowed_operations": []}, None, "allowed_operations"),
        ({"allowed_operations": [1]}, None, "allowed_operations"),
        ({"edit_budget_schedule": []}, None, "edit_budget_schedule"),
        ({}, 0, "epoch_number"),
    ],
)
def test_patch_policy_rejects_ambiguous_budget_configuration(
    tmp_path: Path,
    policy: object,
    epoch_number: int | None,
    expected_field: str,
) -> None:
    applier, registry, _source = _applier(tmp_path)

    with pytest.raises(AnalystBenchError) as raised:
        applier.apply(
            parent_version_id="parent-version",
            structured_patch={
                "operations": [
                    {"op": "append", "path": "SKILL.md", "content": "x"}
                ]
            },
            policy=policy,  # type: ignore[arg-type]
            epoch_number=epoch_number,
        )

    assert raised.value.code == "skill_patch_policy_invalid"
    assert raised.value.details == [{"field": expected_field}]
    assert registry.imported_files is None


@pytest.mark.parametrize(
    ("operation", "policy", "expected_code"),
    [
        ("not-an-object", None, "skill_patch_invalid"),
        (
            {"op": "append", "path": "SKILL.md", "content": "x"},
            {"allowed_operations": ["replace"]},
            "skill_patch_operation_forbidden",
        ),
        (
            {"op": "append", "path": "private.md", "content": "x"},
            None,
            "skill_patch_path_forbidden",
        ),
        (
            {"op": "replace", "path": "references/missing.md", "old": "x", "new": "y"},
            None,
            "skill_patch_target_missing",
        ),
        (
            {"op": "replace", "path": "SKILL.md", "old": "missing", "new": "y"},
            None,
            "skill_patch_anchor_invalid",
        ),
        (
            {"op": "insert_after", "path": "references/missing.md", "anchor": "x", "content": "y"},
            None,
            "skill_patch_target_missing",
        ),
        (
            {"op": "insert_after", "path": "SKILL.md", "anchor": "missing", "content": "y"},
            None,
            "skill_patch_anchor_invalid",
        ),
        (
            {"op": "delete", "path": "references/missing.md"},
            None,
            "skill_patch_target_missing",
        ),
    ],
)
def test_patch_operations_fail_closed_with_stable_codes(
    tmp_path: Path,
    operation: object,
    policy: dict[str, object] | None,
    expected_code: str,
) -> None:
    applier, registry, _source = _applier(
        tmp_path,
        editable_paths=["SKILL.md", "references/*.md"],
    )

    with pytest.raises(AnalystBenchError) as raised:
        applier.apply(
            parent_version_id="parent-version",
            structured_patch={"operations": [operation]},
            policy=policy,
        )

    assert raised.value.code == expected_code
    assert registry.imported_files is None


def test_delete_is_versioned(tmp_path: Path) -> None:
    applier, registry, source = _applier(
        tmp_path,
        files={
            "SKILL.md": "# Demo\n\nInitial instructions.\n",
            "references/obsolete.md": "obsolete\n",
        },
        editable_paths=["SKILL.md", "references/*.md"],
    )
    deleted = applier.apply(
        parent_version_id="parent-version",
        structured_patch={
            "operations": [{"op": "delete", "path": "references/obsolete.md"}]
        },
        policy={"max_single_file_change_ratio": 1.0},
    )
    assert deleted.stats.changed_files == ("references/obsolete.md",)
    assert registry.imported_files == {"SKILL.md": "# Demo\n\nInitial instructions.\n"}
    assert (source / "references/obsolete.md").is_file()


def test_large_skill_package_has_no_hidden_token_limit(tmp_path: Path) -> None:
    original = "x" * 100_000
    applier, registry, _source = _applier(
        tmp_path,
        files={"SKILL.md": original},
    )

    result = applier.apply(
        parent_version_id="parent-version",
        structured_patch={
            "operations": [
                {"op": "append", "path": "SKILL.md", "content": "y"}
            ]
        },
    )

    assert result.version.id == "candidate-version"
    assert registry.imported_files == {"SKILL.md": f"{original}y"}
