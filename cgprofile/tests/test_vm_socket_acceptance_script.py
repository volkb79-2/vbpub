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


def test_the_self_bind_case_uses_dedicated_groups_the_slice_and_a_poll_not_a_sleep():
    """C2: group root is refused by P-5, the daemon needs cgprofile.slice, and 3 s is too tight under TCG."""
    text = SCRIPT.read_text(encoding="utf-8")
    case = text[text.index('CASE="4 activation contract"'):text.index('CASE="5 observer-only user"')]
    assert 'group = "root"' not in case
    assert 'group = "cgprofile-control"' in case and 'group = "cgprofile-observer"' in case
    assert "systemd-run --quiet --unit cgp-selfbind-test --slice cgprofile.slice" in case
    assert "sleep 3" not in case and "wait_for_socket" in case


def test_the_observer_probe_sends_a_request_the_observer_serves():
    """C1: contract-1 `host` is refused by the observer (it serves contract 2 resource-limits and contract 3)."""
    text = SCRIPT.read_text(encoding="utf-8")
    case = text[text.index('CASE="5 observer-only user"'):text.index('CASE="7 downgrade refusal"')]
    assert '"verb": "resource-limits"' in case and '"contract": 2' in case
    assert '"verb":"host"' not in text and "contract\":1" not in text


def test_no_cleanup_claims_to_remove_the_gid_zero_alias_group():
    """C4: groupdel cannot remove a group that is root's primary group, so none is created."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "cgptest-root-gid" not in text
    assert '--control-group root' in text and '--observer-group root' in text


def test_the_old_layout_case_seeds_and_checks_the_2x_layout_and_the_removal_list():
    """C3 + E.2: the seeded ctl_socket config and the cgpd/cgpctl files all appear in the refusal."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "old config keys: ctl_socket" in text and "Manual steps:" in text
    for path in ("/etc/systemd/system/cgpd.service", "/etc/cgprofile/cgpd.toml", "/usr/local/bin/cgpctl"):
        assert path in text
    assert text.count("rm -rf") == 1  # the removal of the seeded 2.x files adds no recursive delete


def _guard_run(tmp_path, mode="guard-check", *, opt_in="yes", hostname="vbpub-test", tester=True, vm=True,
               service_active=False, existing=(), dangling_symlink=None, test_mode=True):
    """Run the script with stub id/hostname/systemd-detect-virt/systemctl and a fake root."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stubs = {
        "id": f'#!/bin/sh\nif [ "$1" = -u ]; then echo 0; else exit {0 if tester else 1}; fi\n',
        "hostname": f"#!/bin/sh\necho {hostname}\n",
        "systemd-detect-virt": f"#!/bin/sh\nexit {0 if vm else 1}\n",
        "systemctl": f"#!/bin/sh\nexit {0 if service_active else 3}\n",
    }
    for name, body in stubs.items():
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    for path in existing:
        (root / path.lstrip("/")).mkdir(parents=True, exist_ok=True)
    if dangling_symlink is not None:
        link = root / dangling_symlink.lstrip("/")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(root / "dangling-target")
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CGP_TEST_ROOT": str(root),
           "CGPROFILE_PIN": "2.2.0@sha256:" + "a" * 64, "CLI_EXTENDED_PIN": "0.5.1@sha256:" + "b" * 64}
    if test_mode:
        env["CGP_TEST_MODE"] = "1"
    if opt_in:
        env["CGP_DISPOSABLE_GUEST"] = opt_in
    return subprocess.run(["bash", str(SCRIPT), mode], capture_output=True, text=True, env=env, check=False)


def test_the_guard_passes_only_on_a_fresh_harness_guest(tmp_path):
    result = _guard_run(tmp_path)
    assert result.returncode == 0 and "guard ok" in result.stdout


@pytest.mark.parametrize("kwargs, message", [
    ({"opt_in": ""}, "CGP_DISPOSABLE_GUEST=yes"),
    ({"opt_in": "1"}, "CGP_DISPOSABLE_GUEST=yes"),
    ({"vm": False}, "not a VM"),
    ({"hostname": "r1002"}, "not a run-vm-harness guest"),
    ({"tester": False}, "not a run-vm-harness guest"),
    ({"existing": ["/opt/cgprofile"]}, "/opt/cgprofile already exists"),
    ({"existing": ["/etc/cgprofile"]}, "/etc/cgprofile already exists"),
    ({"existing": ["/run/cgprofile"]}, "/run/cgprofile already exists"),
    ({"existing": ["/etc/systemd/system/cgprofiled.service"]}, "cgprofiled.service already exists"),
    ({"service_active": True}, "cgprofiled.service is running"),
])
@pytest.mark.parametrize("mode", ["guard-check", "clean-guest", "old-layout-guest"])
def test_every_mode_refuses_on_a_real_host_look_alike(tmp_path, mode, kwargs, message):
    result = _guard_run(tmp_path, mode, **kwargs)
    assert result.returncode == 2 and message in result.stderr
    assert "PASS" not in result.stdout and "FAIL" not in result.stdout  # no case ran


def test_a_dangling_opt_symlink_is_still_an_existing_installation(tmp_path):
    result = _guard_run(tmp_path, dangling_symlink="/opt/cgprofile")
    assert result.returncode == 2
    assert "/opt/cgprofile already exists" in result.stderr
    assert "PASS" not in result.stdout and "FAIL" not in result.stdout


def test_cgp_test_root_requires_the_explicit_unit_test_marker(tmp_path):
    result = _guard_run(tmp_path, test_mode=False)
    assert result.returncode == 2
    assert "CGP_TEST_ROOT is only allowed with CGP_TEST_MODE=1" in result.stderr
    assert "PASS" not in result.stdout and "FAIL" not in result.stdout


def test_the_destructive_removal_needs_the_seed_marker_the_script_writes_itself():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.index("vm-acceptance-seed ] || refuse") < text.index("rm -rf /opt/cgprofile /run/cgprofile /etc/cgprofile")
    assert "printf 'seeded by vm-socket-acceptance.sh" in text
    assert text.count("rm -rf") == 1  # the only recursive delete, behind the marker


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal under test is the non-root one")
def test_it_refuses_to_run_without_root_so_a_host_is_never_touched(tmp_path):
    result = subprocess.run(["bash", str(SCRIPT), "clean-guest"], capture_output=True, text=True,
                            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)}, check=False)
    assert result.returncode == 2 and "run as root" in result.stderr
    usage = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert usage.returncode == 2 and "usage:" in usage.stderr
