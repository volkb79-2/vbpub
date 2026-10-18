"""topos must degrade, not crash, on a host without systemctl (TP-SYSTEMCTL).

49 tests of the collector, the CLI (--once/--record/--headless) and the smoke/steady runs failed with
FileNotFoundError in a container without systemctl, because `default_systemctl_show_runner` ran it
unguarded. These tests pin the contract at the unit and at the real-subprocess level.
"""
from __future__ import annotations

import subprocess

import pytest

from topos.drift import origin
from topos.drift.origin import ShowResult, default_systemctl_show_runner


def test_a_missing_systemctl_yields_an_unavailable_result_not_an_exception(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory: the real subprocess cannot find systemctl

    result = default_systemctl_show_runner("docker.service", ("MemoryMax", "CPUWeight"))

    assert isinstance(result, ShowResult)
    assert result.returncode == 127 and result.stdout == ""
    assert "systemctl is not available" in result.stderr


@pytest.mark.parametrize("error", [FileNotFoundError("no systemctl"), PermissionError("not executable")])
def test_both_ways_a_binary_can_be_unusable_degrade_the_same_way(monkeypatch: pytest.MonkeyPatch, error: OSError) -> None:
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", fail)

    assert default_systemctl_show_runner("u.service", ("MemoryMax",)).returncode == 127


def test_an_ordinary_failure_keeps_its_own_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 4, stdout="", stderr="Unit x.service could not be found.")
    )

    result = default_systemctl_show_runner("x.service", ("MemoryMax",))

    assert (result.returncode, result.stderr) == (4, "Unit x.service could not be found.")


def test_the_unit_view_of_an_unavailable_systemctl_is_simply_not_found() -> None:
    view = origin._unit_view("docker.service", ShowResult(stdout="", stderr="systemctl is not available", returncode=127))

    assert view.found is False and view.fragment_path is None and view.dropin_paths == ()
    assert all(value is origin._UNSET for value in view.values.values())
