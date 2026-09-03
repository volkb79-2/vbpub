"""Tests for lib.caps: TempCaps must refuse whole, apply whole, and restore
whole — every path is exercised against the fake ``cgroup_root`` fixture from
conftest.py, never the real /sys/fs/cgroup (DESIGN.md §7).
"""

from __future__ import annotations

import errno
import os
import signal
from pathlib import Path
from typing import Any, Dict

import pytest

from lib import caps


def read(path: Path) -> str:
    return path.read_text().strip()


# ── CapChange ────────────────────────────────────────────────────────────────

def test_capchange_holds_the_given_fields():
    change = caps.CapChange(cgroup="/a", file="memory.max", old="123", new="456")
    assert (change.cgroup, change.file, change.old, change.new) == ("/a", "memory.max", "123", "456")


# ── refuse up front, never half-apply ───────────────────────────────────────

def test_refuses_when_root_is_not_writable(tmp_path):
    missing_root = tmp_path / "no-such-cgroup-root"
    tc = caps.TempCaps({"/dev.slice": {"memory.high": "4G"}}, root=str(missing_root))
    with pytest.raises(caps.CapsError):
        tc.__enter__()
    assert tc.applied == []


def test_refuses_when_root_exists_but_permissions_deny_writes(cgroup_root):
    # Distinct from "root missing" above: the directory is there, but the
    # write-probe access.cgroup_root_is_writable() performs must fail —
    # exactly what an unprivileged container sees against a real
    # /sys/fs/cgroup that reports rw in the mount table but denies writes.
    os.chmod(cgroup_root, 0o555)
    try:
        tc = caps.TempCaps({"/dev.slice": {"memory.high": "4G"}}, root=str(cgroup_root))
        with pytest.raises(caps.CapsError):
            tc.__enter__()
        assert tc.applied == []
    finally:
        os.chmod(cgroup_root, 0o755)


def test_refuses_when_a_named_cgroup_does_not_exist(cgroup_root):
    tc = caps.TempCaps({"/does/not/exist": {"memory.max": "4G"}}, root=str(cgroup_root))
    with pytest.raises(caps.CapsError):
        tc.__enter__()
    assert tc.applied == []


def test_refuses_when_a_named_file_does_not_exist(cgroup_root):
    tc = caps.TempCaps({"/dev.slice": {"no.such.file": "4G"}}, root=str(cgroup_root))
    with pytest.raises(caps.CapsError):
        tc.__enter__()
    assert tc.applied == []


def test_cgroup_disappearing_between_validation_and_write_rolls_back(cgroup_root, monkeypatch):
    # A container can exit mid-run, deleting its whole cgroup directory —
    # after _refuse_unless_safe already said yes. The apply loop must still
    # end up with nothing half-applied.
    #
    # The vanished cgroup is caught by _apply_one's own read-before-write
    # check (it can no longer read a prior value to record), which raises
    # CapsError rather than reaching _write and failing there with a plain
    # OSError — refusing before writing is the stricter, preferred outcome,
    # and it happens to be indistinguishable from here whether the read
    # failed because the cgroup vanished or for some other transient reason.
    # Either way the invariant under test is the rollback, not the exact
    # exception type.
    first_path = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    doomed_dir = cgroup_root / "wings.slice"
    first_before = read(first_path)

    real_write = caps._write

    def write_that_deletes_the_next_cgroup(path: str, value: str) -> None:
        real_write(path, value)
        if path == str(first_path):
            import shutil
            shutil.rmtree(doomed_dir)   # the container behind wings.slice just exited

    monkeypatch.setattr(caps, "_write", write_that_deletes_the_next_cgroup)

    changes = {
        "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
        "/wings.slice": {"memory.high": "1073741824"},
    }
    tc = caps.TempCaps(changes, root=str(cgroup_root))
    with pytest.raises(caps.CapsError):
        tc.__enter__()

    assert read(first_path) == first_before   # rolled back even though its cgroup still exists
    assert tc.applied == []


