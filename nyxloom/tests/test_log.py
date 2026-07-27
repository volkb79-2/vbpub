"""Tests for nyxloom.log (P01 -- the structlog-based logging core).

Oracles (docs/plan-logging.md §6, P01):
  1. the processor chain renders a record carrying bound context + a UTC
     `ts` matching ^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}$ (no offset/fraction)
  2. bind() nests and clears on exit, INCLUDING on exception
  3. level gating: a DEBUG call is dropped at effective INFO, emitted at
     effective DEBUG
  4. set_level() changes the effective level live (a call dropped before
     set_level() is emitted after it, on the SAME already-obtained logger)
  5. file (JSON) and console (human) handlers hold independent levels
  6. TRACE works and sits below DEBUG
  7. configure() is idempotent and does NOT mutate the stdlib root -- a
     sibling logging.getLogger("other") is unaffected

Oracle 8 (the converted http_bind notice) is covered by the two updated
tests in test_daemon.py, not here.

Oracle 9 (P27-followon): configure() swaps root.handlers via a single
atomic list rebind, never remove-then-add, so a concurrent warning() call
can never observe an empty handler list mid-configure().

structlog's global config (and the "nyxloom" stdlib channel) are PROCESS-
WIDE state, so every test below reconfigures explicitly rather than relying
on ambient state left by another test -- and the local autouse fixture
resets both before and after each test in this file (conftest.py is FROZEN
core; implementation agents add local fixtures in their own test files,
never there).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

import pytest
import structlog.contextvars

from nyxloom import log


@pytest.fixture(autouse=True)
def _reset_structlog_state():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


def _read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


# --------------------------------------------------------------------------
# Oracle 1 -- record content + UTC ts format

def test_record_carries_bound_context_and_utc_ts(tmp_path):
    log.configure(level=log.INFO, log_dir=tmp_path, console=False)
    lg = log.get_logger("widget")

    with log.bind(project="demo", task="T1"):
        lg.info("did a thing", extra=42)

    records = _read_records(tmp_path / "nyxloom.jsonl")
    assert len(records) == 1
    rec = records[0]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", rec["ts"])
    assert rec["level"] == "info"
    assert rec["logger"] == "widget"
    assert rec["msg"] == "did a thing"
    assert rec["project"] == "demo"
    assert rec["task"] == "T1"
    assert rec["extra"] == 42


# --------------------------------------------------------------------------
# Oracle 2 -- bind() nests and clears, including on exception

def test_bind_nests_and_clears_including_on_exception():
    assert structlog.contextvars.get_contextvars() == {}

    with log.bind(project="p1"):
        assert structlog.contextvars.get_contextvars() == {"project": "p1"}
        with log.bind(task="t1"):
            assert structlog.contextvars.get_contextvars() == {"project": "p1", "task": "t1"}
        assert structlog.contextvars.get_contextvars() == {"project": "p1"}
    assert structlog.contextvars.get_contextvars() == {}

    with pytest.raises(RuntimeError):
        with log.bind(project="p2"):
            assert structlog.contextvars.get_contextvars() == {"project": "p2"}
            raise RuntimeError("boom")
    assert structlog.contextvars.get_contextvars() == {}


# --------------------------------------------------------------------------
# Oracle 3 -- level gating (dropped at INFO, emitted at DEBUG)

def test_level_gating_debug_dropped_at_info_emitted_at_debug(tmp_path):
    log_path = tmp_path / "nyxloom.jsonl"

    log.configure(level=log.INFO, log_dir=tmp_path, console=False)
    lg = log.get_logger("gated")
    lg.debug("should be dropped")
    assert _read_records(log_path) == []

    log.configure(level=log.DEBUG, log_dir=tmp_path, console=False)
    lg.debug("should be emitted")
    records = _read_records(log_path)
    assert len(records) == 1
    assert records[0]["msg"] == "should be emitted"
    assert records[0]["level"] == "debug"


# --------------------------------------------------------------------------
# Oracle 4 -- set_level() changes the effective level live, on an already-
# obtained logger (the mechanism modules that imported `log` at start-of-
# process rely on to obey a runtime flip with no restart).

def test_set_level_changes_effective_level_live(tmp_path):
    log_path = tmp_path / "nyxloom.jsonl"
    log.configure(level=log.INFO, log_dir=tmp_path, console=False)
    lg = log.get_logger("live")  # obtained ONCE, before the level flip

    lg.debug("dropped before set_level")
    assert _read_records(log_path) == []

    log.set_level(log.DEBUG)
    lg.debug("emitted after set_level")  # SAME `lg` object as above

    records = _read_records(log_path)
    assert len(records) == 1
    assert records[0]["msg"] == "emitted after set_level"


# --------------------------------------------------------------------------
# Oracle 5 -- file (JSON) and console (human) handlers hold independent
# levels: file gets everything the effective level allows; console is
# pinned at INFO regardless.

def test_file_and_console_handlers_hold_independent_levels(tmp_path, capsys):
    log.configure(level=log.DEBUG, log_dir=tmp_path, console=True)
    lg = log.get_logger("split")

    lg.debug("debug line")
    lg.info("info line")

    file_msgs = {r["msg"] for r in _read_records(tmp_path / "nyxloom.jsonl")}
    assert {"debug line", "info line"} <= file_msgs

    err = capsys.readouterr().err
    assert "info line" in err
    assert "debug line" not in err  # console caps at INFO even though effective level is DEBUG


# --------------------------------------------------------------------------
# Oracle 6 -- TRACE sits below DEBUG

def test_trace_sits_below_debug(tmp_path):
    log_path = tmp_path / "nyxloom.jsonl"

    log.configure(level=log.TRACE, log_dir=tmp_path, console=False)
    lg = log.get_logger("tracer")
    lg.trace("trace line")
    lg.debug("debug line")
    msgs = {r["msg"] for r in _read_records(log_path)}
    assert {"trace line", "debug line"} <= msgs

    log.configure(level=log.DEBUG, log_dir=tmp_path, console=False)
    lg.trace("should be dropped")
    lg.debug("should remain")
    msgs_after = {r["msg"] for r in _read_records(log_path)}
    assert "should be dropped" not in msgs_after
    assert "should remain" in msgs_after


def test_trace_level_accepts_string_name(tmp_path):
    log.configure(level="trace", log_dir=tmp_path, console=False)
    lg = log.get_logger("tracer2")
    lg.trace("via string level")
    msgs = {r["msg"] for r in _read_records(tmp_path / "nyxloom.jsonl")}
    assert "via string level" in msgs


def test_unknown_level_name_raises():
    with pytest.raises(ValueError):
        log.configure(level="not-a-level", console=False)


# --------------------------------------------------------------------------
# Oracle 7 -- configure() is idempotent and never mutates the stdlib root
# (or any sibling logger).

def test_configure_idempotent_and_does_not_touch_stdlib_root(tmp_path):
    real_root = logging.getLogger()
    root_handlers_before = list(real_root.handlers)
    root_level_before = real_root.level

    other = logging.getLogger("other")
    other_level_before = other.level
    other_handlers_before = list(other.handlers)

    log.configure(level=log.INFO, log_dir=tmp_path, console=False)
    log.configure(level=log.INFO, log_dir=tmp_path, console=False)  # repeat call

    assert list(real_root.handlers) == root_handlers_before
    assert real_root.level == root_level_before
    assert other.level == other_level_before
    assert list(other.handlers) == other_handlers_before

    nyxloom_logger = logging.getLogger("nyxloom")
    assert len(nyxloom_logger.handlers) == 1  # no duplicate handler from the repeat call

    lg = log.get_logger("idempotent")
    lg.info("one line only")
    records = _read_records(tmp_path / "nyxloom.jsonl")
    assert len(records) == 1  # not duplicated by the earlier repeat configure()


def test_effective_level_introspection(tmp_path):
    log.configure(level=log.WARNING, log_dir=tmp_path, console=False)
    lg = log.get_logger("introspect")
    assert lg.get_effective_level() == log.WARNING
    assert lg.is_enabled_for(log.ERROR) is True
    assert lg.is_enabled_for(log.INFO) is False


# --------------------------------------------------------------------------
# P27-followon: configure() must swap root.handlers atomically. The old
# remove-then-add sequence left a real window where root.handlers was the
# empty list; a concurrent warning() call landing in that window silently
# vanished (no exception, no output). This proves the window is closed by
# hammering configure() from two threads against distinct tmp dirs while a
# third thread concurrently logs and watches root.handlers for emptiness.

def test_configure_never_leaves_handlers_observably_empty_under_concurrency(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    nyxloom_logger = logging.getLogger("nyxloom")

    # Establish a baseline configuration before the threads start (mirrors
    # real usage: configure() is always called before loggers are used) so
    # the prober thread never observes the pre-first-configure() state,
    # which is a separate, expected, non-racy empty window.
    log.configure(level=log.INFO, log_dir=dir_a, console=False)

    stop = threading.Event()
    empty_observations: list[int] = []

    def configure_loop(log_dir: Path) -> None:
        for _ in range(200):
            log.configure(level=log.INFO, log_dir=log_dir, console=False)

    def warn_loop() -> None:
        lg = log.get_logger("prober")
        i = 0
        while not stop.is_set():
            if len(nyxloom_logger.handlers) == 0:
                empty_observations.append(i)
            lg.warning("probe", seq=i)
            i += 1

    t_warn = threading.Thread(target=warn_loop)
    t_a = threading.Thread(target=configure_loop, args=(dir_a,))
    t_b = threading.Thread(target=configure_loop, args=(dir_b,))

    t_warn.start()
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()
    stop.set()
    t_warn.join(timeout=5)

    assert empty_observations == [], (
        f"root.handlers was observed empty {len(empty_observations)} time(s) "
        "during concurrent configure() calls -- the handler-swap window is "
        "not atomic"
    )

    # Restore default logging so later tests aren't left pointed at tmp_path.
    log.configure(level=log.INFO, console=False)


# --------------------------------------------------------------------------
# paths.py additions (§4.2)

def test_path_helpers(tmp_path, monkeypatch):
    from nyxloom import paths

    monkeypatch.setenv("NYXLOOM_STATE", str(tmp_path))
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.nyxloom_log_path() == tmp_path / "logs" / "nyxloom.jsonl"
    assert paths.daemon_log_level_path() == tmp_path / "daemon" / "log-level"

    paths.ensure_layout()
    assert paths.logs_dir().is_dir()


# --------------------------------------------------------------------------
# Edge cases needed for full diff-coverage of log.py's branches

def test_get_logger_with_no_name_maps_to_the_nyxloom_logger_itself(tmp_path):
    """`get_logger()`/`get_logger("nyxloom")` are the same edge case: the
    factory backs them directly onto `logging.getLogger("nyxloom")` (no
    `.child` suffix), and `_short_logger_name` renders that as `"nyxloom"`."""
    log.configure(level=log.INFO, log_dir=tmp_path, console=False)
    lg = log.get_logger()
    lg.info("root-ish line")
    records = _read_records(tmp_path / "nyxloom.jsonl")
    assert any(r["msg"] == "root-ish line" and r["logger"] == "nyxloom" for r in records)


def test_short_logger_name_processor_fallback_for_unrelated_name():
    """Direct unit test of the processor's defensive else-branch: a `logger`
    whose `.name` is neither "nyxloom" nor "nyxloom.<x>" (can't happen via
    our own factory, but `_short_logger_name` also runs as `foreign_pre_chain`
    for any plain stdlib call, so it must degrade safely for a name it
    doesn't recognize rather than raising)."""

    class _FakeLogger:
        name = "totally-unrelated"

    event_dict = log._short_logger_name(_FakeLogger(), "info", {})
    assert event_dict["logger"] == "totally-unrelated"


def test_trace_supports_percent_style_args(tmp_path):
    log.configure(level=log.TRACE, log_dir=tmp_path, console=False)
    lg = log.get_logger("tracer3")
    lg.trace("value is %s", "42")
    msgs = {r["msg"] for r in _read_records(tmp_path / "nyxloom.jsonl")}
    assert "value is 42" in msgs


def test_trace_returns_none_on_drop_event():
    """Direct unit test of `trace()`'s DropEvent handling -- mirrors
    structlog's own `_proxy_to_logger` (a processor is allowed to signal
    "drop this record" mid-chain), exercised without needing to smuggle a
    drop-triggering processor through the full configure() pipeline."""
    cls = log._make_wrapper_class(log.TRACE)

    class _FakeSelf:
        def _process_event(self, method_name, event, kw):
            raise structlog.DropEvent

    assert cls.trace(_FakeSelf(), "dropped") is None


# ---------------------------------------------------------------------------
# P06 (docs/plan-logging.md §6, D-L6): retention knobs — size-based rotation
# with env-configurable maxBytes x backupCount.

def test_resolve_int_env_falls_back_on_absent_bad_and_negative(monkeypatch):
    """The retention env reader tolerates absent / non-integer / negative
    values, falling back to the default (a compose typo must never crash
    logging setup). Negative: a bad value propagates or raises."""
    monkeypatch.delenv("NYXLOOM_LOG_MAX_BYTES", raising=False)
    assert log._resolve_int_env("NYXLOOM_LOG_MAX_BYTES", 999) == 999   # absent -> default
    monkeypatch.setenv("NYXLOOM_LOG_MAX_BYTES", "4242")
    assert log._resolve_int_env("NYXLOOM_LOG_MAX_BYTES", 999) == 4242  # valid int used
    monkeypatch.setenv("NYXLOOM_LOG_MAX_BYTES", "notanint")
    assert log._resolve_int_env("NYXLOOM_LOG_MAX_BYTES", 999) == 999   # non-int -> default
    monkeypatch.setenv("NYXLOOM_LOG_MAX_BYTES", "-5")
    assert log._resolve_int_env("NYXLOOM_LOG_MAX_BYTES", 999) == 999   # negative -> default


def test_resolve_rotation_reads_env_with_defaults(monkeypatch):
    """resolve_rotation() returns the 10 MB x 5 defaults when unset, and the
    env overrides when present."""
    monkeypatch.delenv("NYXLOOM_LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("NYXLOOM_LOG_BACKUPS", raising=False)
    assert log.resolve_rotation() == (10_000_000, 5)                   # defaults (D-L6)
    monkeypatch.setenv("NYXLOOM_LOG_MAX_BYTES", "200")
    monkeypatch.setenv("NYXLOOM_LOG_BACKUPS", "2")
    assert log.resolve_rotation() == (200, 2)                          # env overrides


def test_log_file_rotates_by_size_and_backups_are_bounded(tmp_path, monkeypatch):
    """A tiny maxBytes forces rotation and the rotated-backup count is bounded
    by NYXLOOM_LOG_BACKUPS (older segments discarded) -- proving the retention
    knobs take effect end-to-end. Negative: no rotation, or unbounded growth
    past backupCount."""
    monkeypatch.setenv("NYXLOOM_LOG_MAX_BYTES", "500")
    monkeypatch.setenv("NYXLOOM_LOG_BACKUPS", "2")
    log.configure(level=log.TRACE, log_dir=tmp_path, console=False)
    logger = log.get_logger("rot")
    for i in range(300):
        logger.info("rotation probe line padded to exceed the tiny segment",
                     i=i, pad="x" * 40)

    base = tmp_path / log._LOG_FILENAME
    assert base.exists()                                              # current segment present
    backups = sorted(tmp_path.glob(log._LOG_FILENAME + ".*"))
    assert backups, "expected at least one rotated backup"            # rotation happened
    assert len(backups) <= 2                                          # bounded by backupCount=2

    # Restore default logging so later tests aren't left pointed at tmp_path.
    log.configure(level=log.INFO, console=False)


# ---------------------------------------------------------------------------
# Guard: never monkeypatch a level method ONTO a structlog lazy proxy.
# ---------------------------------------------------------------------------

def test_no_test_monkeypatches_a_level_method_onto_a_lazy_proxy():
    """A static guard, deliberately not a runtime one.

    `daemon.log` (and every other module's) is a structlog
    `BoundLoggerLazyProxy`. It defines NO per-level methods -- `log.warning` is
    synthesised by `__getattr__`, which re-binds against the LIVE structlog
    config on every call. That is precisely the mechanism that lets
    `log.configure()` reach modules which imported their logger at import time.

    `monkeypatch.setattr(target, name, value)` captures the old value with
    `getattr`. Against this object that captures a SYNTHETIC bound method, and
    `undo()` restores it with `setattr` -- materialising it as a permanent
    instance attribute. The level method is then found by ordinary attribute
    lookup, `__getattr__` never runs again, and every later call in that worker
    uses a logger frozen to whatever config was live during the patching test
    (in practice structlog's unconfigured default, printing to stdout). A
    temporary patch thereby becomes permanent global pollution.

    Evidence this is worth a guard: two such patches in
    test_carver_session_executor.py silently broke
    test_daemon.py::test_resume_attempt_emits_warning_attempt_retry, but only
    under a full `-n 4` run -- the victim configured logging correctly, the
    global config was verified correct at the moment of emission, and the
    record still went to stdout. It cost a long instrumented hunt (2026-07-27).

    STATIC, not runtime, on purpose: a runtime assertion could only observe
    pollution created by a test that already ran, so it would be order- and
    worker-dependent -- exactly the property that made the original bug so hard
    to see. Reading the source is deterministic.

    AST, not regex, also on purpose: the fix's own explanatory docstrings have
    to spell the anti-pattern out, and a text scan cannot tell prose from code
    (the first cut of this guard failed on the comment describing it). Matching
    real Call nodes sees only executable patches.

    The fix is always to patch the OWNING NAMESPACE instead:
    `monkeypatch.setattr(daemon, "log", spy)` -- `log` is a real entry in the
    module `__dict__`, so undo restores it cleanly.
    """
    import ast
    from pathlib import Path

    LEVELS = {"warning", "info", "error", "debug", "critical",
              "exception", "trace", "warn", "msg"}

    def _dotted(node):
        """Render a Name/Attribute chain as 'a.b.c', else None."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        parts.append(node.id)
        return ".".join(reversed(parts))

    offenders = []
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            is_setattr = ((isinstance(fn, ast.Attribute) and fn.attr == "setattr")
                          or (isinstance(fn, ast.Name) and fn.id == "setattr"))
            if not is_setattr or not node.args:
                continue
            a0 = node.args[0]
            # string form: setattr("pkg.mod.log.warning", ...)
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                bits = a0.value.split(".")
                if len(bits) >= 2 and bits[-2] == "log" and bits[-1] in LEVELS:
                    offenders.append(f"{path.name}:{node.lineno}: setattr({a0.value!r}, ...)")
                continue
            # object form: setattr(<...>.log, "warning", ...)
            if len(node.args) >= 2:
                a1 = node.args[1]
                target = _dotted(a0)
                if (target and target.split(".")[-1] == "log"
                        and isinstance(a1, ast.Constant)
                        and a1.value in LEVELS):
                    offenders.append(
                        f"{path.name}:{node.lineno}: setattr({target}, {a1.value!r}, ...)")

    assert not offenders, (
        "monkeypatching a level method onto a structlog lazy proxy permanently "
        "pins that proxy for the whole worker (see this test's docstring). "
        "Patch the owning module attribute instead -- "
        "monkeypatch.setattr(<module>, 'log', spy):\n  " + "\n  ".join(offenders)
    )
