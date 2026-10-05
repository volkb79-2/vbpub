"""Tests for lib.damon: sysfs-path construction and the always-teardown
contract, against a fake sysfs tree and a stubbed SysfsInterface/Classifier —
never a real kdamond (DESIGN.md §7: this suite must not touch the kernel's
one shared DAMON facility).
"""

from __future__ import annotations

import errno
import importlib
import shutil
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from lib import caps, damon


class FakeSysfsInterface:
    """Mirrors the two pieces of state that actually matter for the
    acquire/release contract — ``nr_kdamonds`` and each kdamond's ``state``
    — as real files under a tmp_path tree, so ``damon._read_nr_kdamonds()``
    exercises its real path-joining logic against real reads rather than an
    in-memory dict. Every other sysfs write (context/target/scheme wiring) is
    just recorded in ``calls`` — this suite is proving acquire/release and
    path correctness, not re-deriving the DAMON kernel ABI.
    """

    root: Path
    calls: List[tuple]
    fail_on: Optional[str]
    target_inputs: Dict[tuple, List[Optional[int]]]
    running_targets: Dict[int, List[Optional[int]]]

    @classmethod
    def configure(cls, root: Path) -> None:
        cls.root = root
        cls.calls = []
        cls.fail_on = None
        cls.target_inputs = {}
        cls.running_targets = {}
        cls.root.mkdir(parents=True, exist_ok=True)
        (cls.root / "nr_kdamonds").write_text("0")

    @classmethod
    def _maybe_fail(cls, name: str) -> None:
        if cls.fail_on == name:
            raise RuntimeError(f"synthetic failure in {name}")

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def create_kdamond(cls, idx: int = 0) -> str:
        cls.calls.append(("create_kdamond", idx))
        cls._maybe_fail("create_kdamond")
        current = int((cls.root / "nr_kdamonds").read_text())
        if idx + 1 > current:
            if any(
                (cls.root / str(i) / "state").exists() and cls.state_of(i) == "on"
                for i in range(current)
            ):
                raise OSError(errno.EBUSY, "cannot resize while a kdamond is running")
            cls._replace_registry(idx + 1)
        if not (cls.root / str(idx) / "state").exists():
            (cls.root / str(idx)).mkdir(exist_ok=True)
            (cls.root / str(idx) / "state").write_text("off")
        return str(cls.root / str(idx))

    @classmethod
    def _replace_registry(cls, count: int) -> None:
        """Model DAMON sysfs: every nr_kdamonds write rebuilds all entries."""
        for path in cls.root.iterdir():
            if path.is_dir() and path.name.isdigit():
                shutil.rmtree(path)
        (cls.root / "nr_kdamonds").write_text(str(count))
        for idx in range(count):
            kdamond = cls.root / str(idx)
            kdamond.mkdir()
            (kdamond / "state").write_text("off")

    @classmethod
    def create_context(cls, kdamond_idx: int = 0, ctx_idx: int = 0) -> None:
        cls.calls.append(("create_context", kdamond_idx, ctx_idx))
        cls._maybe_fail("create_context")

    @classmethod
    def set_operations(cls, kdamond_idx: int, ctx_idx: int, ops: str) -> None:
        cls.calls.append(("set_operations", kdamond_idx, ctx_idx, ops))
        cls._maybe_fail("set_operations")

    @classmethod
    def set_intervals(cls, kdamond_idx, ctx_idx, sample_us, aggr_us, update_us) -> None:
        cls.calls.append(("set_intervals", kdamond_idx, ctx_idx, sample_us, aggr_us, update_us))
        cls._maybe_fail("set_intervals")

    @classmethod
    def create_target(cls, kdamond_idx, ctx_idx, target_idx: int = 0) -> None:
        cls.calls.append(("create_target", kdamond_idx, ctx_idx, target_idx))
        cls._maybe_fail("create_target")

    @classmethod
    def set_nr_targets(cls, kdamond_idx, ctx_idx, nr_targets: int) -> None:
        cls.calls.append(("set_nr_targets", kdamond_idx, ctx_idx, nr_targets))
        cls._maybe_fail("set_nr_targets")
        cls.target_inputs[(kdamond_idx, ctx_idx)] = [None] * nr_targets

    @classmethod
    def set_pid_target(cls, kdamond_idx, ctx_idx, target_idx, pid) -> None:
        cls.calls.append(("set_pid_target", kdamond_idx, ctx_idx, target_idx, pid))
        cls._maybe_fail("set_pid_target")
        cls.target_inputs[(kdamond_idx, ctx_idx)][target_idx] = pid

    @classmethod
    def create_scheme(cls, kdamond_idx, ctx_idx, scheme_idx: int = 0) -> None:
        cls.calls.append(("create_scheme", kdamond_idx, ctx_idx, scheme_idx))
        cls._maybe_fail("create_scheme")

    @classmethod
    def set_scheme_action(cls, kdamond_idx, ctx_idx, scheme_idx, action) -> None:
        cls.calls.append(("set_scheme_action", kdamond_idx, ctx_idx, scheme_idx, action))
        cls._maybe_fail("set_scheme_action")

    @classmethod
    def set_scheme_access_pattern(cls, kdamond_idx, ctx_idx, scheme_idx, *bounds) -> None:
        cls.calls.append(("set_scheme_access_pattern", kdamond_idx, ctx_idx, scheme_idx, bounds))
        cls._maybe_fail("set_scheme_access_pattern")

    @classmethod
    def kdamond_commit(cls, idx: int = 0) -> None:
        cls.calls.append(("kdamond_commit", idx))
        cls._maybe_fail("kdamond_commit")
        # The kernel's `commit` command updates an already-running kdamond;
        # initial configuration is applied by `on` after the sysfs inputs
        # have been written. Model the observed off-state EINVAL here.
        if cls.state_of(idx) != "on":
            raise OSError(errno.EINVAL, "commit requires a running kdamond")
        cls.running_targets[idx] = list(cls.target_inputs[(idx, 0)])

    @classmethod
    def kdamond_on(cls, idx: int = 0) -> None:
        cls.calls.append(("kdamond_on", idx))
        cls._maybe_fail("kdamond_on")
        cls.running_targets[idx] = list(cls.target_inputs.get((idx, 0), []))
        (cls.root / str(idx) / "state").write_text("on")

    @classmethod
    def kdamond_off(cls, idx: int = 0) -> None:
        cls.calls.append(("kdamond_off", idx))
        cls._maybe_fail("kdamond_off")
        (cls.root / str(idx) / "state").write_text("off")

    @classmethod
    def kdamond_update_tried_regions(cls, idx: int = 0) -> None:
        cls.calls.append(("kdamond_update_tried_regions", idx))
        cls._maybe_fail("kdamond_update_tried_regions")

    @classmethod
    def read_tried_regions(cls, kdamond_idx=0, ctx_idx=0, scheme_idx=0) -> List[Dict]:
        cls.calls.append(("read_tried_regions", kdamond_idx, ctx_idx, scheme_idx))
        return [{"start": 0, "end": 4096, "nr_accesses": 10, "age": 2}]

    @classmethod
    def _write_int(cls, path: str, value: int) -> None:
        cls.calls.append(("_write_int", path, value))
        cls._maybe_fail("_write_int")
        if path.endswith("/nr_kdamonds"):
            current = int(Path(path).read_text().strip())
            if any(
                (cls.root / str(i) / "state").exists() and cls.state_of(i) == "on"
                for i in range(current)
            ):
                raise OSError(errno.EBUSY, "cannot resize while a kdamond is running")
            cls._replace_registry(value)
        else:
            Path(path).write_text(str(value))

    @classmethod
    def _read_int(cls, path: str) -> int:
        cls.calls.append(("_read_int", path))
        cls._maybe_fail("_read_int")
        return int(Path(path).read_text().strip())

    @classmethod
    def state_of(cls, idx: int = 0) -> str:
        return (cls.root / str(idx) / "state").read_text().strip()

    @classmethod
    def kdamond_state(cls, idx: int) -> str:
        cls.calls.append(("kdamond_state", idx))
        cls._maybe_fail("kdamond_state")
        return cls.state_of(idx)

    @classmethod
    def nr_kdamonds(cls) -> int:
        return int((cls.root / "nr_kdamonds").read_text().strip())


class FakeClassifier:
    def __init__(self, *a, **kw) -> None:
        pass

    def classify_regions(self, regions, sample_us, aggr_us) -> List[Dict]:
        out = []
        for region in regions:
            region = dict(region)
            region["class"] = "hot"
            region["temperature"] = 1.0
            out.append(region)
        return out

    def summary(self, classified) -> Dict:
        return {"hot": {"count": len(classified), "bytes": 0}}


@pytest.fixture(autouse=True)
def fake_damon(tmp_path, monkeypatch):
    FakeSysfsInterface.configure(tmp_path / "kdamonds")
    monkeypatch.setattr(damon, "SysfsInterface", FakeSysfsInterface)
    monkeypatch.setattr(damon, "Classifier", FakeClassifier)
    monkeypatch.setattr(damon, "KDAMONDS_DIR", str(tmp_path / "kdamonds"))
    lock_dir = tmp_path / "cgprofile"
    lock_dir.mkdir()
    monkeypatch.setattr(damon, "DAMON_REGISTRY_LOCK_PATH", str(lock_dir / "damon.lock"))
    return FakeSysfsInterface


def make_target(kind="vaddr", pid=4242, label="t") -> damon.DamonTarget:
    return damon.DamonTarget(kind=kind, pid=pid, label=label)


def test_registry_lock_is_exclusive_nonblocking_and_reusable(fake_damon):
    first = damon._DamonRegistryLock()
    second = damon._DamonRegistryLock()
    first.acquire()
    with pytest.raises(damon.DamonSessionError, match="another cgprofile process"):
        second.acquire()
    first.release()
    first.release()
    second.acquire()
    second.release()


def test_registry_lock_rejects_nonabsolute_override(fake_damon, monkeypatch):
    monkeypatch.setenv("CGPROFILE_DAMON_LOCK_PATH", "relative.lock")
    with pytest.raises(damon.DamonSessionError, match="must be absolute"):
        damon._DamonRegistryLock().acquire()


def test_registry_lock_uses_an_explicit_absolute_override(fake_damon, monkeypatch, tmp_path):
    override = tmp_path / "explicit.lock"
    monkeypatch.setenv("CGPROFILE_DAMON_LOCK_PATH", str(override))
    with damon._DamonRegistryLock():
        assert override.is_file()


