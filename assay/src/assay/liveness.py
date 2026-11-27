"""assay's own liveness mechanism for native R2 python/pytest lanes (B091/D-23,
controller rulings RW-33 and RW-36).

**What this module is for.** B090's incident: a candidate whose mutant flips a
``daemon=True`` thread to non-daemon finishes every test, prints its summary,
and then hangs forever joining that thread at interpreter shutdown -- 0% CPU,
no progress, and (pre-B091) no default per-candidate budget to end it. RW-33's
ruling is ONE mechanism, two parts, applied ONLY to native R2 lanes
(``lane.judge.mutation is not None and not is_ingested``) whose
``adapter.name == "python"``:

1. **A materialized pytest plugin** (:data:`_PLUGIN_SOURCE`, written by
   :func:`materialize_liveness_plugin`) that calls ``os._exit(rc)`` from
   ``pytest_unconfigure`` -- AFTER the terminal summary and pytest-cov's
   sessionfinish write, per a real spike (session 2's LOG/REPORT) --
   bypassing the interpreter's normal thread-join-at-shutdown sequence
   entirely, but ONLY when ``pytest_sessionfinish`` actually ran and
   assigned an exit status (P7 round-1 B1). When the session never started
   (e.g. a raising ``conftest.pytest_configure``/``pytest_sessionstart``),
   ``pytest.main`` has not returned and no summary was printed;
   ``pytest_unconfigure`` still fires (``Config._ensure_unconfigure()``),
   and on that path it now returns WITHOUT exiting, leaving pytest's own
   exit status (and process exit) exactly as it would be with the plugin
   absent -- the earlier unconditional ``os._exit(0)`` there manufactured a
   false SURVIVOR out of a session that genuinely never ran a test. A
   leaked non-daemon thread can no longer hang the process; the mutant
   instead SURVIVES (it must be killed by an honest assertion), which is
   D-23's actual point.
2. **A candidate-only env stamp and, from this session, an ACTIVE monitoring
   loop** (:class:`LivenessRunner`) that turns the plugin's ``os._exit``
   behaviour on ONLY for R2 candidate executions, never for the R0 baseline
   or R1 (coverage atexit writers must stay intact -- RW-33 is explicit that
   ``ASSAY_LIVENESS_EXIT`` is never set for those), and watches the candidate
   process TREE for `hung` (see :class:`LivenessRunner`'s own docstring).

**RW-36 (binding, supersedes session 2's A-036 exception below).** Session 2
shipped injection by extending ``CommandPlan.argv_declared`` directly --
flagged at the time as a genuine, unresolved judgment call against
``mutation.py``'s own A-036 principle ("flags ... never derived by assay").
The controller ruled: the plugin is judge MECHANICS (observation and exit
hygiene; it changes no test selection or behaviour), so it is not the A-036
case in substance -- but the LANE'S OWN DECLARATION must still stay the
lane's own words on the wire. :func:`inject_liveness_plugin` therefore
extends ``argv_appended`` (never ``argv_declared``), gated by a NEW lane key,
``judge.mutation.liveness = "auto" | true | false`` (default ``"auto"``:
liveness is on exactly when the lane's own declared argv invokes pytest,
matching session 2's original behaviour; ``true`` on a non-pytest argv is
refused at LOAD by ``config._load_mutation``, so :func:`inject_liveness_plugin`
only ever sees a ``"true"`` policy already known to apply; ``false`` turns
liveness off unconditionally, leaving D-23's derived
``budget_per_candidate`` as the only bound) -- explicitly NOT gated by
``lane.allow_argv_append``, which keeps meaning exactly what it meant before
this module existed: the CLI's own ``--`` passthrough consent gate, untouched
by assay's own infrastructure appends. See :data:`~assay.runner.CommandPlan.
cli_argv_appended` for the mechanism that keeps the two separate inside
``execute_plan``'s own refusal check.

Both the ``plan`` progress event (:mod:`assay.mutation`) and the verdict's
``judgment.r2`` gain a ``liveness: {active, reason, plugin}`` record so a
reader can see whether liveness ran for this lane and, if not, why --
:class:`LivenessInjection` is the one place that record is computed, so a
progress-stream reader and a verdict reader can never disagree about it
(B088's own "two independent derivations is how a reader and a writer drift
apart" reasoning, applied here the same way A1 applied it to
``budget_per_candidate_derived_s``).
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterator,
    Mapping,
    NamedTuple,
    Sequence,
    TextIO,
)

from . import observer_windows
from .errors import AssayError, Outcome, ReasonCode
from .guards import is_int_at_least, is_real
from .liveness_resources import compare_resource_snapshots, read_liveness_resources
from .mutation_witness import WITNESS_FD_ENV
from .process_tree import (
    ProcessTreeError,
    ProcessTreeSpawnError,
    ProcessTreeWindow,
    _report_window_host_candidate_pid,
)

# B117/P6: termination is process-wide for one Assay invocation. Keep the
# event here so the process-group registry can honor it without importing
# runner.py (runner already imports this module).
_TERMINATION = threading.Event()
_LIVE_GROUPS: set[int] = set()
_LIVE_WINDOW_HOST_GROUPS: set[int] = set()
_LIVE_GROUPS_LOCK = threading.RLock()
_DEFERRED_TERMINATION_SWEEP = False
_CANDIDATE_TREE_CLEANUP_SECONDS = 10.0


def _killpg_group(pgid: int, sig: int) -> None:
    """Best-effort process-group signal used by termination and runner cleanup."""
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def register_live_group(pgid: int) -> None:
    """Register a process group, killing it immediately after termination."""
    with _LIVE_GROUPS_LOCK:
        _LIVE_GROUPS.add(pgid)
        if _TERMINATION.is_set():
            _killpg_group(pgid, signal.SIGKILL)


def unregister_live_group(pgid: int) -> None:
    """Forget a group and complete a deferred signal-handler sweep if needed."""
    global _DEFERRED_TERMINATION_SWEEP
    with _LIVE_GROUPS_LOCK:
        _LIVE_GROUPS.discard(pgid)
        if _DEFERRED_TERMINATION_SWEEP:
            _DEFERRED_TERMINATION_SWEEP = False
            if _TERMINATION.is_set():
                for live_pgid in tuple(_LIVE_GROUPS):
                    _killpg_group(live_pgid, signal.SIGKILL)


def register_live_window_host_group(pgid: int) -> None:
    """Register a host session that must unwind its command tree on SIGTERM."""
    with _LIVE_GROUPS_LOCK:
        _LIVE_WINDOW_HOST_GROUPS.add(pgid)
        if _TERMINATION.is_set():
            _killpg_group(pgid, signal.SIGTERM)


def unregister_live_window_host_group(pgid: int) -> None:
    """Forget a window host after it has returned or been reaped."""
    with _LIVE_GROUPS_LOCK:
        _LIVE_WINDOW_HOST_GROUPS.discard(pgid)


def terminate_live_window_hosts() -> int:
    """Ask every isolated command host to clean up and stop gracefully."""
    acquired = _LIVE_GROUPS_LOCK.acquire(blocking=False)
    if acquired:
        try:
            groups = tuple(_LIVE_WINDOW_HOST_GROUPS)
        finally:
            _LIVE_GROUPS_LOCK.release()
    else:
        groups = tuple(_LIVE_WINDOW_HOST_GROUPS.copy())
    for pgid in groups:
        _killpg_group(pgid, signal.SIGTERM)
    return len(groups)


def terminate_live_process_groups(sig: int = signal.SIGKILL) -> int:
    """Signal registered groups without blocking a signal handler.

    The RLock lets the main thread re-enter if a signal arrives while it owns
    the registry. If another thread owns it, set iteration is atomic under the
    GIL; kill that snapshot and defer a locked sweep until the next unregister.
    Kill-on-register covers groups created after this snapshot.
    """
    global _DEFERRED_TERMINATION_SWEEP
    acquired = _LIVE_GROUPS_LOCK.acquire(blocking=False)
    if acquired:
        try:
            groups = tuple(_LIVE_GROUPS)
            for pgid in groups:
                _killpg_group(pgid, sig)
            return len(groups)
        finally:
            _LIVE_GROUPS_LOCK.release()
    # `set.copy()` is one C-level operation under the GIL; `tuple(set)` would
    # walk a live iterator while the lock owner may be adding/removing groups.
    groups = tuple(_LIVE_GROUPS.copy())
    _DEFERRED_TERMINATION_SWEEP = True
    for pgid in groups:
        _killpg_group(pgid, sig)
    return len(groups)


def _reset_live_groups_for_tests() -> None:
    """Reset process-global termination state for isolated tests only."""
    global _DEFERRED_TERMINATION_SWEEP
    with _LIVE_GROUPS_LOCK:
        _LIVE_GROUPS.clear()
        _LIVE_WINDOW_HOST_GROUPS.clear()
        _DEFERRED_TERMINATION_SWEEP = False
        _TERMINATION.clear()


def oom_kill_count(path: Path = Path("/sys/fs/cgroup/memory.events")) -> int | None:
    """Read the cgroup-v2 ``oom_kill`` counter, or report it unavailable."""
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return None
    matches = [line.split() for line in lines if line.split()[:1] == ["oom_kill"]]
    if len(matches) != 1 or len(matches[0]) != 2:
        return None
    try:
        value = int(matches[0][1])
    except ValueError:
        return None
    return value if value >= 0 else None

if TYPE_CHECKING:  # pragma: no cover -- annotation-only import; importing at runtime creates a cycle
    from .runner import CommandPlan

#: The module name assay's `-p` injection asks pytest to load. Chosen to be
#: obviously assay-owned (never collide with a real project's own plugin) and
#: a legal Python module name (no dots, no dashes).
LIVENESS_PLUGIN_MODULE_NAME = "assay_liveness_plugin"
LIVENESS_PLUGIN_FILENAME = f"{LIVENESS_PLUGIN_MODULE_NAME}.py"

#: Env var names the plugin itself reads (kept here, not duplicated, so the
#: plugin source string below and :class:`LivenessRunner` cannot drift).
ASSAY_LIVENESS_EVENTS_ENV = "ASSAY_LIVENESS_EVENTS"
ASSAY_LIVENESS_EXIT_ENV = "ASSAY_LIVENESS_EXIT"

#: (RW-36) `judge.mutation.liveness`'s three closed spellings. `config.py`
#: imports these rather than re-spelling them, the same "vocabulary from its
#: own owner" discipline `coverage.FORMAT_REGISTRY` already gets one field
#: over (A-068) -- this module owns the injection mechanism, so it owns the
#: policy's own vocabulary too.
LIVENESS_AUTO = "auto"
LIVENESS_TRUE = "true"
LIVENESS_FALSE = "false"
LIVENESS_POLICIES = (LIVENESS_AUTO, LIVENESS_TRUE, LIVENESS_FALSE)

#: Stdlib-only pytest plugin, materialized verbatim by
#: :func:`materialize_liveness_plugin`. Must never import `assay` (the
#: candidate subprocess runs in the PROJECT's interpreter, where assay is
#: often not importable -- a pyz is not on that interpreter's `sys.path` at
#: all) and every hook body is wrapped in `try/except: pass` so a plugin bug
#: can never turn into a false mutant outcome.
#:
#: Spike-validated (session 2's LOG): `os._exit` from `pytest_unconfigure`
#: silently DROPS the terminal summary and pytest-cov's printed report table
#: unless stdout/stderr are flushed first -- the underlying `.coverage` DATA
#: file is written correctly either way (pytest-cov's own `sessionfinish`
#: hook runs and returns before `unconfigure` fires), but the process's own
#: buffered text is lost without an explicit flush. The `sys.stdout.flush()`/
#: `sys.stderr.flush()` pair below is that fix; do not remove it.
#:
#: **Real-bug fix (P7 session 5, found by the real end-to-end fixture test,
#: never by any unit test -- every existing unit test hand-constructs its
#: own valid-JSON event lines and so never exercised what THIS plugin
#: actually writes).** `pytest_runtest_logreport`/`pytest_sessionfinish`
#: used to build each NDJSON line with `%r` (`repr()`) on `nodeid`/
#: `outcome`/`duration_s -- Python's `repr()` of a string is SINGLE-quoted,
#: not the double-quoted JSON string syntax `json.loads` requires, and
#: `repr(None)` is the bare token `None`, not JSON's `null`. Every line this
#: plugin ever wrote was therefore invalid JSON -- silently swallowed by
#: every downstream `json.loads` as a "tolerated torn line"
#: (:func:`compute_expect_next_event_within_s`/:func:`_read_events_progress`
#: both catch `ValueError` and skip, by design, for a genuinely torn LAST
#: line from a plugin still writing). The practical effect: `slowest_test_s`
#: was NEVER found (always the coarse `max(60s, baseline_s/4)` fallback,
#: never the tight measurement-based bound), and `saw_session_finish` was
#: NEVER true (the `session_finish`-then-still-alive `hung` branch RW-33
#: names was live code that could never actually fire). Fixed by building a
#: real `dict` and calling `json.dumps` (stdlib, already an implicit
#: dependency of this file) instead of hand-rolling a JSON-shaped string.
_PLUGIN_SOURCE = '''"""assay's own liveness plugin (B091/D-23/RW-33) -- materialized by
assay, loaded by pytest via `-p assay_liveness_plugin`. Stdlib + pytest only;
never imports assay (the candidate subprocess is often not running in an
environment where assay is importable). Every hook body is exception-safe:
a bug here must never change a test outcome or crash the suite.
"""

import json
import os
import sys
import time

import pytest

_EXIT_STATUS = None  # sentinel: pytest_sessionfinish never ran/decided

# B097: `_append` stamps the producer process. The worker label is descriptive
# metadata; the consumer uses pid ownership when usable stamps are present.


def _events_path():
    return os.environ.get("ASSAY_LIVENESS_EVENTS")


def _append(record):
    try:
        path = _events_path()
        if not path:
            return
        record = dict(record)
        # B097: the process that actually ran the hook is the authoritative
        # identity. Remove caller-supplied identity fields before stamping
        # so a reused hook record cannot leak stale data into a later event.
        record.pop("pid", None)
        record.pop("xdist_worker", None)
        record["t"] = time.time()
        record["pid"] = os.getpid()
        worker = os.environ.get("PYTEST_XDIST_WORKER")
        if worker:
            record["xdist_worker"] = worker
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\\n")
    except Exception:
        pass


def pytest_configure(config):
    # (B091 round-1 B2) The FIRST timestamp of the session, written before
    # collection starts. Without it the events file's earliest stamp is the
    # first test's `setup` report, which is AFTER collection and after every
    # session/module fixture that test needs -- so a slow import or a slow
    # session fixture was invisible to the calibration and the monitor
    # counted that whole stretch as idleness.
    _append({"event": "session_start"})


def pytest_runtest_logstart(nodeid, location):
    # (P4/S2) A test BEGAN. This is NOT progress for the CPU-without-progress
    # bound (nothing has completed) but it IS a live sign for the idle rule
    # and it is what dates the per-test bound's own window: `nodeid` is the
    # only identity the per-test baseline timings are keyed by.
    try:
        _append({"event": "test_start", "nodeid": nodeid})
    except Exception:
        pass


def pytest_runtest_logreport(report):
    # (B091 round-1 B2) EVERY phase, not just `call`. `report.duration` for
    # a `setup` report includes the fixture, and -- more to the point -- the
    # record's own `t` is a live sign of progress the monitoring loop can
    # see. The `call` phase alone keeps the `test` event name: that keyword
    # is what A4 forwards to the progress stream (one per test, RW-33) and
    # what `tests_completed` counts, and both must stay one-per-test. The
    # other two phases get `phase`, which the monitor counts as activity and
    # every `test`-keyed reader ignores by construction.
    try:
        record = {
            "event": "test" if report.when == "call" else "phase",
            "when": report.when,
            "nodeid": report.nodeid,
            "outcome": report.outcome,
            "duration_s": report.duration,
        }
    except Exception:
        return
    _append(record)


def pytest_runtest_logfinish(nodeid, location):
    # (P4 fix 1, review finding 1) pytest calls this ONCE per item, after its
    # whole runtest protocol -- `setup`, `call` AND `teardown` -- has run
    # (pytest 9's `pytest_runtest_protocol` brackets the item with
    # `pytest_runtest_logstart`/`pytest_runtest_logfinish`). It is the ONLY
    # record that proves a test is no longer in flight: its `setup`/`call`/
    # `teardown` reports arrive while it still runs, and `phase` is a
    # COMPLETED event for the CPU-without-progress bound. Written under its
    # own kind, deliberately NOT in `COMPLETED_EVENT_KINDS`, so it neither
    # re-dates that bound nor changes `tests_completed`.
    try:
        _append({"event": "test_finish", "nodeid": nodeid})
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    global _EXIT_STATUS
    try:
        _EXIT_STATUS = int(exitstatus)
    except Exception:
        _EXIT_STATUS = 1
    _append({"event": "session_finish", "exitstatus": _EXIT_STATUS})


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    try:
        if os.environ.get("ASSAY_LIVENESS_EXIT") != "1":
            return
        if _EXIT_STATUS is None:
            # pytest_sessionfinish never ran (the session never started --
            # e.g. a raising conftest.pytest_configure/pytest_sessionstart):
            # pytest never decided an exit status, so let pytest's own exit
            # path stand rather than manufacturing one. B1: the earlier
            # unconditional os._exit(0) here turned exactly this shape into
            # a false SURVIVOR (exit 0) for a session that genuinely never
            # ran a test.
            return
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(_EXIT_STATUS)
    except Exception:
        pass
'''


def plugin_source_hash() -> str:
    """A short, stable content hash of :data:`_PLUGIN_SOURCE` -- the
    materialize-only-when-different rule's own comparator, and cheap enough
    to compute on every lane run without measuring.
    """
    return hashlib.sha256(_PLUGIN_SOURCE.encode("utf-8")).hexdigest()


def materialize_liveness_plugin(liveness_dir: Path) -> Path:
    """Write the plugin module into *liveness_dir*, but only when the file is
    absent or its content differs from :data:`_PLUGIN_SOURCE` -- so a lane run
    after the first never re-writes a file that already matches (RW-33: "an
    assay wheel or the pyz" -- both ship the SAME literal string, so this
    works identically from either).

    Returns the plugin file's path. Never returns before the directory and
    file exist -- a caller can rely on the path being real once this returns.

    (Round-1 S9) An unwritable project root raises a typed refusal naming
    the directory, never a bare `OSError`: this runs inside
    `runner._run_prepared_lane`, where an untyped `OSError` escapes as a
    traceback (or, worse, is relabelled by a broad `except OSError` two
    frames up) rather than as an honest cause.
    """
    target = liveness_dir / LIVENESS_PLUGIN_FILENAME
    try:
        liveness_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8")
            except OSError:
                existing = None
            if existing == _PLUGIN_SOURCE:
                return target
        target.write_text(_PLUGIN_SOURCE, encoding="utf-8")
    except OSError as exc:
        raise AssayError(
            f"could not materialize the liveness plugin into {liveness_dir}: "
            f"{exc}. Liveness needs to write "
            f"{LIVENESS_PLUGIN_FILENAME} under the judged project's own "
            "'.assay/liveness/' directory; make that directory writable, or "
            "declare 'judge.mutation.liveness = false' on the lane.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.OUTPUT_WRITE_FAILED,
        ) from exc
    return target


def argv_invokes_pytest(argv: Sequence[str]) -> bool:
    """RW-33's own rule: a native Python R2 lane's argv must literally invoke
    pytest for liveness to apply. True iff *argv* STARTS with a token equal to
    ``"pytest"`` or ending in ``"/pytest"``, or carries the adjacent pair
    ``"-m", "pytest"``.

    Never a substring match (a lane argument that merely MENTIONS pytest,
    e.g. a `--pytest-args=...` value for some other tool, must not trip
    this) and never order-independent: the `-m`/`pytest` pair must be
    adjacent, matching how a shell would actually invoke `python -m pytest`.

    (Round-1 S2) The bare-token half is POSITIONAL, which is what the
    docstring always claimed and what the rule is for. Before this it
    matched a `pytest` token at ANY index, so ``["make", "pytest"]`` and
    ``["tox", "-e", "pytest"]`` both qualified -- and assay then appended
    ``-p assay_liveness_plugin`` to `make`/`tox`, which is not their
    argument to take. It failed loudly (the baseline broke, and the argv is
    disclosed as `(appended: ...)`), but it was never the rule. A pytest
    invocation puts pytest first; the `-m pytest` pair covers
    `python -m pytest`, wherever the interpreter's own flags sit.
    """
    tokens = list(argv)
    if tokens and (tokens[0] == "pytest" or tokens[0].endswith("/pytest")):
        return True
    for index, token in enumerate(tokens):
        if (
            token == "-m"
            and index + 1 < len(tokens)
            and tokens[index + 1] == "pytest"
        ):
            return True
    return False


class LivenessInjection(NamedTuple):
    """The result of :func:`inject_liveness_plugin` -- what the caller needs
    both to run the lane (``plan``) and to RECORD what it did (``active``,
    ``reason``, ``plugin``), on the ``plan`` progress event and on
    ``judgment.r2.liveness`` alike (RW-36), from the SAME computation so the
    two can never disagree.
    """

    plan: "CommandPlan"
    active: bool
    #: A short, stable, machine-checkable spelling -- never free text --
    #: naming WHY `active` is what it is. The closed set this module
    #: produces: `"auto-pytest-argv"` (default policy, argv invokes pytest),
    #: `"declared-true"` (explicit opt-in, argv invokes pytest -- the only
    #: shape `config._load_mutation` lets reach here), `"declared-false"`
    #: (explicit opt-out), `"argv-does-not-invoke-pytest"` (default policy,
    #: off by the same rule that would refuse an explicit `true`).
    reason: str
    #: The materialized plugin's path, as a string (wire-friendly), or
    #: `None` when `active` is `False` -- nothing was written.
    plugin: str | None

    def to_wire(self) -> dict[str, Any]:
        """The exact ``{active, reason, plugin}`` shape RW-36 asks both the
        `plan` progress event and `judgment.r2.liveness` to carry."""
        return {"active": self.active, "reason": self.reason, "plugin": self.plugin}


#: (RW-36) The one-line WARN/off-reason shown when the auto policy (or a
#: declared `false` explained for symmetry) finds no pytest invocation --
#: named once so the runtime message and `config._load_mutation`'s own
#: load-time refusal for `liveness = true` describe the SAME rule in the
#: SAME words.
_NOT_PYTEST_EXPLANATION = (
    "this lane's argv does not literally invoke pytest (a token 'pytest', "
    "a path ending '/pytest', or the adjacent pair '-m pytest')"
)


def inject_liveness_plugin(
    plan: "CommandPlan",
    *,
    liveness_dir: Path,
    diagnostics: "TextIO | None",
    liveness_policy: str | None = None,
) -> LivenessInjection:
    """Return a :class:`LivenessInjection` for a native R2 python lane.

    *liveness_policy* is ``lane.judge.mutation.liveness`` verbatim: ``None``
    or :data:`LIVENESS_AUTO` behave identically (an omitted key means
    ``"auto"`` -- the same "omission is a real, meaningful default" idiom
    A1 already established for `budget_per_candidate`); :data:`LIVENESS_TRUE`
    forces liveness on (config has already refused this at LOAD time unless
    the lane's argv invokes pytest, but the check is repeated here too --
    cheap, and it means this function's own contract does not depend on the
    loader never being bypassed by a hand-built `Lane`); :data:`LIVENESS_FALSE`
    turns it off unconditionally, no WARN (an explicit, written-down choice
    is not a surprise the caller needs flagged).

    When liveness is OFF, returns *plan* UNCHANGED (`injection.plan is plan`)
    and, for the auto-policy "not pytest" case only, a diagnostics WARN
    (RW-33's own documented requirement) when *diagnostics* is not `None`.
    D-23's derived `budget_per_candidate` (A1) remains the only bound for
    that lane either way -- this function never touches it.

    When liveness is ON: materializes the plugin
    (:func:`materialize_liveness_plugin`), returns a NEW plan whose
    `argv_appended` gains ``-p assay_liveness_plugin`` (RW-36: NEVER
    `argv_declared` -- the lane's own declaration stays the lane's own
    words) and whose `env_effective` gains a `PYTHONPATH` entry for
    *liveness_dir* (prepended to any existing value, never replacing it).
    `cli_argv_appended` is set to the plan's PRE-injection `argv_appended`
    (or carried through unchanged if already set), so
    `assay.runner.execute_plan`'s `allow_argv_append` refusal keeps testing
    only what the CLI's own `--` passthrough contributed -- liveness's own
    append is unconditional infrastructure, gated by *liveness_policy*, not
    by lane consent (RW-36 is explicit: NOT `allow_argv_append`, which keeps
    its coverage-flag/CLI-passthrough meaning). `argv_effective` is
    recomputed to keep `argv_effective == argv_declared + argv_appended`
    (A-036's transparency invariant) true of the NEW `argv_appended`.
    """
    invokes_pytest = argv_invokes_pytest(plan.argv_declared)
    if liveness_policy == LIVENESS_FALSE:
        return LivenessInjection(plan=plan, active=False, reason="declared-false", plugin=None)
    if liveness_policy == LIVENESS_TRUE:
        if not invokes_pytest:
            # (Defensive only -- `config._load_mutation` already refuses this
            # combination at LOAD time.) Reported the same way the auto
            # policy's "not pytest" case is, so a reader never sees two
            # different sentences for the same fact.
            if diagnostics is not None:
                print(
                    f"assay: WARN: judge.mutation.liveness = true but "
                    f"{_NOT_PYTEST_EXPLANATION} -- liveness is OFF for this "
                    f"lane; this should have been refused at load time "
                    f"(config._load_mutation)",
                    file=diagnostics,
                )
            return LivenessInjection(
                plan=plan, active=False, reason="argv-does-not-invoke-pytest", plugin=None
            )
        reason = "declared-true"
    else:
        # None or LIVENESS_AUTO -- byte-identical to each other, and to
        # session 2's original (pre-RW-36) auto behaviour.
        if not invokes_pytest:
            if diagnostics is not None:
                print(
                    f"assay: WARN: {_NOT_PYTEST_EXPLANATION} -- liveness "
                    f"(the os._exit wrapper, hung detection, per-test "
                    f"cadence hints) is OFF for this lane; a native Python "
                    f"R2 lane's argv must invoke pytest for liveness "
                    f"(D-23/RW-33), or declare judge.mutation.liveness = "
                    f"false to silence this WARN. The declared/derived "
                    f"budget_per_candidate remains the only bound.",
                    file=diagnostics,
                )
            return LivenessInjection(
                plan=plan, active=False, reason="argv-does-not-invoke-pytest", plugin=None
            )
        reason = "auto-pytest-argv"
    plugin_path = materialize_liveness_plugin(liveness_dir)
    new_env = dict(plan.env_effective)
    existing_pythonpath = new_env.get("PYTHONPATH", "")
    new_env["PYTHONPATH"] = (
        str(liveness_dir)
        if not existing_pythonpath
        else os.pathsep.join([str(liveness_dir), existing_pythonpath])
    )
    # (RW-36) `cli_argv_appended` freezes the PRE-injection `argv_appended`
    # -- the CLI's own passthrough tokens and nothing else -- so
    # `execute_plan`'s `allow_argv_append` refusal keeps judging only that,
    # never liveness's own unconditional append. If this plan already carries
    # an explicit `cli_argv_appended` (e.g. injection somehow ran twice --
    # not a real call pattern today, but the honest thing to preserve),
    # that value wins rather than being overwritten by the now-augmented
    # `argv_appended`.
    cli_only = (
        plan.argv_appended if plan.cli_argv_appended is None else plan.cli_argv_appended
    )
    new_argv_appended = plan.argv_appended + ("-p", LIVENESS_PLUGIN_MODULE_NAME)
    new_plan = replace(
        plan,
        argv_appended=new_argv_appended,
        argv_effective=plan.argv_declared + new_argv_appended,
        env_effective=MappingProxyType(new_env),
        pre_injection_env_effective=(
            plan.pre_injection_env_effective
            if plan.pre_injection_env_effective is not None
            else MappingProxyType(dict(plan.env_effective))
        ),
        cli_argv_appended=cli_only,
    )
    return LivenessInjection(
        plan=new_plan, active=True, reason=reason, plugin=str(plugin_path)
    )


class LivenessHungExpired(subprocess.TimeoutExpired):
    """Raised by :class:`LivenessRunner`'s monitoring loop when it kills a
    candidate for **hung** (an idle stall) -- never for a genuine elapsed-
    budget timeout, which still raises plain ``subprocess.TimeoutExpired``,
    unchanged (RW-33: a CPU-spinning mutant is NOT hung, it hits the ordinary
    budget ceiling). The ONLY thing this subclass exists for is letting
    ``runner._execute_plan_inner``'s existing ``except subprocess.
    TimeoutExpired`` clause tell the two apart with one ``isinstance`` check,
    so ``mutation._classify_mutant_result`` can report `hung` as a bucket
    distinct from `budget_exceeded` without ``CommandResult``/``Outcome``/
    ``ReasonCode`` growing a parallel return shape (P7 BRIEF-3's decision,
    RW-33).

    ``resource_evidence`` carries candidate-tree CPU readings and
    time-aligned host/cgroup pressure counters for the clean intervals that
    supported this classification. The runner transfers it into the
    per-candidate mutation artifacts; a missing trace is never filled with a
    default.

    Constructed exactly the way ``subprocess.run`` constructs the real
    thing: ``cmd``/``timeout``/``output``/``stderr`` -- the same shape
    ``_decode_timeout_stream``/``_bounded_tail`` in ``runner.py`` already
    consume, so nothing about that decode path changes for this subclass.
    """

    def __init__(
        self,
        cmd: Sequence[str],
        *,
        timeout: float | None,
        output: bytes,
        stderr: bytes,
        resource_evidence: Mapping[str, Any],
    ) -> None:
        super().__init__(cmd, timeout=timeout, output=output, stderr=stderr)
        self.resource_evidence = dict(resource_evidence)


class LivenessNoProgressWallExpired(subprocess.TimeoutExpired):
    """Raised when an armed candidate has no completed event for its stop cap.

    This stops the run without classifying the attempt. `mutation.run_mutation`
    converts it to the existing lane-timeout fatal path, so there is no
    candidate bucket or stored result. The cap uses wall time until one
    resource interval is comparable, then uses the same pressure-credited
    clock as the idle rule.
    """

    def __init__(
        self,
        cmd: Sequence[str],
        *,
        timeout: float,
        wall_seconds: float,
        eligible_seconds: float,
        wall_bound_s: float,
        pressure_credit_active: bool,
        output: bytes,
        stderr: bytes,
    ) -> None:
        super().__init__(cmd, timeout=timeout, output=output, stderr=stderr)
        self.wall_seconds = wall_seconds
        self.eligible_seconds = eligible_seconds
        self.wall_bound_s = wall_bound_s
        self.pressure_credit_active = pressure_credit_active


#: (B091/RW-33, P7 A3) The idle-stall thresholds, named once rather than as
#: bare literals scattered through the monitoring loop below.
_HUNG_CPU_WINDOW_S = 30.0
_HUNG_CPU_GROWTH_FLOOR_S = 1.0
_HUNG_CPU_GROWTH_FRACTION = 1.0 / 30.0
_HUNG_SESSION_FINISH_GRACE_S = 30.0
NO_PROGRESS_WALL_FACTOR = 4.0
_LIVENESS_POLL_INTERVAL_S = 1.0
_LIVENESS_RESOURCE_TRACE_MAX_SAMPLES = 1024


class _PidStat(NamedTuple):
    """One ``/proc/<pid>/stat`` read: the process's own CPU ticks
    (utime+stime). This is the hang-detection control input; it is never
    recorded as a measurement (B206)."""

    ticks: int


def _pid_stat(pid: int) -> _PidStat:
    """One read of ``/proc/<pid>/stat``.

    The ``comm`` field (field 2) is parenthesized and may itself contain
    spaces or parentheses (a renamed process, e.g. via ``prctl``), so this
    splits on the LAST ``)`` rather than naively splitting the whole line --
    everything after it is space-separated and stable, starting at field 3
    (``state``).
    """
    with open(f"/proc/{pid}/stat", "r", encoding="ascii", errors="replace") as stream:
        raw = stream.read()
    close_paren = raw.rfind(")")
    fields = raw[close_paren + 2 :].split()
    # `fields[0]` is field 3 (state); `fields[i]` is field i + 3: utime (14)
    # is fields[11] and stime fields[12].
    return _PidStat(int(fields[11]) + int(fields[12]))


def _pid_cpu_ticks(pid: int) -> int:
    """utime+stime (fields 14+15 of ``/proc/<pid>/stat``), in clock ticks."""
    return _pid_stat(pid).ticks


def _pid_children_via_task(pid: int) -> list[int]:
    """``/proc/<pid>/task/*/children`` -- the cheap, race-free enumeration
    (Linux >= 3.5) of a process's own direct children, unioned across every
    thread's own `children` file (any thread may have spawned one).
    """
    children: list[int] = []
    task_dir = Path(f"/proc/{pid}/task")
    for thread_dir in task_dir.iterdir():
        text = (thread_dir / "children").read_text(encoding="ascii", errors="replace")
        children.extend(int(token) for token in text.split())
    return children


def _pid_children_via_ppid_scan(pid: int) -> list[int]:
    """Fallback when ``/proc/<pid>/task/*/children`` is unavailable: scan
    every ``/proc/<n>/stat`` for a ``ppid`` field equal to *pid*. Per-entry
    read failures (a process that exits mid-scan) are skipped, never raised
    -- this function's whole point is to be a best-effort fallback, and a
    process that vanished mid-scan is not this pid's problem to report.
    """
    children: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        close_paren = raw.rfind(")")
        fields = raw[close_paren + 2 :].split()
        try:
            ppid = int(fields[1])  # field 4 (ppid) is fields[1].
        except (IndexError, ValueError):
            continue
        if ppid == pid:
            children.append(int(entry.name))
    return children


def _walk_tree(root_pid: int, read: Callable[[int], Any]) -> list[Any]:
    """The per-process ``read`` values of *root_pid* and every live
    descendant, in visit order (the root first).

    Each process is read BEFORE its own children are listed (a pre-order
    walk), so a child reaped mid-walk is lost from the walk, never counted
    both live and through its parent's ``cutime``. The root's own read is
    allowed to raise; a descendant that already exited is skipped.
    """
    values = [read(root_pid)]  # allowed to raise -- see `tree_cpu_seconds`.
    visited = {root_pid}
    try:
        frontier = _pid_children_via_task(root_pid)
        use_task_api = True
    except OSError:
        frontier = _pid_children_via_ppid_scan(root_pid)
        use_task_api = False
    stack = list(frontier)
    while stack:
        pid = stack.pop()
        if pid in visited:
            continue
        visited.add(pid)
        try:
            values.append(read(pid))
        except OSError:
            continue  # Already exited -- not the root's own read failure.
        try:
            children = _pid_children_via_task(pid) if use_task_api else (
                _pid_children_via_ppid_scan(pid)
            )
        except OSError:
            children = _pid_children_via_ppid_scan(pid)
        stack.extend(child for child in children if child not in visited)
    return values


def tree_cpu_seconds(root_pid: int) -> float:
    """Sum ``utime+stime`` across *root_pid* and every live descendant, in
    seconds.

    **Raises** (``OSError`` and subclasses, e.g. ``ProcessLookupError`` /
    ``FileNotFoundError``) if *root_pid* itself cannot be read -- the caller
    (:class:`LivenessRunner`'s monitoring loop) is the one that turns that
    into "CPU looks like it's still growing" (RW-33's explicit requirement:
    ANY `/proc` read failure must never be read as proof of no growth). A
    child that has already exited by the time it is visited is silently
    skipped (an expected race in an active process tree, not a failure of
    the root's own measurement).
    """
    return sum(_walk_tree(root_pid, _pid_cpu_ticks)) / os.sysconf("SC_CLK_TCK")


#: (B091 round-1 B2) The plugin's three event names, spelled once here so a
#: reader can never drift from the writer's vocabulary. `test` is the `call`
#: phase ONLY -- one per test, which is what A4's progress forwarding and
#: `tests_completed` both require; `phase` is `setup`/`teardown`;
#: `session_start`/`session_finish` bracket the whole session.
TEST_EVENT = "test"
PHASE_EVENT = "phase"
SESSION_START_EVENT = "session_start"
SESSION_FINISH_EVENT = "session_finish"
TEST_START_EVENT = "test_start"
#: (P4 fix 1, review finding 1) `pytest_runtest_logfinish`: pytest calls it
#: once per item, AFTER its whole runtest protocol (setup, call, teardown),
#: so it is the only proof that a test is no longer in flight. It closes
#: the per-test CPU window (see `_EventProgressReader.in_flight`) and is
#: deliberately NOT a completed event: nothing new finished when it arrives.
TEST_FINISH_EVENT = "test_finish"

#: (P4/S1) What counts as a COMPLETED plugin event for the
#: CPU-without-progress bound: a `test` (`call`) report or a `phase`
#: (`setup`/`teardown`) report -- i.e. a unit of work the suite FINISHED.
#: `session_start`/`session_finish` are deliberately NOT here (a session that
#: started and then spun forever has still completed nothing), and neither are
#: `test_start` (a report that a test BEGAN proves nothing finished) nor
#: `test_finish` (the wrapper around those phases completes nothing itself --
#: the `teardown` report just before it already did).
COMPLETED_EVENT_KINDS = frozenset({TEST_EVENT, PHASE_EVENT})

#: (P4/S1) The resource-evidence vocabulary of the CPU-without-progress
#: bound. `mutation_store.valid_hang_resource_evidence` reads these names
#: through this module, never as its own literals, so a resumed record can
#: never be validated against a policy the writer does not produce.
CPU_PROGRESS_POLICY = "cpu-since-completed-event-v1"
CPU_PROGRESS_SOURCE = "process-tree-cpu-seconds-monotonic"
cpu_without_progress_decision = "cpu-without-progress"

#: (P4/S2) The per-test CPU bound's own vocabulary: ``max(10 x the baseline's
#: setup+call+teardown duration for THIS nodeid, 60 s)`` CPU seconds. Ten
#: suite-lengths of CPU for one test is the same generous multiple the
#: derived bound uses for a whole suite, and the absolute 60 s floor keeps a
#: sub-second test from deriving a bound too tight for one interpreter
#: (deliberately NOT `baseline + 60`: that would scale the floor with the
#: test's own trivial duration).
PER_TEST_CPU_MULTIPLIER_S = 10.0
PER_TEST_CPU_FLOOR_S = 60.0
per_test_cpu_bound_decision = "per-test-cpu-bound"

#: The closed set of resource-evidence decisions this module can produce for
#: a `hung` kill. `mutation_store.valid_hang_resource_evidence` reads it here
#: rather than keeping its own literal pair.
CPU_HANG_DECISIONS = frozenset(
    {cpu_without_progress_decision, per_test_cpu_bound_decision}
)
IDLE_HANG_DECISIONS = frozenset({"idle-hang", "session-finish-hang"})
IDLE_HANG_POLICY = "pressure-adjusted-idle-v1"

def per_test_cpu_bound_from_baseline(baseline_s: float) -> float:
    """``max(10 x baseline_s, 60 s)`` -- the per-test bound, one formula.

    Operator item (A), binding (`p4-operator-input-20261125.md`): "each test
    gets a multiple of its own baseline duration ... so a looping test is
    caught at about its normal duration". Ten suite-lengths of CPU for one
    test is the same generous multiple the derived bound uses for a whole
    suite; the 60 s floor is an ABSOLUTE one (not `baseline + 60`), because a
    fast test's bound must not scale with its own trivial duration.
    """
    return max(PER_TEST_CPU_MULTIPLIER_S * baseline_s, PER_TEST_CPU_FLOOR_S)

#: (P4/S1) The floor in the derived bound
#: :func:`derived_bound_from_baseline` -- the `+60 s` half of
#: ``max(3 x baseline, baseline + 60 s)``. Named once here because BOTH the
#: per-candidate wall budget (`mutation.auto_budget_per_candidate_seconds`)
#: and the CPU-without-progress bound derive from the SAME formula (design
#: `docs/design/no-time-budgets/DESIGN.md` section 7.1: "the existing auto
#: formula"); two literal `60.0`s would be two derivations of one number.
DERIVED_BOUND_FLOOR_S = 60.0


def derived_bound_from_baseline(baseline_wall_s: float) -> float:
    """``max(3 x baseline_wall_s, baseline_wall_s + 60 s)`` -- the ONE
    derived bound formula, in SECONDS of the unit it bounds.

    It bounds a candidate's WALL time (the ``"auto"``
    ``budget_per_candidate``) and, since P4, a candidate's process-tree CPU
    seconds since its last completed plugin event. The two are the same
    NUMBER because they are the same policy: three suite-lengths, with a
    60 s floor so a near-instant baseline cannot derive a bound too tight for
    interpreter start-up.

    A negative *baseline_wall_s* (impossible upstream of a real
    :class:`~assay.runner.CommandResult`, clamped at its source too) yields
    the 60 s floor rather than a negative bound.
    """
    return max(3.0 * baseline_wall_s, baseline_wall_s + DERIVED_BOUND_FLOOR_S)

#: (B091/RW-33) The floor under every measurement-derived idle bound -- no
#: calibration, however fast the baseline, ever asks a candidate for a sign
#: of life sooner than this.
LIVENESS_IDLE_FLOOR_S = 15.0

#: (B091/RW-33) The floor under the PLUGIN-INACTIVE fallback bound -- see
#: :func:`compute_liveness_calibration` for why that path is deliberately
#: much coarser than the calibrated one.
LIVENESS_FALLBACK_FLOOR_S = 60.0


def _iter_events(events_path: "Path | None") -> Iterator[dict[str, Any]]:
    """Yield every valid record in *events_path*, of EVERY event kind, in
    file order for full-file readers such as calibration and reporting.

    The monitoring loop uses :class:`_EventProgressReader` to avoid parsing
    the same appended-only file from its beginning on every poll; that reader
    preserves this helper's record and line-boundary semantics.

    `None` (never wired with `ASSAY_LIVENESS_EVENTS`), an absent/unreadable
    path, and a torn last line (the file's own writer still appending when
    this is read) all yield nothing extra -- tolerated by skipping, never
    raised, matching the plugin's own append-per-event contract which makes
    a torn line possible only for the very last one.
    """
    if events_path is None:
        return
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except (ValueError, RecursionError):
            continue  # Tolerant of a torn last line.
        if isinstance(record, dict):
            yield record


def _valid_pid(value: Any) -> int | None:
    """Return *value* as a usable producer pid, or ``None``.

    ``bool`` is an ``int`` subclass, so it has to be rejected explicitly.
    The parser trusts only the pid stamped by the producer; it never tries to
    resolve a descriptive xdist worker name or consult the process table.
    """
    if not is_int_at_least(value, 1):
        return None
    return value


def _record_pid(record: Mapping[str, Any]) -> int | None:
    return _valid_pid(record.get("pid"))


def _session_owner_pid(records: Sequence[Mapping[str, Any]]) -> int | None:
    """The pid owning the first session-start record, when it is stamped."""
    for record in records:
        if record.get("event") == SESSION_START_EVENT:
            return _record_pid(record)
    return None


def _selected_test_events(events_path: "Path | None") -> list[dict[str, Any]]:
    """Read test events with the owner-process rule, or legacy-merge them.

    The xdist controller receives the worker reports as well as its own hook
    reports. The controller is the process that owns the first
    ``session_start`` record, so its stamped ``test`` events are the one
    candidate/baseline timeline to count and forward. This is deliberately
    an event-record count, not a nodeid set: retries or repeated reports must
    not disappear merely because their nodeid is equal.

    A usable pid is required on every relevant ``test`` record before this
    partition is applied. Missing, malformed, or mixed pid records retain
    the old merged interpretation, preserving evidence from legacy files.
    A file without a usable session owner also remains merged because no
    identity may be invented from ``xdist_worker``.
    """
    return [
        record for record in _owner_stream(events_path) if record.get("event") == TEST_EVENT
    ]


def _owner_stream(events_path: "Path | None") -> list[dict[str, Any]]:
    """The `test` and `phase` records, in file order, after the ONE owner
    rule (:func:`_selected_test_events` and :func:`baseline_phase_durations`
    both read through it, so they can never select different processes).

    When every `test` record carries a usable pid and the file has a stamped
    session owner, only that owner's records remain; otherwise the legacy
    merged view is kept.
    """
    records = list(_iter_events(events_path))
    stream = [
        record for record in records if record.get("event") in (TEST_EVENT, PHASE_EVENT)
    ]
    test_records = [record for record in stream if record.get("event") == TEST_EVENT]
    if not test_records or any(_record_pid(record) is None for record in test_records):
        return stream
    owner_pid = _session_owner_pid(records)
    if owner_pid is None:
        return stream
    return [record for record in stream if _record_pid(record) == owner_pid]


def _iter_test_events(events_path: "Path | None") -> Iterator[dict[str, Any]]:
    """Yield every valid `test` event record in *events_path*, in file
    order.

    `None` (never wired with `ASSAY_LIVENESS_EVENTS`), an absent/unreadable
    path, and a torn last line (the file's own writer still appending when
    this is read) all yield nothing extra -- tolerated by skipping, never
    raised, matching the plugin's own append-per-event contract which makes
    a torn line possible only for the very last one.

    The ONE parse loop over one of these NDJSON side files that
    :func:`baseline_slowest_test_s`, :func:`baseline_test_events` and
    :func:`count_test_events` all build on, so the three can never drift
    into three different ideas of what counts as a valid `test` event
    (B088's own "two independent derivations is how a reader and a writer
    drift apart" reasoning, applied here to a THIRD and FOURTH reader this
    session (B091 A4) adds beside :func:`compute_liveness_calibration`
    -- itself now also built on the same generator, below).

    (B091 round-1 B2) The traversal itself now lives in :func:`_iter_events`,
    which yields EVERY event kind; this function is the `test`-only filter
    over it. `test` still means the `call` phase and nothing else, so every
    reader keyed on it -- :func:`baseline_slowest_test_s`,
    :func:`baseline_test_events`, :func:`count_test_events` -- keeps its
    exact pre-B2 meaning now that the plugin also records `setup`/`teardown`
    (as `phase`) and the two session brackets.
    """
    yield from _selected_test_events(events_path)


def baseline_slowest_test_s(baseline_events_path: "Path | None") -> float | None:
    """The slowest well-typed `duration_s` among *baseline_events_path*'s
    own `test` events, or `None` when there is no such event to measure
    (see :func:`_iter_test_events` for what "no such event" covers).

    The SAME computation :func:`compute_expect_next_event_within_s` already
    made internally before this function existed -- extracted here so a
    caller that also needs the raw `slowest_test_s` figure (the `plan`
    progress event's own new field, B091 A4) reads it back from this ONE
    function rather than hand-rolling a second, independent parse of the
    same file (B088's own "two derivations drift" lesson).
    """
    slowest_test_s: float | None = None
    for record in _iter_test_events(baseline_events_path):
        duration = record.get("duration_s")
        if not is_real(duration):
            continue
        if slowest_test_s is None or duration > slowest_test_s:
            slowest_test_s = duration
    return slowest_test_s


def baseline_test_events(baseline_events_path: Path) -> list[dict[str, Any]]:
    """Every `test` event in *baseline_events_path*, in file order, as the
    exact ``{nodeid, outcome, duration_s}`` triple the plugin wrote --
    B091 A4's own baseline-forwarding rule: these are translated verbatim
    onto the progress stream as one ``test`` event per baseline test, the
    BASELINE only, never per candidate (RW-33). `[]` for a `None`, absent,
    unreadable, or event-less path -- see :func:`_iter_test_events`.
    """
    return [
        {
            "nodeid": record.get("nodeid"),
            "outcome": record.get("outcome"),
            "duration_s": record.get("duration_s"),
        }
        for record in _iter_test_events(baseline_events_path)
    ]


def _finite_number(value: Any) -> float | int | None:
    """*value* when it is a finite int or float (a bool never is), else
    ``None``. An int too large for a float is not finite for this purpose."""
    import math

    if not is_real(value):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _phase_duration(record: Mapping[str, Any]) -> float | int | None:
    duration = _finite_number(record.get("duration_s"))
    if duration is None or duration < 0:
        return None
    return duration


def baseline_phase_durations(path: Path | None) -> list[dict[str, float | None]]:
    """``{"setup_s", "teardown_s"}`` for each event
    :func:`baseline_test_events` returns, aligned one-to-one and in order.

    Both lists come from :func:`_owner_stream`. Records are paired
    POSITIONALLY in the owner's stream, never summed per nodeid: a call's
    ``setup_s`` is the nearest preceding unconsumed ``setup`` phase record for
    its nodeid (a setup that never reached a call is left unpaired and cannot
    shift later pairings), and its ``teardown_s`` is the next following
    ``teardown`` phase record for that nodeid before the nodeid's next call.
    A missing, non-numeric, bool, negative or non-finite duration is ``None``.
    """
    rows: list[dict[str, float | None]] = []
    setups: dict[Any, float | int | None] = {}
    awaiting_teardown: dict[Any, dict[str, float | None]] = {}
    for record in _owner_stream(path):
        nodeid = record.get("nodeid")
        is_call = record.get("event") == TEST_EVENT
        if not isinstance(nodeid, str):
            if is_call:  # Nothing can pair with a call that has no usable nodeid.
                rows.append({"setup_s": None, "teardown_s": None})
            continue
        if is_call:
            row: dict[str, float | None] = {
                "setup_s": setups.pop(nodeid, None),
                "teardown_s": None,
            }
            rows.append(row)
            awaiting_teardown[nodeid] = row
        elif record.get("when") == "setup":
            setups[nodeid] = _phase_duration(record)
        elif record.get("when") == "teardown" and nodeid in awaiting_teardown:
            awaiting_teardown.pop(nodeid)["teardown_s"] = _phase_duration(record)
    return rows


def plugin_events_observed(events_path: Path | None) -> bool:
    """(P4/S3) Whether the BASELINE's events file carries at least one plugin
    record -- the one precondition of arming the progress watch that only the
    baseline can answer.

    "Plugin injected" is the request; "the plugin actually ran and wrote
    something" is the fact. A lane whose plugin was materialized but never
    loaded (pytest could not import it, a `-p no:...` in addopts, an early
    collection error) has no progress signal at all, and its per-candidate
    budget must therefore keep binding rather than be demoted to advice.
    `False` for a `None`, absent, unreadable or empty path.
    """
    return next(_iter_events(events_path), None) is not None


def baseline_per_test_seconds(path: Path | None) -> dict[str, float]:
    """(P4/S2) ``{nodeid: setup+call+teardown seconds}`` from the BASELINE's
    own events file -- the per-test baseline the per-test CPU bound is
    derived from.

    Keep the LAST duration for each phase of a nodeid, then sum its setup,
    call and teardown values. A repeated nodeid therefore replaces the prior
    duration for the phase it reports, rather than adding another whole run
    to the same baseline. A missing, non-numeric, bool, negative or
    non-finite duration contributes nothing -- an under-measured baseline
    can only make the bound TIGHTER, so callers that need a trustworthy
    number must check the nodeid is present at all (an absent nodeid has no
    bound; see the per-test window arming rule).

    `{}` for a `None`, absent, unreadable or event-less path.
    """
    phases: dict[str, dict[str, float | None]] = {}
    for record in _owner_stream(path):
        nodeid = record.get("nodeid")
        if not isinstance(nodeid, str) or not nodeid:
            continue
        kind = record.get("event")
        if kind == TEST_EVENT:
            phase = "call"
        elif kind == PHASE_EVENT and record.get("when") in {
            "setup",
            "teardown",
        }:
            phase = str(record["when"])
        else:
            continue
        # Assign even an invalid final duration as None so a repeated nodeid
        # cannot silently fall back to an earlier run's value for that phase.
        phases.setdefault(nodeid, {})[phase] = (
            None
            if (duration := _phase_duration(record)) is None
            else float(duration)
        )
    return {
        nodeid: sum(value for value in durations.values() if value is not None)
        for nodeid, durations in phases.items()
        if any(value is not None for value in durations.values())
    }


#: The per-candidate resource sidecar, written next to the events file by
#: :meth:`LivenessRunner._monitor` on every exit path. Diagnostic only.
RESOURCE_SIDECAR_SUFFIX = ".resources.json"


def read_resource_sidecar(events_path: Path) -> dict[str, Any] | None:
    """The parsed resource sidecar of *events_path*, or ``None`` when it is
    absent, unreadable, malformed, not an object, or not ``format`` 1."""
    try:
        parsed = json.loads(
            events_path.with_suffix(RESOURCE_SIDECAR_SUFFIX).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict) or parsed.get("format") != 1:
        return None
    return parsed


def first_event_times(events_path: "Path | None") -> tuple[float | None, float | None]:
    """``(t of the first session_start, t of the owner's first test record)``.

    Each is ``None`` when the record is missing or its ``t`` is not a finite
    int or float (a bool is not).
    """
    session_start = next(
        (record for record in _iter_events(events_path) if record.get("event") == SESSION_START_EVENT),
        None,
    )
    tests = _selected_test_events(events_path)
    return (
        None if session_start is None else _finite_number(session_start.get("t")),
        _finite_number(tests[0].get("t")) if tests else None,
    )


def count_test_events(events_path: "Path | None") -> int:
    """How many `test` events *events_path* carries -- B091 A4's own
    per-candidate `tests_completed` progress field. For a fully stamped xdist
    file this is the owner process's records, where the owner is the process
    that wrote the first `session_start`; this avoids counting controller
    reports and worker reports twice without deduplicating nodeids. `0` for a
    `None`, absent, unreadable, or event-less path -- see
    :func:`_iter_test_events`.
    """
    return sum(1 for _ in _iter_test_events(events_path))


def first_failing_test(events_path: "Path | None") -> str | None:
    """B205: the node id of the first `test` event the plugin recorded with a
    ``failed`` outcome, in the owner stream's order, or ``None`` when the file
    is absent or no failed test was reported (a collection error, a crash
    before any test, a lane without the plugin).

    It is the first failure the suite REPORTED, which is what a kill is
    attributed to; with ``-x`` it is also the failure that stopped the run.
    """
    for record in _selected_test_events(events_path):
        nodeid = record.get("nodeid")
        if record.get("outcome") == "failed" and isinstance(nodeid, str) and nodeid:
            return nodeid
    return None


class BaselineEventGaps(NamedTuple):
    """(B091 round-1 B2) What the BASELINE's own events file says about how
    long this suite goes QUIET, which is the only question the monitoring
    loop actually asks.

    *worst_gap_s* is the largest interval between consecutive stamped records
    within any one producer pid timeline. Each timeline is ordered by its
    event timestamp before its gaps are measured, and the largest process-local
    gap wins. Because the plugin brackets the session with `session_start`
    (from `pytest_configure`, before collection) and `session_finish`, that gap
    set INCLUDES the leading gap (collection plus whatever session/module
    fixture the first test needs) and the trailing gap (session teardown).
    Those two are exactly the stretches the pre-B2 `call`-duration calibration
    could not see.

    *leading_gap_s* is the first interval on the process that owns the first
    `session_start` -- i.e. how long this suite takes to produce its first
    sign of life. It calibrates the window before a CANDIDATE has produced
    any event at all. When that owner identity is unavailable (an older
    baseline, a malformed/mixed pid set, or a plugin whose `pytest_configure`
    write failed) there is no honest leading measurement, so this falls back
    to *worst_gap_s*, the conservative choice -- never a tighter bound
    derived from an interval that is not the leading one.
    """

    worst_gap_s: float
    leading_gap_s: float


class LivenessCalibration(NamedTuple):
    """(B091 round-1 B2) Everything :class:`LivenessRunner` and the `plan`
    progress event need about a lane's idle expectations, from ONE parse of
    ONE file -- never two hand-rolled derivations of the same measurement
    (B088's own "two derivations drift" lesson).
    """

    #: The steady-state bound: how long the candidate may go without any
    #: plugin event before the CPU-growth check gets to call it `hung`.
    expect_next_event_within_s: float
    #: The bound that applies before the candidate's FIRST event.
    pre_first_event_within_s: float
    #: The raw measurement `expect_next_event_within_s` was derived from --
    #: `None` on the plugin-inactive fallback path, where no gap was
    #: observed at all. Disclosed on the `plan` progress event.
    worst_gap_s: float | None
    #: The slowest `call`-phase duration, unchanged in meaning from before
    #: B2 (it is NOT what the bound is derived from any more -- see
    #: :data:`worst_gap_s`). Disclosed on the `plan` progress event because
    #: it is still the figure an operator reads as "the slowest test".
    slowest_test_s: float | None
    #: The declared/effective idle floor passed to this calibration.
    idle_floor_s: float
    #: (P4/S1) ``max(3 x baseline_wall, baseline_wall + 60 s)`` PROCESS-TREE
    #: CPU seconds -- the bound since the candidate's last COMPLETED plugin
    #: event (or since its start). The same derived formula that bounds a
    #: candidate's wall time, applied to the one series that cannot be paused
    #: by host pressure.
    cpu_without_progress_bound_s: float
    #: (P4/S2) ``{nodeid: setup+call+teardown seconds}`` from the baseline --
    #: the per-test bound's own input. `{}` when there is no baseline
    #: measurement, which disables the per-test bound and leaves S1 as the
    #: backstop.
    per_test_baseline_s: Mapping[str, float]


def baseline_event_gaps(
    baseline_events_path: "Path | None",
) -> "BaselineEventGaps | None":
    """The BASELINE's own worst and leading inter-event gaps, or `None` when
    fewer than two stamped records exist to form a single gap (a `None`,
    absent, unreadable or near-empty path -- see :func:`_iter_events`).

    Gaps are clamped at zero: `t` is wall-clock `time.time()` in the
    candidate's own interpreter, so a clock step backwards during a run must
    read as "no time passed", never as a negative gap that would drag the
    maximum down.
    """
    records = list(_iter_events(baseline_events_path))
    stamps: list[float] = []
    first_event: Any = None
    timestamped: list[tuple[float, Any, int | None]] = []
    for record in records:
        # (P4/S2) `test_start` is deliberately not a calibration stamp: a
        # "this test began" report inside a quiet stretch would shorten a gap
        # that never actually closed, silently tightening every lane's idle
        # bound. `test_finish` is a real timestamp after teardown and can add
        # an inter-event gap; only files differing by `test_start` records
        # alone retain byte-identical calibration.
        if record.get("event") == TEST_START_EVENT:
            continue
        stamp = record.get("t")
        if not is_real(stamp):
            continue
        if not stamps:
            first_event = record.get("event")
        stamp_float = float(stamp)
        stamps.append(stamp_float)
        timestamped.append((stamp_float, record.get("event"), _record_pid(record)))
    if len(stamps) < 2:
        return None

    # A legacy or mixed identity set must retain the old merged timeline. In
    # particular, a malformed pid is not permission to drop a record before
    # measuring it.
    if any(pid is None for _, _, pid in timestamped):
        gaps = [
            max(0.0, later - earlier)
            for earlier, later in zip(stamps, stamps[1:])
        ]
        worst_gap_s = max(gaps)
        leading_gap_s = gaps[0] if first_event == SESSION_START_EVENT else worst_gap_s
        return BaselineEventGaps(worst_gap_s=worst_gap_s, leading_gap_s=leading_gap_s)

    by_pid: dict[int, list[tuple[float, Any]]] = {}
    for stamp, event, pid in timestamped:
        assert pid is not None
        by_pid.setdefault(pid, []).append((stamp, event))
    for events in by_pid.values():
        events.sort(key=lambda item: item[0])
    per_pid_gaps: dict[int, list[float]] = {
        pid: [
            max(0.0, later[0] - earlier[0])
            for earlier, later in zip(events, events[1:])
        ]
        for pid, events in by_pid.items()
        if len(events) >= 2
    }
    # If every stamped process contributed only one record, no per-process
    # interval exists. Keep the established merged result rather than
    # turning a populated file into a false "no measurement" fallback.
    if not per_pid_gaps:
        gaps = [
            max(0.0, later - earlier)
            for earlier, later in zip(stamps, stamps[1:])
        ]
        worst_gap_s = max(gaps)
        leading_gap_s = gaps[0] if first_event == SESSION_START_EVENT else worst_gap_s
        return BaselineEventGaps(worst_gap_s=worst_gap_s, leading_gap_s=leading_gap_s)

    worst_gap_s = max(max(gaps) for gaps in per_pid_gaps.values())
    owner_pid = _session_owner_pid(records)
    owner_events = by_pid.get(owner_pid, []) if owner_pid is not None else []
    if len(owner_events) >= 2:
        owner_gaps = per_pid_gaps[owner_pid]
        leading_gap_s = (
            owner_gaps[0]
            if owner_events[0][1] == SESSION_START_EVENT
            else max(owner_gaps)
        )
    else:
        # No stamped first session-start owner with a measurable interval is
        # available, so the leading interval cannot honestly be identified.
        leading_gap_s = worst_gap_s
    return BaselineEventGaps(worst_gap_s=worst_gap_s, leading_gap_s=leading_gap_s)


def compute_liveness_calibration(
    baseline_events_path: "Path | None",
    baseline_s: float,
    *,
    idle_floor_s: float | None = None,
) -> LivenessCalibration:
    """RW-33's `expect_next_event_within_s`, as re-derived by RW-49/D3
    calibration (a): ``max(3 x the baseline's worst observed inter-event
    gap, 15s)``, with ``max(3 x the baseline's leading gap, 15s)`` governing
    the window before the candidate's first event -- else the documented
    plugin-inactive fallback ``max(60s, baseline_s / 4)`` for both.

    **Why gaps and not `call` durations (round-1 blocker B2).** The pre-B2
    formula was ``max(3 x slowest_test_s, 15s)`` over `call` durations only.
    The monitor's question is not "how long does a test body take" but "how
    long does this suite go SILENT", and a suite goes silent during
    collection, during session/module fixture setup and during teardown --
    none of which produce a `call` report. On a measured real project whose
    module-scoped fixture slept 40s, `slowest_test_s` was 0.0004 and the
    bound collapsed to its 15s floor, so every candidate was killed as
    `hung` at ~31s -- including candidates the suite was about to kill
    honestly. Gaps between the plugin's own stamped records measure the
    silence directly, and the two session brackets make the first and last
    stretch measurable too.

    **Why the pre-first-event bound is separate, and tighter.** Once the
    plugin is loaded, `session_start` arrives within an interpreter start-up
    of the spawn, so a candidate that has produced NOTHING is a candidate
    whose `pytest_configure` never completed -- a much stronger signal than
    an ordinary quiet stretch mid-run, and one the baseline's own leading
    gap (collection, which happens strictly after `session_start`) already
    over-estimates. It can never fire early on its own regardless:
    :class:`LivenessRunner` needs :data:`_HUNG_CPU_WINDOW_S` of flat-CPU
    history before it may declare anything `hung`, so 30s is the real floor
    under every kill.

    **The fallback path is deliberately coarse.** *baseline_events_path* is
    `None` when the baseline was never wired with `ASSAY_LIVENESS_EVENTS`,
    and carries too few stamped records when the plugin never loaded or
    never ran. There is then no measurement to calibrate against and the
    only remaining progress signal is growth of the candidate's stdout/
    stderr FILES -- which, for a pytest lane under its default global
    capture, stays flat at 0 bytes until the run ends. A plugin-less lane
    therefore gets COARSE liveness only: the `max(60s, baseline_s/4)` bound,
    the 30s CPU window, and the elapsed budget -- documented as such in
    CONSUMERS.md so no consumer reads an all-`hung` sweep as a measurement.
    """
    effective_idle_floor_s = (
        LIVENESS_IDLE_FLOOR_S if idle_floor_s is None else idle_floor_s
    )
    gaps = baseline_event_gaps(baseline_events_path)
    slowest_test_s = baseline_slowest_test_s(baseline_events_path)
    if gaps is None:
        fallback = max(LIVENESS_FALLBACK_FLOOR_S, baseline_s / 4.0)
        return LivenessCalibration(
            expect_next_event_within_s=fallback,
            pre_first_event_within_s=fallback,
            worst_gap_s=None,
            slowest_test_s=slowest_test_s,
            idle_floor_s=effective_idle_floor_s,
            cpu_without_progress_bound_s=derived_bound_from_baseline(baseline_s),
            # No stamped plugin records means no per-test measurement either;
            # an empty mapping disables the per-test bound rather than
            # inventing timings from a file that carries none.
            per_test_baseline_s={},
        )
    return LivenessCalibration(
        expect_next_event_within_s=max(
            3.0 * gaps.worst_gap_s, effective_idle_floor_s
        ),
        pre_first_event_within_s=max(
            3.0 * gaps.leading_gap_s, effective_idle_floor_s
        ),
        worst_gap_s=gaps.worst_gap_s,
        slowest_test_s=slowest_test_s,
        idle_floor_s=effective_idle_floor_s,
        cpu_without_progress_bound_s=derived_bound_from_baseline(baseline_s),
        # (P4/S2) The per-test bound's own baseline: the summed phase
        # durations per nodeid. Read from the SAME file this call already
        # parsed for the gaps, so the two can never describe different
        # baselines.
        per_test_baseline_s=baseline_per_test_seconds(baseline_events_path),
    )


def _read_events_progress(
    events_path: Path, previous_count: int, candidate_pid: int | None = None
) -> tuple[int, bool]:
    """Re-read *events_path* end to end and return ``(valid_record_count,
    saw_session_finish)``. Tolerant of a torn last line (``json.loads``
    failure is skipped, never raised) exactly like
    :func:`compute_liveness_calibration`.

    (B091 round-1 B2) The count is over EVERY event kind -- `session_start`,
    `phase` (`setup`/`teardown`), `test` (`call`) and `session_finish` alike
    -- because the monitoring loop's question is "did this candidate do
    anything since the last tick", and a `setup` report is as good an answer
    as a `call` report. Calibration and the full-file helpers use
    :func:`_iter_events` so their complete event vocabulary is parsed in one
    place. The monitor uses :class:`_EventProgressReader`, whose incremental
    parser preserves this helper's valid-record count and finish-pid semantics.

    *candidate_pid* is the real candidate process's pid, when the monitor has
    one. If every ``session_finish`` record is stamped, only that pid's finish
    qualifies. An incomplete finish identity set falls back to the old
    any-finish interpretation so old and mixed files remain usable.

    *previous_count* is unused
    by this function itself -- it exists only so a caller unable to import
    both this function's return convention and its own bookkeeping in one
    line can compare the two counts inline; kept as a parameter (rather than
    dropped) to keep every call site's own diff small and self-explanatory.
    """
    del previous_count  # Documented above: comparison is the CALLER's job.
    records = list(_iter_events(events_path))
    finishes = [
        record for record in records if record.get("event") == SESSION_FINISH_EVENT
    ]
    saw_session_finish = bool(finishes)
    candidate_pid = _valid_pid(candidate_pid)
    if candidate_pid is not None and finishes and all(
        _record_pid(record) is not None for record in finishes
    ):
        saw_session_finish = any(
            _record_pid(record) == candidate_pid for record in finishes
        )
    return len(records), saw_session_finish


class _EventProgressReader:
    """Track one append-only candidate event file without rescanning it.

    The public-to-this-module ``_read_events_progress`` helper remains the
    full-file reference used by readers outside the monitor. A running
    candidate gets one of these instead: each tick parses only bytes appended
    since its previous tick, while retaining an unterminated tail so a
    partially written final record can be completed on a later tick.

    Candidate files are unlinked before launch and the pytest plugin appends
    newline-terminated records. Device/inode changes, truncation, disappearance
    and read errors reset the file-local parser state, so the idle rule sees
    the current file's counts as it did with the full-file reader. Once a file
    with at least one record is disrupted, ``stream_broken`` stays true for
    this candidate run; the monitor disarms the event-derived P4 bounds. The
    counters and flags reset per file, while that fact alone survives reset.
    """

    def __init__(self, candidate_pid: int | None) -> None:
        self._candidate_pid = _valid_pid(candidate_pid)
        self.stream_broken = False
        self._reset()

    def _reset(
        self,
        path: Path | None = None,
        identity: tuple[int, int] | None = None,
    ) -> None:
        self._path = path
        self._identity = identity
        self._offset = 0
        self._pending = b""
        self._count = 0
        self._has_finish = False
        self._all_finish_pids_valid = True
        self._candidate_finish_seen = False
        #: The first stamped `session_start` identifies the process whose
        #: pytest hooks own this stream. Before it arrives, use the spawned pid.
        self._owner_pid: int | None = None
        #: (P4/S1) Per-file count of completed plugin events on the candidate's
        #: own timeline; a reset returns it to zero, like the valid-record count.
        self.completed_events = 0
        #: (P4/S2) ``(nodeid, completed_events_at_start, record_index)`` of the
        #: test currently in flight, or ``None``. Both counters are per-file.
        self.in_flight = None
        #: Per-file overlap: after a reset a new stream starts this false again.
        self.overlapping_tests = False

    def _mark_broken_if_records_seen(self) -> None:
        """Break only after a complete or valid unterminated record was read."""
        valid_tail = self._parse_line(self._pending.decode("utf-8")) is not None
        if self._count or valid_tail:
            self.stream_broken = True

    @staticmethod
    def _parse_line(line: str) -> dict[str, Any] | None:
        stripped = line.strip()
        if not stripped:
            return None
        try:
            record = json.loads(stripped)
        except (ValueError, RecursionError):
            return None  # Tolerant of a torn or malformed line.
        return record if isinstance(record, dict) else None

    def _consume(self, line: str) -> None:
        record = self._parse_line(line)
        if record is None:
            return
        self._count += 1
        self._track_progress(record)
        if record.get("event") != SESSION_FINISH_EVENT:
            return
        self._has_finish = True
        record_pid = _record_pid(record)
        if record_pid is None:
            self._all_finish_pids_valid = False
        elif record_pid == (self._owner_pid or self._candidate_pid):
            self._candidate_finish_seen = True

    def _track_progress(self, record: Mapping[str, Any]) -> None:
        """(P4/S1,S2) Date the two CPU bounds against THIS record.

        The first stamped `session_start` identifies the pytest session
        owner, which can be a child of the process Assay spawned (for example
        `timeout ... python -m pytest`). Until that record arrives, the
        spawned pid is the fallback. This is the same owner-selection rule as
        :func:`_owner_stream`; records from a nested pytest or xdist worker do
        not enter the owner's timeline. Unstamped records retain the merged
        interpretation because they cannot be attributed to a process.
        """
        kind = record.get("event")
        record_pid = _record_pid(record)
        if kind == SESSION_START_EVENT and self._owner_pid is None:
            self._owner_pid = _session_owner_pid((record,))
        owner_pid = self._owner_pid or self._candidate_pid
        if (
            owner_pid is not None
            and record_pid is not None
            and record_pid != owner_pid
        ):
            # Another process's report (an xdist worker under the coordinator,
            # a nested pytest). An UNSTAMPED record keeps the legacy merged
            # interpretation, exactly like `_owner_stream`.
            return
        if kind in COMPLETED_EVENT_KINDS:
            self.completed_events += 1
        elif kind == TEST_START_EVENT:
            nodeid = record.get("nodeid")
            if isinstance(nodeid, str) and nodeid:
                # (P4 fix 1, review finding 1) A start while a window is
                # still open proves two tests really were in flight. Every
                # start that follows a `test_finish` opens its OWN window --
                # `_count` keys it, so a rerun of one nodeid gets a new one.
                if self.in_flight is not None:
                    self.overlapping_tests = True
                self.in_flight = (nodeid, self.completed_events, self._count)
        elif kind == TEST_FINISH_EVENT:
            # (P4 fix 1, review finding 1) The MATCHING finish is the only
            # thing that closes a test's window: its setup/call/teardown
            # reports arrive while it still runs, and a finish for another
            # nodeid belongs to a different test.
            nodeid = record.get("nodeid")
            if (
                isinstance(nodeid, str)
                and nodeid
                and self.in_flight is not None
                and self.in_flight[0] == nodeid
            ):
                self.in_flight = None
        elif kind == SESSION_FINISH_EVENT:
            # Pytest can omit `pytest_runtest_logfinish` when a test aborts
            # with KeyboardInterrupt/pytest.exit. Once the owning session
            # finishes, no item remains in flight; keeping this sticky in the
            # reader prevents the monitor from reopening a per-test window on
            # the next poll during session teardown.
            self.in_flight = None

    def _summary(self) -> tuple[int, bool]:
        tail = self._parse_line(self._pending.decode("utf-8"))
        tail_finish = tail is not None and tail.get("event") == SESSION_FINISH_EVENT
        count = self._count + (tail is not None)
        has_finish = self._has_finish or tail_finish
        all_finish_pids_valid = self._all_finish_pids_valid
        candidate_finish_seen = self._candidate_finish_seen
        if tail_finish:
            assert tail is not None
            tail_pid = _record_pid(tail)
            if tail_pid is None:
                all_finish_pids_valid = False
            elif tail_pid == (self._owner_pid or self._candidate_pid):
                candidate_finish_seen = True
        saw_finish = has_finish
        if (
            (self._owner_pid is not None or self._candidate_pid is not None)
            and has_finish
            and all_finish_pids_valid
        ):
            saw_finish = candidate_finish_seen
        return count, saw_finish

    def read(self, events_path: Path) -> tuple[int, bool]:
        """Return the current valid-record count and finish status."""
        try:
            with events_path.open("rb") as stream:
                # The path may be replaced after opening. Only metadata from
                # the descriptor whose bytes we will read can identify this
                # poll's file and detect a truncation before using the offset.
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if (
                    events_path != self._path
                    or identity != self._identity
                    or stat.st_size < self._offset
                ):
                    self._mark_broken_if_records_seen()
                    self._reset(events_path, identity)
                stream.seek(self._offset)
                appended = stream.read()
        except OSError:
            self._mark_broken_if_records_seen()
            self._reset()
            return 0, False
        self._offset += len(appended)
        lines = (self._pending + appended).decode("utf-8").splitlines(keepends=True)
        if lines and not self._has_line_ending(lines[-1]):
            self._pending = lines.pop().encode("utf-8")
        else:
            self._pending = b""
        for line in lines:
            self._consume(line)
        return self._summary()

    @staticmethod
    def _has_line_ending(line: str) -> bool:
        """Match every boundary recognized by ``str.splitlines()``."""
        return bool(line) and line[-1] in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


class _ResourceTrace:
    """A bounded trace of resource samples that stays VALID at any length.

    P4/S1: the retained trace used to be capped at
    :data:`_LIVENESS_RESOURCE_TRACE_MAX_SAMPLES` and, once past it, marked
    ``trace_truncated``/``trace_complete=False`` -- which silently disabled
    EVERY hang verdict for a candidate that ran longer than about 17 minutes
    of 1 s polls, including one that went idle afterwards. A cap must bound
    the stored artifact, never the verdict.

    Compaction MERGES adjacent known rows (oldest pair first), rather than
    dropping rows: dropping one would break ``mutation_store``'s pairwise
    re-validation of the stored evidence. A compacted `stalled` row can cover
    clear time plus pressure deltas; its eligible interval remains the exact
    difference between the retained endpoints.

    * the merged row keeps the newer row's endpoints
      (``wall_elapsed_s``/``eligible_elapsed_s``), CPU reading, progress
      counters and resource snapshot, so every OTHER retained row's delta
      arithmetic against its own predecessor is unchanged;
    * ``eligible_interval_s`` is recomputed from the retained endpoints
      (``newer.eligible - predecessor.eligible``), never as a sum of two
      rounded intervals, so repeated merges cannot accumulate drift;
    * two `clear` rows merge into a `clear` row whose (empty)
      ``resource_deltas`` stay empty -- no counter moved, so the span's
      endpoints compare clear too;
    * any pair containing a `stalled` row merges into a `stalled` row whose
      deltas are the SUM of the pair's positive deltas -- exactly what
      ``compare_resource_snapshots(predecessor, newer_snapshot)`` returns for
      monotone counters. When a clear interval is part of that span,
      ``eligible_interval_s`` retains its clear-time contribution while the
      resource deltas disclose that some pressure also occurred. The store
      validator accepts that mixed summary only when ``trace_compacted`` is
      true;
    * an `unknown` row is never merged (it can only be the first row, and it
      may carry ``previous_resources``), and **rows 0 and 1 are never merge
      sources**, so the opening anchor and first post-anchor interval stay
      byte-identical. Compaction always chooses the oldest middle pair, which
      leaves the latest 1021 original samples intact at the 1024-row cap; those
      rows contain the trailing
      CPU window needed by the validator (row 1 is also the earliest CPU point
      when that first interval is clear).

    ``compacted``/``merged`` disclose that older rows are summaries. Once the
    opening two anchors are present, every adjacent pair of known rows can be
    merged, so the retained trace stays within its configured cap.
    """

    def __init__(self, max_samples: int | None = None) -> None:
        self._samples: list[dict[str, Any]] = []
        self.compacted = False
        self.merged = 0
        self.max_samples = max_samples

    def _cap(self) -> int:
        # Read the module global at CALL time so a test can monkeypatch it,
        # exactly like the retired inline comparison did.
        return (
            _LIVENESS_RESOURCE_TRACE_MAX_SAMPLES
            if self.max_samples is None
            else self.max_samples
        )

    def reset(self, sample: dict[str, Any] | None = None) -> None:
        self._samples = []
        self.compacted = False
        self.merged = 0
        if sample is not None:
            self._samples.append(sample)

    @property
    def samples(self) -> list[dict[str, Any]]:
        return self._samples

    def append(self, sample: dict[str, Any]) -> None:
        self._samples.append(sample)
        cap = self._cap()
        while len(self._samples) > cap:
            if not self._merge_one():
                break

    def _merge_one(self) -> bool:
        # Rows 0 and 1 are NEVER merge sources: row 0 anchors the evidence
        # window (`idle_eligible_s` and the first interval's
        # `previous_resources` are read off it) and row 1 is the first
        # post-anchor interval. Searching from index 2 compacts the oldest
        # middle pair first and leaves the latest 1021 original rows intact
        # at the 1024-row cap; those are the rows that prove the trailing CPU window
        # really spans a full
        # `_HUNG_CPU_WINDOW_S`.
        for index in range(2, len(self._samples) - 1):
            older = self._samples[index]
            newer = self._samples[index + 1]
            older_kind = older.get("resource_interval")
            newer_kind = newer.get("resource_interval")
            if older_kind not in ("clear", "stalled") or newer_kind not in (
                "clear",
                "stalled",
            ):
                continue
            kind = (
                "stalled"
                if "stalled" in (older_kind, newer_kind)
                else "clear"
            )
            merged: dict[str, Any] = dict(newer)
            merged["resource_interval"] = kind
            merged["eligible_interval_s"] = round(
                float(newer["eligible_elapsed_s"])
                - float(self._samples[index - 1]["eligible_elapsed_s"]),
                6,
            )
            if kind == "clear":
                merged["resource_deltas"] = {}
            else:
                merged["resource_deltas"] = {
                    key: int(older.get("resource_deltas", {}).get(key, 0))
                    + int(newer.get("resource_deltas", {}).get(key, 0))
                    for key in set(older.get("resource_deltas", {}))
                    | set(newer.get("resource_deltas", {}))
                }
            self._samples[index] = merged
            del self._samples[index + 1]
            self.compacted = True
            self.merged += 1
            return True
        return False


class _PerTestWindow(NamedTuple):
    """(P4/S2) One in-flight test's own CPU window.

    *started_cpu* is the MONOTONIC process-tree series at the instant the
    candidate reported that it began this nodeid, so the bound measures that
    test's own CPU and not the suite's; *baseline_s* is the sum of its
    baseline setup+call+teardown durations. *start_index* is the reader's
    record index of the `test_start` that OPENED this window, so a rerun of
    one nodeid (a retry, a repeated parameter) opens a NEW window rather than
    inheriting the first run's already-spent CPU.
    """

    nodeid: str
    baseline_s: float
    started_cpu: float | None
    started_wall: float
    start_index: int


def per_test_bound_enabled(
    *, per_test_xdist: bool, overlapping: bool = False
) -> bool:
    """Whether the per-test CPU bound may arm for this run.

    Distribution makes "one test in flight" false: several workers run tests
    concurrently, their plugin records are stamped with their OWN pids, and
    this reader deliberately keeps only the candidate owner's timeline (see
    :meth:`_EventProgressReader._track_progress`). So under xdist there is no
    measurable in-flight test to bound and the process-tree bound (S1) is the
    only one. A candidate whose file shows more than one in-flight test_start
    before a completion is skipped for the same reason, at runtime.
    """
    return not per_test_xdist and not overlapping


class _CpuSampleHistory:
    """Keep only samples that can still be the trailing CPU-window edge."""

    def __init__(self, window_s: float) -> None:
        self._window_s = window_s
        self._samples: deque[tuple[float, float]] = deque()

    def add(self, now: float, cpu_seconds: float) -> float | None:
        """Append a successful CPU reading and return the window baseline."""
        # The sum of live process-tree CPU can fall when a busy descendant
        # exits. A lower reading does not prove the tree was quiet: it breaks
        # comparability with every earlier sample in this window.
        if self._samples and cpu_seconds < self._samples[-1][1]:
            self._samples.clear()
        self._samples.append((now, cpu_seconds))
        # Retain the newest sample that is at least one window old. The next
        # sample is also old enough exactly when the oldest one has become
        # obsolete, so one left-pop preserves the reverse-list search's
        # boundary choice without keeping older samples.
        while len(self._samples) > 1 and now - self._samples[1][0] >= self._window_s:
            self._samples.popleft()
        oldest_t, oldest_cpu = self._samples[0]
        if now - oldest_t >= self._window_s:
            return oldest_cpu
        return None

    def reset(self) -> None:
        """Forget samples across an interval whose resource evidence is unknown."""
        self._samples.clear()


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def candidate_events_path(events_dir: Path, cwd: Path) -> Path:
    """The events NDJSON file :class:`LivenessRunner` writes for a candidate
    whose mutant snapshot's own execution ``cwd`` is *cwd*, under
    *events_dir* -- the lane's own PERSISTENT ``.assay/liveness/
    candidates/`` directory, never the ephemeral per-mutant snapshot
    itself (which is torn down before this sweep's own progress records
    are built, well after this path still needs to resolve to the same
    file).

    A free function, not a method, so :class:`LivenessRunner` (which only
    ever WRITES here) and :mod:`assay.mutation` (which only ever READS
    back afterwards, for B091 A4's own `tests_completed` progress field)
    compute the SAME path from the SAME one implementation -- never two
    independent hashes of the same *cwd* that could drift apart from each
    other (B088's own "two derivations drift" lesson, applied to a path
    computation rather than a measurement).
    """
    digest = hashlib.sha256(str(cwd).encode("utf-8")).hexdigest()[:16]
    return events_dir / f"{digest}.ndjson"


class LivenessRunner:
    """A :class:`~assay.runner.ProcessRunner` used for R2 CANDIDATE execution
    ONLY (never the R0 baseline, never any other lane or language -- "other
    runners untouched" per the handoff).

    **A3 (this session, superseding v1's blocking env-stamp-then-delegate
    scope).** Launches the candidate NON-BLOCKING
    (``subprocess.Popen(start_new_session=True)``, stdio to files -- never
    pipes, RW-33 is explicit), then polls once a second: (a) has the process
    exited; (b) the events side file's newest `test`/`session_finish`
    record, tolerant of a torn last line; (c) the process TREE's CPU time
    (:func:`tree_cpu_seconds`; a `/proc` failure is still treated as unknown,
    never as proof of flat CPU); (d) cumulative host/candidate-cgroup PSI and
    cgroup CPU-throttle counters; (e) the plain elapsed-budget bound,
    enforced by this loop itself now that it owns the launch
    (`process_runner`'s caller-side ``subprocess.run(timeout=...)`` no longer
    applies once `Popen` replaces it).

    Resource counters are aggregate observations, not a measurement of this
    process's lost runtime. A `hung` result therefore requires a complete
    trace with unchanged pressure/throttle counters across the eligible idle
    window. Any observed pressure, missing source, or counter reset pauses
    both the liveness clock and CPU-growth window; the monitor never prorates
    a host/cgroup delta into invented candidate time. If the independent wall
    budget expires before a clean window exists, the candidate is explicitly
    `budget_exceeded` with its available evidence, not assigned a functional
    mutation result.

    `hung` iff: no progress (a NEW plugin event of ANY kind --
    `session_start`, `phase`, `test` or `session_finish`, B091 round-1 B2 --
    OR growth of the candidate's stdout/stderr FILES, the "plugin inactive"
    fallback RW-33 names, folded in as an extra progress signal rather than
    a separate code path, since either one is sufficient evidence the
    candidate is still doing something) for the applicable bound
    (`pre_first_event_within_s` until the candidate's first event,
    `expect_next_event_within_s` after it) AND the tree's CPU grew less
    than :data:`_HUNG_CPU_GROWTH_FLOOR_S` over the trailing
    :data:`_HUNG_CPU_WINDOW_S`; OR a `session_finish` event was seen and the
    process is still alive :data:`_HUNG_SESSION_FINISH_GRACE_S` later with
    no event or stdout/stderr growth for that whole grace AND the same full
    trailing CPU window is quiet (RW-57: an xdist worker's finish must not
    expire a candidate still reporting progress or consuming CPU).
    Both branches require the relevant idle window to have complete resource
    evidence and advance only through intervals with no observed pressure.
    On `hung`: kills the whole process group and raises
    :class:`LivenessHungExpired`. On plain elapsed-budget expiry (a CPU-
    spinning mutant, RW-33 is explicit this is NOT hung): kills the same way
    and raises plain ``subprocess.TimeoutExpired`` -- unchanged meaning from
    before this class existed.

    Every dependency the loop needs from the outside world --
    :func:`time.monotonic`, :func:`time.sleep`, :func:`tree_cpu_seconds`,
    ``subprocess.Popen`` itself -- is a constructor parameter with a real
    default, so a test can substitute a fake clock, a no-op sleep and a
    scripted CPU reader to drive the loop's DECISION logic deterministically
    (a fake ``popen`` returning a fake process object) or substitute only
    the clock/sleep/CPU-reader around a REAL, cheap ``subprocess.Popen(["sh",
    "-c", "sleep 0.01"])`` child for a closer-to-real integration check,
    without any test needing to wait out a real 15s/30s/60s threshold.

    **The file-growth fallback is coarse, by measurement, and that is
    disclosed rather than hidden (B091 round-1 B2).** The stdout/stderr
    signal reads the SIZE of the two side files, which is the only thing
    that can be read without a pipe -- but pytest under its default global
    capture writes nothing to the real fds until the run ends, so for the
    one runner liveness is restricted to those files stay at 0 bytes for the
    whole run. On a lane where the plugin IS active that costs nothing (the
    events file carries a record per phase). On a lane where it is not, the
    fallback is effectively inert and the candidate is governed by the
    coarse `max(60s, baseline_s/4)` bound, the 30s flat-CPU window and the
    elapsed budget alone -- stated in CONSUMERS.md so nobody reads such a
    lane's `hung` count as a fine-grained measurement.
    """

    def __init__(
        self,
        *,
        events_dir: Path,
        expect_next_event_within_s: float,
        #: (B091 round-1 B2) The bound that applies until the candidate's
        #: FIRST plugin event arrives -- `None` (the default) means "use
        #: *expect_next_event_within_s* for that window too", which is both
        #: the pre-B2 behaviour and what the plugin-inactive fallback path
        #: wants, since it has no separate leading measurement to offer.
        pre_first_event_within_s: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_s: float = _LIVENESS_POLL_INTERVAL_S,
        cpu_reader: Callable[[int], float] = tree_cpu_seconds,
        resource_reader: Callable[[int], Mapping[str, Any]] = read_liveness_resources,
        popen: Callable[..., Any] = subprocess.Popen,
        process_group_killer: Callable[[int, int], None] | None = None,
        cpu_window_s: float | None = None,
        cpu_without_progress_bound_s: float | None = None,
        per_test_cpu_baseline_s: Mapping[str, float] | None = None,
        per_test_xdist: bool = False,
        advisory_budget_s: float | None = None,
    ) -> None:
        self._events_dir = events_dir
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._expect_next_event_within_s = expect_next_event_within_s
        self._pre_first_event_within_s = (
            expect_next_event_within_s
            if pre_first_event_within_s is None
            else pre_first_event_within_s
        )
        self.cpu_window_s = _HUNG_CPU_WINDOW_S if cpu_window_s is None else cpu_window_s
        self.cpu_growth_floor_s = self.cpu_window_s * _HUNG_CPU_GROWTH_FRACTION
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval_s = poll_interval_s
        self._cpu_reader = cpu_reader
        self._resource_reader = resource_reader
        self._popen = popen
        # Injectable so tests using synthetic PIDs cannot signal an unrelated
        # real process group on the host. Resolve the default at call time, so
        # existing syscall-level tests can still monkeypatch os.killpg.
        self._process_group_killer = process_group_killer
        # (P4/S1) The CPU-without-progress bound, in process-tree CPU seconds
        # since the candidate's last COMPLETED plugin event (or its start).
        # `None` switches this bound OFF for every caller that does not have a
        # derived value -- a hand-built runner in another module must not
        # invent one, and the idle rule plus the wall budget are unchanged.
        self.cpu_without_progress_bound_s = cpu_without_progress_bound_s
        # (P4/S2) The per-test CPU bound's baseline: `{nodeid: seconds}` summed
        # from setup+call+teardown durations. `None` (or an empty mapping)
        # disables the per-test bound; an unknown nodeid is then governed by
        # S1's process-tree bound alone.
        self._per_test_cpu_baseline_s = dict(per_test_cpu_baseline_s or {})
        self._per_test_xdist = per_test_xdist
        # (P4/S3) `judge.mutation.budget_per_candidate`, when the no-progress
        # watch is armed for this run: an ADVISORY wall bound. It is never a
        # reason to end a candidate -- one WARN per runner (one lane run) when
        # it is first exceeded, and the candidate continues. Where the watch
        # is NOT armed (this argument is `None`), the caller passes the budget
        # as the ordinary `timeout` instead and it binds exactly as before.
        # If the FIRST CPU reading fails or the event stream later breaks, the
        # watch cannot remain armed, so this bound falls back to binding.
        self.advisory_budget_s = advisory_budget_s
        self.advisory_exceeded_at_s: float | None = None

    def assay_window_host_spec(self) -> Mapping[str, object] | None:
        """Describe a production-configured runner for an isolated host.

        Injected clock, process, or signal seams cannot be serialized
        faithfully. Such runners stay on the in-process path unless they
        provide their own explicit host protocol.
        """
        if (
            self._monotonic is not time.monotonic
            or self._sleep is not time.sleep
            or self._popen is not subprocess.Popen
            or self._cpu_reader is not tree_cpu_seconds
            or self._resource_reader is not read_liveness_resources
            or self._process_group_killer is not None
        ):
            return None
        return {
            "kind": "liveness",
            "events_dir": str(self._events_dir),
            "expect_next_event_within_s": self._expect_next_event_within_s,
            "pre_first_event_within_s": self._pre_first_event_within_s,
            "cpu_window_s": self.cpu_window_s,
            # (P4/S1,S2) The progress bounds travel with the runner: a worker
            # that rebuilt this runner WITHOUT them would watch for idle only
            # and let a spinning candidate run to the wall budget -- a
            # silently different execution policy, which is exactly what the
            # host protocol exists to avoid.
            "cpu_without_progress_bound_s": self.cpu_without_progress_bound_s,
            "per_test_cpu_baseline_s": dict(self._per_test_cpu_baseline_s),
            "per_test_xdist": self._per_test_xdist,
            # (P4/S3) The ADVISORY per-candidate wall bound travels with the
            # runner for the same reason: an isolated worker that dropped it
            # would enforce a budget this run decided to only warn about.
            "advisory_budget_s": self.advisory_budget_s,
        }

    def _events_path_for_cwd(self, cwd: Path) -> Path:
        return candidate_events_path(self._events_dir, cwd)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float | None,
    ) -> "subprocess.CompletedProcess[str]":
        argv_tuple = tuple(argv)
        events_path = self._events_path_for_cwd(cwd)
        # A resumed/re-submitted candidate must never read a PRIOR attempt's
        # stale events as "recent progress" -- each execution starts every
        # side file (events, stdout, stderr) fresh.
        for stale in (
            events_path,
            events_path.with_suffix(".stdout"),
            events_path.with_suffix(".stderr"),
            events_path.with_suffix(RESOURCE_SIDECAR_SUFFIX),
        ):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        stamped_env = dict(env)
        stamped_env[ASSAY_LIVENESS_EVENTS_ENV] = str(events_path)
        stamped_env[ASSAY_LIVENESS_EXIT_ENV] = "1"
        stdout_path = events_path.with_suffix(".stdout")
        stderr_path = events_path.with_suffix(".stderr")
        start = self._monotonic()
        spawned_at = time.time()
        proc = None
        try:
            with ProcessTreeWindow(
                cleanup_seconds=_CANDIDATE_TREE_CLEANUP_SECONDS
            ) as process_tree:
                try:
                    with open(stdout_path, "wb") as stdout_fh, open(
                        stderr_path, "wb"
                    ) as stderr_fh:
                        receipt_fd = stamped_env.get(WITNESS_FD_ENV)
                        descriptor_options = (
                            {} if receipt_fd is None else {"pass_fds": (int(receipt_fd),)}
                        )
                        # B206: with an observer window requested the root is
                        # a child subreaper, so orphaned grandchildren stay in
                        # its tree.
                        descriptor_options.update(
                            observer_windows.subreaper_preexec(
                                observer_windows.current_slot()
                            )
                        )
                        try:
                            proc = self._popen(
                                list(argv),
                                env=stamped_env,
                                cwd=cwd,
                                stdout=stdout_fh,
                                stderr=stderr_fh,
                                start_new_session=True,
                                **descriptor_options,
                            )
                        except OSError as exc:
                            evidence = process_tree.finish()
                            spawn_error = ProcessTreeSpawnError.from_oserror(exc)
                            spawn_error.process_tree_evidence = evidence.to_dict()
                            raise spawn_error from exc
                        process_tree.attach(proc)
                        register_live_group(proc.pid)
                        _report_window_host_candidate_pid(proc.pid)
                    window = observer_windows.open_for_root(proc.pid)
                    try:
                        result = self._monitor(
                            proc,
                            argv=argv_tuple,
                            timeout=timeout,
                            events_path=events_path,
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            start=start,
                            spawned_at=spawned_at,
                            window=window,
                        )
                    except BaseException as exc:
                        evidence = process_tree.finish(
                            timed_out=isinstance(exc, subprocess.TimeoutExpired),
                            force=True,
                        )
                        setattr(exc, "process_tree_evidence", evidence.to_dict())
                        raise
                    else:
                        evidence = process_tree.finish()
                        result.process_tree_evidence = evidence.to_dict()
                except OSError as exc:
                    if process_tree.evidence is None:
                        evidence = process_tree.finish(force=True)
                        setattr(exc, "process_tree_evidence", evidence.to_dict())
                    if isinstance(exc, ProcessTreeSpawnError):
                        raise
                    raise ProcessTreeError(
                        f"native liveness process runner failed: {exc}"
                    ) from exc
                finally:
                    _unregister_started_group(proc)
        except ProcessTreeError as exc:
            raise AssayError(
                f"native R2 process-tree cleanup failed: {exc}",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.EXEC_FAILED,
            ) from exc
        return result

    def _cleanup_process_group(self, proc: Any) -> None:
        """Remove descendants left in this candidate's process group.

        A candidate launched with ``start_new_session=True`` is its own
        process-group leader, so its pid remains the group id even after the
        leader has exited.  ``killpg`` is intentionally best-effort: an empty
        group is the normal completion case, not an infrastructure error.
        """
        try:
            self._kill_process_group(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            pass

    def _kill(self, proc: Any) -> None:
        """Kill the whole process group and reap, tolerating a process that
        has already exited between the caller's last check and this call
        (an expected race, never a failure this method should surface).
        """
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = None
        if pgid is not None:
            try:
                self._kill_process_group(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            pass

    def _kill_process_group(self, pgid: int, sig: int) -> None:
        (self._process_group_killer or os.killpg)(pgid, sig)

    def _monitor(
        self,
        proc: Any,
        *,
        argv: tuple[str, ...],
        timeout: float | None,
        events_path: Path,
        stdout_path: Path,
        stderr_path: Path,
        start: float,
        spawned_at: float,
        window: "observer_windows.OpenWindow | None" = None,
    ) -> "subprocess.CompletedProcess[str]":
        try:
            return self._poll(
                proc,
                argv=argv,
                timeout=timeout,
                events_path=events_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                start=start,
                window=window,
            )
        finally:
            # An open window is always closed (the daemon limits open windows
            # per process); close is idempotent.
            if window is not None:
                window.close()
            # The sidecar keeps only the spawn time (Assay's own clock): every
            # resource number now comes from the cgprofile observer (B206).
            # Diagnostics never fail a candidate: every exit path (normal,
            # hung, timeout, any raise) leaves the sidecar, and a write
            # failure is swallowed.
            sidecar = events_path.with_suffix(RESOURCE_SIDECAR_SUFFIX)
            try:
                payload = json.dumps({"format": 1, "spawned_at": spawned_at})
                temporary = sidecar.with_name(sidecar.name + ".tmp")
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, sidecar)
            except (OSError, TypeError, ValueError):
                pass

    def _poll(
        self,
        proc: Any,
        *,
        argv: tuple[str, ...],
        timeout: float | None,
        events_path: Path,
        stdout_path: Path,
        stderr_path: Path,
        start: float,
        window: "observer_windows.OpenWindow | None" = None,
    ) -> "subprocess.CompletedProcess[str]":
        previous_poll_at = start
        liveness_clock = 0.0
        last_progress_at = 0.0
        no_progress_wall_marker = start
        no_progress_eligible_marker = 0.0
        pressure_interval_seen = False
        last_event_count = 0
        session_finish_at: float | None = None
        last_stdout_size = 0
        last_stderr_size = 0
        event_reader = _EventProgressReader(proc.pid)
        cpu_samples = _CpuSampleHistory(self.cpu_window_s)
        previous_resources: Mapping[str, Any] | None = None
        trace = _ResourceTrace()
        resource_trace_complete = False

        # (P4/S1) The MONOTONIC process-tree CPU series. `tree_cpu_seconds`
        # sums the LIVE tree, so a busy descendant exiting drops the total --
        # which would otherwise hand a spinning candidate a "no CPU burned"
        # verdict exactly when it burned the most. A drop is a discontinuity
        # of the measurement, so the peak so far is banked into `cpu_offset`
        # and subtracted again only by the next growth: the series never
        # decreases, and `cpu_since_last_completed_event_s` can therefore only
        # over-approximate a candidate's work, never erase it.
        cpu_series: float | None = None
        cpu_offset = 0.0
        cpu_last: float | None = None
        #: Series value at the last COMPLETED plugin event (or at start).
        completed_marker_cpu: float | None = None
        #: Which `event_reader.completed_events` count that marker reflects.
        completed_marker_count = 0
        completed_marker_wall: float | None = None
        #: (P4/S2) the single in-flight test's own window.
        per_test_window: _PerTestWindow | None = None
        #: (P4/S3) Whether the candidate's CPU was readable at least once --
        #: the CPU precondition for arming the watch. The stream-integrity
        #: condition is tracked separately by `event_reader.stream_broken`.
        cpu_readable = False

        def evidence(reason: str, *, required_idle_s: float) -> dict[str, Any]:
            return {
                "schema_version": 1,
                "policy": "pressure-adjusted-idle-v1",
                "decision": reason,
                "candidate_pid": proc.pid,
                "candidate_cpu_source": "process-tree-cpu-seconds",
                "wall_elapsed_s": round(max(0.0, now - start), 3),
                "eligible_elapsed_s": round(liveness_clock, 3),
                "idle_eligible_s": round(max(0.0, liveness_clock - last_progress_at), 3),
                "required_idle_eligible_s": round(required_idle_s, 3),
                "required_cpu_growth_window_s": self.cpu_window_s,
                "required_cpu_growth_floor_s": self.cpu_growth_floor_s,
                "candidate_session_finish_seen": session_finish_at is not None,
                "session_finish_eligible_s": (
                    round(session_finish_at, 3) if session_finish_at is not None else None
                ),
                "trace_complete": resource_trace_complete,
                # P4/S1: the trace is COMPACTED, never discarded, so the
                # stored artifact is complete at any length.
                "trace_truncated": False,
                "trace_compacted": trace.compacted,
                "trace_samples_merged": trace.merged,
                "samples": list(trace.samples),
            }

        def cpu_evidence(
            decision: str,
            *,
            now: float,
            cpu_series: float,
            since: float,
            required: float,
            per_test: "_PerTestWindow | None" = None,
        ) -> dict[str, Any]:
            """(P4/S1,S2) Evidence for a CPU-without-progress kill.

            Deliberately a DIFFERENT policy name from the idle one: this
            verdict never depended on pressure evidence, so it is never
            validated against the idle trace's snapshot rules -- it only has
            to name the numbers that killed the candidate.
            """
            return {
                "schema_version": 1,
                "policy": CPU_PROGRESS_POLICY,
                "decision": decision,
                "candidate_pid": proc.pid,
                "candidate_cpu_source": CPU_PROGRESS_SOURCE,
                "wall_elapsed_s": round(max(0.0, now - start), 3),
                "candidate_cpu_s": round(cpu_series, 6),
                "cpu_since_last_completed_event_s": round(max(0.0, since), 6),
                "required_cpu_s": round(required, 6),
                "completed_events": event_reader.completed_events,
                "event_count": event_count,
                "wall_since_last_completed_event_s": (
                    None
                    if completed_marker_wall is None
                    else round(max(0.0, now - completed_marker_wall), 3)
                ),
                "test_nodeid": None if per_test is None else per_test.nodeid,
                "per_test_baseline_s": (
                    None if per_test is None else round(per_test.baseline_s, 6)
                ),
                "per_test_cpu_s": (
                    None if per_test is None else round(max(0.0, since), 6)
                ),
                "resources": dict(resources),
                "trace_compacted": trace.compacted,
            }

        while True:
            now = self._monotonic()
            if window is not None and observer_windows.exited_unreaped(proc.pid):
                # Close BEFORE the root is reaped so the daemon reads exact
                # CPU (design 4.1); `poll()` below then reaps it.
                window.close()
            if proc.poll() is not None:
                stdout_text = _read_bytes(stdout_path).decode("utf-8", errors="replace")
                stderr_text = _read_bytes(stderr_path).decode("utf-8", errors="replace")
                return subprocess.CompletedProcess(
                    args=list(argv),
                    returncode=proc.returncode,
                    stdout=stdout_text,
                    stderr=stderr_text,
                )

            event_count, saw_session_finish = event_reader.read(events_path)
            stream_broken = event_reader.stream_broken
            stdout_size = _safe_size(stdout_path)
            stderr_size = _safe_size(stderr_path)
            try:
                cpu_now = self._cpu_reader(proc.pid)
            except Exception:
                cpu_now = None

            try:
                resource_result = self._resource_reader(proc.pid)
                if not isinstance(resource_result, Mapping):
                    raise TypeError("resource reader returned a non-mapping")
                resources: Mapping[str, Any] = resource_result
            except Exception as exc:
                resources = {
                    "schema_version": 1,
                    "status": "unavailable",
                    "reason": f"resource-reader-failed:{type(exc).__name__}",
                }
            prior_resources = previous_resources
            resource_status, resource_deltas = compare_resource_snapshots(
                prior_resources, resources
            )
            if resource_status in ("clear", "stalled"):
                # These are the comparator's only comparable outcomes. Keep
                # this sticky for the run: once pressure credit is established,
                # later unknown intervals pause the clock rather than restoring
                # the wall fallback.
                pressure_interval_seen = True
            resources_complete = (
                resources.get("status") == "available"
                and compare_resource_snapshots(resources, resources)[0] == "clear"
            )
            interval_s = max(0.0, now - previous_poll_at)
            eligible_interval_s = 0.0
            if resource_status == "clear":
                # Only unchanged aggregate counters establish an interval
                # without observed external/cgroup pressure.
                eligible_interval_s = interval_s
                liveness_clock += eligible_interval_s
            else:
                # PSI/cpu.stat are aggregate counters, not a measurement of
                # this candidate's lost runtime. Any positive delta, missing
                # source, or reset makes the whole interval ineligible;
                # prorating would invent a per-process fact.
                cpu_samples.reset()
            previous_resources = (
                dict(resources) if resources_complete else None
            )
            previous_poll_at = now

            sample = {
                "wall_elapsed_s": round(max(0.0, now - start), 3),
                "eligible_elapsed_s": round(liveness_clock, 3),
                "eligible_interval_s": round(eligible_interval_s, 6),
                "candidate_cpu_s": round(cpu_now, 6) if cpu_now is not None else None,
                "event_count": event_count,
                "stdout_bytes": stdout_size,
                "stderr_bytes": stderr_size,
                "resource_interval": resource_status,
                "resource_deltas": resource_deltas,
                "resources": dict(resources),
            }
            if resource_status == "unknown":
                sample["previous_resources"] = (
                    dict(prior_resources) if prior_resources is not None else None
                )

            if resource_status == "unknown":
                # An available current sample is a trustworthy new baseline;
                # no time before it is credited, and the retained trace starts
                # there. An unavailable sample keeps evidence incomplete.
                trace.reset(sample)
                resource_trace_complete = resources_complete
                last_progress_at = liveness_clock
            else:
                # P4/S1: the trace is never truncated. Past
                # `_LIVENESS_RESOURCE_TRACE_MAX_SAMPLES` rows are merged in
                # place, so the artifact stays bounded and VALID and no hang
                # verdict is ever lost to a long run.
                trace.append(sample)

            made_progress = (
                event_count > last_event_count
                or stdout_size > last_stdout_size
                or stderr_size > last_stderr_size
            )
            if made_progress:
                last_progress_at = liveness_clock
                cpu_samples.reset()
                trace.reset(sample)
                resource_trace_complete = resources_complete
                # This trace starts at the current sample, so retain the
                # preceding snapshot needed to verify its first interval.
                if resource_status != "unknown" and prior_resources is not None:
                    sample["previous_resources"] = dict(prior_resources)
            last_event_count = event_count
            last_stdout_size, last_stderr_size = stdout_size, stderr_size
            session_finish_first_seen = (
                saw_session_finish and session_finish_at is None
            )
            if session_finish_first_seen:
                session_finish_at = liveness_clock

            # (P4/S1) The MONOTONIC series, updated BEFORE any bound reads it.
            if cpu_now is not None:
                cpu_readable = True
                if cpu_last is not None and cpu_now < cpu_last:
                    # A descendant exited. Bank the peak so the series cannot
                    # fall; from here on, growth is measured from the banked
                    # peak until the live sum passes it again.
                    cpu_offset += cpu_last - cpu_now
                cpu_series = cpu_now + cpu_offset
                cpu_last = cpu_now
            # (P4/S2) Track the single in-flight test (never under xdist, never
            # with more than one in flight -- see `_PerTestWindow`).
            #
            # (P4 fix 1, review finding 1) The window is OPEN from
            # `test_start` to the matching `test_finish` -- across `setup`,
            # `call` and `teardown` -- and keyed on the START RECORD, so a
            # rerun of one nodeid opens its own window. Before this it was
            # closed by the first COMPLETED event (`setup` is one), which
            # meant the body of every test ran unbounded.
            in_flight = event_reader.in_flight
            if stream_broken:
                # A replaced, truncated, vanished or unreadable stream cannot
                # support an event-derived per-test CPU window for this run.
                per_test_window = None
            elif in_flight is None:
                per_test_window = None
            else:
                nodeid, _started_after, start_index = in_flight
                if (
                    per_test_window is None
                    or per_test_window.start_index != start_index
                    or (
                        per_test_window.started_cpu is None
                        and cpu_series is not None
                    )
                ):
                    baseline_s = self._per_test_cpu_baseline_s.get(nodeid)
                    if baseline_s is None or not per_test_bound_enabled(
                        per_test_xdist=self._per_test_xdist,
                        overlapping=event_reader.overlapping_tests,
                    ):
                        per_test_window = None
                    else:
                        per_test_window = _PerTestWindow(
                            nodeid=nodeid,
                            baseline_s=baseline_s,
                            started_cpu=cpu_series,
                            started_wall=now,
                            start_index=start_index,
                        )
            # (P4/S1) A completed event re-dates the process-tree window.
            if event_reader.completed_events > completed_marker_count:
                completed_marker_count = event_reader.completed_events
                completed_marker_cpu = cpu_series
                completed_marker_wall = now
                no_progress_wall_marker = now
                no_progress_eligible_marker = liveness_clock
            if session_finish_first_seen:
                # `session_finish` is not a completed test/phase event, but
                # teardown after it gets one full CPU-without-progress bound
                # measured from the finish itself. It also closes any stale
                # per-test window: no item remains in flight once the session
                # has reported its finish. This is one-shot, guarded by the
                # first assignment to `session_finish_at` above.
                per_test_window = None
                completed_marker_count = event_reader.completed_events
                completed_marker_cpu = cpu_series
                completed_marker_wall = now
                no_progress_wall_marker = now
                no_progress_eligible_marker = liveness_clock

            cpu_growing = True
            if resource_status == "clear" and cpu_now is not None:
                baseline_cpu = cpu_samples.add(liveness_clock, cpu_now)
                if baseline_cpu is not None:
                    cpu_growing = (cpu_now - baseline_cpu) >= self.cpu_growth_floor_s
            else:
                cpu_samples.reset()

            idle_for = liveness_clock - last_progress_at
            # (B091 round-1 B2) Which bound applies depends on whether this
            # candidate has produced ANY plugin event yet. Before the first
            # one the only thing that can have happened is interpreter
            # start-up and plugin registration, calibrated by the baseline's
            # own leading gap; after it the candidate is inside collection,
            # fixtures or test bodies, calibrated by the baseline's worst
            # observed gap. `_pre_first_event_within_s` IS
            # `_expect_next_event_within_s` unless the caller distinguished
            # them, so this selection is inert for every caller that does
            # not (the plugin-inactive fallback path included).
            bound = (
                self._expect_next_event_within_s
                if event_count > 0
                else self._pre_first_event_within_s
            )
            idle_hang = idle_for >= bound and not cpu_growing
            finish_hang = (
                session_finish_at is not None
                and (liveness_clock - session_finish_at) >= _HUNG_SESSION_FINISH_GRACE_S
                and idle_for >= _HUNG_SESSION_FINISH_GRACE_S
                and not cpu_growing
            )
            # (P4/S1) CPU-without-progress. PROGRESS here is a COMPLETED plugin
            # event and nothing else: output growth and `test_start` do NOT
            # reset it, and it has no pressure term -- CPU accrues only while
            # the candidate runs, so a paused clock would only hide the one
            # signal this bound exists to read. Pressure therefore neither
            # pauses it nor makes it ineligible. (The idle rule above keeps
            # its own pressure-adjusted clock, unchanged.)
            cpu_since_completed_s = 0.0
            cpu_without_progress = False
            if (
                not stream_broken
                and self.cpu_without_progress_bound_s is not None
                and cpu_series is not None
            ):
                if completed_marker_cpu is None:
                    # Nothing completed yet: the window starts at the spawn.
                    completed_marker_cpu = cpu_series
                    completed_marker_wall = now
                cpu_since_completed_s = max(0.0, cpu_series - completed_marker_cpu)
                cpu_without_progress = (
                    cpu_since_completed_s >= self.cpu_without_progress_bound_s
                )
            # (P4/S2) The per-test bound. It needs a readable CPU series, an
            # open single-test window, and that test's own baseline; an
            # unknown nodeid has no baseline, so S1 is the only bound for it.
            per_test_hit = False
            per_test_cpu_s = 0.0
            per_test_required = 0.0
            if (
                not stream_broken
                and per_test_window is not None
                and cpu_series is not None
                and per_test_window.started_cpu is not None
            ):
                per_test_cpu_s = max(0.0, cpu_series - per_test_window.started_cpu)
                per_test_required = per_test_cpu_bound_from_baseline(
                    per_test_window.baseline_s
                )
                per_test_hit = per_test_cpu_s >= per_test_required

            hung = (resource_trace_complete and (idle_hang or finish_hang)) or (
                cpu_without_progress or per_test_hit
            )
            if hung:
                if window is not None:
                    window.close()
                self._kill(proc)
                if per_test_hit:
                    # The per-test bound is the narrower statement ("this
                    # TEST overran its own window"), so it names the kill
                    # whenever it is the one that fired.
                    resource_evidence = cpu_evidence(
                        per_test_cpu_bound_decision,
                        now=now,
                        cpu_series=cpu_series if cpu_series is not None else 0.0,
                        since=per_test_cpu_s,
                        required=per_test_required,
                        per_test=per_test_window,
                    )
                elif cpu_without_progress:
                    resource_evidence = cpu_evidence(
                        cpu_without_progress_decision,
                        now=now,
                        cpu_series=cpu_series if cpu_series is not None else 0.0,
                        since=cpu_since_completed_s,
                        required=float(self.cpu_without_progress_bound_s),
                    )
                else:
                    required_idle_s = (
                        bound if idle_hang else _HUNG_SESSION_FINISH_GRACE_S
                    )
                    resource_evidence = evidence(
                        "idle-hang" if idle_hang else "session-finish-hang",
                        required_idle_s=required_idle_s,
                    )
                raise LivenessHungExpired(
                    cmd=list(argv),
                    timeout=timeout,
                    output=_read_bytes(stdout_path),
                    stderr=_read_bytes(stderr_path),
                    resource_evidence=resource_evidence,
                )

            # (P4/S3) `budget_per_candidate` as an ADVISORY bound: one WARN
            # when it is first exceeded, and the candidate continues. It ends
            # the candidate when the CPU series was never readable or a
            # previously observed event stream broke, which are the cases
            # where the progress watch is unavailable and the budget binds.
            if self.advisory_budget_s is not None:
                elapsed_wall = max(0.0, now - start)
                if elapsed_wall >= self.advisory_budget_s:
                    if cpu_readable and not stream_broken:
                        if self.advisory_exceeded_at_s is None:
                            self.advisory_exceeded_at_s = elapsed_wall
                            print(
                                f"assay: WARN: candidate overran "
                                f"judge.mutation.budget_per_candidate "
                                f"({self.advisory_budget_s:.1f}s) but is "
                                f"still watched for progress: the "
                                f"budget is ADVISORY for this run (a "
                                f"hang is caught by the "
                                f"CPU-without-progress and per-test "
                                f"bounds, bucket 'hung')",
                                file=sys.stderr,
                            )
                    else:
                        if window is not None:
                            window.close()
                        self._kill(proc)
                        exc = subprocess.TimeoutExpired(
                            cmd=list(argv),
                            timeout=self.advisory_budget_s,
                            output=_read_bytes(stdout_path),
                            stderr=_read_bytes(stderr_path),
                        )
                        exc.liveness_resource_evidence = evidence(
                            "budget-expired-watch-unarmed",
                            required_idle_s=bound,
                        )
                        raise exc

            if timeout is not None and (now - start) >= timeout:
                if window is not None:
                    window.close()
                self._kill(proc)
                exc = subprocess.TimeoutExpired(
                    cmd=list(argv),
                    timeout=timeout,
                    output=_read_bytes(stdout_path),
                    stderr=_read_bytes(stderr_path),
                )
                exc.liveness_resource_evidence = evidence(
                    "configured-budget-expired", required_idle_s=bound
                )
                raise exc

            # (P4/M2) A no-progress backstop for candidates that keep burning
            # a little CPU, or whose CPU source later becomes unreadable. It is
            # armed only with the calibrated CPU bound and an intact stream.
            # Until the first comparable interval, it uses wall time when
            # pressure has not been classified, so missing data cannot disable
            # the stop. After that it uses the idle rule's existing
            # pressure-credited liveness clock. Completed events and the first
            # session_finish re-date both windows above.
            # CPU and per-test hung bounds run first; the lane timeout above
            # also wins when it reaches its deadline first.
            if (
                self.cpu_without_progress_bound_s is not None
                and not stream_broken
            ):
                wall_bound_s = (
                    NO_PROGRESS_WALL_FACTOR * self.cpu_without_progress_bound_s
                )
                wall_seconds = max(0.0, now - no_progress_wall_marker)
                eligible_seconds = max(
                    0.0, liveness_clock - no_progress_eligible_marker
                )
                window_seconds = (
                    eligible_seconds if pressure_interval_seen else wall_seconds
                )
                if window_seconds >= wall_bound_s:
                    if window is not None:
                        window.close()
                    self._kill(proc)
                    raise LivenessNoProgressWallExpired(
                        cmd=list(argv),
                        timeout=wall_bound_s,
                        wall_seconds=wall_seconds,
                        eligible_seconds=eligible_seconds,
                        wall_bound_s=wall_bound_s,
                        pressure_credit_active=pressure_interval_seen,
                        output=_read_bytes(stdout_path),
                        stderr=_read_bytes(stderr_path),
                    )

            self._sleep(self._poll_interval_s)


def _unregister_started_group(proc: Any) -> None:
    """Unregister *proc*'s process group when it was started; a spawn that never
    produced a process has nothing to unregister."""
    if proc is not None:
        unregister_live_group(proc.pid)
