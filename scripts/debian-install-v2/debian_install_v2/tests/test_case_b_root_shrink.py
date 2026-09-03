"""Case B: root-fills-disk shrink -- _plan_root_shrink()/_verify_and_apply_root_shrink().

Covers the Python-side planning/verify/cleanup logic (the parts unit-testable
without a real device or reboot) per CASE-B-ROOT-SHRINK-DESIGN.md. The
initramfs hook scripts themselves are content-checked here (fixed paths,
structural shape) and were separately verified with `dash -n` + `shellcheck`
against realistic sfdisk/resize2fs fixture output -- a real end-to-end proof
still needs the privileged-container harness in testing/ (design doc open
item 4), which this file does not attempt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from debian_install_v2.config import Config
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateStore
from debian_install_v2.tests.test_fake_integration import FakeHostActions


# A disk small enough, with root large enough, that the default swap shape
# (32 GiB / 8 devices) cannot fit after root without shrinking it first:
# 50 GiB disk, root already at 45 GiB -> only ~3.8 GiB free.
DISK_GIB = 50
ROOT_SECTORS = 45 * 1024 ** 3 // 512
ROOT_START = 2_500_608
CASE_B_DUMP = (
    "label: gpt\ndevice: /dev/vda\n\n"
    f"/dev/vda3 : start={ROOT_START}, size={ROOT_SECTORS}, type=0fc63daf-8483-4772-8e79-3d69d8477de4"
)

DUMPE2FS_OUTPUT = "\n".join([
    "Filesystem volume name:   <none>",
    "Block size:               4096",
    "",
])
RESIZE2FS_OUTPUT = "\n".join([
    "resize2fs 1.47.0 (5-Feb-2023)",
    "Estimated minimum size of the filesystem: 2000000",
    "",
])


def make_case_b_installer(tmp_path: Path, **config_overrides) -> tuple[Installer, FakeHostActions]:
    defaults = {
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "auto_reboot_after_stage1": False,
        "swap_disk_total_gb": 32,
        "swap_file_count": 8,
    }
    defaults.update(config_overrides)
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        **defaults,
    )
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    actions.outputs[("/usr/bin/findmnt", "-rn", "-o", "SOURCE,TARGET,FSTYPE")] = "/dev/vda3 / ext4\n"
    actions.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = str(DISK_GIB * 1024 ** 3)
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = CASE_B_DUMP
    actions.outputs[("/usr/sbin/dumpe2fs", "-h", "/dev/vda3")] = DUMPE2FS_OUTPUT
    actions.outputs[("/usr/sbin/resize2fs", "-P", "/dev/vda3")] = RESIZE2FS_OUTPUT
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer, actions


def make_case_a_installer(tmp_path: Path) -> tuple[Installer, FakeHostActions]:
    """The existing (small root, huge disk) fixture -- plenty of free space."""
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        auto_reboot_after_stage1=False,
        swap_disk_total_gb=32,
        swap_file_count=8,
    )
    actions = FakeHostActions()
    actions.outputs[("/usr/bin/findmnt", "-n", "-o", "SOURCE", "/")] = "/dev/vda3\n"
    actions.outputs[("/usr/sbin/blockdev", "--getsize64", "/dev/vda")] = str(512 * 1024 ** 3)
    installer = Installer(config, actions)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer, actions


# --- _root_filesystem_facts ------------------------------------------------

def test_root_filesystem_facts_dry_run_is_synthetic(tmp_path):
    installer, _ = make_case_a_installer(tmp_path)
    installer.actions.dry_run = True
    block_size, minimum_blocks = installer._root_filesystem_facts()
    assert block_size == 4096
    assert minimum_blocks > 0


def test_root_filesystem_facts_parses_real_tool_output(tmp_path):
    installer, _ = make_case_b_installer(tmp_path)
    block_size, minimum_blocks = installer._root_filesystem_facts()
    assert block_size == 4096
    assert minimum_blocks == 2_000_000


# --- _plan_root_shrink: Case A (no-op) -------------------------------------

def test_plan_root_shrink_is_noop_when_free_space_already_covers_swap(tmp_path):
    installer, actions = make_case_a_installer(tmp_path)
    installer._plan_root_shrink()
    state = StateStore(installer.config.state_dir).load()
    assert state["steps"]["root_shrink"] == {
        "status": "not_needed",
        "detail": "existing free space already covers the planned swap shape",
    }
    assert "/etc/vbpub/root-shrink-plan.env" not in actions.files
    assert "/etc/initramfs-tools/hooks/vbpub-root-shrink" not in actions.files


# --- _plan_root_shrink: Case B (hook install) ------------------------------

def test_plan_root_shrink_installs_hook_when_disk_lacks_space(tmp_path):
    installer, actions = make_case_b_installer(tmp_path)
    installer._plan_root_shrink()

    state = StateStore(installer.config.state_dir).load()
    step = state["steps"]["root_shrink"]
    assert step["status"] == "planned"

    env_content = actions.files["/etc/vbpub/root-shrink-plan.env"].decode()
    fields = dict(line.split("=", 1) for line in env_content.splitlines() if line)
    assert fields["DEVICE"] == "/dev/vda3"
    assert fields["DISK"] == "/dev/vda"
    assert fields["SFDISK_PLAN"] == "/etc/vbpub/root-shrink-plan.sfdisk"
    target_sectors = int(fields["TARGET_SECTORS"])
    target_blocks = int(fields["TARGET_BLOCKS"])
    assert 0 < target_sectors < ROOT_SECTORS  # genuinely shrinks, never a no-op or growth
    assert target_blocks == target_sectors * 512 // 4096

    plan_text = actions.files["/etc/vbpub/root-shrink-plan.sfdisk"].decode()
    assert f"size={target_sectors}" in plan_text
    assert plan_text.count("type=0657fd6d") == 8  # 8 swap partitions appended, per config

    hook = actions.files["/etc/initramfs-tools/hooks/vbpub-root-shrink"].decode()
    assert "copy_exec /usr/sbin/sfdisk /usr/sbin/sfdisk" in hook
    premount = actions.files["/etc/initramfs-tools/scripts/local-premount/vbpub-root-shrink"].decode()
    assert "/etc/vbpub/root-shrink-plan.env" in premount
    assert premount.startswith("#!/bin/sh\n")

    update_initramfs_calls = [a for a in actions.planned if a.argv[:2] == ("/usr/sbin/update-initramfs", "-u")]
    assert update_initramfs_calls


def test_plan_root_shrink_honors_preserve_root_size_gb_above_filesystem_minimum(tmp_path):
    # default preserve_root_size_gb=10 GiB is well above the fixture's ~7.8
    # GiB (minimum_blocks + margin) -- the configured value should win.
    installer, actions = make_case_b_installer(tmp_path)
    installer._plan_root_shrink()
    env_content = actions.files["/etc/vbpub/root-shrink-plan.env"].decode()
    fields = dict(line.split("=", 1) for line in env_content.splitlines() if line)
    target_gib = int(fields["TARGET_SECTORS"]) * 512 / 1024 ** 3
    assert target_gib == pytest.approx(10, abs=0.01)


def test_plan_root_shrink_clamps_to_filesystem_minimum_when_preserve_is_smaller(tmp_path):
    # preserve_root_size_gb=1 GiB is far below the fixture's real minimum
    # (~7.8 GiB with margin) -- the filesystem's own floor must win, not the
    # (unsafe) configured value.
    installer, actions = make_case_b_installer(tmp_path, preserve_root_size_gb=1)
    installer._plan_root_shrink()
    env_content = actions.files["/etc/vbpub/root-shrink-plan.env"].decode()
    fields = dict(line.split("=", 1) for line in env_content.splitlines() if line)
    target_sectors = int(fields["TARGET_SECTORS"])
    minimum_sectors = 2_000_000 * 4096 // 512
    assert target_sectors >= minimum_sectors


def test_plan_root_shrink_raises_when_disk_still_lacks_space_after_shrink(tmp_path):
    installer, _ = make_case_b_installer(tmp_path, swap_disk_total_gb=10240)
    with pytest.raises(RuntimeError, match="still lacks space"):
        installer._plan_root_shrink()


def test_plan_root_shrink_refuses_a_noop_or_growing_target(tmp_path):
    # preserve_root_size_gb larger than the CURRENT root -- the computed
    # target would not actually shrink anything.
    installer, _ = make_case_b_installer(tmp_path, preserve_root_size_gb=1000)
    with pytest.raises(RuntimeError, match="not smaller than the current"):
        installer._plan_root_shrink()


def test_plan_root_shrink_reraises_swap_too_small_unchanged(tmp_path):
    # swap_file_count=2048 against swap_disk_total_gb=1 makes
    # _plan_swap_partitions() raise "too small for N devices" -- BEFORE it
    # could ever reach the "lacks space" check _plan_root_shrink() actually
    # handles. Confirms the except clause's message match is specific: this
    # different RuntimeError propagates untouched, not misread as "needs a
    # shrink" (and confirms _plan_root_shrink() has no reachable duplicate of
    # this same per-device guard -- reaching its Case B branch at all already
    # proves per_device > 0 for these config values).
    installer, _ = make_case_b_installer(tmp_path, swap_disk_total_gb=1, swap_file_count=2048)
    with pytest.raises(RuntimeError, match="too small for 2048 devices"):
        installer._plan_root_shrink()


def test_plan_root_shrink_reraises_unrelated_swap_planning_errors(tmp_path):
    # swap_file_count=0 makes _plan_swap_partitions() raise ZeroDivisionError
    # long before it could ever raise the "lacks space" RuntimeError -- not
    # the condition _plan_root_shrink() exists to handle, so it must
    # propagate untouched rather than being misread as "needs a shrink".
    installer, _ = make_case_b_installer(tmp_path, swap_file_count=0)
    with pytest.raises(ZeroDivisionError):
        installer._plan_root_shrink()


def test_root_filesystem_facts_raises_when_block_size_unparseable(tmp_path):
    installer, actions = make_case_b_installer(tmp_path)
    actions.outputs[("/usr/sbin/dumpe2fs", "-h", "/dev/vda3")] = "Filesystem volume name:   <none>\n"
    with pytest.raises(RuntimeError, match="block size"):
        installer._root_filesystem_facts()


def test_root_filesystem_facts_raises_when_minimum_size_unparseable(tmp_path):
    installer, actions = make_case_b_installer(tmp_path)
    actions.outputs[("/usr/sbin/resize2fs", "-P", "/dev/vda3")] = "resize2fs 1.47.0 (5-Feb-2023)\n"
    with pytest.raises(RuntimeError, match="minimum root filesystem size"):
        installer._root_filesystem_facts()


# --- _verify_and_apply_root_shrink ------------------------------------------

def _seed_root_shrink_step(installer: Installer, status: str, detail: str = "") -> None:
    StateStore(installer.config.state_dir).mark_step("root_shrink", status, detail)


def test_verify_root_shrink_noop_when_no_step_recorded(tmp_path):
    installer, actions = make_case_a_installer(tmp_path)
    installer._verify_and_apply_root_shrink()  # no root_shrink step at all -> Case A, silent no-op
    state = StateStore(installer.config.state_dir).load()
    assert "root_shrink" not in state["steps"]


def test_verify_root_shrink_noop_when_step_not_planned(tmp_path):
    installer, actions = make_case_a_installer(tmp_path)
    _seed_root_shrink_step(installer, "not_needed", "existing free space already covers the planned swap shape")
    installer._verify_and_apply_root_shrink()
    state = StateStore(installer.config.state_dir).load()
    assert state["steps"]["root_shrink"]["status"] == "not_needed"  # untouched


def _patch_plan_env(monkeypatch, content: str) -> None:
    """_verify_and_apply_root_shrink() reads the fixed real path
    /etc/vbpub/root-shrink-plan.env directly (same established pattern as
    _detect_release()'s os-release read and resume()'s credential-file reads
    -- a raw Path(...) gated by `not self.actions.dry_run`, not something
    HostActions wraps). Patch only THAT path's is_file/read_text, delegating
    every other Path to the real implementation -- StateStore's own
    state.json read goes through the same methods and must keep working.
    """
    target = "/etc/vbpub/root-shrink-plan.env"
    real_is_file = Path.is_file
    real_read_text = Path.read_text

    def fake_is_file(self, *a, **kw):
        if str(self) == target:
            return True
        return real_is_file(self, *a, **kw)

    def fake_read_text(self, *a, **kw):
        if str(self) == target:
            return content
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    monkeypatch.setattr(Path, "read_text", fake_read_text)
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **kw: None)


def test_verify_root_shrink_success_cleans_up_and_falls_through(tmp_path, monkeypatch):
    installer, actions = make_case_b_installer(tmp_path)
    # Simulate the hook having ALREADY shrunk root on a prior boot: the live
    # sfdisk dump now reports a root partition at/below the planned target.
    shrunk_sectors = 10 * 1024 ** 3 // 512
    actions.outputs[("/usr/sbin/sfdisk", "--dump", "/dev/vda")] = (
        f"label: gpt\ndevice: /dev/vda\n\n/dev/vda3 : start={ROOT_START}, size={shrunk_sectors}, "
        f"type=0fc63daf-8483-4772-8e79-3d69d8477de4"
    )
    _seed_root_shrink_step(installer, "planned", "target root ... hook installed")
    _patch_plan_env(
        monkeypatch,
        f"DEVICE=/dev/vda3\nDISK=/dev/vda\nTARGET_BLOCKS=1\nTARGET_SECTORS={shrunk_sectors + 1000}\n"
        f"SFDISK_PLAN=/etc/vbpub/root-shrink-plan.sfdisk\n",
    )
    installer._verify_and_apply_root_shrink()
    state = StateStore(installer.config.state_dir).load()
    assert state["steps"]["root_shrink"]["status"] == "success"
    cleanup_initramfs_calls = [a for a in actions.planned if a.argv[:2] == ("/usr/sbin/update-initramfs", "-u")]
    assert cleanup_initramfs_calls


def test_verify_root_shrink_failure_raises_and_notifies_without_crashing(tmp_path, monkeypatch):
    installer, actions = make_case_b_installer(tmp_path)
    # Root is STILL the original (unshrunk) size -- the hook silently no-op'd
    # or failed on the prior boot.
    _seed_root_shrink_step(installer, "planned", "target root ... hook installed")
    _patch_plan_env(
        monkeypatch,
        "DEVICE=/dev/vda3\nDISK=/dev/vda\nTARGET_BLOCKS=1\nTARGET_SECTORS=1000\n"
        "SFDISK_PLAN=/etc/vbpub/root-shrink-plan.sfdisk\n",
    )
    with pytest.raises(RuntimeError, match="root shrink did not complete"):
        installer._verify_and_apply_root_shrink()
    state = StateStore(installer.config.state_dir).load()
    assert state["steps"]["root_shrink"]["status"] == "failed"


def test_verify_root_shrink_dry_run_assumes_success(tmp_path):
    installer, actions = make_case_a_installer(tmp_path)
    installer.actions.dry_run = True
    _seed_root_shrink_step(installer, "planned", "target root ... hook installed")
    installer._verify_and_apply_root_shrink()
    state = StateStore(installer.config.state_dir).load()
    assert state["steps"]["root_shrink"]["status"] == "success"


# --- stage1/stage2 wiring: still no-op / unaffected in the ordinary dry-run path

def test_stage1_and_stage2_still_dry_run_clean_with_root_shrink_wired_in(tmp_path):
    from debian_install_v2.actions import HostActions

    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        telegram_bot_token="", telegram_chat_id="",
    )
    actions = HostActions(dry_run=True)
    installer = Installer(config, actions)
    installer.install()
    # A real (non-dry-run) StateStore seed is required here regardless of
    # this test's own purpose: install()'s own dry-run state.save_new()
    # never touches disk (StateStore.dry_run mirrors actions.dry_run), so
    # resume()'s later state.load() would otherwise raise StateError. Same
    # pattern test_gstammtisch_incorporation.py's install_dry() helper uses.
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer.resume()  # must not raise -- proves _plan_root_shrink()/_verify_and_apply_root_shrink() are silent no-ops on the default Case-A dry-run fixture
    # stage2 still reached and planned the ordinary swap-activation work
    # (unaffected by the new root_shrink wiring on this Case-A fixture).
    assert any(action.argv[0] == "/usr/sbin/mkswap" for action in actions.planned)