def test_registry_lock_does_not_apply_host_permissions_to_an_override(
    fake_damon, monkeypatch, tmp_path,
):
    override = tmp_path / "explicit.lock"
    calls = []
    monkeypatch.setenv("CGPROFILE_DAMON_LOCK_PATH", str(override))
    monkeypatch.setattr(
        damon, "_set_registry_lock_permissions",
        lambda _fd, path: calls.append(path),
    )

    with damon._DamonRegistryLock():
        assert override.is_file()

    assert calls == []


def test_registry_lock_reports_missing_parent(fake_damon, monkeypatch, tmp_path):
    monkeypatch.setattr(
        damon, "DAMON_REGISTRY_LOCK_PATH", str(tmp_path / "missing" / "damon.lock")
    )
    with pytest.raises(damon.DamonSessionError, match="cannot acquire shared DAMON lock"):
        damon._DamonRegistryLock().acquire()


def test_registry_lock_rejects_nonregular_file(fake_damon, monkeypatch):
    monkeypatch.setattr(damon.stat, "S_ISREG", lambda _mode: False)
    with pytest.raises(damon.DamonSessionError, match="not a regular file"):
        damon._DamonRegistryLock().acquire()


def test_prepare_registry_lock_file_sets_shared_permissions(fake_damon):
    # Model a caller creating the lock first under a restrictive umask. The
    # host directory's group is inherited in deployment via its setgid bit;
    # the caller must widen only the file mode to the shared 0660 contract.
    old_umask = damon.os.umask(0o077)
    try:
        path = Path(damon.prepare_registry_lock_file())
    finally:
        damon.os.umask(old_umask)
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o660
    assert path.stat().st_gid == path.parent.stat().st_gid


def test_prepare_registry_lock_file_accepts_daemon_first_shared_file(
    fake_damon, monkeypatch,
):
    path = Path(damon.DAMON_REGISTRY_LOCK_PATH)
    path.touch()
    path.chmod(0o660)

    def caller_cannot_mutate_daemon_owned_file(*_args):
        raise PermissionError("daemon owns the already-correct shared lock")

    monkeypatch.setattr(damon.os, "fchown", caller_cannot_mutate_daemon_owned_file)
    monkeypatch.setattr(damon.os, "fchmod", caller_cannot_mutate_daemon_owned_file)

    assert damon.prepare_registry_lock_file() == str(path)
    assert path.stat().st_mode & 0o777 == 0o660
    assert path.stat().st_gid == path.parent.stat().st_gid


def test_prepare_registry_lock_file_fails_closed_when_group_cannot_be_repaired(
    fake_damon, monkeypatch,
):
    path = Path(damon.DAMON_REGISTRY_LOCK_PATH)
    path.touch()
    path.chmod(0o660)
    actual_directory_gid = path.parent.stat().st_gid
    requested_gid = actual_directory_gid + 1
    attempted = {}

    directory_stat = type("DirectoryStat", (), {"st_gid": requested_gid})()
    real_stat = damon.os.stat

    def fake_stat(candidate, *args, **kwargs):
        if str(candidate) == str(path.parent):
            return directory_stat
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(damon.os, "stat", fake_stat)
    monkeypatch.setattr(
        damon.os, "fchown",
        lambda fd, uid, gid: attempted.update(fd=fd, uid=uid, gid=gid),
    )
    with pytest.raises(damon.DamonSessionError, match="unusable group or mode"):
        damon.prepare_registry_lock_file()
    assert attempted["uid"] == -1
    assert attempted["gid"] == requested_gid


def test_prepare_registry_lock_file_rejects_nonregular_file(fake_damon, monkeypatch):
    monkeypatch.setattr(damon.stat, "S_ISREG", lambda _mode: False)
    with pytest.raises(damon.DamonSessionError, match="not a regular file"):
        damon.prepare_registry_lock_file()


def test_prepare_registry_lock_file_wraps_permission_failures(fake_damon, monkeypatch):
    path = Path(damon.DAMON_REGISTRY_LOCK_PATH)
    path.touch()
    path.chmod(0o600)

    def deny_mode_change(*_args):
        raise PermissionError("denied")

    monkeypatch.setattr(damon.os, "fchmod", deny_mode_change)
    with pytest.raises(damon.DamonSessionError, match="cannot prepare shared DAMON lock"):
        damon.prepare_registry_lock_file()


def test_prepare_registry_lock_file_refuses_when_host_directory_is_missing(
    fake_damon, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        damon, "DAMON_REGISTRY_LOCK_PATH", str(tmp_path / "missing" / "damon.lock")
    )
    with pytest.raises(damon.DamonSessionError, match="cannot prepare shared DAMON lock"):
        damon.prepare_registry_lock_file()


def test_prepare_registry_lock_file_refuses_a_symlink(fake_damon, monkeypatch, tmp_path):
    target = tmp_path / "target"
    target.touch()
    lock_path = Path(damon.DAMON_REGISTRY_LOCK_PATH)
    lock_path.symlink_to(target)
    with pytest.raises(damon.DamonSessionError, match="cannot prepare shared DAMON lock"):
        damon.prepare_registry_lock_file()


def test_solo_session_refuses_when_another_cgprofile_process_holds_the_lock(fake_damon):
    held = damon._DamonRegistryLock()
    held.acquire()
    try:
        with pytest.raises(damon.DamonSessionError, match="another cgprofile process"):
            with damon.DamonSession([make_target()]):
                pytest.fail("session must not race the existing DAMON owner")
        assert fake_damon.nr_kdamonds() == 0
        assert fake_damon.calls == []
    finally:
        held.release()


def test_pool_reservation_refuses_while_one_shot_owns_the_shared_lock(fake_damon):
    held = damon._DamonRegistryLock()
    held.acquire()
    try:
        with pytest.raises(damon.DamonSessionError, match="another cgprofile process"):
            damon.KdamondPool().acquire()
        assert fake_damon.nr_kdamonds() == 0
        assert fake_damon.calls == []
    finally:
        held.release()


# ── available() / damo_usable() ─────────────────────────────────────────────

def test_available_true_when_sysfs_and_lib_present(fake_damon):
    assert damon.available() is True


def test_available_false_when_sysfs_interface_absent(monkeypatch):
    monkeypatch.setattr(damon, "SysfsInterface", None)
    assert damon.available() is False


def test_available_false_when_is_available_raises(monkeypatch):
    class Broken:
        @staticmethod
        def is_available():
            raise OSError("no such directory")

    monkeypatch.setattr(damon, "SysfsInterface", Broken)
    assert damon.available() is False


def test_damo_usable_false_when_script_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(damon, "_DAMO_BIN", str(tmp_path / "no-such-damo"))
    assert damon.damo_usable() is False


def test_damo_usable_false_for_broken_host_shebang(tmp_path, monkeypatch):
    # A real absolute path here (this project's own actual host venv, as an
    # earlier version of this test used) is not a reliable "known broken"
    # reference: found live inside a tester-unified container, where a
    # damon-analysis/venv/bin/python3 symlink resolves to /usr/bin/python3.13
    # -- that exact absolute path happens to be tester-unified's own system
    # interpreter, so the "broken" shebang accidentally became a working one
    # in that one environment, flipping this assertion. tmp_path's own
    # namespace guarantees an interpreter path no real environment provides.
    script = tmp_path / "damo"
    script.write_text(f"#!{tmp_path}/definitely-not-a-real-interpreter\n")
    monkeypatch.setattr(damon, "_DAMO_BIN", str(script))
    assert damon.damo_usable() is False


def test_damo_usable_true_for_a_live_interpreter(tmp_path, monkeypatch):
    script = tmp_path / "damo"
    script.write_text(f"#!{sys.executable}\n")
    monkeypatch.setattr(damon, "_DAMO_BIN", str(script))
    assert damon.damo_usable() is True


def test_damo_usable_false_when_the_file_has_no_shebang_at_all(tmp_path, monkeypatch):
    script = tmp_path / "damo"
    script.write_text("just a plain text file, not a script\n")
    monkeypatch.setattr(damon, "_DAMO_BIN", str(script))
    assert damon.damo_usable() is False


def test_module_import_degrades_cleanly_when_damon_analysis_is_absent():
    """Adversarial: simulate the sibling checkout being entirely missing by
    blocking ``import damon_analysis`` and reloading this module fresh — the
    guarded ``try/except`` at module scope must degrade to
    ``SysfsInterface = None`` rather than let ImportError escape, and
    ``available()`` must reflect that without raising. Restores the real
    binding afterwards so every other test in this file keeps working.
    """
    real_module = sys.modules.get("damon_analysis")
    sys.modules["damon_analysis"] = None   # forces ImportError on the next import
    try:
        importlib.reload(damon)
        assert damon.SysfsInterface is None
        assert damon.Classifier is None
        assert damon.available() is False
    finally:
        if real_module is not None:
            sys.modules["damon_analysis"] = real_module
        else:
            sys.modules.pop("damon_analysis", None)
        importlib.reload(damon)   # restore real bindings for the rest of the suite


def test_reimporting_skips_a_duplicate_sys_path_insert():
    # The module-scope guard only inserts _DAMON_ANALYSIS_LIB once; prove the
    # "already present" branch is exercised too, not just implied by the
    # very first import order at collection time.
    assert damon._DAMON_ANALYSIS_LIB in sys.path   # true ever since the first import
    importlib.reload(damon)
    assert damon._DAMON_ANALYSIS_LIB in sys.path


# ── DamonTarget / DamonSession construction-time validation ────────────────

def test_damon_target_is_a_plain_dataclass():
    target = damon.DamonTarget(kind="vaddr", pid=123, label="soulmask")
    assert (target.kind, target.pid, target.label) == ("vaddr", 123, "soulmask")


def test_session_rejects_empty_targets():
    with pytest.raises(ValueError):
        damon.DamonSession([])


def test_session_rejects_mixed_kinds():
    with pytest.raises(ValueError):
        damon.DamonSession([make_target("vaddr"), make_target("paddr", pid=None)])


def test_session_rejects_unknown_kind():
    with pytest.raises(ValueError):
        damon.DamonSession([damon.DamonTarget(kind="bogus", pid=1, label="x")])


