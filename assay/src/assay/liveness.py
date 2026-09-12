"""assay's own liveness mechanism for native R2 python/pytest lanes (B091/D-23,
controller ruling RW-33).

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
   sessionfinish write, per a real spike (this session's LOG/REPORT) --
   bypassing the interpreter's normal thread-join-at-shutdown sequence
   entirely. A leaked non-daemon thread can no longer hang the process; the
   mutant instead SURVIVES (it must be killed by an honest assertion), which
   is D-23's actual point.
2. **A candidate-only env stamp** (:class:`LivenessRunner`, v1 scope: see its
   own docstring for exactly what is and is not implemented yet) that turns
   the plugin's ``os._exit`` behaviour on ONLY for R2 candidate executions,
   never for the R0 baseline or R1 (coverage atexit writers must stay
   intact -- RW-33 is explicit that ``ASSAY_LIVENESS_EXIT`` is never set for
   those).

**A documented, deliberate exception to A-036.** ``mutation.py``'s own module
docstring states the general rule: "Flags may be appended by the caller,
never derived by assay (A-036), and an append the lane did not permit is
refused before the process starts (A-095)." That rule governs the CLI's own
``--`` passthrough feature (``CommandPlan.argv_appended``, gated by
``lane.allow_argv_append`` and enforced by ``execute_plan``'s own refusal
check) -- a LANE-CONSENTED mechanism for a human's extra arguments.

Liveness injection is a different kind of thing: assay's OWN infrastructure,
unconditional for the lanes RW-33 names, with no lane opt-in and no
`allow_argv_append` gate -- putting it through `argv_appended` would either
require every native R2 python lane to declare `allow_argv_append: true`
(defeating "liveness is automatic infra, not lane opt-in") or bypass the
refusal gate for a field whose entire meaning is "the lane permitted this".
Neither is right, so this module extends `argv_declared` instead (mirroring
how `resolve_command_plan` already extends `env_effective` beyond
`env_declared` for `env_passthrough`/`infrastructure` facts -- an
assay-augmented view distinct from, and layered above, the lane's own literal
declaration) via :func:`inject_liveness_plugin`, which returns a NEW
`CommandPlan` with `argv_appended`/`allow_argv_append` untouched, so the CLI's
own passthrough feature and its gate are completely unaffected.

**Flagged for reviewer attention (BLOCKED-protocol default, not re-litigating
RW-33 itself):** this is a genuine judgment call, not settled by any existing
ruling -- A-036's docstring predates B091 and was written about the CLI
passthrough feature specifically, not assay's own infrastructure. The
alternative (a dedicated third `CommandPlan` field for assay-owned argv
augmentation, threaded through `execute_plan`'s refusal logic and the
verdict's transparency recording) would be architecturally cleaner but is a
materially larger change than this cut's budget allows; recorded here and in
the P7 LOG for a reviewer to weigh in on explicitly.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Sequence, TextIO

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

#: Stdlib-only pytest plugin, materialized verbatim by
#: :func:`materialize_liveness_plugin`. Must never import `assay` (the
#: candidate subprocess runs in the PROJECT's interpreter, where assay is
#: often not importable -- a pyz is not on that interpreter's `sys.path` at
#: all) and every hook body is wrapped in `try/except: pass` so a plugin bug
#: can never turn into a false mutant outcome.
#:
#: Spike-validated (this session's LOG): `os._exit` from `pytest_unconfigure`
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


def inject_liveness_plugin(
    plan: "CommandPlan",
    *,
    liveness_dir: Path,
    diagnostics: "TextIO | None",
) -> "tuple[CommandPlan, bool]":
    """Return ``(plan', injected)`` for a native R2 python/pytest lane.

    When *plan*'s DECLARED argv (never the appended/effective one -- the
    question is "does the LANE'S OWN command invoke pytest", not "does the
    final effective command") does not literally invoke pytest
    (:func:`argv_invokes_pytest`), liveness is OFF: returns *plan* unchanged
    and, when *diagnostics* is not ``None``, WARNs exactly once naming the
    rule (RW-33's own documented requirement). D-23's derived
    `budget_per_candidate` (A1) remains the only bound for that lane -- this
    function does not touch it.

    Otherwise: materializes the plugin (:func:`materialize_liveness_plugin`),
    returns a NEW plan whose `argv_declared` gains ``-p
    assay_liveness_plugin`` and whose `env_effective` gains a `PYTHONPATH`
    entry for *liveness_dir* (prepended to any existing value, never
    replacing it -- a lane that already sets `PYTHONPATH` keeps seeing its
    own entries). `argv_appended`/`allow_argv_append` are carried through
    UNCHANGED (see this module's own docstring for why: this is not the CLI's
    `--` passthrough mechanism and must not interact with its consent gate);
    `argv_effective` is recomputed to keep `argv_effective == argv_declared +
    argv_appended` (A-036's transparency invariant) true of the NEW
    `argv_declared`.
    """
    if not argv_invokes_pytest(plan.argv_declared):
        if diagnostics is not None:
            print(
                "assay: WARN: this lane's argv does not literally invoke "
                "pytest (a token 'pytest', a path ending '/pytest', or the "
                "adjacent pair '-m pytest') -- liveness (the os._exit "
                "wrapper, hung detection, per-test cadence hints) is OFF "
                "for this lane; a native Python R2 lane's argv must invoke "
                "pytest for liveness (D-23/RW-33). The declared/derived "
                "budget_per_candidate remains the only bound.",
                file=diagnostics,
            )
        return plan, False
    materialize_liveness_plugin(liveness_dir)
    new_argv_declared = plan.argv_declared + ("-p", LIVENESS_PLUGIN_MODULE_NAME)
    new_env = dict(plan.env_effective)
    existing_pythonpath = new_env.get("PYTHONPATH", "")
    new_env["PYTHONPATH"] = (
        str(liveness_dir)
        if not existing_pythonpath
        else os.pathsep.join([str(liveness_dir), existing_pythonpath])
    )
    new_plan = replace(
        plan,
        argv_declared=new_argv_declared,
        argv_effective=new_argv_declared + plan.argv_appended,
        env_effective=MappingProxyType(new_env),
    )
    return new_plan, True


class LivenessRunner:
    """A :class:`~assay.runner.ProcessRunner` used for R2 CANDIDATE execution
    ONLY (never the R0 baseline, never any other lane or language -- "other
    runners untouched" per the handoff).

    **v1 scope (this commit).** Stamps :data:`ASSAY_LIVENESS_EVENTS_ENV`
    (a per-candidate side file derived from the child's *cwd* -- each
    candidate already runs in its OWN P22 replacement-snapshot directory, so
    hashing *cwd* gives a stable, collision-free per-candidate file with no
    change to `_execute_mutation_jobs`'s single shared `process_runner`
    parameter) and :data:`ASSAY_LIVENESS_EXIT_ENV` ``= "1"`` into the child's
    env, then delegates to *inner* -- exactly today's blocking
    ``subprocess.run`` contract. This alone delivers D-23's `os._exit(rc)`
    wrapper for every candidate: a leaked non-daemon thread can no longer
    hang the candidate at interpreter shutdown (pytest_unconfigure forces
    `os._exit` right after the summary prints), so the mutant SURVIVES
    instead of hanging the whole run.

    **NOT yet implemented (open work, tracked in the P7 BRIEF-2 for the next
    session):** the ACTIVE Popen-based monitoring loop -- launching
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
