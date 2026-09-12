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
   entirely. A leaked non-daemon thread can no longer hang the process; the
   mutant instead SURVIVES (it must be killed by an honest assertion), which
   is D-23's actual point.
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
import os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, NamedTuple, Sequence, TextIO

if TYPE_CHECKING:
    import subprocess

    from .runner import CommandPlan, ProcessRunner

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
_PLUGIN_SOURCE = '''"""assay's own liveness plugin (B091/D-23/RW-33) -- materialized by
assay, loaded by pytest via `-p assay_liveness_plugin`. Stdlib + pytest only;
never imports assay (the candidate subprocess is often not running in an
environment where assay is importable). Every hook body is exception-safe:
a bug here must never change a test outcome or crash the suite.
"""

import os
import sys
import time

import pytest

_EXIT_STATUS = 0


def _events_path():
    return os.environ.get("ASSAY_LIVENESS_EVENTS")


def pytest_runtest_logreport(report):
    try:
        if report.when != "call":
            return
        path = _events_path()
        if not path:
            return
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(
                '{"event": "test", "nodeid": %r, "outcome": %r, '
                '"duration_s": %r, "t": %r}\\n'
                % (report.nodeid, report.outcome, report.duration, time.time())
            )
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    global _EXIT_STATUS
    try:
        _EXIT_STATUS = int(exitstatus)
    except Exception:
        _EXIT_STATUS = 1
    try:
        path = _events_path()
        if not path:
            return
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(
                '{"event": "session_finish", "exitstatus": %r, "t": %r}\\n'
                % (_EXIT_STATUS, time.time())
            )
    except Exception:
        pass


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    try:
        if os.environ.get("ASSAY_LIVENESS_EXIT") != "1":
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
    """
    liveness_dir.mkdir(parents=True, exist_ok=True)
    target = liveness_dir / LIVENESS_PLUGIN_FILENAME
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError:
            existing = None
        if existing == _PLUGIN_SOURCE:
            return target
    target.write_text(_PLUGIN_SOURCE, encoding="utf-8")
    return target


def argv_invokes_pytest(argv: Sequence[str]) -> bool:
    """RW-33's own rule, verbatim: a native Python R2 lane's argv must
    literally invoke pytest for liveness to apply. True iff *argv* contains a
    token equal to ``"pytest"``, a token ending in ``"/pytest"``, or the
    adjacent pair ``"-m", "pytest"``. Never a substring match (a lane
    argument that merely MENTIONS pytest, e.g. a `--pytest-args=...` value
    for some other tool, must not trip this) and never order-independent
    (the `-m`/`pytest` pair must be adjacent, matching how a shell would
    actually invoke `python -m pytest`).
    """
    tokens = list(argv)
    for index, token in enumerate(tokens):
        if token == "pytest" or token.endswith("/pytest"):
            return True
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


class LivenessRunner:
    """A :class:`~assay.runner.ProcessRunner` used for R2 CANDIDATE execution
    ONLY (never the R0 baseline, never any other lane or language -- "other
    runners untouched" per the handoff).

    **v1 scope (session 2, `f4fa1788`).** Stamps
    :data:`ASSAY_LIVENESS_EVENTS_ENV` (a per-candidate side file derived from
    the child's *cwd* -- each candidate already runs in its OWN P22
    replacement-snapshot directory, so hashing *cwd* gives a stable,
    collision-free per-candidate file with no change to
    `_execute_mutation_jobs`'s single shared `process_runner` parameter) and
    :data:`ASSAY_LIVENESS_EXIT_ENV` ``= "1"`` into the child's env, then
    delegates to *inner* -- exactly today's blocking ``subprocess.run``
    contract. This alone delivers D-23's `os._exit(rc)` wrapper for every
    candidate: a leaked non-daemon thread can no longer hang the candidate at
    interpreter shutdown (pytest_unconfigure forces `os._exit` right after
    the summary prints), so the mutant SURVIVES instead of hanging the whole
    run.

    **NOT yet implemented (open work, tracked in the P7 BRIEF-2/BRIEF-3 for
    the next session):** the ACTIVE Popen-based monitoring loop -- launching
    non-blocking, sampling the candidate process TREE's `/proc/<pid>/stat`
    CPU time every 1 s, and classifying `hung` (idle stall) distinctly from
    `budget_exceeded` (a genuine CPU-bound runaway) per RW-33's exact
    thresholds. Until that lands, a candidate that never reaches
    `pytest_sessionfinish` at all (a true deadlock, not a leaked-thread-
    after-summary) is still caught only by the EXISTING `timeout=`/
    `budget_exceeded` path -- unchanged from before this module existed, not
    a regression, and not yet the new `hung` bucket B091 asks for.
    """

    def __init__(self, *, events_dir: Path, inner: "ProcessRunner") -> None:
        self._events_dir = events_dir
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._inner = inner

    def _events_path_for_cwd(self, cwd: Path) -> Path:
        digest = hashlib.sha256(str(cwd).encode("utf-8")).hexdigest()[:16]
        return self._events_dir / f"{digest}.ndjson"

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float | None,
    ) -> "subprocess.CompletedProcess[str]":
        stamped_env = dict(env)
        stamped_env[ASSAY_LIVENESS_EVENTS_ENV] = str(self._events_path_for_cwd(cwd))
        stamped_env[ASSAY_LIVENESS_EXIT_ENV] = "1"
        return self._inner(argv, env=stamped_env, cwd=cwd, timeout=timeout)
