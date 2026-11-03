"""Smoke test against a tiny REAL QEMU guest: no disk, so SeaBIOS prints its banner and "No bootable device" in VGA text mode.

This is the only test that starts QEMU.  It must run where QEMU may run (the VM harness / tester image), never in the
interactive devcontainer.  Missing QEMU FAILS by default; the one explicit opt-out is VMSCREEN_SKIP_QEMU_SMOKE=1
(same convention as pwmcp's PWMCP_ALLOW_MISSING_TEST_DEPS: a skip must be chosen, never silent).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from vmscreen import capture, keys, sources, waiter
from vmscreen.qmp import QmpClient

SKIP_VAR = "VMSCREEN_SKIP_QEMU_SMOKE"
pytestmark = pytest.mark.qemu_smoke


@pytest.fixture
def guest(tmp_path):
    if os.environ.get(SKIP_VAR) == "1":
        pytest.skip(f"{SKIP_VAR}=1: explicit opt-out of the real-QEMU smoke test")
    qemu = shutil.which("qemu-system-x86_64")
    if qemu is None:
        pytest.fail(f"qemu-system-x86_64 is not installed; the real-QEMU smoke test FAILS by default. Install it, or set {SKIP_VAR}=1.", pytrace=False)
    sock = tmp_path / "qmp.sock"
    serial = tmp_path / "serial.log"
    proc = subprocess.Popen(
        ["nice", "-n", "10", qemu, "-machine", "pc", "-accel", "tcg", "-m", "32", "-smp", "1", "-display", "none", "-vga", "std",
         "-serial", f"file:{serial}", "-monitor", "none", "-qmp", f"unix:{sock},server=on,wait=off", "-no-reboot"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.1)
        else:
            detail = proc.stderr.read().decode(errors="replace") if proc.poll() is not None else "timeout"
            pytest.fail("QEMU did not create its QMP socket: " + detail, pytrace=False)
        yield sock, tmp_path
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_seabios_banner_is_read_exactly(guest):
    sock, tmp = guest
    with QmpClient(str(sock), 10) as c:
        out = waiter.wait_for(lambda: sources.read_text(c, source="vga", dump_dir=str(tmp)), "SeaBIOS", 60, 0.5)
        assert out["source"] == "vga-text"
        res = sources.read_text(c, source="vga", dump_dir=str(tmp))
        assert res.geometry["cols"] == 80 and res.geometry["verified_by_screendump"]
        keys.send_sequence(c, [["shift"]])  # send-key is accepted by a live guest
        pair = capture.ocr_pair(c, tmp / "pair", dump_dir=str(tmp))
        assert pair["exact"]
