"""Validation and canonical hashing for immutable Skill packages."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from analystbench.errors import AnalystBenchError
from analystbench.storage.content import canonical_json, content_hash


@dataclass(frozen=True, slots=True)
class PackageLimits:
    max_files: int
    max_total_bytes: int
    max_single_file_bytes: int


@dataclass(frozen=True, slots=True)
class PackageSnapshot:
    root: Path
    package_hash: str
    manifest: dict[str, object]


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\x00" in value
    ):
        raise AnalystBenchError(
            "skill_path_invalid", f"Skill 包含不安全的相对路径：{value}"
        )
    return path


def validate_install_path(value: str, *, skill_key: str) -> str:
    relative = safe_relative_path(value)
    expected = PurePosixPath(".claude", "skills", skill_key)
    if relative != expected:
        raise AnalystBenchError(
            "skill_install_path_invalid",
            f"claude Harness 的安装路径必须为 {expected.as_posix()}。",
        )
    return relative.as_posix()


def inspect_package(source: Path, limits: PackageLimits) -> PackageSnapshot:
    root = source.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise AnalystBenchError(
            "skill_source_invalid", f"Skill 源目录不存在或是符号链接：{source}"
        )
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        directory_names.sort()
        for name in list(directory_names):
            child = directory_path / name
            if child.is_symlink():
                raise AnalystBenchError(
                    "skill_symlink_forbidden",
                    f"Skill 不允许包含符号链接：{child.relative_to(root).as_posix()}",
                )
            if name in {".git", "__pycache__"}:
                directory_names.remove(name)
        for name in sorted(file_names):
            if name in {".DS_Store"} or name.endswith((".pyc", ".pyo")):
                continue
            child = directory_path / name
            relative = child.relative_to(root).as_posix()
            safe_relative_path(relative)
            if child.is_symlink() or not child.is_file():
                raise AnalystBenchError(
                    "skill_file_invalid", f"Skill 只允许普通文件：{relative}"
                )
            size = child.stat().st_size
            if size > limits.max_single_file_bytes:
                raise AnalystBenchError(
                    "skill_package_too_large",
                    f"Skill 文件超过单文件上限：{relative}",
                )
            total_bytes += size
            digest = hashlib.sha256(child.read_bytes()).hexdigest()
            entries.append({"path": relative, "size_bytes": size, "sha256": digest})
    entries.sort(key=lambda item: str(item["path"]))
    if not any(item["path"] == "SKILL.md" for item in entries):
        raise AnalystBenchError("skill_manifest_missing", "Skill 根目录必须包含 SKILL.md。")
    if len(entries) > limits.max_files or total_bytes > limits.max_total_bytes:
        raise AnalystBenchError(
            "skill_package_too_large",
            "Skill 包超过文件数量或总字节上限。",
            [
                {
                    "file_count": len(entries),
                    "total_bytes": total_bytes,
                    "max_files": limits.max_files,
                    "max_total_bytes": limits.max_total_bytes,
                }
            ],
        )
    manifest: dict[str, object] = {
        "format": "analystbench.skill-package.v1",
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "files": entries,
    }
    digest = content_hash(canonical_json(manifest).encode("utf-8"))
    return PackageSnapshot(root=root, package_hash=digest, manifest=manifest)


def copy_package(snapshot: PackageSnapshot, destination: Path) -> None:
    if destination.exists():
        raise AnalystBenchError(
            "skill_destination_exists", f"Skill 安装目标已存在：{destination}"
        )
    destination.mkdir(parents=True)
    files = snapshot.manifest.get("files", [])
    assert isinstance(files, list)
    for entry in files:
        assert isinstance(entry, dict)
        relative = safe_relative_path(str(entry["path"]))
        source = snapshot.root.joinpath(*relative.parts)
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=False)


def make_package_read_only(root: Path) -> None:
    for item in root.rglob("*"):
        item.chmod(0o555 if item.is_dir() else 0o444)
    root.chmod(0o555)
