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
import time
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

from .errors import AssayError, Outcome, ReasonCode

if TYPE_CHECKING:
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


def _events_path():
    return os.environ.get("ASSAY_LIVENESS_EVENTS")


def _append(record):
    try:
        path = _events_path()
        if not path:
            return
        record["t"] = time.time()
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

    Constructed exactly the way ``subprocess.run`` constructs the real
    thing: ``cmd``/``timeout``/``output``/``stderr`` -- the same shape
    ``_decode_timeout_stream``/``_bounded_tail`` in ``runner.py`` already
    consume, so nothing about that decode path changes for this subclass.
    """


#: (B091/RW-33, P7 A3) The idle-stall thresholds, named once rather than as
#: bare literals scattered through the monitoring loop below.
_HUNG_CPU_WINDOW_S = 30.0
_HUNG_CPU_GROWTH_FLOOR_S = 1.0
_HUNG_SESSION_FINISH_GRACE_S = 30.0
_LIVENESS_POLL_INTERVAL_S = 1.0


def _pid_cpu_ticks(pid: int) -> int:
    """utime+stime (fields 14+15 of ``/proc/<pid>/stat``), in clock ticks.

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
    # `fields[0]` is field 3 (state); field 14 (utime) is fields[11], field
    # 15 (stime) is fields[12].
    return int(fields[11]) + int(fields[12])


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
    total_ticks = _pid_cpu_ticks(root_pid)  # allowed to raise -- see docstring.
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
            total_ticks += _pid_cpu_ticks(pid)
        except OSError:
            continue  # Already exited -- not the root's own read failure.
        try:
            children = _pid_children_via_task(pid) if use_task_api else (
                _pid_children_via_ppid_scan(pid)
            )
        except OSError:
            children = _pid_children_via_ppid_scan(pid)
        stack.extend(child for child in children if child not in visited)
    clock_ticks_per_s = os.sysconf("SC_CLK_TCK")
    return total_ticks / clock_ticks_per_s


#: (B091 round-1 B2) The plugin's three event names, spelled once here so a
#: reader can never drift from the writer's vocabulary. `test` is the `call`
#: phase ONLY -- one per test, which is what A4's progress forwarding and
#: `tests_completed` both require; `phase` is `setup`/`teardown`;
#: `session_start`/`session_finish` bracket the whole session.
TEST_EVENT = "test"
PHASE_EVENT = "phase"
SESSION_START_EVENT = "session_start"
SESSION_FINISH_EVENT = "session_finish"

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
    file order -- THE one parse loop over one of these NDJSON side files.

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
    for record in _iter_events(events_path):
        if record.get("event") == TEST_EVENT:
            yield record


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
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
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


def count_test_events(events_path: "Path | None") -> int:
    """How many `test` events *events_path* carries -- B091 A4's own
    per-candidate `tests_completed` progress field. `0` for a `None`,
    absent, unreadable, or event-less path -- see :func:`_iter_test_events`.
    """
    return sum(1 for _ in _iter_test_events(events_path))


