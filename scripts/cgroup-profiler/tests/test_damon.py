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
    with pytest.raises(RuntimeError) as excinfo:
        with damon.DamonSession([make_target()]):
            pytest.fail("should never reach the with-body")
    assert fake_damon.state_of(0) == "off"
    assert fake_damon.nr_kdamonds() == 0
    # RG-55 live acceptance (2026-09-12): a foreign exception type raised by
    # SysfsInterface (a plain RuntimeError here, standing in for the real
    # OSError `kdamond_commit` raised live) must come out of __enter__ as a
    # DamonSessionError — DamonSessionError subclasses RuntimeError, so the
    # `pytest.raises(RuntimeError)` above alone would not catch a
    # regression back to a bare `raise`; assert the wrapped type and message
    # explicitly.
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

    monkeypatch.setattr(fake_damon, "kdamond_commit", _boom)
    with pytest.raises(damon.DamonSessionError) as excinfo:
        with damon.DamonSession([make_target()]):
            pytest.fail("should never reach the with-body")
    assert excinfo.value is sentinel
    assert str(excinfo.value) == "sentinel-already-a-damon-session-error"
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


def test_pool_allocates_only_slots_beyond_the_pool_baseline(fake_damon):
    # Index 0 belongs to another owner before the pool captures its baseline.
    # The pool must allocate index 1, and a complete session lifecycle must
    # leave the foreign kdamond both present and on.
    fake_damon.create_kdamond(0)
    fake_damon.kdamond_on(0)
    assert fake_damon.nr_kdamonds() == 1
    fake_damon.calls.clear()
    pool = damon.KdamondPool()
    with damon.DamonSession([make_target()], pool=pool) as session:
        assert session.kdamond_idx == 1
        assert fake_damon.state_of(0) == "on"
    assert fake_damon.nr_kdamonds() == 1
    assert fake_damon.state_of(0) == "on"
    assert not any(call[0] == "kdamond_off" and call[1] == 0 for call in fake_damon.calls)
    assert not any(call[0] == "kdamond_on" and call[1] == 0 for call in fake_damon.calls)


def test_pool_teardown_turns_its_owned_kdamond_off(fake_damon):
    # The pool owns the newly-created index, while index 0 remains a foreign
    # kdamond.  Releasing the pool slot must stop that owned kdamond before
    # shrinking the count; otherwise the directory disappears from the
    # registry while its monitoring state is still on.
    fake_damon.create_kdamond(0)
    fake_damon.kdamond_on(0)
    pool = damon.KdamondPool()
    with damon.DamonSession([make_target()], pool=pool) as session:
        owned_idx = session.kdamond_idx
        assert owned_idx == 1
        assert fake_damon.state_of(owned_idx) == "on"
    assert fake_damon.state_of(owned_idx) == "off"
    assert fake_damon.state_of(0) == "on"
    assert fake_damon.nr_kdamonds() == 1


def test_bare_session_refuses_a_foreign_existing_slot(fake_damon):
    fake_damon.create_kdamond(0)
    fake_damon.kdamond_on(0)
    fake_damon.calls.clear()
    with pytest.raises(damon.DamonSessionError):
        damon.DamonSession([make_target()], kdamond_idx=0).__enter__()
    assert fake_damon.state_of(0) == "on"
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
    session._teardown()
    assert fake_damon.calls == []


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


def test_teardown_survives_kdamond_off_raising_and_still_shrinks_nr_kdamonds(fake_damon):
    session = damon.DamonSession([make_target()])
    session.__enter__()
    assert fake_damon.nr_kdamonds() == 1
    fake_damon.fail_on = "kdamond_off"
    session.__exit__(None, None, None)   # must not raise
    assert fake_damon.nr_kdamonds() == 0   # still released despite kdamond_off failing


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


# ── KdamondPool (C3): the required two-session, then three-session, sequence ──

def test_pool_two_sessions_get_indices_0_and_1(fake_damon):
    pool = damon.KdamondPool()
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    assert (s0.kdamond_idx, s1.kdamond_idx) == (0, 1)
    assert fake_damon.nr_kdamonds() == 2
    s1.__exit__(None, None, None)
    s0.__exit__(None, None, None)


def test_pool_stopping_the_low_index_while_the_high_one_lives_writes_no_nr_kdamonds(fake_damon):
    pool = damon.KdamondPool()
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    fake_damon.calls.clear()
    s0.__exit__(None, None, None)   # index 0 freed; index 1 (s1) still live
    nr_writes = [c for c in fake_damon.calls if c[0] == "_write_int" and c[1].endswith("nr_kdamonds")]
    assert nr_writes == []          # NO write to nr_kdamonds at all
    assert fake_damon.nr_kdamonds() == 2
    assert fake_damon.state_of(1) == "on"       # s1's kdamond untouched
    s1.__exit__(None, None, None)


