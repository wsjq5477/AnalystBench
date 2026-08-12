from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "analystbench"


def test_root_python_modules_are_limited_to_entrypoints_and_shared_contracts() -> None:
    assert {path.name for path in PACKAGE_ROOT.glob("*.py")} == {
        "__init__.py",
        "cli.py",
        "config.py",
        "errors.py",
        "worker.py",
    }


def test_application_modules_are_grouped_by_capability() -> None:
    expected_packages = {
        "api",
        "catalog",
        "db",
        "evaluation",
        "execution",
        "runtime",
        "scoring",
        "skill_optimization",
        "storage",
    }
    package_directories = {
        path.name
        for path in PACKAGE_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert expected_packages <= package_directories
    assert all((PACKAGE_ROOT / name / "__init__.py").is_file() for name in expected_packages)