class BaselineEventGaps(NamedTuple):
    """(B091 round-1 B2) What the BASELINE's own events file says about how
    long this suite goes QUIET, which is the only question the monitoring
    loop actually asks.

    *worst_gap_s* is the largest interval between two consecutive stamped
    records -- and because the plugin brackets the session with
    `session_start` (from `pytest_configure`, before collection) and
    `session_finish`, that interval set INCLUDES the leading gap (collection
    plus whatever session/module fixture the first test needs) and the
    trailing gap (session teardown). Those two are exactly the stretches the
    pre-B2 `call`-duration calibration could not see.

    *leading_gap_s* is the first interval specifically -- `session_start` to
    the first report -- i.e. how long this suite takes to produce its first
    sign of life. It calibrates the window before a CANDIDATE has produced
    any event at all. When the file does not begin with `session_start`
    (an older baseline, or a plugin whose `pytest_configure` write failed)
    there is no honest leading measurement, so this falls back to
    *worst_gap_s*, the conservative choice -- never a tighter bound derived
    from an interval that is not the leading one.
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
    stamps: list[float] = []
    first_event: Any = None
    for record in _iter_events(baseline_events_path):
        stamp = record.get("t")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        if not stamps:
            first_event = record.get("event")
        stamps.append(float(stamp))
    if len(stamps) < 2:
        return None
    gaps = [max(0.0, later - earlier) for earlier, later in zip(stamps, stamps[1:])]
    worst_gap_s = max(gaps)
    leading_gap_s = gaps[0] if first_event == SESSION_START_EVENT else worst_gap_s
    return BaselineEventGaps(worst_gap_s=worst_gap_s, leading_gap_s=leading_gap_s)


def compute_liveness_calibration(
    baseline_events_path: "Path | None", baseline_s: float
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
    gaps = baseline_event_gaps(baseline_events_path)
    slowest_test_s = baseline_slowest_test_s(baseline_events_path)
    if gaps is None:
        fallback = max(LIVENESS_FALLBACK_FLOOR_S, baseline_s / 4.0)
        return LivenessCalibration(
            expect_next_event_within_s=fallback,
            pre_first_event_within_s=fallback,
            worst_gap_s=None,
            slowest_test_s=slowest_test_s,
        )
    return LivenessCalibration(
        expect_next_event_within_s=max(
            3.0 * gaps.worst_gap_s, LIVENESS_IDLE_FLOOR_S
        ),
        pre_first_event_within_s=max(
            3.0 * gaps.leading_gap_s, LIVENESS_IDLE_FLOOR_S
        ),
        worst_gap_s=gaps.worst_gap_s,
        slowest_test_s=slowest_test_s,
    )


def _read_events_progress(events_path: Path, previous_count: int) -> tuple[int, bool]:
    """Re-read *events_path* end to end and return ``(valid_record_count,
    saw_session_finish)``. Tolerant of a torn last line (``json.loads``
    failure is skipped, never raised) exactly like
    :func:`compute_liveness_calibration`.

    (B091 round-1 B2) The count is over EVERY event kind -- `session_start`,
    `phase` (`setup`/`teardown`), `test` (`call`) and `session_finish` alike
    -- because the monitoring loop's question is "did this candidate do
    anything since the last tick", and a `setup` report is as good an answer
    as a `call` report. It shares :func:`_iter_events` with the calibration
    so the writer's vocabulary is read back in exactly one place.

    *previous_count* is unused
    by this function itself -- it exists only so a caller unable to import
    both this function's return convention and its own bookkeeping in one
    line can compare the two counts inline; kept as a parameter (rather than
    dropped) to keep every call site's own diff small and self-explanatory.
    """
    del previous_count  # Documented above: comparison is the CALLER's job.
    valid = 0
    saw_session_finish = False
    for record in _iter_events(events_path):
        valid += 1
        if record.get("event") == SESSION_FINISH_EVENT:
            saw_session_finish = True
    return valid, saw_session_finish


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
    (:func:`tree_cpu_seconds`) -- ANY `/proc` read failure is read as "CPU
    is still growing", never as proof of a stall (RW-33's explicit
    requirement); (d) the plain elapsed-budget bound, enforced by this loop
    itself now that it owns the launch (`process_runner`'s caller-side
    ``subprocess.run(timeout=...)`` no longer applies once `Popen` replaces
    it).

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
    process is still alive :data:`_HUNG_SESSION_FINISH_GRACE_S` later
    (this second branch never consults CPU at all -- once pytest has decided
    its own exit status, a process still alive well after is hung whether or
    not it is burning CPU, e.g. spinning inside a thread-join deadlock).
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
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self._events_dir = events_dir
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._expect_next_event_within_s = expect_next_event_within_s
        self._pre_first_event_within_s = (
            expect_next_event_within_s
            if pre_first_event_within_s is None
            else pre_first_event_within_s
        )
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval_s = poll_interval_s
        self._cpu_reader = cpu_reader
        self._popen = popen

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
        stdout_fh = open(stdout_path, "wb")
        stderr_fh = open(stderr_path, "wb")
        try:
            proc = self._popen(
                list(argv),
                env=stamped_env,
                cwd=cwd,
                stdout=stdout_fh,
                stderr=stderr_fh,
                start_new_session=True,
            )
        finally:
            stdout_fh.close()
            stderr_fh.close()
        return self._monitor(
            proc,
            argv=argv_tuple,
            timeout=timeout,
            events_path=events_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            start=start,
        )

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
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            pass

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
    ) -> "subprocess.CompletedProcess[str]":
        last_progress_at = start
        last_event_count = 0
        session_finish_at: float | None = None
        last_stdout_size = 0
        last_stderr_size = 0
        # (t, cpu_seconds) samples, oldest first -- enough history to find a
        # sample at least `_HUNG_CPU_WINDOW_S` old, never trimmed further
        # back than that (a candidate's own budget bounds how large this
        # ever gets: at most `budget_seconds / poll_interval_s` entries).
        cpu_samples: list[tuple[float, float]] = []
        while True:
            now = self._monotonic()
            if proc.poll() is not None:
                stdout_text = _read_bytes(stdout_path).decode("utf-8", errors="replace")
                stderr_text = _read_bytes(stderr_path).decode("utf-8", errors="replace")
                return subprocess.CompletedProcess(
                    args=list(argv),
                    returncode=proc.returncode,
                    stdout=stdout_text,
                    stderr=stderr_text,
                )

            event_count, saw_session_finish = _read_events_progress(
                events_path, last_event_count
            )
            if event_count > last_event_count:
                last_progress_at = now
            last_event_count = event_count
            if saw_session_finish and session_finish_at is None:
                session_finish_at = now

            stdout_size = _safe_size(stdout_path)
            stderr_size = _safe_size(stderr_path)
            if stdout_size > last_stdout_size or stderr_size > last_stderr_size:
                last_progress_at = now
            last_stdout_size, last_stderr_size = stdout_size, stderr_size

            # (RW-33) ANY /proc failure reading this tick's CPU sample is
            # read as "still growing" -- this tick contributes no sample at
            # all, so the CPU-growth check below finds no baseline to
            # compare against yet and defaults to `cpu_growing = True`
            # exactly as it would in the first `_HUNG_CPU_WINDOW_S` of a
            # perfectly healthy candidate's life.
            try:
                cpu_now = self._cpu_reader(proc.pid)
            except Exception:
                cpu_now = None
            cpu_growing = True
            if cpu_now is not None:
                cpu_samples.append((now, cpu_now))
                # Newest-to-oldest: the FIRST sample at least
                # `_HUNG_CPU_WINDOW_S` old is the one closest to the trailing
                # window's edge (samples are chronological oldest-first, and
                # `now - sample_t` only ever grows as `sample_t` gets
                # older, so this is the newest sample that still qualifies).
                # Exhausting the loop with no `break` -- not enough history
                # yet, e.g. the first `_HUNG_CPU_WINDOW_S` of any candidate's
                # life -- leaves `baseline_cpu` `None`, which is the same
                # "cannot prove no-growth" default a `/proc` read failure
                # gets.
                baseline_cpu: float | None = None
                for sample_t, sample_cpu in reversed(cpu_samples):
                    if now - sample_t >= _HUNG_CPU_WINDOW_S:
                        baseline_cpu = sample_cpu
                        break
                if baseline_cpu is not None:
                    cpu_growing = (cpu_now - baseline_cpu) >= _HUNG_CPU_GROWTH_FLOOR_S

            idle_for = now - last_progress_at
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
            hung = (idle_for >= bound and not cpu_growing) or (
                session_finish_at is not None
                and (now - session_finish_at) >= _HUNG_SESSION_FINISH_GRACE_S
            )
            if hung:
                self._kill(proc)
                raise LivenessHungExpired(
                    cmd=list(argv),
                    timeout=timeout,
                    output=_read_bytes(stdout_path),
                    stderr=_read_bytes(stderr_path),
                )

            if timeout is not None and (now - start) >= timeout:
                self._kill(proc)
                raise subprocess.TimeoutExpired(
                    cmd=list(argv),
                    timeout=timeout,
                    output=_read_bytes(stdout_path),
                    stderr=_read_bytes(stderr_path),
                )

            self._sleep(self._poll_interval_s)
