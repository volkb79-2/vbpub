"""The os_integration marker and its VM-lane requirement (MDTC-02 T7), proven in a throw-away
pytest session so the real plugin is exercised end to end (marker registration, deselection in
gate containers, skip == failure under VBPUB_REQUIRE_OS_INTEGRATION=1)."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]

SAMPLE = textwrap.dedent(
    """
    import pytest

    @pytest.mark.os_integration
    @pytest.mark.skipif(True, reason="binary missing")
    def test_marked_and_skipped():
        pass

    @pytest.mark.os_integration
    def test_marked_and_skipped_inside():
        pytest.skip("precondition missing")

    @pytest.mark.os_integration
    def test_marked_and_passing():
        pass

    @pytest.mark.skipif(True, reason="not an os test")
    def test_unmarked_and_skipped():
        pass
    """
)


def _session(tmp_path: Path, *args: str, require: bool = False) -> tuple[int, dict[str, int], str]:
    (tmp_path / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "VBPUB_REQUIRE_OS_INTEGRATION"}
    env["PYTHONPATH"] = str(PROJECT)  # the plugin ships with the tests, so the guest copy has it too
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if require:
        env["VBPUB_REQUIRE_OS_INTEGRATION"] = "1"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "debian_install_v2.tests.os_integration_plugin", "--strict-markers", "-q",
         "-p", "no:cacheprovider", "-rN", *args, str(tmp_path / "test_sample.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    summary = {name: int(count) for count, name in re.findall(r"(\d+) (passed|failed|skipped|deselected|errors?)", run.stdout)}
    return run.returncode, summary, run.stdout


def test_the_marker_is_registered_so_strict_markers_accepts_it(tmp_path: Path) -> None:
    code, summary, out = _session(tmp_path)
    assert "not found in `markers`" not in out and "Unknown pytest.mark" not in out, out
    assert code == 0, out


def test_without_the_requirement_skips_stay_skips(tmp_path: Path) -> None:
    code, summary, out = _session(tmp_path)
    assert (code, summary.get("passed"), summary.get("skipped"), summary.get("failed")) == (0, 1, 3, None), out


def test_a_gate_container_deselects_the_marked_tests_explicitly(tmp_path: Path) -> None:
    code, summary, out = _session(tmp_path, "-m", "not os_integration")
    assert code == 0 and summary.get("deselected") == 3 and summary.get("skipped") == 1 and "passed" not in summary, out


def test_under_the_vm_requirement_a_skip_of_a_marked_test_is_a_failure(tmp_path: Path) -> None:
    code, summary, out = _session(tmp_path, "-m", "os_integration", require=True)
    assert code == 1, out
    # a skip in the setup phase (skipif) surfaces as an error, one in the call phase as a failure
    assert summary.get("failed", 0) + summary.get("error", 0) + summary.get("errors", 0) == 2, out
    assert summary.get("passed") == 1 and "skipped" not in summary, out
    assert "SKIPPED while VBPUB_REQUIRE_OS_INTEGRATION=1" in out and "binary missing" in out


def test_the_requirement_never_turns_an_unmarked_skip_into_a_failure(tmp_path: Path) -> None:
    code, summary, out = _session(tmp_path, "-k", "unmarked", require=True)
    assert code == 0 and summary.get("skipped") == 1 and "failed" not in summary, out


def test_the_three_real_binary_tests_carry_the_marker() -> None:
    tests = PROJECT / "debian_install_v2" / "tests"
    expectations = {
        "test_lt_mem.py": "@pytest.mark.os_integration\n@pytest.mark.skipif(not Path(\"/usr/bin/systemd-tmpfiles\")",
        "test_io_benchmark.py": "@pytest.mark.os_integration\ndef test_bounded_run_returns_output_and_exit_status",
        "test_inuse_partition_editor_r1.py": "pytest.mark.os_integration(_REAL_SFDISK_SKIPIF(test))",
    }
    for name, needle in expectations.items():
        assert needle in (tests / name).read_text(encoding="utf-8"), name
    r1 = (tests / "test_inuse_partition_editor_r1.py").read_text(encoding="utf-8")
    assert "pytest.mark.os_integration(_REAL_VM_SWAP_SKIPIF(test))" in r1