def test_session_rejects_vaddr_without_pid():
    with pytest.raises(ValueError):
        damon.DamonSession([damon.DamonTarget(kind="vaddr", pid=None, label="x")])


def test_session_construction_does_not_touch_sysfs(fake_damon):
    damon.DamonSession([make_target()])
    assert fake_damon.calls == []


# ── acquire / release: the core contract ────────────────────────────────────

def test_normal_exit_tears_down_and_restores_nr_kdamonds(fake_damon):
    with damon.DamonSession([make_target()]) as session:
        assert session._entered is True
        assert fake_damon.nr_kdamonds() == 1
        assert fake_damon.state_of(0) == "on"
    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0


def test_solo_teardown_closes_identity_pin_even_if_close_reports_error(fake_damon):
    class FailingIdentity:
        def matches(self, _idx):
            return True

        def close(self):
            raise OSError("synthetic close failure")

    session = damon.DamonSession([make_target()])
    session.__enter__()
    session._solo_identity.close()
    session._solo_identity = FailingIdentity()

    session.__exit__(None, None, None)
    assert session._solo_identity is None
    assert session.cleanup_confirmed
    assert fake_damon.nr_kdamonds() == 0


def test_sysfs_path_construction_for_nr_kdamonds(tmp_path, fake_damon):
    # damon._read_nr_kdamonds must build exactly <KDAMONDS_DIR>/nr_kdamonds:
    # write a sentinel directly at that path and read it back through the
    # real function under test, not the fake's own convenience accessor.
    (tmp_path / "kdamonds" / "nr_kdamonds").write_text("3")
    assert damon._read_nr_kdamonds() == 3


def test_read_nr_kdamonds_returns_none_on_a_real_read_failure(fake_damon):
    fake_damon.fail_on = "_read_int"
    assert damon._read_nr_kdamonds() is None


def test_install_signal_teardown_swallows_a_set_signal_failure(monkeypatch):
    def flaky_set_signal(sig, handler):
        if sig == signal.SIGTERM:
            raise ValueError("signal only works in the main thread")
        return "PRIOR"

    monkeypatch.setattr(damon, "_set_signal", flaky_set_signal)
    previous = damon._install_signal_teardown(lambda: None)
    # SIGINT installed fine; SIGTERM's failure is swallowed, not raised, and
    # simply absent from what gets restored later.
    assert signal.SIGINT in previous
    assert signal.SIGTERM not in previous


def test_restore_signal_handlers_swallows_a_set_signal_failure(monkeypatch):
    def flaky_set_signal(sig, handler):
        raise OSError("cannot restore from here")

    monkeypatch.setattr(damon, "_set_signal", flaky_set_signal)
    damon._restore_signal_handlers({signal.SIGTERM: "PRIOR"})   # must not raise


def test_enter_wires_the_vaddr_target_correctly(fake_damon):
    target = damon.DamonTarget(kind="vaddr", pid=999, label="soulmask")
    with damon.DamonSession([target], sample_us=50_000, aggr_us=1_000_000):
        names = [call[0] for call in fake_damon.calls]
        assert "set_operations" in names
        ops_call = next(c for c in fake_damon.calls if c[0] == "set_operations")
        assert ops_call[-1] == "vaddr"
        pid_call = next(c for c in fake_damon.calls if c[0] == "set_pid_target")
        assert pid_call[-1] == 999
        assert fake_damon.target_inputs[(0, 0)] == [999]
        assert fake_damon.running_targets[0] == [999]
        intervals_call = next(c for c in fake_damon.calls if c[0] == "set_intervals")
        assert intervals_call[3:5] == (50_000, 1_000_000)
        action_call = next(c for c in fake_damon.calls if c[0] == "set_scheme_action")
        assert action_call[-1] == "stat"   # never a migrate/reclaim action
        names = [call[0] for call in fake_damon.calls]
        assert names[-1] == "kdamond_on"
        assert "kdamond_commit" not in names


def test_exception_inside_with_block_still_tears_down(fake_damon):
    with pytest.raises(RuntimeError):
        with damon.DamonSession([make_target()]):
            assert fake_damon.state_of(0) == "on"
            raise RuntimeError("boom mid-session")
    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0


def test_exception_during_enter_still_tears_down(fake_damon):
    fake_damon.fail_on = "set_scheme_action"
    with pytest.raises(RuntimeError) as excinfo:
        with damon.DamonSession([make_target()]):
            pytest.fail("should never reach the with-body")
    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0
    # A foreign exception type raised by SysfsInterface must come out of
    # __enter__ as a DamonSessionError. The broad RuntimeError assertion above
    # would not catch a regression back to a bare `raise`, so assert the
    # wrapped type and message explicitly.
    assert isinstance(excinfo.value, damon.DamonSessionError)
    assert "RuntimeError: synthetic failure in set_scheme_action" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


def test_exception_during_enter_that_is_already_damon_session_error_is_not_rewrapped(
    fake_damon, monkeypatch
):
    # If something inside the try block ever raises DamonSessionError
    # directly (rather than a foreign exception type SysfsInterface might
    # raise), __enter__ must re-raise it bare — never double-wrap it as
    # "DamonSessionError: DamonSessionError: <original message>".
    sentinel = damon.DamonSessionError("sentinel-already-a-damon-session-error")

    def _boom(idx: int = 0) -> None:
        raise sentinel

    monkeypatch.setattr(fake_damon, "kdamond_on", _boom)
    with pytest.raises(damon.DamonSessionError) as excinfo:
        with damon.DamonSession([make_target()]):
            pytest.fail("should never reach the with-body")
    assert excinfo.value is sentinel
    assert str(excinfo.value) == "sentinel-already-a-damon-session-error"
    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0


def test_bare_session_refuses_to_resize_a_preexisting_damon_registry(fake_damon):
    # Any nr_kdamonds write destroys every existing context, including a
    # stopped foreign monitor's staged configuration. Refuse before mutation.
    fake_damon.create_kdamond(0)
    marker = fake_damon.root / "0" / "foreign-context"
    marker.write_text("preserve-this-configuration")
    fake_damon.kdamond_on(0)
    assert fake_damon.nr_kdamonds() == 1
    fake_damon.kdamond_off(0)
    fake_damon.calls.clear()
    with pytest.raises(damon.DamonSessionError, match="pre-existing nr_kdamonds=1"):
        damon.DamonSession([make_target()], kdamond_idx=1).__enter__()
    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == "off"
    assert marker.read_text() == "preserve-this-configuration"
    assert not any(call[0] in {"create_kdamond", "kdamond_off", "kdamond_on"}
                   or (call[0] == "_write_int" and call[1].endswith("nr_kdamonds"))
                   for call in fake_damon.calls)


@pytest.mark.parametrize("foreign_state", ["off", "on"])
def test_pool_refuses_to_resize_and_preserves_preexisting_foreign_configuration(
    fake_damon, foreign_state,
):
    fake_damon.create_kdamond(0)
    marker = fake_damon.root / "0" / "foreign-context"
    marker.write_text("preserve-this-configuration")
    if foreign_state == "on":
        fake_damon.kdamond_on(0)
    assert fake_damon.nr_kdamonds() == 1
    fake_damon.calls.clear()
    pool = damon.KdamondPool()
    with pytest.raises(damon.DamonSessionError, match="pre-existing nr_kdamonds=1"):
        pool.acquire()
    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == foreign_state
    assert marker.read_text() == "preserve-this-configuration"
    assert pool._owned == set()
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]


def test_pool_teardown_turns_its_owned_kdamond_off(fake_damon):
    pool = damon.KdamondPool()
    with damon.DamonSession([make_target()], pool=pool) as session:
        owned_idx = session.kdamond_idx
        assert owned_idx == 0
        assert fake_damon.state_of(owned_idx) == "on"
    assert fake_damon.nr_kdamonds() == 0


def test_pool_quarantines_owned_slot_when_state_cannot_be_read(fake_damon, monkeypatch):
    pool = damon.KdamondPool()
    owned = pool.acquire()
    real_state = fake_damon.kdamond_state.__func__

    def unreadable_owned_state(cls, idx):
        raise PermissionError("synthetic unreadable owned state")

    monkeypatch.setattr(
        fake_damon, "kdamond_state", classmethod(unreadable_owned_state)
    )
    fake_damon.calls.clear()

    assert not pool.release(owned)
    assert pool.quarantined_indices == frozenset({owned})
    assert fake_damon.nr_kdamonds() == 1
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]
    monkeypatch.setattr(fake_damon, "kdamond_state", classmethod(real_state))
    assert pool.close()
    assert fake_damon.nr_kdamonds() == 0


def test_bare_session_refuses_a_foreign_existing_slot(fake_damon):
    fake_damon.create_kdamond(0)
    marker = fake_damon.root / "0" / "foreign-context"
    marker.write_text("preserve-this-configuration")
    fake_damon.calls.clear()
    with pytest.raises(damon.DamonSessionError, match="pre-existing nr_kdamonds=1"):
        damon.DamonSession([make_target()], kdamond_idx=0).__enter__()
    assert fake_damon.state_of(0) == "off"
    assert marker.read_text() == "preserve-this-configuration"
    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.calls == [("_read_int", str(fake_damon.root / "nr_kdamonds"))]


def test_enter_wires_a_paddr_target_too(fake_damon):
    # paddr targets carry no pid, and exercise the "not vaddr" arm of the
    # per-target wiring loop that the vaddr-only tests above never touch.
    target = damon.DamonTarget(kind="paddr", pid=None, label="whole-system")
    with damon.DamonSession([target]):
        names = [call[0] for call in fake_damon.calls]
        assert "set_pid_target" not in names
        ops_call = next(c for c in fake_damon.calls if c[0] == "set_operations")
        assert ops_call[-1] == "paddr"


def test_teardown_on_a_never_entered_session_is_a_true_no_op(fake_damon):
    # `_torn_down` starts True precisely so a session that never got as far
    # as `__enter__` (e.g. constructed, then abandoned) is a genuine no-op
    # when torn down -- it must NEVER issue a real sysfs write (`kdamond_off`
    # on index 0 could hit an unrelated kdamond this session never created).
    session = damon.DamonSession([make_target()])
    assert session.cleanup_confirmed
    assert session._teardown()
    assert session.cleanup_confirmed
    assert fake_damon.calls == []


