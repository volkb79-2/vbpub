"""RG-122: pytest-cov must exit nonzero when the printed total is below the
`--cov-fail-under` floor.

pytest-cov decides the floor at the configured `[report] precision` (default
0 digits) but prints the total at two digits, so a 96.97% total passed a 97
floor with exit 0 and a `kind = "command"` lane read that as PASS. These
tests run the real pytest and pytest-cov in a child process, at the tool
boundary, against this project's own coverage configuration.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

# 33 statements, 32 executed: 96.97%, which rounds to 97 at precision 0.
_MODULE = "".join(f"a{i} = {i}\n" for i in range(31)) + \
    "def never_called():\n    return 1\n"
_TEST = "import m\n\n\ndef test_import():\n    assert m.a0 == 0\n"


def _run_pytest_cov(tmp_path, config_text):
    (tmp_path / "m.py").write_text(_MODULE, encoding="utf-8")
    (tmp_path / "test_m.py").write_text(_TEST, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(config_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--cov=m", "--cov-fail-under=97"],
        cwd=tmp_path, capture_output=True, text=True, check=False)


def _project_report_section():
    config = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    precision = config["tool"]["coverage"]["report"]["precision"]
    return f"[tool.coverage.report]\nprecision = {precision}\n"


def test_project_coverage_config_fails_a_total_below_the_floor(tmp_path):
    proc = _run_pytest_cov(tmp_path, _project_report_section())
    assert "96.97%" in proc.stdout
    assert proc.returncode != 0, proc.stdout


def test_default_precision_would_let_the_same_total_pass(tmp_path):
    """The control: this is the pytest-cov behaviour the project config
    guards against, so the test above cannot go green for another reason."""
    proc = _run_pytest_cov(tmp_path, "[tool.coverage.report]\nprecision = 0\n")
    assert "97%" in proc.stdout
    assert proc.returncode == 0, proc.stdout
