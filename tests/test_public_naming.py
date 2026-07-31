"""Keep the open-source product name consistent across authored text."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".txt",
    ".vue",
    ".yaml",
    ".yml",
}
EXCLUDED_PARTS = {".git", ".pytest_cache", ".venv", "__pycache__", "node_modules"}


def _project_text_files() -> list[Path]:
    files = [PROJECT_ROOT / ".gitignore", PROJECT_ROOT / "README.md"]
    for relative_root in ("alembic", "docs", "src", "tests", ".claude/skills"):
        root = PROJECT_ROOT / relative_root
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in TEXT_SUFFIXES
            and not EXCLUDED_PARTS.intersection(path.parts)
        )
    return files


def test_public_product_name_is_claude() -> None:
    aliases = (
        "-".join(("claude", "code")),
        " ".join(("claude", "code")),
        "".join(("code", "agent")),
    )
    title_case_name = "".join(("Cl", "aude"))
    violations: list[str] = []
    for path in _project_text_files():
        text = path.read_text(encoding="utf-8")
        folded = text.casefold()
        if any(alias in folded for alias in aliases) or title_case_name in text:
            violations.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert not violations, f"non-canonical product names found in: {violations}"