@pytest.mark.parametrize("use_pool", [False, True])
def test_cleanup_is_unconfirmed_while_an_entered_monitor_is_owned(
    fake_damon, use_pool,
):
    pool = damon.KdamondPool(capacity=1) if use_pool else None
    session = damon.DamonSession([make_target()], pool=pool)

    session.__enter__()
    assert not session.cleanup_confirmed

    session.__exit__(None, None, None)
    assert session.cleanup_confirmed


def test_collect_before_enter_refuses_rather_than_reading_a_foreign_kdamond(fake_damon):
    # `_entered` starts False precisely so a freshly-constructed-but-never-
    # entered session's `collect()` refuses instead of silently reading
    # kdamond index 0's tried-regions -- which may belong to a DIFFERENT
    # session entirely (the pool-acquire path picks whichever slot is free,
    # not necessarily this session's own). A `False -> True` flip on the
    # `_entered` init would let this proceed as if `__enter__` had run.
    session = damon.DamonSession([make_target()])
    with pytest.raises(damon.DamonSessionError):
        session.collect()


def test_teardown_is_idempotent_when_called_twice(fake_damon):
    session = damon.DamonSession([make_target()])
    session.__enter__()
    session._teardown()
    calls_after_first = list(fake_damon.calls)
    session._teardown()   # must be a pure no-op the second time
    assert fake_damon.calls == calls_after_first
    session.__exit__(None, None, None)   # a third call, via the normal path
    assert fake_damon.calls == calls_after_first


def test_teardown_does_not_shrink_when_kdamond_off_fails(fake_damon):
    session = damon.DamonSession([make_target()])
    session.__enter__()
    assert fake_damon.nr_kdamonds() == 1
    fake_damon.fail_on = "kdamond_off"
    session.__exit__(None, None, None)   # must not raise
    assert fake_damon.state_of(0) == "on"
    assert fake_damon.nr_kdamonds() == 1
    assert not session.cleanup_confirmed


def test_session_refuses_when_the_prior_count_could_not_be_determined(fake_damon, monkeypatch):
    # If the pre-flight read of nr_kdamonds itself failed, there is no
    # trustworthy basis for proving that the requested slot is ours. Refuse
    # before any sysfs create/configure/on/off operation rather than creating
    # an unowned placeholder and guessing how to tear it down.
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: None)
    with pytest.raises(damon.DamonSessionError):
        damon.DamonSession([make_target()]).__enter__()
    assert fake_damon.calls == []
    assert fake_damon.nr_kdamonds() == 0


def test_teardown_survives_the_shrink_write_itself_failing(fake_damon):
    # kdamond_off succeeds, the prior count is known, but the final write
    # that shrinks nr_kdamonds back down fails (a raced-out sysfs write) —
    # __exit__ must still not raise.
    session = damon.DamonSession([make_target()])
    session.__enter__()
    fake_damon.fail_on = "_write_int"
    session.__exit__(None, None, None)   # must not raise
    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 1   # shrink attempted but failed — left as-is, not crashed


def test_create_kdamond_failure_still_tears_down(fake_damon):
    fake_damon.fail_on = "create_kdamond"
    with pytest.raises(RuntimeError):
        with damon.DamonSession([make_target()]):
            pytest.fail("should never reach the with-body")
    assert fake_damon.nr_kdamonds() == 0


def test_collect_returns_classified_regions_and_summary(fake_damon):
    with damon.DamonSession([make_target()]) as session:
        regions = session.collect()
    assert regions and regions[0]["class"] == "hot"
    assert session.last_summary["hot"]["count"] == len(regions)


def test_collect_outside_session_raises():
    session = damon.DamonSession([make_target()])
    with pytest.raises(damon.DamonSessionError):
        session.collect()


def test_collect_raising_still_allows_normal_teardown(fake_damon):
    fake_damon.fail_on = "kdamond_update_tried_regions"
    with pytest.raises(RuntimeError):
        with damon.DamonSession([make_target()]) as session:
            session.collect()
    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0


def test_enter_raises_cleanly_when_damon_unavailable(monkeypatch):
    monkeypatch.setattr(damon, "SysfsInterface", None)
    with pytest.raises(damon.DamonSessionError):
        with damon.DamonSession([make_target()]):
            pytest.fail("must not enter when damon.available() is False")


def test_exception_in_body_survives_a_failing_kdamond_off_during_teardown(fake_damon):
    # The *original* exception raised in the with-body must be what
    # propagates, even when cleanup itself hits trouble internally — nothing
    # about a failing kdamond_off may replace or hide it.
    fake_damon.fail_on = "kdamond_off"
    with pytest.raises(RuntimeError, match="boom original"):
        with damon.DamonSession([make_target()]):
            raise RuntimeError("boom original")
    assert fake_damon.state_of(0) == "on"
    assert fake_damon.nr_kdamonds() == 1   # never shrink across a monitor still on


# ── SIGINT/SIGTERM ───────────────────────────────────────────────────────────

class FakeSignalRegistry:
    def __init__(self) -> None:
        self.handlers: Dict[int, Any] = {}

    def set_signal(self, sig, handler):
        prev = self.handlers.get(sig, "DEFAULT")
        self.handlers[sig] = handler
        return prev


def test_sigterm_during_session_tears_down_and_raises_system_exit(fake_damon, monkeypatch):
    registry = FakeSignalRegistry()
    monkeypatch.setattr(damon, "_set_signal", registry.set_signal)

    session = damon.DamonSession([make_target()])
    session.__enter__()
    assert fake_damon.state_of(0) == "on"

    handler = registry.handlers[signal.SIGTERM]
    with pytest.raises(SystemExit) as exc_info:
        handler(signal.SIGTERM, None)   # simulate delivery — no real kill involved

    assert exc_info.value.code == 128 + signal.SIGTERM
    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0
    assert registry.handlers[signal.SIGTERM] == "DEFAULT"  # prior handler put back first


def test_sigint_is_armed_too(fake_damon, monkeypatch):
    registry = FakeSignalRegistry()
    monkeypatch.setattr(damon, "_set_signal", registry.set_signal)
    with damon.DamonSession([make_target()]):
        assert signal.SIGINT in registry.handlers
        assert signal.SIGTERM in registry.handlers


def test_exit_restores_prior_signal_handlers_on_the_normal_path(fake_damon, monkeypatch):
    registry = FakeSignalRegistry()
    registry.handlers[signal.SIGINT] = "PRIOR-INT"
    registry.handlers[signal.SIGTERM] = "PRIOR-TERM"
    monkeypatch.setattr(damon, "_set_signal", registry.set_signal)
    with damon.DamonSession([make_target()]):
        pass
    assert registry.handlers[signal.SIGINT] == "PRIOR-INT"
    assert registry.handlers[signal.SIGTERM] == "PRIOR-TERM"


def test_sigterm_during_enter_wiring_tears_down_the_partial_session(fake_damon, monkeypatch):
    # A signal landing mid-__enter__ (kdamond already created, wiring not yet
    # finished) must still release the half-built kdamond, not just one
    # created and fully committed.
    registry = FakeSignalRegistry()
    monkeypatch.setattr(damon, "_set_signal", registry.set_signal)

    def signaling_set_scheme_action(cls, *a, **kw):
        cls.calls.append(("set_scheme_action", a))
        registry.handlers[signal.SIGTERM](signal.SIGTERM, None)   # never returns

    monkeypatch.setattr(
        FakeSysfsInterface, "set_scheme_action", classmethod(signaling_set_scheme_action)
    )

    with pytest.raises(SystemExit):
        damon.DamonSession([make_target()]).__enter__()

    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0


def test_sigterm_during_teardown_is_reentrancy_safe(fake_damon, monkeypatch):
    # A second SIGTERM (or a slow supervisor sending it twice) landing while
    # teardown is already mid-flight must not leave the shrink half-done: the
    # reentrant call runs to completion synchronously before the interrupted
    # outer call would ever resume, and it must be the one that finishes the
    # job — this is the scenario that motivated moving `_torn_down = True`
    # to the *end* of `_teardown` (see its docstring).
    registry = FakeSignalRegistry()
    monkeypatch.setattr(damon, "_set_signal", registry.set_signal)

    session = damon.DamonSession([make_target()])
    session.__enter__()

    signaled = {"done": False}
    real_kdamond_off = FakeSysfsInterface.kdamond_off.__func__

    def kdamond_off_that_signals_once(cls, idx=0):
        real_kdamond_off(cls, idx)
        if not signaled["done"]:
            signaled["done"] = True
            registry.handlers[signal.SIGTERM](signal.SIGTERM, None)   # reentrant, never returns

    monkeypatch.setattr(
        FakeSysfsInterface, "kdamond_off", classmethod(kdamond_off_that_signals_once)
    )

    with pytest.raises(SystemExit):
        session.__exit__(None, None, None)

    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0   # the reentrant call finished the shrink


# ── KdamondPool (C3): pre-reserve before start, then reuse only verified-off slots ──