def test_transient_read_failure_refuses_instead_of_treating_value_as_max(cgroup_root, monkeypatch):
    # Regression test: a read failure on the PRIOR value must never be
    # silently treated the same as the file genuinely holding "max". The
    # cgroup here never disappears and its file never stops existing — only
    # the one read_text() call for it fails, simulating a transient race
    # rather than the file being genuinely gone. If that failure were
    # conflated with "was max" (the bug), TempCaps would go on to apply the
    # change and, on restore, permanently overwrite this cgroup's real
    # memory.high (14 GiB) with the literal string "max" instead of putting
    # the real value back.
    second_path = cgroup_root / "wings.slice/memory.high"
    second_before = read(second_path)
    assert second_before != "max"   # sanity: this cgroup has a real declared value

    real_read_text = caps.read_text

    def flaky_read_text(path: str):
        if path == str(second_path):
            return None   # the file is still there; this one read just failed
        return real_read_text(path)

    monkeypatch.setattr(caps, "read_text", flaky_read_text)

    changes = {
        "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
        "/wings.slice": {"memory.high": "1073741824"},
    }
    tc = caps.TempCaps(changes, root=str(cgroup_root))
    with pytest.raises(caps.CapsError):
        tc.__enter__()

    assert tc.applied == []
    # The real value must survive untouched — never overwritten with the new
    # value, and never later clobbered with "max" by a restore that thought
    # it had to put back an "unlimited" sentinel.
    assert read(second_path) == second_before


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError(errno.EACCES, "Permission denied"),
        FileNotFoundError(errno.ENOENT, "No such file or directory"),
        OSError(errno.EINVAL, "Invalid argument"),
    ],
    ids=["EACCES", "ENOENT", "EINVAL"],
)
def test_a_failing_write_rolls_back_fully_regardless_of_errno(cgroup_root, monkeypatch, exc):
    first_path = str(cgroup_root / "dev.slice/dev-background.slice/memory.max")
    second_path = str(cgroup_root / "wings.slice/memory.high")
    first_before = read(Path(first_path))
    second_before = read(Path(second_path))

    real_write = caps._write

    def flaky_write(path: str, value: str) -> None:
        if path == second_path:
            raise exc
        real_write(path, value)

    monkeypatch.setattr(caps, "_write", flaky_write)

    changes = {
        "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
        "/wings.slice": {"memory.high": "1073741824"},
    }
    tc = caps.TempCaps(changes, root=str(cgroup_root))
    with pytest.raises(OSError):
        tc.__enter__()

    assert read(Path(first_path)) == first_before
    assert read(Path(second_path)) == second_before
    assert tc.applied == []


def test_refusal_leaves_every_named_file_untouched_even_when_an_earlier_one_was_valid(cgroup_root):
    target = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    before = read(target)
    changes = {
        "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
        "/does/not/exist": {"memory.max": "1073741824"},
    }
    tc = caps.TempCaps(changes, root=str(cgroup_root))
    with pytest.raises(caps.CapsError):
        tc.__enter__()
    assert read(target) == before
    assert tc.applied == []


# ── apply + restore ──────────────────────────────────────────────────────────

def test_apply_then_normal_exit_restores_the_original_value(cgroup_root):
    target = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    before = read(target)
    assert before == str(8 * 1024 ** 3)

    tc = caps.TempCaps(
        {"/dev.slice/dev-background.slice": {"memory.max": "1073741824"}}, root=str(cgroup_root)
    )
    with tc as applied:
        assert read(target) == "1073741824"
        assert len(applied) == 1
        assert applied[0].old == before
        assert applied[0].new == "1073741824"

    assert read(target) == before


def test_restoring_an_originally_max_value_writes_back_the_literal_string(cgroup_root):
    target = cgroup_root / "dev.slice/memory.high"
    assert read(target) == "max"

    tc = caps.TempCaps({"/dev.slice": {"memory.high": "2147483648"}}, root=str(cgroup_root))
    with tc as applied:
        assert read(target) == "2147483648"
        assert applied[0].old is None   # None is the "was max" sentinel

    assert read(target) == "max"


def test_exception_inside_with_block_still_restores(cgroup_root):
    target = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    before = read(target)

    with pytest.raises(RuntimeError):
        with caps.TempCaps(
            {"/dev.slice/dev-background.slice": {"memory.max": "1073741824"}}, root=str(cgroup_root)
        ):
            assert read(target) == "1073741824"
            raise RuntimeError("boom mid-session")

    assert read(target) == before


def test_only_the_named_files_are_ever_touched(cgroup_root):
    max_path = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    high_path = cgroup_root / "dev.slice/dev-background.slice/memory.high"
    high_before = read(high_path)

    with caps.TempCaps(
        {"/dev.slice/dev-background.slice": {"memory.max": "1073741824"}}, root=str(cgroup_root)
    ):
        assert read(high_path) == high_before   # untouched while applied

    assert read(high_path) == high_before        # still untouched after restore
    assert read(max_path) == str(8 * 1024 ** 3)  # and memory.max is back


