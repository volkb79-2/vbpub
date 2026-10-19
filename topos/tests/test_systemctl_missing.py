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


# ── "systemctl is not available" is a different state from "unit absent" (V3) ─────────────────────


def test_the_unit_view_distinguishes_unavailable_from_unit_absent() -> None:
    unavailable = origin._unit_view("a.service", ShowResult("", "systemctl is not available: x", 127, unavailable=True))
    absent = origin._unit_view("a.service", ShowResult("", "Unit a.service could not be found.", 4))

    assert (unavailable.found, unavailable.available) == (False, False)
    assert unavailable.unavailable_reason == "systemctl is not available: x"
    assert (absent.found, absent.available, absent.unavailable_reason) == (False, True, None)


def test_a_missing_systemctl_is_flagged_unavailable_by_the_real_runner(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert default_systemctl_show_runner("x.service", ("MemoryMax",)).unavailable is True


def _collect(runner, tmp_path):
    import shutil

    from conftest import fixture_root
    from topos.collect.collector import Collector
    from topos.config import ToposConfig

    root = tmp_path / "cg"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", root)
    key = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
    (root / key / "memory.high").write_text("2147483648")  # a live value no systemd record explains
    frame = Collector(
        cgroup_root=root, config=ToposConfig(interval=5.0), host_collector=lambda: {}, now=lambda: 100.0,
        network_providers=(), systemctl_show_runner=runner,
    ).collect_once()
    return frame.entities[key]


def test_without_systemctl_no_drift_is_claimed_and_the_unavailability_is_reported(tmp_path) -> None:
    entity = _collect(lambda unit, props: ShowResult("", "systemctl is not available: nope", 127, unavailable=True), tmp_path)

    summary = entity.governance["summary"]
    assert summary["drift"] is False and summary["severity"] == "none"
    assert summary["systemd"] == {"available": False, "reason": "systemctl is not available: nope"}
    detail = entity.governance["limits"]["mem_high"]
    assert detail["origin"] != "raw_write" and detail["systemd"] == "unavailable"
    assert "unavailable" in detail["reason"] and "cannot be attributed" in detail["reason"]


def test_an_absent_unit_with_a_working_systemctl_still_reports_raw_write_drift(tmp_path) -> None:
    entity = _collect(lambda unit, props: ShowResult("", "Unit could not be found.", 4), tmp_path)

    assert entity.governance["limits"]["mem_high"]["origin"] == "raw_write"
    assert "systemd" not in entity.governance["summary"]


def test_the_diagnostic_says_governance_is_unavailable_not_that_a_unit_is_absent(tmp_path) -> None:
    from topos.config import ToposConfig
    from topos.diag.rules import RULES

    entity = _collect(lambda unit, props: ShowResult("", "systemctl is not available: nope", 127, unavailable=True), tmp_path)
    rule = next(r for r in RULES if r.rule_id == "governance_systemd_unavailable")
    match = rule.evaluator(entity, ToposConfig(interval=5.0))

    assert match is not None and match.severity == "info"
    assert "unavailable" in match.message and "no drift is claimed" in match.message