def test_pool_reserves_concurrent_slots_before_first_monitor_starts(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    assert fake_damon.nr_kdamonds() == 2
    assert fake_damon.state_of(0) == "on"
    assert fake_damon.state_of(1) == "off"
    resize_calls = [call for call in fake_damon.calls
                    if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]
    assert len(resize_calls) == 1
    assert resize_calls[0][2] == 2

    fake_damon.calls.clear()
    s1.__enter__()
    assert (s0.kdamond_idx, s1.kdamond_idx) == (0, 1)
    assert not [call for call in fake_damon.calls
                if call[0] == "create_kdamond" or
                (call[0] == "_write_int" and call[1].endswith("nr_kdamonds"))]
    s1.__exit__(None, None, None)
    s0.__exit__(None, None, None)


def test_pool_stopping_low_index_never_resizes_while_high_index_runs(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    fake_damon.calls.clear()
    s0.__exit__(None, None, None)
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]
    assert fake_damon.nr_kdamonds() == 2
    assert fake_damon.state_of(1) == "on"
    s1.__exit__(None, None, None)


def test_pool_reuses_verified_off_index_for_a_third_session(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    s0.__exit__(None, None, None)
    s2 = damon.DamonSession([make_target(pid=300)], pool=pool)
    s2.__enter__()
    assert s2.kdamond_idx == 0
    assert fake_damon.nr_kdamonds() == 2
    assert fake_damon.running_targets[0] == [300]
    assert fake_damon.running_targets[1] == [200]
    s2.__exit__(None, None, None)
    s1.__exit__(None, None, None)


def test_pool_shrinks_only_after_every_owned_session_is_off(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    s0.__exit__(None, None, None)
    assert fake_damon.nr_kdamonds() == 2
    s1.__exit__(None, None, None)
    assert fake_damon.nr_kdamonds() == 0


def test_pool_refuses_any_reservation_resize_after_a_monitor_is_on(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s0.__enter__()
    assert fake_damon.nr_kdamonds() == 2
    before = [call for call in fake_damon.calls
              if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]
    with pytest.raises(damon.DamonSessionError, match="cannot grow"):
        pool._reserve_capacity(0)
    after = [call for call in fake_damon.calls
             if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]
    assert after == before
    assert fake_damon.state_of(0) == "on"
    s0.__exit__(None, None, None)


def test_failed_stop_quarantines_pool_slot_and_later_acquisition_recovers_it(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    session = damon.DamonSession([make_target()], pool=pool)
    session.__enter__()
    fake_damon.fail_on = "kdamond_off"
    session.__exit__(None, None, None)
    assert fake_damon.state_of(0) == "on"
    assert fake_damon.nr_kdamonds() == 1
    assert not session.cleanup_confirmed
    assert pool.quarantined_indices == frozenset({0})

    fake_damon.calls.clear()
    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        pool.acquire()
    assert not [call for call in fake_damon.calls
                if call[0] == "create_kdamond" or
                (call[0] == "_write_int" and call[1].endswith("nr_kdamonds"))]
    assert fake_damon.state_of(0) == "on"

    fake_damon.fail_on = None
    assert pool.acquire() == 0
    assert fake_damon.state_of(0) == "off"
    assert pool.release(0)
    assert fake_damon.nr_kdamonds() == 0


def test_pool_close_retries_quarantined_stop_before_restoring_count(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    session = damon.DamonSession([make_target()], pool=pool)
    session.__enter__()
    fake_damon.fail_on = "kdamond_off"
    session.__exit__(None, None, None)
    assert not pool.close()
    assert fake_damon.nr_kdamonds() == 1
    fake_damon.fail_on = None
    assert pool.close()
    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0


def test_pool_retains_free_slot_ownership_when_shrink_fails(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    fake_damon.fail_on = "_write_int"
    assert pool.release(idx)
    assert fake_damon.nr_kdamonds() == 1
    assert pool._owned == {idx}
    assert pool._free == {idx}
    fake_damon.fail_on = None
    fake_damon.calls.clear()
    assert pool.acquire() == idx
    assert not [call for call in fake_damon.calls if call[0] == "create_kdamond"]
    assert pool.release(idx)
    assert fake_damon.nr_kdamonds() == 0


def test_pool_preserves_foreign_indices_present_at_baseline(fake_damon):
    fake_damon.create_kdamond(0)
    marker = fake_damon.root / "0" / "foreign-context"
    marker.write_text("preserve-this-configuration")
    pool = damon.KdamondPool(capacity=2)
    with pytest.raises(damon.DamonSessionError, match="pre-existing nr_kdamonds=1"):
        pool.acquire()
    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == "off"
    assert marker.read_text() == "preserve-this-configuration"
    assert pool._owned == set()


def test_solo_session_refuses_nonzero_registry_without_touching_foreign_slot(fake_damon):
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 1)
    marker = fake_damon.root / "0" / "foreign-context"
    marker.write_text("preserve-this-configuration")
    session = damon.DamonSession([make_target()], kdamond_idx=1)
    fake_damon.calls.clear()
    with pytest.raises(damon.DamonSessionError, match="pre-existing nr_kdamonds=1"):
        session.__enter__()
    assert fake_damon.nr_kdamonds() == 1
    assert marker.read_text() == "preserve-this-configuration"
    assert not [call for call in fake_damon.calls
                if call[0] in {"create_kdamond", "kdamond_off", "kdamond_on"}
                or (call[0] == "_write_int" and call[1].endswith("nr_kdamonds"))]


def test_solo_teardown_does_not_write_when_counter_equals_previous(fake_damon):
    # A same-count write replaces the kdamond sysfs object. The pinned inode
    # must make teardown leave the replacement configuration untouched.
    session = damon.DamonSession([make_target()], kdamond_idx=0)
    session.__enter__()
    fake_damon.kdamond_off(0)
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 1)
    marker = fake_damon.root / "0" / "foreign-context"
    marker.write_text("preserve-replacement")
    fake_damon.calls.clear()

    session.__exit__(None, None, None)

    nr_writes = [
        call for call in fake_damon.calls
        if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")
    ]
    assert nr_writes == []
    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == "off"
    assert marker.read_text() == "preserve-replacement"


def test_solo_teardown_with_no_previous_count_fails_closed(fake_damon):
    # The defensive solo teardown branch has no trustworthy baseline to
    # restore.  It may still turn off the slot this session owns, but it must
    # not guess a counter write that could tear down another owner's slot.
    session = damon.DamonSession([make_target()])
    session.__enter__()
    session._prev_nr_kdamonds = None
    fake_damon.calls.clear()

    session.__exit__(None, None, None)

    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 1
    assert not any(call[0] == "_write_int" for call in fake_damon.calls)


def test_pool_detects_same_count_registry_replacement_before_reuse_or_shrink(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    first = pool.acquire()
    second = pool.acquire()
    pool.release(first)
    fake_damon.fail_on = "_write_int"
    assert pool.release(second)  # failed shrink retains the pinned objects
    assert fake_damon.nr_kdamonds() == 2
    fake_damon.fail_on = None
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 2)
    marker = fake_damon.root / "1" / "foreign-context"
    marker.write_text("preserve-same-count-replacement")
    fake_damon.calls.clear()

    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        pool.acquire()

    assert pool.quarantined_indices == frozenset({0, 1})
    assert marker.read_text() == "preserve-same-count-replacement"
    assert not [call for call in fake_damon.calls
                if call[0] in {"kdamond_state", "kdamond_off", "create_kdamond"}
                or (call[0] == "_write_int" and call[1].endswith("nr_kdamonds"))]
    assert not pool.close()


def test_pool_quarantines_a_live_claim_after_same_count_identity_replacement(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    # The same-count write replaces every sysfs state inode, even though the
    # numeric index still exists and the pool had marked it live.
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 1)

    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        pool.acquire()

    assert idx in pool.quarantined_indices
    assert idx not in pool._free


def test_pool_refuses_a_freed_owned_slot_that_disappeared(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    pool.acquire()
    pool.acquire()
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 0)
    with pytest.raises(damon.DamonSessionError):
        pool.acquire()
    assert pool.quarantined_indices == frozenset({0, 1})
    assert pool.live_indices == frozenset()


def test_pool_retains_live_claim_when_counter_cannot_be_read(fake_damon, monkeypatch):
    pool = damon.KdamondPool()
    assert pool.acquire() == 0
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: None)

    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        pool.acquire()

    assert pool.live_indices == frozenset({0})
    assert pool.quarantined_indices == frozenset()


def test_pool_marks_external_growth_while_reusing_a_free_slot(fake_damon):
    pool = damon.KdamondPool(capacity=2)
    first = pool.acquire()
    second = pool.acquire()
    pool.release(first)
    fake_damon.create_kdamond(2)
    marker = fake_damon.root / "0" / "foreign-context"
    marker.write_text("replacement")
    fake_damon.calls.clear()
    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        pool.acquire()
    assert pool._foreign_growth is True
    assert fake_damon.nr_kdamonds() == 3
    assert marker.read_text() == "replacement"
    assert not [call for call in fake_damon.calls
                if call[0] in {"kdamond_off", "create_kdamond"}
                or (call[0] == "_write_int" and call[1].endswith("nr_kdamonds"))]
    assert not pool.close()


def test_pool_refuses_a_counter_that_shrank_before_a_fresh_acquire(fake_damon, monkeypatch):
    readings = iter((0, -1))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    with pytest.raises(damon.DamonSessionError, match="negative nr_kdamonds"):
        damon.KdamondPool().acquire()


def test_pool_refuses_if_registry_grows_before_reservation(fake_damon, monkeypatch):
    # A stale initial zero is not permission to append after another owner
    # creates a slot; even an off slot may carry valuable configuration.
    real_read = damon._read_nr_kdamonds
    readings = iter((0, 0, 1))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    pool = damon.KdamondPool()
    with pytest.raises(damon.DamonSessionError, match="registry changed before reservation"):
        pool.acquire()
    assert pool._owned == set()
    assert pool.live_indices == frozenset()
    assert pool._foreign_growth is True
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]
    monkeypatch.setattr(damon, "_read_nr_kdamonds", real_read)

    # A subsequent return to the original counter does not erase the fact
    # that reservation raced an external registry mutation.
    fake_damon.create_kdamond(0)
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 0)
    calls_before_retry = list(fake_damon.calls)
    with pytest.raises(damon.DamonSessionError, match="ambiguous external registry change"):
        pool.acquire()
    retry_calls = fake_damon.calls[len(calls_before_retry):]
    assert not [call for call in retry_calls
                if call[0] == "create_kdamond"
                or (call[0] == "_write_int" and call[1].endswith("nr_kdamonds"))]


def test_pool_refuses_unexpected_count_after_capacity_write(fake_damon, monkeypatch):
    real_read = damon._read_nr_kdamonds
    reads = 0

    def read_counter():
        nonlocal reads
        reads += 1
        if reads == 4:
            return 2  # the one-write reservation returned an unexpected count
        return real_read()

    monkeypatch.setattr(damon, "_read_nr_kdamonds", read_counter)
    pool = damon.KdamondPool()
    with pytest.raises(damon.DamonSessionError, match="cannot prove DAMON pool capacity"):
        pool.acquire()
    assert pool._foreign_growth is True
    assert pool._owned == set()
    assert fake_damon.nr_kdamonds() == 1
    assert not pool.close()


def test_pool_supports_two_full_batches_in_sequence(fake_damon):
    # After the pool fully drains once (baseline reset to None), a later,
    # unrelated batch of sessions through the SAME pool object must recapture
    # its own fresh baseline rather than reuse the first batch's.
    pool = damon.KdamondPool()
    with damon.DamonSession([make_target(pid=1)], pool=pool):
        assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.nr_kdamonds() == 0
    with damon.DamonSession([make_target(pid=2)], pool=pool) as s:
        assert s.kdamond_idx == 0
        assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.nr_kdamonds() == 0


def test_pool_acquire_propagates_a_create_kdamond_failure(fake_damon):
    pool = damon.KdamondPool()
    fake_damon.fail_on = "_write_int"
    with pytest.raises(damon.DamonSessionError, match="cannot reserve DAMON pool capacity"):
        pool.acquire()
    assert pool.live_indices == frozenset()
    assert pool._owned == set()


def test_pool_release_of_an_index_never_acquired_is_a_harmless_noop(fake_damon):
    pool = damon.KdamondPool()
    pool.release(7)   # never acquired -- must not raise, must not touch sysfs
    assert fake_damon.calls == []


def test_pool_release_swallows_a_shrink_write_failure(fake_damon):
    pool = damon.KdamondPool()
    idx = pool.acquire()
    fake_damon.fail_on = "_write_int"
    pool.release(idx)   # must not raise even though the shrink write fails
    assert fake_damon.nr_kdamonds() == 1   # shrink attempted, failed, left as-is


def test_pool_release_skips_the_write_when_nr_kdamonds_is_already_at_baseline(fake_damon):
    pool = damon.KdamondPool()
    idx = pool.acquire()
    assert fake_damon.nr_kdamonds() == 1
    # Something outside the pool already brought nr_kdamonds back down to
    # the baseline before release runs -- release must see nothing to shrink
    # and skip the write, not just skip it when it fails.
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 0)
    fake_damon.calls.clear()
    pool.release(idx)
    assert [c for c in fake_damon.calls if c[0] == "_write_int"] == []
    assert fake_damon.nr_kdamonds() == 0


def test_pool_acquire_refuses_when_the_baseline_could_not_be_read(fake_damon, monkeypatch):
    pool = damon.KdamondPool()
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: None)
    with pytest.raises(damon.DamonSessionError):
        pool.acquire()
    assert pool.live_indices == frozenset()
    assert fake_damon.calls == []