def test_restoring_a_literal_zero_writes_back_zero_not_max(cgroup_root):
    # "0" is a legitimate, meaningful value (memory.min defaults to it) —
    # distinct from None ("was max"), and must round-trip exactly, not get
    # coerced into the max sentinel by an over-eager falsiness check.
    target = cgroup_root / "dev.slice/dev-background.slice/memory.min"
    assert read(target) == "0"

    with caps.TempCaps(
        {"/dev.slice/dev-background.slice": {"memory.min": "1048576"}}, root=str(cgroup_root)
    ) as applied:
        assert read(target) == "1048576"
        assert applied[0].old == "0"

    assert read(target) == "0"


def test_restoring_an_originally_empty_file_writes_back_empty(cgroup_root):
    target = cgroup_root / "dev.slice/dev-background.slice/memory.min"
    target.write_text("")   # exists, readable, but empty — not the same as "max"

    with caps.TempCaps(
        {"/dev.slice/dev-background.slice": {"memory.min": "1048576"}}, root=str(cgroup_root)
    ) as applied:
        assert read(target) == "1048576"
        assert applied[0].old == ""

    assert read(target) == ""


def test_restore_of_one_entry_failing_still_restores_the_rest(cgroup_root, monkeypatch):
    path_a = str(cgroup_root / "dev.slice/dev-background.slice/memory.max")
    path_b = str(cgroup_root / "wings.slice/memory.high")
    path_c = str(cgroup_root / "dev.slice/memory.high")
    before = {p: read(Path(p)) for p in (path_a, path_b, path_c)}

    real_write = caps._write

    def write_that_fails_to_restore_b(path: str, value: str) -> None:
        if path == path_b and value == before[path_b]:
            raise OSError("synthetic failure restoring the middle entry")
        real_write(path, value)

    tc = caps.TempCaps(
        {
            "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
            "/wings.slice": {"memory.high": "1073741824"},
            "/dev.slice": {"memory.high": "2147483648"},
        },
        root=str(cgroup_root),
    )
    tc.__enter__()
    assert len(tc.applied) == 3

    monkeypatch.setattr(caps, "_write", write_that_fails_to_restore_b)
    tc._restore()   # must not raise — OSError per entry is swallowed

    assert read(Path(path_a)) == before[path_a]   # restored
    assert read(Path(path_c)) == before[path_c]   # restored
    assert read(Path(path_b)) == "1073741824"      # this one alone stayed applied
    assert tc.applied == []   # still popped off the list even though its write failed


def test_never_applies_a_partial_change_set_when_a_later_write_fails(cgroup_root, monkeypatch):
    first_path = str(cgroup_root / "dev.slice/dev-background.slice/memory.max")
    second_path = str(cgroup_root / "wings.slice/memory.high")
    first_before = read(Path(first_path))
    second_before = read(Path(second_path))

    real_write = caps._write

    def flaky_write(path: str, value: str) -> None:
        if path == second_path:
            raise OSError("synthetic failure writing the second file")
        real_write(path, value)

    monkeypatch.setattr(caps, "_write", flaky_write)

    changes = {
        "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
        "/wings.slice": {"memory.high": "1073741824"},
    }
    tc = caps.TempCaps(changes, root=str(cgroup_root))
    with pytest.raises(OSError):
        tc.__enter__()

    assert read(Path(first_path)) == first_before   # rolled back, not left applied
    assert read(Path(second_path)) == second_before
    assert tc.applied == []


# ── SIGINT/SIGTERM ───────────────────────────────────────────────────────────

class FakeSignalRegistry:
    def __init__(self) -> None:
        self.handlers: Dict[int, Any] = {}

    def set_signal(self, sig, handler):
        prev = self.handlers.get(sig, "DEFAULT")
        self.handlers[sig] = handler
        return prev


def test_sigterm_mid_run_restores_and_raises_system_exit(cgroup_root, monkeypatch):
    registry = FakeSignalRegistry()
    monkeypatch.setattr(caps, "_set_signal", registry.set_signal)

    target = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    before = read(target)

    tc = caps.TempCaps(
        {"/dev.slice/dev-background.slice": {"memory.max": "1073741824"}}, root=str(cgroup_root)
    )
    tc.__enter__()
    assert read(target) == "1073741824"

    handler = registry.handlers[signal.SIGTERM]
    with pytest.raises(SystemExit) as exc_info:
        handler(signal.SIGTERM, None)   # simulate delivery — no real kill involved

    assert exc_info.value.code == 128 + signal.SIGTERM
    assert read(target) == before
    assert registry.handlers[signal.SIGTERM] == "DEFAULT"


