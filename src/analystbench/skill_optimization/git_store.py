"""AnalystBench-owned Git storage that never touches a user's repository."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from analystbench.errors import AnalystBenchError
from analystbench.skill_optimization.package import PackageSnapshot, copy_package


class ManagedGitStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.repos_root = self.root / "repositories"
        self.tmp_root = self.root / "tmp"

    def _repo(self, skill_id: str) -> Path:
        if not skill_id or any(char not in "0123456789abcdef-" for char in skill_id.lower()):
            raise AnalystBenchError("skill_id_invalid", "Skill ID 无效。")
        return self.repos_root / f"{skill_id}.git"

    def _environment(self, home: Path) -> dict[str, str]:
        environment = os.environ.copy()
        for key in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_COUNT",
            "GIT_SSH",
            "GIT_SSH_COMMAND",
        ):
            environment.pop(key, None)
        for key in list(environment):
            if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
                environment.pop(key, None)
        environment.update(
            {
                "HOME": str(home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return environment

    def _run(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        home: Path,
    ) -> str:
        executable = shutil.which("git")
        if executable is None:
            raise AnalystBenchError("git_unavailable", "找不到 Git 可执行程序。")
        try:
            completed = subprocess.run(
                [executable, "-c", "core.hooksPath=/dev/null", *arguments],
                cwd=cwd,
                env=self._environment(home),
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise AnalystBenchError(
                "skill_git_failed",
                "内部 Skill Git 操作失败。",
                [{"detail": str(detail)[-2000:]}],
            ) from exc
        return completed.stdout.strip()

    def ensure_repository(self, skill_id: str) -> Path:
        repository = self._repo(skill_id)
        self.repos_root.mkdir(parents=True, exist_ok=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        if repository.exists():
            if not repository.is_dir():
                raise AnalystBenchError("skill_git_invalid", "内部 Git 路径不是目录。")
            return repository
        repository.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.tmp_root) as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            self._run(["init", "--bare", str(repository)], home=home)
        return repository

    def commit(
        self,
        *,
        skill_id: str,
        version_id: str,
        snapshot: PackageSnapshot,
        parent_commit: str | None,
        message: str,
    ) -> tuple[str, str, str]:
        repository = self.ensure_repository(skill_id)
        with tempfile.TemporaryDirectory(dir=self.tmp_root) as temporary:
            temporary_path = Path(temporary)
            home = temporary_path / "home"
            worktree = temporary_path / "worktree"
            home.mkdir()
            self._run(["clone", "--no-checkout", str(repository), str(worktree)], home=home)
            if parent_commit:
                self._run(["checkout", "--detach", parent_commit], cwd=worktree, home=home)
                for child in worktree.iterdir():
                    if child.name == ".git":
                        continue
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            copy_package(snapshot, worktree / "package")
            for child in list((worktree / "package").iterdir()):
                child.rename(worktree / child.name)
            (worktree / "package").rmdir()
            self._run(["add", "--all"], cwd=worktree, home=home)
            self._run(
                [
                    "-c",
                    "user.name=AnalystBench",
                    "-c",
                    "user.email=analystbench@local",
                    "commit",
                    "--no-gpg-sign",
                    "--allow-empty",
                    "-m",
                    message,
                ],
                cwd=worktree,
                home=home,
            )
            commit = self._run(["rev-parse", "HEAD"], cwd=worktree, home=home)
            tree = self._run(["rev-parse", "HEAD^{tree}"], cwd=worktree, home=home)
            object_format = self._run(
                ["rev-parse", "--show-object-format"], cwd=worktree, home=home
            )
            ref = f"refs/analystbench/versions/{version_id}"
            self._run(["push", "origin", f"{commit}:{ref}"], cwd=worktree, home=home)
            return commit, tree, object_format

    def materialize(self, *, skill_id: str, commit: str, destination: Path) -> None:
        repository = self.ensure_repository(skill_id)
        if destination.exists():
            raise AnalystBenchError(
                "skill_destination_exists", f"Skill 安装目标已存在：{destination}"
            )
        with tempfile.TemporaryDirectory(dir=self.tmp_root) as temporary:
            temporary_path = Path(temporary)
            home = temporary_path / "home"
            worktree = temporary_path / "worktree"
            home.mkdir()
            self._run(["clone", "--no-checkout", str(repository), str(worktree)], home=home)
            self._run(["checkout", "--detach", commit], cwd=worktree, home=home)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.mkdir()
            for child in worktree.iterdir():
                if child.name == ".git":
                    continue
                target = destination / child.name
                if child.is_dir():
                    shutil.copytree(child, target, symlinks=False)
                else:
                    shutil.copyfile(child, target, follow_symlinks=False)

    def diff(self, *, skill_id: str, old_commit: str, new_commit: str) -> str:
        repository = self.ensure_repository(skill_id)
        with tempfile.TemporaryDirectory(dir=self.tmp_root) as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            return self._run(
                [
                    f"--git-dir={repository}",
                    "diff",
                    "--no-ext-diff",
                    "--no-color",
                    old_commit,
                    new_commit,
                    "--",
                ],
                home=home,
            )
