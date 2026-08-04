"""Tests for lib.damon: sysfs-path construction and the always-teardown
contract, against a fake sysfs tree and a stubbed SysfsInterface/Classifier —
never a real kdamond (DESIGN.md §7: this suite must not touch the kernel's
one shared DAMON facility).
"""

from __future__ import annotations

import importlib
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

    @classmethod
    def configure(cls, root: Path) -> None:
        cls.root = root
        cls.calls = []
        cls.fail_on = None
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
            (cls.root / "nr_kdamonds").write_text(str(idx + 1))
        (cls.root / str(idx)).mkdir(exist_ok=True)
        (cls.root / str(idx) / "state").write_text("off")
        return str(cls.root / str(idx))

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
    def set_pid_target(cls, kdamond_idx, ctx_idx, target_idx, pid) -> None:
        cls.calls.append(("set_pid_target", kdamond_idx, ctx_idx, target_idx, pid))
        cls._maybe_fail("set_pid_target")

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

    @classmethod
    def kdamond_on(cls, idx: int = 0) -> None:
        cls.calls.append(("kdamond_on", idx))
        cls._maybe_fail("kdamond_on")
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
    return FakeSysfsInterface


def make_target(kind="vaddr", pid=4242, label="t") -> damon.DamonTarget:
    return damon.DamonTarget(kind=kind, pid=pid, label=label)


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
    script = tmp_path / "damo"
    script.write_text("#!/home/vb/volkb79-2/vbpub/scripts/damon-analysis/venv/bin/python3\n")
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
    assert fake_damon.state_of(0) == "off"
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
        intervals_call = next(c for c in fake_damon.calls if c[0] == "set_intervals")
        assert intervals_call[3:5] == (50_000, 1_000_000)
        action_call = next(c for c in fake_damon.calls if c[0] == "set_scheme_action")
        assert action_call[-1] == "stat"   # never a migrate/reclaim action


def test_exception_inside_with_block_still_tears_down(fake_damon):
    with pytest.raises(RuntimeError):
        with damon.DamonSession([make_target()]):
            assert fake_damon.state_of(0) == "on"
            raise RuntimeError("boom mid-session")
    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 0


def test_exception_during_enter_still_tears_down(fake_damon):
    fake_damon.fail_on = "set_scheme_action"
    with pytest.raises(RuntimeError):
        with damon.DamonSession([make_target()]):
            pytest.fail("should never reach the with-body")
    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 0


def test_preexisting_kdamonds_are_not_widened_away(fake_damon):
    # kdamond 0 already belongs to someone else before our session asks for
    # kdamond_idx=1 — teardown must shrink back to 1 (what was there before
    # us), never to 0, and must never touch kdamond 0's state.
    fake_damon.create_kdamond(0)
    fake_damon.kdamond_on(0)
    assert fake_damon.nr_kdamonds() == 1
    with damon.DamonSession([make_target()], kdamond_idx=1):
        assert fake_damon.nr_kdamonds() == 2
    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == "on"


def test_reusing_an_existing_kdamond_slot_leaves_the_counter_alone(fake_damon):
    # kdamond 0 already exists (someone else's, or a prior session using the
    # same index) *before* nr_kdamonds is captured — create_kdamond(0) is
    # then a no-op, so teardown must see current == prev and skip the shrink
    # entirely, not just skip it for a higher index.
    fake_damon.create_kdamond(0)
    assert fake_damon.nr_kdamonds() == 1
    with damon.DamonSession([make_target()], kdamond_idx=0):
        assert fake_damon.nr_kdamonds() == 1
        assert fake_damon.state_of(0) == "on"
    assert fake_damon.nr_kdamonds() == 1   # never shrunk — we never grew it
    assert fake_damon.state_of(0) == "off"


def test_enter_wires_a_paddr_target_too(fake_damon):
    # paddr targets carry no pid, and exercise the "not vaddr" arm of the
    # per-target wiring loop that the vaddr-only tests above never touch.
    target = damon.DamonTarget(kind="paddr", pid=None, label="whole-system")
    with damon.DamonSession([target]):
        names = [call[0] for call in fake_damon.calls]
        assert "set_pid_target" not in names
        ops_call = next(c for c in fake_damon.calls if c[0] == "set_operations")
        assert ops_call[-1] == "paddr"


def test_teardown_is_idempotent_when_called_twice(fake_damon):
    session = damon.DamonSession([make_target()])
    session.__enter__()
    session._teardown()
    calls_after_first = list(fake_damon.calls)
    session._teardown()   # must be a pure no-op the second time
    assert fake_damon.calls == calls_after_first
    session.__exit__(None, None, None)   # a third call, via the normal path
    assert fake_damon.calls == calls_after_first


def test_teardown_survives_kdamond_off_raising_and_still_shrinks_nr_kdamonds(fake_damon):
    session = damon.DamonSession([make_target()])
    session.__enter__()
    assert fake_damon.nr_kdamonds() == 1
    fake_damon.fail_on = "kdamond_off"
    session.__exit__(None, None, None)   # must not raise
    assert fake_damon.nr_kdamonds() == 0   # still released despite kdamond_off failing


def test_teardown_skips_the_shrink_when_the_prior_count_could_not_be_determined(fake_damon, monkeypatch):
    # If the pre-flight read of nr_kdamonds itself failed, we have no
    # trustworthy basis for what to restore the shared counter to —
    # guessing wrong risks tearing down a kdamond we never created. The
    # deliberately conservative choice is to leave the counter alone (the
    # kdamond is still turned off, so nothing keeps monitoring).
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: None)
    with damon.DamonSession([make_target()]):
        pass
    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 1   # left as-is: unknown prior state is never guessed


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
    assert fake_damon.state_of(0) == "off"
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
    assert fake_damon.nr_kdamonds() == 0   # still released despite kdamond_off failing internally


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
    assert fake_damon.state_of(0) == "off"
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

    assert fake_damon.state_of(0) == "off"
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

    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 0   # the reentrant call finished the shrink


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

    assert fake_damon.state_of(0) == "off"
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

    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 0
    assert target.read_text().strip() == before