def test_sigterm_during_apply_rolls_back_the_partial_batch(cgroup_root, monkeypatch):
    # A signal landing between two files' applies, not after the batch
    # finished — only the first file was ever written, and it must come
    # back, while the second must never have been touched at all.
    registry = FakeSignalRegistry()
    monkeypatch.setattr(caps, "_set_signal", registry.set_signal)

    path_a = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    path_b = cgroup_root / "wings.slice/memory.high"
    before_a, before_b = read(path_a), read(path_b)

    real_write = caps._write

    def write_that_signals_on_the_second_file(path: str, value: str) -> None:
        if path == str(path_b):
            registry.handlers[signal.SIGTERM](signal.SIGTERM, None)   # never returns
        real_write(path, value)

    monkeypatch.setattr(caps, "_write", write_that_signals_on_the_second_file)

    tc = caps.TempCaps(
        {
            "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
            "/wings.slice": {"memory.high": "1073741824"},
        },
        root=str(cgroup_root),
    )
    with pytest.raises(SystemExit):
        tc.__enter__()

    assert read(path_a) == before_a   # applied, then rolled back by the signal handler's cleanup
    assert read(path_b) == before_b   # never applied in the first place
    assert tc.applied == []


def test_sigterm_during_restore_is_reentrancy_safe(cgroup_root, monkeypatch):
    # A signal landing *while restore is already in progress* (a second
    # SIGTERM, or a slow supervisor) re-enters _restore through the same
    # handler. self.applied is a list being drained, which makes the
    # reentrant call and the interrupted outer call cooperate safely: the
    # reentrant call finishes draining it, and the abandoned outer call's
    # loop simply finds nothing left to do.
    registry = FakeSignalRegistry()
    monkeypatch.setattr(caps, "_set_signal", registry.set_signal)

    path_a = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    path_b = cgroup_root / "wings.slice/memory.high"
    before_a, before_b = read(path_a), read(path_b)

    tc = caps.TempCaps(
        {
            "/dev.slice/dev-background.slice": {"memory.max": "1073741824"},
            "/wings.slice": {"memory.high": "1073741824"},
        },
        root=str(cgroup_root),
    )
    tc.__enter__()
    assert len(tc.applied) == 2

    real_write = caps._write

    def write_that_signals_partway_through_restore(path: str, value: str) -> None:
        real_write(path, value)
        if path == str(path_b):   # LIFO: path_b's restore write happens first
            registry.handlers[signal.SIGTERM](signal.SIGTERM, None)   # never returns

    monkeypatch.setattr(caps, "_write", write_that_signals_partway_through_restore)

    with pytest.raises(SystemExit):
        tc._restore()

    assert read(path_a) == before_a
    assert read(path_b) == before_b
    assert tc.applied == []


def test_exit_restores_prior_signal_handlers_on_the_normal_path(cgroup_root, monkeypatch):
    registry = FakeSignalRegistry()
    registry.handlers[signal.SIGINT] = "PRIOR-INT"
    registry.handlers[signal.SIGTERM] = "PRIOR-TERM"
    monkeypatch.setattr(caps, "_set_signal", registry.set_signal)

    with caps.TempCaps(
        {"/dev.slice/dev-background.slice": {"memory.max": "1073741824"}}, root=str(cgroup_root)
    ):
        pass

    assert registry.handlers[signal.SIGINT] == "PRIOR-INT"
    assert registry.handlers[signal.SIGTERM] == "PRIOR-TERM"


def test_install_signal_teardown_swallows_a_set_signal_failure(monkeypatch):
    def flaky_set_signal(sig, handler):
        if sig == signal.SIGTERM:
            raise ValueError("signal only works in the main thread")
        return "PRIOR"

    monkeypatch.setattr(caps, "_set_signal", flaky_set_signal)
    previous = caps._install_signal_teardown(lambda: None)
    assert signal.SIGINT in previous
    assert signal.SIGTERM not in previous


def test_restore_signal_handlers_swallows_a_set_signal_failure(monkeypatch):
    def flaky_set_signal(sig, handler):
        raise OSError("cannot restore from here")

    monkeypatch.setattr(caps, "_set_signal", flaky_set_signal)
    caps._restore_signal_handlers({signal.SIGTERM: "PRIOR"})   # must not raise
