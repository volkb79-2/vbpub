"""The guest-side VM acceptance script is written but only the controller runs it.

These checks keep it honest without a VM: it parses, it refuses to run outside a root
guest (so a copy of the command cannot touch a real host), and it still covers every case
of the review's VM list (CP-61).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "vm-socket-acceptance.sh"

CASES = {
    "1 install + doctor": "unattended install, green doctors",
    "2 layout + systemd verify": "stat of every node and directory, nested RuntimeDirectory mode, verify",
    "3 restarts keep or change the inode as designed": "service restart keeps inodes, socket restart does not",
    "4 activation contract": "exit 2 without activation, --self-bind on test paths",
    "5 observer-only user": "observer ok, control EACCES, connect skip",
    "6 P-11 old-layout refusal": "second guest seeded with old files",
    "7 downgrade refusal": "refusal before fetch, optional real rollback",
    "8 uninstall contract (venv route, twice)": "slice ActiveState, venv route, rerun",
    "9 enable of a slice without [Install]": "systemctl enable returns 0",
    "10 rerun with a saved record that lists users (H2)": "install twice",
}


def test_the_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.parametrize("case", sorted(CASES))
def test_every_case_of_the_review_list_is_present(case):
    assert f'CASE="{case}"' in SCRIPT.read_text(encoding="utf-8"), CASES[case]


def test_the_uninstall_cases_cover_the_review_additions():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "ActiveState" in text  # M4: an unloaded unit file is not the same as a stopped slice
    assert 'uninstall_checks "$CGPD"' in text and 'uninstall_checks "bootstrap"' in text  # M6
    assert "a second uninstall is a clean no-op" in text  # H4
    assert "uninstall refuses before changing anything" in text  # H3, mdt-style install
    assert "systemctl enable cgprofile.slice" in text


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal under test is the non-root one")
def test_it_refuses_to_run_without_root_so_a_host_is_never_touched(tmp_path):
    result = subprocess.run(["bash", str(SCRIPT), "clean-guest"], capture_output=True, text=True,
                            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, check=False)
    assert result.returncode == 2 and "run as root" in result.stderr
    usage = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert usage.returncode == 2 and "usage:" in usage.stderr