def test_zero_capacity_pool_refuses_without_reserving_a_slot(fake_damon):
    pool = damon.KdamondPool(capacity=0)
    with pytest.raises(damon.DamonSessionError, match="capacity=0"):
        pool.acquire()
    assert fake_damon.nr_kdamonds() == 0
    assert not any(call[0] == "create_kdamond" for call in fake_damon.calls)


def test_negative_pool_capacity_is_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        damon.KdamondPool(capacity=-1)


@pytest.mark.parametrize("after_count", [None, 0])
def test_pool_quarantines_slot_when_post_create_count_is_unprovable(
    fake_damon, monkeypatch, after_count,
):
    real_read = damon._read_nr_kdamonds
    readings = iter((0, 0, 0, after_count))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    pool = damon.KdamondPool()

    with pytest.raises(damon.DamonSessionError, match="cannot prove DAMON pool capacity"):
        pool.acquire()

    assert pool.quarantined_indices == frozenset()
    assert pool.live_indices == frozenset()
    monkeypatch.setattr(damon, "_read_nr_kdamonds", real_read)
    assert not pool.close()
    assert fake_damon.nr_kdamonds() == 1


@pytest.mark.parametrize("failed_read", [None, "growth"])
def test_pool_does_not_claim_ambiguous_failed_resize_side_effect(
    fake_damon, monkeypatch, failed_read,
):
    real_read = damon._read_nr_kdamonds
    real_write = fake_damon._write_int.__func__
    reads = 0

    def read_counter():
        nonlocal reads
        reads += 1
        if reads == 4 and failed_read is None:
            return None
        return real_read()

    def write_then_fail(cls, path, value):
        if failed_read == "growth":
            real_write(cls, path, value)
        raise OSError(errno.EIO, "synthetic ambiguous resize failure")

    monkeypatch.setattr(damon, "_read_nr_kdamonds", read_counter)
    monkeypatch.setattr(fake_damon, "_write_int", classmethod(write_then_fail))
    pool = damon.KdamondPool()

    with pytest.raises(damon.DamonSessionError, match="cannot reserve"):
        pool.acquire()

    assert pool._owned == set()
    assert pool.live_indices == frozenset()
    assert pool._foreign_growth is True
    if failed_read == "growth":
        assert fake_damon.nr_kdamonds() == 1
        assert not pool.close()
    else:
        assert fake_damon.nr_kdamonds() == 0
        assert pool.close()


def test_session_entering_via_pool_skips_the_manual_prev_nr_kdamonds_path(fake_damon):
    pool = damon.KdamondPool()
    with damon.DamonSession([make_target()], pool=pool) as session:
        assert session._acquired_from_pool is True
        assert session._prev_nr_kdamonds is None   # the pool owns that bookkeeping now


def test_session_pool_acquire_failure_during_enter_still_tears_down_cleanly(fake_damon):
    pool = damon.KdamondPool()
    fake_damon.fail_on = "_write_int"
    with pytest.raises(damon.DamonSessionError, match="cannot reserve DAMON pool capacity"):
        with damon.DamonSession([make_target()], pool=pool):
            pytest.fail("should never reach the with-body")
    # Nothing was ever acquired from the pool, so nothing was released either.
    assert pool.live_indices == frozenset()
    assert not any(call[0] == "kdamond_off" for call in fake_damon.calls)


def test_failed_pool_acquire_cannot_release_a_live_constructor_index(fake_damon):
    """A failed second acquire must not free the first session's slot.

    ``DamonSession`` starts with constructor index zero.  If its
    ``_acquired_from_pool`` guard were initialized true, a failed acquire for
    a second pooled session would call ``pool.release(0)`` during exception
    teardown and remove the first session's live index.  That is the exact
    failure mode the flag's false initialization prevents.
    """
    pool = damon.KdamondPool(capacity=1)
    first = damon.DamonSession([make_target(pid=100)], pool=pool)
    first.__enter__()
    assert first.kdamond_idx == 0
    assert pool.live_indices == frozenset({0})

    fake_damon.fail_on = "create_kdamond"
    try:
        second = damon.DamonSession([make_target(pid=200)], pool=pool)
        with pytest.raises(damon.DamonSessionError, match="no verified-off"):
            second.__enter__()
        assert pool.live_indices == frozenset({0})
        assert fake_damon.state_of(0) == "on"
        assert fake_damon.nr_kdamonds() == 1
    finally:
        fake_damon.fail_on = None
        first.__exit__(None, None, None)


def test_a_failed_pool_acquisition_does_not_release_the_constructor_placeholder(fake_damon):
    """A failed ``acquire`` never claimed the session's constructor index.

    Keep an unrelated pool slot live so ``DamonSession``'s default placeholder
    is observable if teardown incorrectly releases it.
    """
    pool = damon.KdamondPool(capacity=1)
    assert pool.acquire() == 0
    fake_damon.fail_on = "create_kdamond"

    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        with damon.DamonSession([make_target()], kdamond_idx=0, pool=pool):
            pytest.fail("pool acquisition should fail before the body")

    assert pool.live_indices == frozenset({0})
    fake_damon.fail_on = None
    pool.release(0)


# ── DamonSession.thresholds / last_class_bytes (C3) ─────────────────────────

def test_thresholds_reports_the_classifier_cutoffs_in_seconds(fake_damon):
    session = damon.DamonSession(
        [make_target()],
        hot_rate_pct=60.0, warm_rate_pct=10.0, cold_age_sec=45.0, idle_age_sec=90.0,
    )
    assert session.thresholds == {
        "hot_rate_pct": 60.0, "warm_rate_pct": 10.0,
        "cold_age_s": 45.0, "idle_age_s": 90.0,
    }


def test_thresholds_available_before_entering(fake_damon):
    # Reported alongside the summary even for a session that never entered
    # (e.g. DAMON turned out unavailable) -- contract §3 damon.thresholds is
    # part of the always-present-when-damon-enabled block.
    session = damon.DamonSession([make_target()])
    assert session.thresholds["hot_rate_pct"] == 50.0


def test_collect_uses_the_configured_classifier_thresholds(fake_damon, monkeypatch):
    captured = {}

    class RecordingClassifier(FakeClassifier):
        def __init__(self, *, hot_access_rate_pct, warm_access_rate_pct,
                     cold_age_sec, idle_age_sec):
            captured["hot"] = hot_access_rate_pct
            captured["warm"] = warm_access_rate_pct
            captured["cold"] = cold_age_sec
            captured["idle"] = idle_age_sec

    monkeypatch.setattr(damon, "Classifier", RecordingClassifier)
    with damon.DamonSession([make_target()], hot_rate_pct=77.0) as session:
        session.collect()
    assert captured["hot"] == 77.0


def test_last_class_bytes_reshapes_the_summary_to_a_flat_bytes_dict(fake_damon):
    with damon.DamonSession([make_target()]) as session:
        session.collect()
    assert session.last_class_bytes == {"hot": 0, "warm": 0, "cold": 0, "idle": 0}


def test_last_class_bytes_before_any_collect_is_all_zero(fake_damon):
    session = damon.DamonSession([make_target()])
    assert session.last_class_bytes == {"hot": 0, "warm": 0, "cold": 0, "idle": 0}


# ── DamonSession.recommit_targets (C3: topology change mid-session) ────────

def test_recommit_targets_rewires_vaddr_targets_and_recommits(fake_damon):
    with damon.DamonSession([make_target(pid=1)]) as session:
        fake_damon.calls.clear()
        session.recommit_targets([10, 20, 30])
    assert session.targets == [
        damon.DamonTarget(kind="vaddr", pid=10, label="10"),
        damon.DamonTarget(kind="vaddr", pid=20, label="20"),
        damon.DamonTarget(kind="vaddr", pid=30, label="30"),
    ]
    pid_calls = [c for c in fake_damon.calls if c[0] == "set_pid_target"]
    assert [c[-1] for c in pid_calls] == [10, 20, 30]
    assert ("kdamond_commit", 0) in fake_damon.calls


def test_recommit_targets_with_an_empty_list_is_a_noop(fake_damon):
    with damon.DamonSession([make_target(pid=1)]) as session:
        before = list(session.targets)
        fake_damon.calls.clear()
        session.recommit_targets([])
        assert session.targets == before
        assert fake_damon.calls == []


def test_recommit_targets_outside_session_raises(fake_damon):
    session = damon.DamonSession([make_target()])
    with pytest.raises(damon.DamonSessionError):
        session.recommit_targets([1])


