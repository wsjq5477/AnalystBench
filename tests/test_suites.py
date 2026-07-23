from typer.testing import CliRunner

from analystbench.cli import app
from analystbench.suites import get_suite, list_suites


def test_builtin_generic_and_kdiag_suites_are_registered() -> None:
    assert get_suite("generic-analysis", "1.0.0") is not None
    assert get_suite("kdiag", "0.1.0") is not None
    assert len(list_suites()) == 2


def test_suite_list_cli_is_machine_readable() -> None:
    result = CliRunner().invoke(app, ["suite-list"])
    assert result.exit_code == 0
    assert "generic-analysis" in result.stdout
    assert "kdiag" in result.stdout