def test_pool_a_third_session_reuses_the_freed_index_0(fake_damon):
    pool = damon.KdamondPool()
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    s0.__exit__(None, None, None)
    s2 = damon.DamonSession([make_target(pid=300)], pool=pool)
    s2.__enter__()
    assert s2.kdamond_idx == 0
    assert fake_damon.nr_kdamonds() == 2
    s2.__exit__(None, None, None)
    s1.__exit__(None, None, None)


def test_pool_stopping_every_session_shrinks_nr_kdamonds_back_to_the_pre_daemon_value(fake_damon):
    # Pre-daemon value here is 0 (fake_damon's fresh fixture state) — the
    # exact scenario the fixture starts every test at, matching a freshly
    # started daemon that owns no kdamonds yet.
    pool = damon.KdamondPool()
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    assert fake_damon.nr_kdamonds() == 2
    s0.__exit__(None, None, None)
    assert fake_damon.nr_kdamonds() == 2   # still 2 -- s1 alive
    s1.__exit__(None, None, None)
    assert fake_damon.nr_kdamonds() == 0   # last one out shrinks to baseline


def test_pool_reuse_at_expected_end_does_not_mark_foreign_growth(fake_damon):
    # A free slot is ordinary pool reuse when the counter is exactly the
    # highest index the pool owns plus one.  The Gt->GtE mutant at
    # KdamondPool.acquire:275 marks this benign boundary as foreign growth,
    # which then suppresses the required final shrink back to baseline.
    pool = damon.KdamondPool()
    first = pool.acquire()
    second = pool.acquire()
    pool.release(first)
    assert pool.acquire() == first
    pool.release(first)
    pool.release(second)
    assert fake_damon.nr_kdamonds() == 0


def test_pool_preserves_foreign_growth_seen_during_free_slot_reuse(fake_damon):
    # The final counter is deliberately brought back to the pool's expected
    # end after an outside owner grew it.  That makes the foreign-growth flag,
    # rather than the later counter inequality, the only ownership evidence
    # preventing a destructive baseline write.
    pool = damon.KdamondPool()
    first = pool.acquire()
    second = pool.acquire()
    pool.release(first)
    fake_damon.create_kdamond(2)  # outside owner grows nr_kdamonds to 3
    reused = pool.acquire()
    assert reused == first
    (fake_damon.root / "nr_kdamonds").write_text("2")  # outside owner exits
    fake_damon.calls.clear()
    pool.release(reused)
    pool.release(second)
    nr_writes = [
        call for call in fake_damon.calls
        if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")
    ]
    assert nr_writes == []
    assert fake_damon.nr_kdamonds() == 2


def test_pool_preserves_foreign_indices_seen_before_fresh_create(fake_damon):
    # With no free slot, a counter ahead of the next pool index means the
    # intervening index belongs to another owner.  The pool must preserve it
    # when its own last slot is released.
    pool = damon.KdamondPool()
    own = pool.acquire()
    fake_damon.create_kdamond(1)  # foreign index 1; nr_kdamonds becomes 2
    fresh = pool.acquire()        # skips index 1 and creates index 2
    assert fresh == 2
    pool.release(own)
    fake_damon.calls.clear()
    pool.release(fresh)
    nr_writes = [
        call for call in fake_damon.calls
        if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")
    ]
    assert nr_writes == []
    assert fake_damon.nr_kdamonds() == 3


def test_solo_teardown_does_not_write_when_counter_is_below_previous(fake_damon):
    # A non-pool session must not restore its old count after another owner
    # has already shrunk the shared counter below that session's snapshot.
    # The And->Or mutant at DamonSession._teardown:596 calls the write anyway;
    # spying on the fake's write log keeps the equal-content case observable.
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 1)
    session = damon.DamonSession([make_target()], kdamond_idx=1)
    session.__enter__()
    (fake_damon.root / "nr_kdamonds").write_text("0")
    fake_damon.calls.clear()
    session.__exit__(None, None, None)
    nr_writes = [
        call for call in fake_damon.calls
        if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")
    ]
    assert nr_writes == []
    assert fake_damon.nr_kdamonds() == 0


def test_solo_teardown_does_not_write_when_counter_equals_previous(fake_damon):
    # A concurrent owner may remove this session's slot and leave the shared
    # counter exactly at the session's pre-entry value.  Equality is not proof
    # that this session still owns a slot; the teardown must not perform a
    # same-value write which could become destructive if the counter changes
    # between the read and the write.
    fake_damon.create_kdamond(0)  # foreign slot; the session starts at index 1
    session = damon.DamonSession([make_target()], kdamond_idx=1)
    session.__enter__()
    (fake_damon.root / "nr_kdamonds").write_text("1")
    fake_damon.calls.clear()

    session.__exit__(None, None, None)

    nr_writes = [
        call for call in fake_damon.calls
        if call[0] == "_write_int" and call[1].endswith("nr_kdamonds")
    ]
    assert nr_writes == []
    assert fake_damon.nr_kdamonds() == 1


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


