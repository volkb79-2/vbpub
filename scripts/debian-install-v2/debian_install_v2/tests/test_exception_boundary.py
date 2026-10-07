"""The registry's exception policy, observed through the real entrypoint.

Domain errors (DOMAIN_ERRORS) become one plain `[ERROR]` line; anything else is
"unexpected": one `[ERROR] unexpected <Type>: ...` line with a --traceback hint,
and --traceback re-raises. A probe script patches the one host-touching seam
(Installer construction) inside the child process, then runs the real
debian-install-v2.py, so no real host operation is possible and HOME is tmp.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_extended import invoke_script

PROJECT = Path(__file__).resolve().parents[2]
ENTRYPOINT = PROJECT / "debian-install-v2.py"
CONFIG_JSON = '{"schema_version": 1}'

PROBE = """\
import runpy, sys

project, mode, entry = sys.argv[1:4]
sys.path.insert(0, project)
import debian_install_v2.installer as installer
from debian_install_v2.installer import InstallerError

RAISES = {
    "unexpected": RuntimeError("boom"),
    "oserror": OSError("disk gone"),
    "domain": InstallerError("bad"),
}


def refuse(self, *args, **kwargs):
    raise RAISES[mode]


installer.Installer.__init__ = refuse
sys.argv = [entry, *sys.argv[4:]]
runpy.run_path(entry, run_name="__main__")
"""


def run_probe(tmp_path, mode, *extra):
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    return invoke_script(
        probe,
        [str(PROJECT), mode, str(ENTRYPOINT), "status", "--config-json", CONFIG_JSON, *extra],
        home=tmp_path / "home",
        cwd=tmp_path,
    )


def error_lines(result):
    return [line for line in result.stderr.splitlines() if line.startswith("[ERROR]")]


def test_an_oserror_is_a_domain_error_with_one_plain_error_line(tmp_path):
    result = run_probe(tmp_path, "oserror")
    assert result.returncode == 1
    assert result.stdout == ""
    assert error_lines(result) == ["[ERROR] disk gone"]
    assert result.stderr.startswith("[ERROR] disk gone\n")
    assert "unexpected" not in result.stderr
    assert "Hint:" not in result.stderr
    assert "Traceback" not in result.stderr


def test_an_installer_error_is_a_plain_error_line_even_with_traceback(tmp_path):
    for extra in ((), ("--traceback",)):
        result = run_probe(tmp_path, "domain", *extra)
        assert result.returncode == 1
        assert error_lines(result) == ["[ERROR] bad"]
        assert "Hint:" not in result.stderr
        assert "Traceback" not in result.stderr


def test_an_unexpected_exception_is_reported_with_the_traceback_hint(tmp_path):
    result = run_probe(tmp_path, "unexpected")
    assert result.returncode == 1
    assert result.stdout == ""
    assert error_lines(result) == ["[ERROR] unexpected RuntimeError: boom"]
    assert result.stderr.startswith(
        "[ERROR] unexpected RuntimeError: boom\nHint: rerun with --traceback to see the stack\n"
    )
    assert "Traceback" not in result.stderr


def test_traceback_reraises_an_unexpected_exception(tmp_path):
    result = run_probe(tmp_path, "unexpected", "--traceback")
    assert result.returncode == 1
    assert "Traceback (most recent call last)" in result.stderr
    assert result.stderr.rstrip().endswith("RuntimeError: boom")
    assert "[ERROR]" not in result.stderr


@pytest.mark.parametrize("source", ["--config", "--config-json"])
def test_an_unreadable_or_invalid_configuration_is_one_plain_error_line_exit_2(tmp_path, source):
    value = str(tmp_path / "missing.json") if source == "--config" else "{not json"
    result = invoke_script(
        ENTRYPOINT, ["status", source, value], home=tmp_path / "home", cwd=tmp_path
    )
    assert result.returncode == 2
    assert len(error_lines(result)) == 1
    assert error_lines(result)[0].startswith("[ERROR] invalid installation configuration: ")
    assert "Traceback" not in result.stderr
    assert "unexpected" not in result.stderr