def test_recommit_targets_refuses_on_a_paddr_session(fake_damon):
    target = damon.DamonTarget(kind="paddr", pid=None, label="whole-system")
    with damon.DamonSession([target]) as session:
        with pytest.raises(damon.DamonSessionError):
            session.recommit_targets([1])


def test_recommit_targets_shrinks_the_live_target_array(fake_damon):
    with damon.DamonSession([make_target(pid=1)]) as session:
        session.recommit_targets([10, 20, 30])
        assert fake_damon.running_targets[0] == [10, 20, 30]
        fake_damon.calls.clear()
        session.recommit_targets([11])   # shrink to one pid
    assert session.targets == [damon.DamonTarget(kind="vaddr", pid=11, label="11")]
    assert fake_damon.target_inputs[(0, 0)] == [11]
    assert fake_damon.running_targets[0] == [11]
    create_calls = [c for c in fake_damon.calls if c[0] == "create_target"]
    assert create_calls == [("create_target", 0, 0, 0)]   # only index 0 touched


def test_recommit_targets_same_pid_set_is_a_noop_even_if_order_differs(fake_damon):
    with damon.DamonSession([make_target(pid=10), make_target(pid=20)]) as session:
        fake_damon.calls.clear()

        session.recommit_targets([20, 10])

        assert fake_damon.running_targets[0] == [10, 20]
        assert fake_damon.calls == []


def test_recommit_targets_normalizes_duplicate_initial_pids(fake_damon):
    session = damon.DamonSession([make_target(pid=10), make_target(pid=10)])
    session.__enter__()
    fake_damon.calls.clear()

    session.recommit_targets([10])

    assert session.targets == [damon.DamonTarget(kind="vaddr", pid=10, label="10")]
    assert fake_damon.target_inputs[(0, 0)] == [10]
    assert fake_damon.running_targets[0] == [10]
    assert [call for call in fake_damon.calls if call[0] == "set_nr_targets"] == [
        ("set_nr_targets", 0, 0, 1)
    ]
    session.__exit__(None, None, None)


def test_teardown_does_not_stop_after_ownership_changes_to_an_unrelated_slot(
    fake_damon, monkeypatch,
):
    session = damon.DamonSession([make_target()])
    session.__enter__()
    ownership_checks = 0

    def ownership_changes_after_state_read():
        nonlocal ownership_checks
        ownership_checks += 1
        return ownership_checks == 1

    monkeypatch.setattr(session, "_ownership_matches", ownership_changes_after_state_read)
    fake_damon.calls.clear()

    session.__exit__(None, None, None)

    assert not [call for call in fake_damon.calls if call[0] == "kdamond_off"]
    assert not session.cleanup_confirmed


# ── nested with caps.TempCaps ────────────────────────────────────────────────
#
# These two modules are the collector's only writers to live, shared host
# state; a real profiling run nests one inside the other (narrow a cap, then
# also sample DAMON, or vice versa), so an exception in the innermost body
# must unwind through *both* — this is exactly what raising SystemExit
# instead of re-killing (see both modules' _install_signal_teardown
# docstrings) was chosen to guarantee.

def test_nested_tempcaps_inside_damonsession_with_exception_releases_both(fake_damon, cgroup_root):
    changes = {"/dev.slice/dev-background.slice": {"memory.max": "1073741824"}}
    target = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    before = target.read_text().strip()

    with pytest.raises(RuntimeError, match="boom nested"):
        with damon.DamonSession([make_target()]):
            with caps.TempCaps(changes, root=str(cgroup_root)):
                assert fake_damon.state_of(0) == "on"
                raise RuntimeError("boom nested")

    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0
    assert target.read_text().strip() == before


def test_nested_damonsession_inside_tempcaps_with_exception_releases_both(fake_damon, cgroup_root):
    changes = {"/dev.slice/dev-background.slice": {"memory.max": "1073741824"}}
    target = cgroup_root / "dev.slice/dev-background.slice/memory.max"
    before = target.read_text().strip()

    with pytest.raises(RuntimeError, match="boom nested"):
        with caps.TempCaps(changes, root=str(cgroup_root)):
            with damon.DamonSession([make_target()]):
                assert fake_damon.state_of(0) == "on"
                raise RuntimeError("boom nested")

    assert not (fake_damon.root / "0").exists()
    assert fake_damon.nr_kdamonds() == 0
    assert target.read_text().strip() == before


def test_pool_confirm_off_fails_closed_when_state_is_unreadable(fake_damon, monkeypatch):
    def unreadable_state(cls, idx):
        raise OSError(errno.EIO, "synthetic unreadable state")

    monkeypatch.setattr(fake_damon, "kdamond_state", classmethod(unreadable_state))
    assert not damon.KdamondPool()._confirm_off(7)


def test_pool_refuses_capacity_reservation_when_counter_cannot_be_proven(fake_damon, monkeypatch):
    readings = iter((0, None))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    pool = damon.KdamondPool()
    pool._baseline = 0

    with pytest.raises(damon.DamonSessionError, match="registry changed before reservation"):
        pool.acquire()

    assert not any(call[0] == "create_kdamond" for call in fake_damon.calls)
    assert pool.live_indices == frozenset()


def test_pool_quarantines_free_slot_if_it_is_not_off_at_claim_time(fake_damon, monkeypatch):
    pool = damon.KdamondPool()
    reserve = pool._reserve_capacity

    def reserve_then_start(baseline):
        reserve(baseline)
        fake_damon.kdamond_on(0)

    monkeypatch.setattr(pool, "_reserve_capacity", reserve_then_start)
    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        pool.acquire()

    assert pool.live_indices == frozenset()
    assert pool.quarantined_indices == frozenset({0})
    assert fake_damon.state_of(0) == "on"


def test_pool_does_not_restore_baseline_while_a_slot_is_live(fake_damon):
    pool = damon.KdamondPool()
    idx = pool.acquire()

    assert not pool._restore_baseline()
    assert pool.live_indices == frozenset({idx})
    assert fake_damon.nr_kdamonds() == 1


def test_zero_capacity_pool_close_clears_captured_baseline(fake_damon):
    pool = damon.KdamondPool(capacity=0)
    with pytest.raises(damon.DamonSessionError, match="capacity=0"):
        pool.acquire()

    assert pool._baseline == 0
    assert pool.close()
    assert pool._baseline is None
    assert fake_damon.nr_kdamonds() == 0


def test_pool_retains_ownership_when_baseline_readback_disagrees(fake_damon, monkeypatch):
    pool = damon.KdamondPool()
    idx = pool.acquire()
    # The write succeeds in the fake sysfs, but the independent readback does
    # not confirm it. Keep the ownership ledger for a later safe retry.
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: 1)

    assert pool.release(idx)

    assert fake_damon.nr_kdamonds() == 0
    assert pool._baseline == 0
    assert pool._owned == {idx}
    assert pool._free == {idx}


def test_pool_close_fails_if_baseline_readback_disagrees(fake_damon, monkeypatch):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: 1)

    assert not pool.close()

    assert fake_damon.nr_kdamonds() == 0  # changed, but readback did not verify it
    assert pool._baseline == 0
    assert pool._owned == {idx}


def test_pool_close_fails_and_retains_ownership_when_shrink_raises(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    fake_damon.fail_on = "_write_int"

    assert not pool.close()

    assert fake_damon.nr_kdamonds() == 1
    assert pool._baseline == 0
    assert pool._owned == {idx}


def test_solo_teardown_does_not_shrink_when_state_readback_is_unavailable(
    fake_damon, monkeypatch,
):
    session = damon.DamonSession([make_target()])
    session.__enter__()

    def unreadable_state(cls, idx):
        raise OSError(errno.EIO, "synthetic state readback failure")

    monkeypatch.setattr(fake_damon, "kdamond_state", classmethod(unreadable_state))
    session._teardown()

    assert not session.cleanup_confirmed
    assert fake_damon.nr_kdamonds() == 1
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]


def test_identity_pin_closes_descriptor_when_fstat_fails(fake_damon, monkeypatch):
    fake_damon.create_kdamond(0)
    real_fstat = damon.os.fstat
    real_close = damon.os.close
    closed = []

    def fail_fstat(_fd):
        raise OSError(errno.EIO, "synthetic fstat failure")

    def record_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(damon.os, "fstat", fail_fstat)
    monkeypatch.setattr(damon.os, "close", record_close)

    with pytest.raises(OSError, match="synthetic fstat failure"):
        damon._KdamondIdentity.pin(0)

    assert len(closed) == 1
    with pytest.raises(OSError):
        real_fstat(closed[0])


def test_identity_match_fails_when_the_pinned_state_path_disappears(fake_damon):
    fake_damon.create_kdamond(0)
    identity = damon._KdamondIdentity.pin(0)
    (fake_damon.root / "0" / "state").unlink()
    try:
        assert not identity.matches(0)
    finally:
        identity.close()


def test_pool_clear_ignores_already_closed_identity_descriptor(fake_damon):
    class _ClosedIdentity:
        def close(self):
            raise OSError(errno.EBADF, "already closed")

    pool = damon.KdamondPool()
    pool._baseline = 0
    pool._owned = {0}
    pool._free = {0}
    pool._quarantined = {1}
    pool._foreign_growth = True
    pool._identities = {0: _ClosedIdentity()}

    pool._clear_if_restored()

    assert pool._identities == {}
    assert pool._baseline is None
    assert pool._owned == pool._free == pool._quarantined == set()
    assert pool._foreign_growth is False


@pytest.mark.parametrize("break_invariant", ["identity", "membership"])
def test_pool_owns_slot_requires_both_membership_and_matching_identity(
    fake_damon, break_invariant,
):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    if break_invariant == "identity":
        identity = pool._identities.pop(idx)
        identity.close()
    else:
        pool._owned.remove(idx)

    try:
        assert not pool.owns_slot(idx)
    finally:
        identity = pool._identities.pop(idx, None)
        if identity is not None:
            identity.close()


def test_stop_and_confirm_off_refuses_identity_change_after_state_read(fake_damon, monkeypatch):
    pool = damon.KdamondPool()
    idx = pool.acquire()
    identity = pool._identities[idx]
    matches = iter((True, False))
    monkeypatch.setattr(identity, "matches", lambda _idx: next(matches))
    fake_damon.calls.clear()

    assert not pool._stop_and_confirm_off(idx)

    assert not [call for call in fake_damon.calls
                if call[0] in {"kdamond_off", "_write_int"}]