def test_pool_never_shrinks_away_foreign_growth(fake_damon):
    pool = damon.KdamondPool()
    with damon.DamonSession([make_target()], pool=pool) as session:
        assert session.kdamond_idx == 0
        fake_damon.create_kdamond(1)
        fake_damon.kdamond_on(1)
    assert fake_damon.nr_kdamonds() == 2
    assert fake_damon.state_of(1) == "on"
    assert not [c for c in fake_damon.calls if c[0] == "_write_int" and c[1].endswith("nr_kdamonds")]


def test_pool_refuses_a_freed_owned_slot_that_disappeared(fake_damon):
    pool = damon.KdamondPool()
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    s0.__exit__(None, None, None)
    fake_damon._write_int(str(fake_damon.root / "nr_kdamonds"), 0)
    with pytest.raises(damon.DamonSessionError):
        pool.acquire()
    s1.__exit__(None, None, None)


def test_pool_marks_external_growth_while_reusing_a_free_slot(fake_damon):
    pool = damon.KdamondPool()
    s0 = damon.DamonSession([make_target(pid=100)], pool=pool)
    s1 = damon.DamonSession([make_target(pid=200)], pool=pool)
    s0.__enter__()
    s1.__enter__()
    s0.__exit__(None, None, None)
    fake_damon.create_kdamond(2)
    fake_damon.kdamond_on(2)
    s2 = damon.DamonSession([make_target(pid=300)], pool=pool)
    s2.__enter__()
    assert s2.kdamond_idx == 0
    s2.__exit__(None, None, None)
    s1.__exit__(None, None, None)
    assert fake_damon.nr_kdamonds() == 3
    assert fake_damon.state_of(2) == "on"


def test_pool_refuses_a_counter_that_shrank_before_a_fresh_acquire(fake_damon, monkeypatch):
    readings = iter((0, -1))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    with pytest.raises(damon.DamonSessionError):
        damon.KdamondPool().acquire()


def test_pool_skips_foreign_indices_when_the_counter_grows_before_create(fake_damon, monkeypatch):
    readings = iter((0, 2))
    monkeypatch.setattr(damon, "_read_nr_kdamonds", lambda: next(readings))
    pool = damon.KdamondPool()
    assert pool.acquire() == 2
    pool.release(2)
    assert fake_damon.nr_kdamonds() == 3


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
    fake_damon.fail_on = "create_kdamond"
    with pytest.raises(RuntimeError):
        pool.acquire()
    assert pool.live_indices == frozenset()


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


def test_session_entering_via_pool_skips_the_manual_prev_nr_kdamonds_path(fake_damon):
    pool = damon.KdamondPool()
    with damon.DamonSession([make_target()], pool=pool) as session:
        assert session._acquired_from_pool is True
        assert session._prev_nr_kdamonds is None   # the pool owns that bookkeeping now


def test_session_pool_acquire_failure_during_enter_still_tears_down_cleanly(fake_damon):
    pool = damon.KdamondPool()
    fake_damon.fail_on = "create_kdamond"
    with pytest.raises(RuntimeError):
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
    pool = damon.KdamondPool()
    first = damon.DamonSession([make_target(pid=100)], pool=pool)
    first.__enter__()
    assert first.kdamond_idx == 0
    assert pool.live_indices == frozenset({0})

    fake_damon.fail_on = "create_kdamond"
    try:
        second = damon.DamonSession([make_target(pid=200)], pool=pool)
        with pytest.raises(RuntimeError):
            second.__enter__()
        assert pool.live_indices == frozenset({0})
        assert fake_damon.state_of(0) == "on"
        assert fake_damon.nr_kdamonds() == 1
    finally:
        fake_damon.fail_on = None
        first.__exit__(None, None, None)


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


def test_recommit_targets_fewer_pids_leaves_higher_target_dirs_but_updates_kept_ones(fake_damon):
    with damon.DamonSession([make_target(pid=1)]) as session:
        session.recommit_targets([10, 20, 30])
        fake_damon.calls.clear()
        session.recommit_targets([11])   # shrink to one pid
    assert session.targets == [damon.DamonTarget(kind="vaddr", pid=11, label="11")]
    create_calls = [c for c in fake_damon.calls if c[0] == "create_target"]
    assert create_calls == [("create_target", 0, 0, 0)]   # only index 0 touched


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
