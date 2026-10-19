"""pytest plugin (ships beside the tests so the VM guest copy has it): the ``os_integration`` marker and its VM-lane requirement (MDTC-02 T7).

A test that executes a real low-level OS binary (systemd-tmpfiles, sfdisk, losetup, sysctl ...)
is marked ``os_integration``. Gate containers (tester-unified, built on the minimal
tester-base) deselect it explicitly with ``-m "not os_integration"``; the owning project's QEMU
VM lane selects it and sets ``VBPUB_REQUIRE_OS_INTEGRATION=1``, under which a SKIP of such a
test (missing binary, missing precondition) is reported as a FAILURE: in the one place those
tests are meant to run, "skipped" must never read as "passed".
"""
from __future__ import annotations

import os

import pytest

REQUIRE_ENV = "VBPUB_REQUIRE_OS_INTEGRATION"
MARKER = "os_integration"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}: executes a real low-level OS binary; deselected in gate containers, "
        f"required (skip == failure when {REQUIRE_ENV}=1) in the project's QEMU VM lane",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if (
        os.environ.get(REQUIRE_ENV) == "1"
        and report.skipped
        and item.get_closest_marker(MARKER) is not None
    ):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        report.outcome = "failed"
        report.longrepr = f"{MARKER} test was SKIPPED while {REQUIRE_ENV}=1: {reason}"