def test_stop_and_confirm_off_refuses_a_missing_identity(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    identity = pool._identities.pop(idx)
    identity.close()
    fake_damon.calls.clear()

    assert not pool._stop_and_confirm_off(idx)

    assert not [call for call in fake_damon.calls if call[0] == "kdamond_off"]


def test_stop_and_confirm_off_treats_state_read_errors_as_unconfirmed(
    fake_damon, monkeypatch,
):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    fake_damon.kdamond_on(idx)

    def fail_state_read(cls, _idx):
        raise OSError(errno.EIO, "synthetic state read failure")

    monkeypatch.setattr(fake_damon, "kdamond_state", classmethod(fail_state_read))

    assert not pool._stop_and_confirm_off(idx)
    assert fake_damon.state_of(idx) == "on"


def test_pool_all_kdamonds_off_fails_closed_on_unreadable_state(fake_damon):
    pool = damon.KdamondPool()
    fake_damon.fail_on = "kdamond_state"

    assert not pool._all_kdamonds_off(1)


@pytest.mark.parametrize(
    ("baseline", "foreign_growth", "message"),
    [
        (1, False, "pre-existing nr_kdamonds=1"),
        (0, True, "ambiguous external registry change"),
    ],
)
def test_pool_reservation_refuses_unsafe_preconditions(
    fake_damon, baseline, foreign_growth, message,
):
    pool = damon.KdamondPool()
    pool._foreign_growth = foreign_growth

    with pytest.raises(damon.DamonSessionError, match=message):
        pool._reserve_capacity(baseline)

    assert fake_damon.calls == []
    assert fake_damon.nr_kdamonds() == 0


def test_pool_quarantines_capacity_if_slot_identity_cannot_be_pinned(
    fake_damon, monkeypatch,
):
    def fail_pin(_cls, _idx):
        raise OSError(errno.EIO, "synthetic identity pin failure")

    monkeypatch.setattr(damon._KdamondIdentity, "pin", classmethod(fail_pin))
    pool = damon.KdamondPool()

    with pytest.raises(damon.DamonSessionError, match="cannot pin identity"):
        pool.acquire()

    assert pool._owned == {0}
    assert pool.quarantined_indices == frozenset({0})
    assert pool.live_indices == frozenset()
    assert fake_damon.nr_kdamonds() == 1
    assert not pool.close()


@pytest.mark.parametrize("observed_count", [None, 0])
def test_pool_quarantines_free_slot_when_count_disappears_before_claim(
    fake_damon, monkeypatch, observed_count,
):
    pool = damon.KdamondPool()
    pool._baseline = 0
    pool._reserve_capacity(0)
    real_read = damon._read_nr_kdamonds
    reads = 0

    def read_with_late_disappearance():
        nonlocal reads
        reads += 1
        if reads == 2:
            return observed_count
        return real_read()

    monkeypatch.setattr(damon, "_read_nr_kdamonds", read_with_late_disappearance)

    with pytest.raises(damon.DamonSessionError, match="no verified-off"):
        pool.acquire()

    assert reads >= 2
    assert pool.live_indices == frozenset()
    assert pool.quarantined_indices == frozenset({0})


def test_pool_records_foreign_growth_between_reconciliation_and_claim(
    fake_damon, monkeypatch,
):
    pool = damon.KdamondPool()
    pool._baseline = 0
    pool._reserve_capacity(0)
    real_read = damon._read_nr_kdamonds
    reads = 0

    def grow_after_reconciliation():
        nonlocal reads
        reads += 1
        if reads == 2:
            return 2
        return real_read()

    monkeypatch.setattr(damon, "_read_nr_kdamonds", grow_after_reconciliation)

    assert pool.acquire() == 0

    assert pool._foreign_growth is True
    assert pool.live_indices == frozenset({0})
    assert fake_damon.nr_kdamonds() == 1


def test_restore_baseline_is_a_noop_without_a_captured_baseline(fake_damon):
    assert damon.KdamondPool()._restore_baseline()
    assert fake_damon.calls == []


def test_pool_clears_ownership_when_external_actor_already_restored_count(
    fake_damon, monkeypatch,
):
    pool = damon.KdamondPool()
    pool._baseline = 0
    pool._reserve_capacity(0)
    fake_damon.calls.clear()
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: 0)

    assert pool._restore_baseline()

    assert pool._baseline is None
    assert pool._owned == set()
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]


def test_pool_restore_refuses_same_count_replacement_of_a_freed_slot(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    pool._live.remove(idx)
    pool._free.add(idx)
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 1)
    marker = fake_damon.root / str(idx) / "foreign-context"
    marker.write_text("preserve replacement")
    fake_damon.calls.clear()

    assert not pool._restore_baseline()

    assert fake_damon.nr_kdamonds() == 1
    assert marker.read_text() == "preserve replacement"
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]


def test_pool_restore_returns_false_when_registry_write_raises(fake_damon):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()
    pool._live.remove(idx)
    pool._free.add(idx)
    fake_damon.fail_on = "_write_int"

    assert not pool._restore_baseline()

    assert fake_damon.nr_kdamonds() == 1
    assert pool._owned == {idx}


def test_pool_will_not_restore_after_ambiguous_foreign_growth(fake_damon):
    pool = damon.KdamondPool()
    pool._baseline = 0
    pool._reserve_capacity(0)
    pool._foreign_growth = True
    fake_damon.calls.clear()

    assert not pool._restore_baseline()

    assert fake_damon.nr_kdamonds() == 1
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]


def test_pool_will_not_restore_while_any_kdamond_is_running(fake_damon):
    pool = damon.KdamondPool()
    pool._baseline = 0
    pool._reserve_capacity(0)
    fake_damon.kdamond_on(0)
    fake_damon.calls.clear()

    assert not pool._restore_baseline()

    assert fake_damon.nr_kdamonds() == 1
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]


def test_pool_release_quarantines_live_slot_when_registry_lock_is_refused(
    fake_damon, monkeypatch,
):
    pool = damon.KdamondPool()
    idx = pool.acquire()

    class _RefusedLock:
        def __enter__(self):
            raise damon.DamonSessionError("synthetic shared-lock refusal")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(damon, "_DamonRegistryLock", lambda: _RefusedLock())

    assert not pool.release(idx)

    assert pool.live_indices == frozenset()
    assert pool.quarantined_indices == frozenset({idx})
    assert pool._free == set()
    assert fake_damon.nr_kdamonds() == 1


def test_pool_close_reports_failure_when_registry_lock_is_refused(
    fake_damon, monkeypatch,
):
    pool = damon.KdamondPool(capacity=1)
    idx = pool.acquire()

    class RefusedLock:
        def __enter__(self):
            raise damon.DamonSessionError("synthetic shared-lock refusal")

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(damon, "_DamonRegistryLock", lambda: RefusedLock())

    assert not pool.close()
    assert fake_damon.nr_kdamonds() == 1
    assert pool._owned == {idx}


def test_solo_session_refuses_constructor_index_that_is_not_fresh(fake_damon):
    session = damon.DamonSession([make_target()], kdamond_idx=1)

    with pytest.raises(damon.DamonSessionError, match="fresh slot is 0"):
        session.__enter__()

    assert fake_damon.nr_kdamonds() == 0
    assert not [call for call in fake_damon.calls
                if call[0] == "create_kdamond"]


def test_solo_session_refuses_registry_change_before_reserving(fake_damon, monkeypatch):
    readings = iter((0, 1))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    session = damon.DamonSession([make_target()])

    with pytest.raises(damon.DamonSessionError, match="changed before one-shot"):
        session.__enter__()

    assert fake_damon.nr_kdamonds() == 0
    assert not [call for call in fake_damon.calls
                if call[0] == "create_kdamond"]


def test_solo_session_refuses_unproven_create_without_configuring_slot(
    fake_damon, monkeypatch,
):
    readings = iter((0, 0, 0))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    session = damon.DamonSession([make_target()])

    with pytest.raises(damon.DamonSessionError, match="cannot prove the one-shot"):
        session.__enter__()

    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == "off"
    assert not [call for call in fake_damon.calls
                if call[0] in {"create_context", "kdamond_on"}]


def test_solo_session_refuses_slot_replaced_before_configuration(fake_damon, monkeypatch):
    real_pin = damon._KdamondIdentity.pin.__func__
    marker = fake_damon.root / "0" / "foreign-context"

    def replace_after_pin(cls, idx):
        identity = real_pin(cls, idx)
        fake_damon._replace_registry(1)
        (fake_damon.root / "0" / "foreign-context").write_text("foreign")
        return identity

    monkeypatch.setattr(
        damon._KdamondIdentity, "pin", classmethod(replace_after_pin)
    )
    session = damon.DamonSession([make_target()])

    with pytest.raises(damon.DamonSessionError, match="changed before configuration"):
        session.__enter__()

    assert fake_damon.nr_kdamonds() == 1
    assert marker.read_text() == "foreign"
    assert not [call for call in fake_damon.calls
                if call[0] in {"create_context", "kdamond_on"}]


def test_solo_session_refuses_replacement_during_configuration(fake_damon, monkeypatch):
    session = damon.DamonSession([make_target()])
    checks = 0

    def replace_before_final_start_check():
        nonlocal checks
        checks += 1
        if checks == 2:
            fake_damon._replace_registry(1)
            (fake_damon.root / "0" / "foreign-context").write_text("foreign")
            return False
        return checks == 1

    monkeypatch.setattr(session, "_ownership_matches", replace_before_final_start_check)

    with pytest.raises(damon.DamonSessionError, match="changed during configuration"):
        session.__enter__()

    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == "off"
    assert (fake_damon.root / "0" / "foreign-context").read_text() == "foreign"
    assert not [call for call in fake_damon.calls if call[0] == "kdamond_on"]


def test_solo_teardown_does_not_shrink_after_ownership_check_fails(fake_damon, monkeypatch):
    session = damon.DamonSession([make_target()])
    session.__enter__()
    checks = 0

    def ownership_lost_at_shrink():
        nonlocal checks
        checks += 1
        return checks <= 4

    monkeypatch.setattr(session, "_ownership_matches", ownership_lost_at_shrink)
    fake_damon.calls.clear()

    session.__exit__(None, None, None)

    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 1
    assert not [call for call in fake_damon.calls
                if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")]
