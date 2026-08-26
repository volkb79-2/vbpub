"""Execute one declared command (R0), and build the verdict that describes it.

The claim this module exists to defend: **the command that actually ran is
recorded honestly on every terminal path.** assay is not a test runner (§7):
it executes exactly one declared ``argv`` and never discovers, selects,
orders, or retries anything. Flags may be *appended by the caller*, never
*derived* by assay (A-036), and an append the lane did not permit is refused
before the process starts (A-095).

Four functions are the module's real surface, deliberately kept as separate
steps rather than one run-and-build-verdict function (A-094):

* :func:`execute_command` — the R0 step. Resolves what will run (a
  :class:`CommandPlan`), then actually runs it (or refuses to, or fails to),
  and returns a :class:`CommandResult` on every terminal path. This is the
  exact seam P05's :func:`evaluate_r1` is called after, before handing an
  expanded ``claims`` tuple to :func:`assemble_verdict`.
* :func:`build_r0_claim` — the R0 :class:`~assay.verdict.Claim` from a
  :class:`CommandResult`. A one-line pure mapping, kept separate so nothing
  has to reach into :func:`execute_command`'s internals to get it.
* :func:`evaluate_r1` (P05, widened P17) — the R1 step: P02's two
  measurability guards, then P03's ``EMPTY_COVERAGE`` guard, then reading
  and diffing against the resolved base, short-circuiting on the first
  :class:`~assay.errors.AssayError` any of them raises with a matching
  R1 :class:`~assay.verdict.Claim` (A-090, widened by P17 work item 6 to
  every ``AssayError`` this guard sequence can raise, not only the three
  ``NO_MEASUREMENT`` causes — see its own docstring). Like
  :func:`execute_command`, this never raises for a judged outcome; only a
  genuine programmer error (not an ``AssayError`` at all) propagates.
* :func:`assemble_verdict` — final verdict construction (A-023's rollup,
  A-036's transparency fields, A-024's one-claim-per-declared-rigor). Takes
  the *whole* ``claims`` tuple, not just R0's, so a caller appends the R1
  claim to the tuple it passes rather than needing a different function.
  P10 threads *evidence*/*declared_evidence* through the same way (both
  default to empty, so every earlier caller is unaffected); P12 adds one
  more optional parameter, *mutation_claim* (A-119), for the R2 claim
  :func:`assay.mutation.build_mutation_claim` produces — appended to
  *claims* here rather than requiring every caller to remember to fold it
  in by hand. P17 adds *ended* (default ``None``, so every earlier caller
  is unaffected) — see its own docstring.
* :func:`run_lane` (P17) — the real pipeline :mod:`assay.cli` calls: ties
  the four functions above together into one commit-bound operation, plus
  the prerequisite checks (whole-tree cleanliness, coverage-artifact
  safety) none of them owns alone. See its own docstring for the full
  ordering.

**The injectable process/budget boundary** (work item 1): the real
``subprocess`` is :func:`default_process_runner`, the seam's default: a
test-supplied replacement raising ``FileNotFoundError`` (an executable that
cannot be started) or ``subprocess.TimeoutExpired`` (a budget that expired)
proves :func:`execute_command`'s ``EXEC_FAILED``/``BUDGET_EXCEEDED`` mapping
without a real missing binary or a real wall-clock wait — AUTHORING.md §3b.A's
constraint on timing-dependent oracles. The default runner still exists and is
exercised directly (not only mocked away) by the environment-isolation
proof (O2): ``subprocess.run`` never merges a non-``None`` ``env`` with the
parent's, so passing exactly :attr:`CommandPlan.env_effective` is what makes
"no ambient leak" true by construction, not by convention.

What :func:`execute_command` never does, on purpose: it does not read
``judge`` config, does not parse coverage, does not compute R1-R3.
``NO_MEASUREMENT`` is not a status it can ever return — that outcome is
:func:`evaluate_r1`'s alone to produce, and :func:`execute_command` has no
path that reaches it.

Every rejection here raises :class:`~assay.errors.AssayError` directly; there
is no locally-defined exception type (A-092 — ``errors.py`` is outside this
package's ``scope.touch``).
"""

from __future__ import annotations

import fnmatch
import math
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import (
    Any,
    Callable,
    ContextManager,
    Iterator,
    Mapping,
    Protocol,
    Sequence,
    TextIO,
)

from . import coverage, diff, git, isolation, measurability, mutation, safeio
from .adapters.base import LanguageAdapter
from .config import (
    MAX_INFRASTRUCTURE_VALUE_BYTES,
    IsolationConfig,
    Lane,
    parse_duration,
)
from .coverage import derive_branch_capability
from .coverage_parsers.model import CoverageProfile
from .errors import AssayError, Outcome, ReasonCode
from .evaluate import evaluate_coverage, evaluate_targets
from .output import VerdictOutput
from .verdict import (
    CanaryResult,
    Claim,
    Coverage,
    Evidence,
    EvidenceDeclaration,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR3,
    JudgmentResolved,
    SnapshotPolicy,
    Verdict,
    iso_utc,
    rollup,
)

__all__ = [
    "CommandPlan",
    "CommandResult",
    "LaneDeadline",
    "MonotonicClock",
    "ProcessRunner",
    "ScratchRootFactory",
    "assemble_verdict",
    "build_r0_claim",
    "default_process_runner",
    "evaluate_r1",
    "execute_plan",
    "execute_command",
    "default_scratch_root",
    "refuse_lane",
    "resolve_command_plan",
    "run_lane",
    "write_verdict",
]


class ProcessRunner(Protocol):
    """The injectable process boundary. :func:`default_process_runner` is the
    real implementation; a test substitutes a callable with this same shape.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


#: The injectable clock. :func:`execute_command` calls this twice (before and
#: after the child runs) to timestamp the claim -- never ``time.sleep`` or an
#: elapsed-time comparison, so a test can supply a fixed sequence of moments
#: instead of waiting on a real one (AUTHORING.md §3b.A).
Clock = Callable[[], datetime]

MonotonicClock = Callable[[], float]


@dataclass(frozen=True, kw_only=True)
class LaneDeadline:
    """One lane-wide monotonic deadline; no lower layer chooses a clock."""

    expires_at: float
    monotonic: MonotonicClock

    @classmethod
    def start(
        cls, *, budget_seconds: float, monotonic: MonotonicClock
    ) -> "LaneDeadline":
        if (
            isinstance(budget_seconds, bool)
            or not isinstance(budget_seconds, (int, float))
            or not math.isfinite(budget_seconds)
            or budget_seconds <= 0
        ):
            raise ValueError(
                f"budget_seconds must be a positive finite number, got {budget_seconds!r}"
            )
        started = monotonic()
        if (
            isinstance(started, bool)
            or not isinstance(started, (int, float))
            or not math.isfinite(started)
        ):
            raise ValueError(f"monotonic clock returned invalid value {started!r}")
        return cls(expires_at=started + float(budget_seconds), monotonic=monotonic)

    def remaining(self) -> float:
        remaining = self.expires_at - self.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise AssayError(
                "the lane-wide deadline expired",
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
            )
        return remaining

ScratchRootFactory = Callable[[], ContextManager[Path]]


@contextmanager
def default_scratch_root() -> Iterator[Path]:
    """One caller-owned absolute scratch root for the whole higher-rigor lane."""
    with tempfile.TemporaryDirectory(prefix="assay-p23-") as raw:
        yield Path(raw).resolve()


def default_process_runner(
    argv: Sequence[str], *, env: Mapping[str, str], cwd: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """The real boundary: an actual child process, the seam's default.

    ``env`` REPLACES the child's environment completely -- ``subprocess.run``
    never merges a non-``None`` ``env`` with the parent's -- which is what
    makes "the child receives exactly lane env plus declared passthrough, and
    no ambient sentinel" true by construction rather than by convention (O2).

    ``errors="replace"`` (B027 round 2 finding N-W2): without it, undecodable
    child output raised an uncaught ``UnicodeDecodeError`` here -- on the
    NORMAL completion path, not the timeout path B027 fixed -- contradicting
    ``_bounded_tail``'s own docstring ("undecodable bytes have already
    become U+FFFD by then") and the claim B027's ``_decode_timeout_stream``
    makes about matching this path's policy. Tolerant decoding here is what
    makes both of those true instead of aspirational.
    """
    return subprocess.run(
        list(argv),
        env=dict(env),
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
        text=True,
        errors="replace",
    )


COMMAND_TAIL_BYTES = 64 * 1024

#: (B032/A-322) The ceiling on one `environment_command` preflight probe.
#: A probe answers a yes/no question about the INVOKING environment -- "is
#: this the environment this lane declared?" -- before any repository,
#: snapshot or lane work, so it has no legitimate reason to run long, and a
#: probe that hangs must not be able to spend the lane's whole declared
#: budget before the lane's own command has even started. The effective
#: timeout is `min(this, deadline.remaining())`: the lane budget still wins
#: when it is the smaller of the two.
PROBE_BUDGET_SECONDS = 30.0


def _probe_refusal_terminal(
    probe_result: "CommandResult",
) -> tuple[Outcome, ReasonCode]:
    """The `(outcome, reason_code)` an `environment_command` probe failure
    renders as (B032/A-321).

    `8a2a4731` hardcoded `ERROR`/`BAD_LANE_CONFIG` for every probe failure
    and discarded `execute_plan`'s own classification, so a probe that
    genuinely exhausted its budget -- which `execute_plan` classifies
    correctly as `BUDGET_EXCEEDED`/`LANE_TIMEOUT` -- was recorded as an
    operator config error, exit 2 instead of exit 4. A gate that retries a
    timeout but hard-fails a config error (the estate's own run-gate shape)
    then does exactly the wrong thing on a real timeout.

    A-321's ruling: the timeout is the ONE distinction that must survive.
    Every other probe failure -- a missing binary (`ERROR`/`EXEC_FAILED`), a
    nonzero exit or a signal death (`FAIL`/`COMMAND_FAILED`) -- means the
    same actionable thing to a consumer ("this lane's declared environment is
    not the invoking one; the lane as declared cannot be honoured here"), so
    those keep collapsing into `ERROR`/`BAD_LANE_CONFIG`. What separates them
    is now carried as text on the diagnostics stream
    (:func:`_report_probe_refusal`), not by widening the closed reason-code
    vocabulary (A-138/A-170).
    """
    if probe_result.reason_code is ReasonCode.LANE_TIMEOUT:
        return probe_result.outcome, ReasonCode.LANE_TIMEOUT
    return Outcome.ERROR, ReasonCode.BAD_LANE_CONFIG


def _report_probe_refusal(
    lane: Lane,
    probe_result: "CommandResult",
    *,
    status: Outcome,
    reason_code: ReasonCode,
    diagnostics: "TextIO | None",
    probe_timeout: float = PROBE_BUDGET_SECONDS,
) -> None:
    """B010's own stated deliverable, finally shipped (B032/A-322).

    B010 asked, verbatim, for a refusal that says "this lane's declared
    environment does not match the invoking one; run via `<declared
    wrapper>`" *instead of surfacing the suite's raw traceback*. What
    `8a2a4731` shipped wrote **0 bytes** to stderr and emitted a generic
    `BAD_LANE_CONFIG` whose `argv_effective` named the LANE's command --
    which never executed -- so a consumer got neither the raw error B010
    complained about nor the clear message B010 asked for.

    The verdict artifact stays exactly as closed as it was: no free-text
    field is added to it (A-138/A-170). The diagnosis goes to the caller's
    own stream, which is where :mod:`assay.cli` already prints every other
    typed refusal it catches.
    """
    if diagnostics is None:
        return
    wrapper = shlex.join(lane.environment_command or ())
    if reason_code is ReasonCode.LANE_TIMEOUT:
        # NOT B010's message: a probe that never finished proved nothing
        # about the environment either way, so claiming a mismatch would be
        # an invented fact. The lane's own command still never started.
        #
        # The window actually enforced is `min(PROBE_BUDGET_SECONDS,
        # deadline.remaining())` (B032/A-322's `probe_timeout`, passed in by
        # the caller) -- when the lane's own remaining budget is the
        # tighter bound, hardcoding the fixed 30s cap here would be a false
        # claim about which bound actually fired (round-2 review finding).
        cause = (
            f"its declared environment_command did not finish within its "
            f"{probe_timeout:g}s preflight window (the lesser of the "
            f"{PROBE_BUDGET_SECONDS:g}s probe cap and the lane's remaining "
            f"budget), so the lane's own command never started"
        )
    else:
        detail = {
            ReasonCode.EXEC_FAILED: "the probe command could not be executed",
            ReasonCode.COMMAND_FAILED: (
                f"the probe exited {probe_result.returncode}"
                if probe_result.returncode is not None
                else "the probe was killed before it could exit"
            ),
        }.get(probe_result.reason_code, "the probe did not pass")
        cause = (
            f"its declared environment does not match the invoking one "
            f"({detail}), so the lane's own command never started"
        )
    print(
        f"assay: {status.value}/{reason_code.value}: lane {lane.name!r}: "
        f"{cause}. Run via the declared wrapper: {wrapper}",
        file=diagnostics,
    )
    for label, tail in (
        ("stderr", probe_result.stderr_tail),
        ("stdout", probe_result.stdout_tail),
    ):
        if tail:
            print(f"  probe {label}: {tail.rstrip()}", file=diagnostics)


def _decode_timeout_stream(raw: str | bytes | None) -> str | None:
    """(B027) `subprocess.TimeoutExpired.stdout`/`.stderr` are `bytes`, even
    under `text=True` -- the one path CPython does not text-decode for us.
    Tolerant decode (never raises on undecodable bytes), matching the
    replacement-character policy `_bounded_tail`'s own docstring already
    describes for the normal completion path, so a hung mutant's tail reads
    the same way a completed one's does.
    """
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def _bounded_tail(raw: str | None) -> tuple[str, int]:
    """Keep the final *COMMAND_TAIL_BYTES* of captured text.

    The input is decoded by ``subprocess`` under ``text=True``, so the byte
    count is measured on its UTF-8 encoding: the same currency as the
    process's output and the artifact's stated bound. Undecodable child bytes
    have already become U+FFFD at that boundary; re-encoding them preserves
    the replacement character without inventing a second decode policy.
    """
    if raw is None or raw == "":
        return "", 0
    encoded = raw.encode("utf-8")
    if len(encoded) <= COMMAND_TAIL_BYTES:
        return raw, 0
    cutoff = len(encoded) - COMMAND_TAIL_BYTES
    while cutoff < len(encoded) and (encoded[cutoff] & 0xC0) == 0x80:
        cutoff += 1
    return encoded[cutoff:].decode("utf-8"), cutoff


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, kw_only=True)
class CommandPlan:
    """What WILL run, resolved before anything executes.

    ``argv_effective`` is always ``argv_declared + argv_appended`` -- even on
    a path where the append is later refused -- because :class:`~assay.verdict.Verdict`
    itself enforces that equality (A-036); recording the attempted effective
    argv is what lets a consumer see *what was asked for*, not only what ran.
    """

    argv_declared: tuple[str, ...]
    argv_appended: tuple[str, ...]
    argv_effective: tuple[str, ...]
    #: exactly ``lane.env``, verbatim (A-019: declared-only).
    env_declared: Mapping[str, str]
    #: ``env_declared`` plus whichever ``env_passthrough`` names were actually
    #: present in the passthrough source. Nothing else -- no ambient merge.
    env_effective: Mapping[str, str]
    env_passthrough: tuple[str, ...]
    allow_argv_append: bool
    budget_seconds: float
    project_prefix: PurePosixPath | None


@dataclass(frozen=True, kw_only=True)
class CommandResult:
    """The real outcome of the R0 step -- append rejected, executable
    missing, budget exceeded, command failed, or command passed. Exactly what
    A-094 requires as separable output: everything final verdict assembly
    needs to build the R0 claim and the transparency fields, with nothing
    about R1 evaluation baked in.
    """

    plan: CommandPlan
    #: PASS, FAIL, ERROR or BUDGET_EXCEEDED for :func:`execute_command`'s
    #: own output -- never NO_MEASUREMENT or INCONCLUSIVE, those are not
    #: R0-producible outcomes in this package. :func:`run_lane` (P17) MAY
    #: construct a :class:`CommandResult` with ``ERROR`` or
    #: ``NO_MEASUREMENT`` directly, WITHOUT calling
    #: :func:`execute_command` at all, to represent "a prerequisite failed
    #: and the command never launched" (work items 3/5) -- the plan and
    #: timing still need somewhere honest to live, and this is that
    #: somewhere; :func:`build_r0_claim` is never called on one of these
    #: (:func:`run_lane` builds the refusal claims by hand instead), so no
    #: caller reads this field back out as if it were R0's own judgement.
    outcome: Outcome
    reason_code: ReasonCode | None
    #: ``None`` when the process never started (append refused, exec failed,
    #: or the budget expired before it returned).
    returncode: int | None
    #: B014: bounded final output retained for every non-PASS terminal. A
    #: process that never started has no tail; a PASS omits evidence it does
    #: not need unless a caller explicitly supplies one.
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    stdout_dropped_bytes: int = 0
    stderr_dropped_bytes: int = 0
    started: str
    ended: str


def resolve_command_plan(
    lane: Lane,
    *,
    argv_append: Sequence[str] = (),
    passthrough_source: Mapping[str, str] | None = None,
    project_prefix: PurePosixPath | None = None,
    infrastructure_source: Path | None = None,
    infrastructure_environment: Mapping[str, str] | None = None,
) -> CommandPlan:
    """Resolve what will run. Never launches anything.

    *passthrough_source* is the ambient environment to read
    ``lane.env_passthrough`` names FROM -- ``os.environ`` by default, but
    injectable so a test proves "no ambient leak" without mutating real
    process-global state (AUTHORING.md §3b.B). Only names the lane actually
    declared in ``env_passthrough`` AND that are present in the source are
    carried into ``env_effective``; everything else in the source is invisible
    to the child, matching A-019's "declared-only" env contract.

    **B013:** when *lane* declares ``infrastructure``, those facts are resolved
    HERE, in the invoking process, before any snapshot or command exists.
    ``required-env`` reads only *infrastructure_environment*, and ``derived``
    reads only the rendered TOML at *infrastructure_source*. Missing, empty, or
    malformed values refuse with the named fact; resolution never reads inside
    a snapshot and never invents a default.
    """
    source = os.environ if passthrough_source is None else passthrough_source
    env_effective: dict[str, str] = dict(lane.env)
    if lane.infrastructure:
        has_derived = any(
            declaration.startswith("derived:")
            for declaration in lane.infrastructure.values()
        )
        if has_derived and infrastructure_source is None:
            raise AssayError(
                "lane declares a derived infrastructure fact but no "
                "infrastructure_source was supplied to resolve it against",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        for name, declaration in lane.infrastructure.items():
            source_name, separator, expression = declaration.partition(":")
            if not separator or source_name not in ("required-env", "derived"):
                raise AssayError(
                    f"infrastructure fact {name!r} has invalid declaration {declaration!r}",
                    outcome=Outcome.ERROR,
                    reason_code=ReasonCode.BAD_LANE_CONFIG,
                )
            if source_name == "required-env":
                ambient = (
                    os.environ
                    if infrastructure_environment is None
                    else infrastructure_environment
                )
                value = ambient.get(expression)
                if value is None or not value:
                    raise AssayError(
                        f"infrastructure fact {name!r} requires non-empty "
                        f"environment variable {expression!r}",
                        outcome=Outcome.ERROR,
                        reason_code=ReasonCode.BAD_LANE_CONFIG,
                    )
            else:
                assert source_name == "derived"
                path = infrastructure_source
                try:
                    with path.open("rb") as stream:
                        document = tomllib.load(stream)
                except (OSError, tomllib.TOMLDecodeError) as exc:
                    raise AssayError(
                        f"infrastructure fact {name!r} could not read rendered CIU state "
                        f"{path}: {exc}",
                        outcome=Outcome.ERROR,
                        reason_code=ReasonCode.BAD_LANE_CONFIG,
                    ) from exc
                node: Any = document
                for part in expression.split("."):
                    if not isinstance(node, Mapping) or part not in node:
                        node = None
                        break
                    node = node[part]
                if not isinstance(node, str) or not node:
                    raise AssayError(
                        f"infrastructure fact {name!r} derived {expression!r} is absent, "
                        f"not a string, or empty",
                        outcome=Outcome.ERROR,
                        reason_code=ReasonCode.BAD_LANE_CONFIG,
                    )
                value = node
            if len(value.encode("utf-8")) > MAX_INFRASTRUCTURE_VALUE_BYTES:
                # (B022 item 4) a resolved value this large would otherwise
                # fail late and opaquely at `E2BIG` on exec, or silently
                # inflate every child process's environment.
                raise AssayError(
                    f"infrastructure fact {name!r} resolved to a value of "
                    f"{len(value.encode('utf-8'))} bytes, exceeding the "
                    f"{MAX_INFRASTRUCTURE_VALUE_BYTES}-byte bound",
                    outcome=Outcome.ERROR,
                    reason_code=ReasonCode.BAD_LANE_CONFIG,
                )
            env_effective[name] = value
    for name in lane.env_passthrough:
        if name not in source:
            continue
        if name in env_effective:
            # (B022) `config.py` already refuses an `env_passthrough` name
            # that collides with a declared `infrastructure` fact at LOAD
            # time -- this can only fire if that loader check is ever
            # weakened, but the runtime had no defence of its own: an
            # unconditional overwrite here would let a passthrough value
            # silently win over an infrastructure-injected one, the exact
            # opposite of A-293's "every injected fact has exactly one
            # owner". Refuse loudly instead of silently overwriting.
            raise AssayError(
                f"env_passthrough name {name!r} collides with a declared "
                f"infrastructure fact of the same name",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        env_effective[name] = source[name]
    argv_declared = tuple(lane.argv)
    argv_appended = tuple(argv_append)
    return CommandPlan(
        argv_declared=argv_declared,
        argv_appended=argv_appended,
        argv_effective=argv_declared + argv_appended,
        env_declared=lane.env,
        env_effective=MappingProxyType(env_effective),
        env_passthrough=tuple(lane.env_passthrough),
        allow_argv_append=lane.allow_argv_append,
        budget_seconds=lane.budget_seconds,
        project_prefix=project_prefix,
    )


def execute_plan(
    plan: CommandPlan,
    *,
    cwd: Path,
    timeout: float,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
) -> CommandResult:
    """Execute one already-frozen command plan with a required remainder."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError(f"timeout must be a positive finite number, got {timeout!r}")
    started_at = clock()
    if plan.argv_appended and not plan.allow_argv_append:
        return CommandResult(
            plan=plan,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=None,
            stdout_tail="",
            stderr_tail="",
            started=iso_utc(started_at),
            ended=iso_utc(clock()),
        )
    try:
        proc = process_runner(
            plan.argv_effective,
            env=plan.env_effective,
            cwd=cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # (B027) `TimeoutExpired.stdout`/`.stderr` carry whatever partial
        # bytes `Popen._communicate` had buffered when the timeout fired --
        # CPython does NOT run this exception path through the same
        # text-decode step a normal `CompletedProcess` return does, even
        # though `text=True` was passed to `subprocess.run`. `_bounded_tail`
        # is documented and typed to receive an already-decoded `str`; decode
        # here, at the one call site that actually receives `bytes`, so its
        # contract stays accurate everywhere else.
        stdout_tail, stdout_dropped_bytes = _bounded_tail(_decode_timeout_stream(exc.stdout))
        stderr_tail, stderr_dropped_bytes = _bounded_tail(_decode_timeout_stream(exc.stderr))
        return CommandResult(
            plan=plan,
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.LANE_TIMEOUT,
            returncode=None,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            stdout_dropped_bytes=stdout_dropped_bytes,
            stderr_dropped_bytes=stderr_dropped_bytes,
            started=iso_utc(started_at),
            ended=iso_utc(clock()),
        )
    except OSError:
        return CommandResult(
            plan=plan,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=None,
            stdout_tail="",
            stderr_tail="",
            started=iso_utc(started_at),
            ended=iso_utc(clock()),
        )
    ended = iso_utc(clock())
    if proc.returncode == 0:
        return CommandResult(
            plan=plan,
            outcome=Outcome.PASS,
            reason_code=None,
            returncode=0,
            started=iso_utc(started_at),
            ended=ended,
        )
    stdout_tail, stdout_dropped_bytes = _bounded_tail(proc.stdout)
    stderr_tail, stderr_dropped_bytes = _bounded_tail(proc.stderr)
    return CommandResult(
        plan=plan,
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        returncode=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        stdout_dropped_bytes=stdout_dropped_bytes,
        stderr_dropped_bytes=stderr_dropped_bytes,
        started=iso_utc(started_at),
        ended=ended,
    )


def execute_command(
    lane: Lane,
    *,
    argv_append: Sequence[str] = (),
    cwd: Path,
    passthrough_source: Mapping[str, str] | None = None,
    project_prefix: PurePosixPath | None = None,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
) -> CommandResult:
    """The R0 step (A-094): resolve the plan, then run it, and return a
    :class:`CommandResult` on every terminal path.

    Ordering, all before anything launches:

    1. :func:`resolve_command_plan` -- pure, but CAN raise
       :class:`~assay.errors.AssayError` since B013: this function accepts no
       *infrastructure_source*/*infrastructure_environment* of its own, so a
       lane declaring any infrastructure fact reaches :func:`resolve_command_plan`
       with neither -- a ``derived:`` fact always raises here regardless of
       whether it is actually resolvable elsewhere (B029, filed rather than
       fixed: this function's only caller, :mod:`assay.canary`'s R3 side-run,
       catches the raise and reports a misattributed ``ERROR``/
       ``BAD_LANE_CONFIG`` R3 claim rather than crashing, but the claim's own
       cause is wrong on a lane whose infrastructure resolves everywhere else).
    2. the append-permission gate (A-095): if *argv_append* is non-empty and
       ``lane.allow_argv_append`` is false, this returns
       ``ERROR``/``EXEC_FAILED`` WITHOUT calling *process_runner* at all --
       the process never starts, which is A-073's own definition of
       ``EXEC_FAILED``.

    Then *process_runner* is called with the plan's effective argv and
    environment. ``OSError`` (the parent of ``FileNotFoundError`` --
    "no such file", "not executable", "not a directory": every shape of
    "the process could not be started") maps to ``ERROR``/``EXEC_FAILED``.
    ``subprocess.TimeoutExpired`` maps to ``BUDGET_EXCEEDED``/``LANE_TIMEOUT``.
    Otherwise the exit code decides: 0 is ``PASS``; anything else is
    ``FAIL``/``COMMAND_FAILED`` (A-073 -- an ordinary non-zero exit is a
    judged R0 FAIL, never ``EXEC_FAILED``, and never a universal PASS).
    """
    plan = resolve_command_plan(
        lane, argv_append=argv_append, passthrough_source=passthrough_source,
        project_prefix=project_prefix,
    )
    return execute_plan(
        plan,
        cwd=cwd,
        timeout=plan.budget_seconds,
        process_runner=process_runner,
        clock=clock,
    )


def build_r0_claim(result: CommandResult) -> Claim:
    """The R0 :class:`~assay.verdict.Claim` from a :class:`CommandResult`.

    A pure, one-line mapping -- kept separate from :func:`execute_command` so
    a caller (P05) that already has a :class:`CommandResult` never has to
    reconstruct this by hand.
    """
    return Claim(
        rigor="R0",
        source="computed",
        status=result.outcome,
        verified_by_assay=True,
        reason_code=result.reason_code,
    )


#: The fixed ceiling on one source file read for coverage classification or
#: mutation-target resolution (O4: a FIXED byte bound, never an ambient
#: guess). Reviewer repair: both call sites previously used
#: ``Path.read_text``, which the project's own DESIGN-GUIDE 6 already names
#: as not equivalent to the safe seam -- "it can reopen a swapped
#: parent/object and has no work bound" -- while
#: :func:`assay.safeio.read_bounded_file` had been built for exactly this and
#: left unused here. Reproduced before repair: a 24 MiB tracked source file
#: was read whole, 8 MiB past this module's own coverage-artifact ceiling.
#: Sized well above any plausible hand-written source file so it can never
#: decide a verdict on a legitimate repository, only make the work finite.
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024


def _read_bounded_source_text(repo_top: Path, path: str, *, why: str) -> str:
    """Read one repo-top-relative source file through the fixed bounded
    safe-open seam, or refuse with ``ERROR``/``UNREADABLE_ARTIFACT``.

    Routes through :func:`assay.safeio.read_bounded_file`, so the same
    no-follow ``dir_fd`` traversal, ``O_NONBLOCK`` open, regular-file
    ``fstat`` and ``limit + 1`` ceiling that already guard the coverage
    artifact now also guard every source read (P20 work item 5's "bounded
    source reads", O4). Three previously-unguarded shapes become the exact
    terminal the packet's own translation table already reserves for them --
    a non-regular file (a FIFO or device could BLOCK the whole judgment under
    ``read_text``), a symlink (silently followed before, potentially right
    out of the repository), and an oversized file. A file the diff names but
    that is absent on disk keeps its previous meaning: ``read_text`` raised
    ``FileNotFoundError`` -> ``OSError`` -> ``UNREADABLE_ARTIFACT``, and
    ``None`` from the bounded read renders the identical pair here.
    """
    try:
        raw = safeio.read_bounded_file(repo_top, path, limit=MAX_SOURCE_FILE_BYTES)
    except AssayError as exc:
        raise AssayError(
            f"source file {path!r} could not be read for {why}: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc
    if raw is None:
        raise AssayError(
            f"source file {path!r} is named by the diff but is absent from "
            f"the work tree; it could not be read for {why}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssayError(
            f"source file {path!r} is not valid UTF-8 and could not be read "
            f"for {why}: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc


def evaluate_r1(
    lane: Lane,
    *,
    repo: Path,
    project_root: Path,
    base: str | None,
    adapter: LanguageAdapter,
    on_base_resolved: Callable[[str], None] | None = None,
    on_added_resolved: Callable[[diff.AddedLines], None] | None = None,
    profile: CoverageProfile | None = None,
    remaining: git.Remaining | None = None,
) -> Claim:
    """The R1 step (A-090, A-094): P02's two measurability guards, then
    P03's ``EMPTY_COVERAGE`` guard, short-circuiting on the first that trips
    -- then the four-way union (:func:`assay.evaluate.evaluate_coverage`).

    Mirrors :func:`execute_command`'s own contract: never raises for a
    JUDGED outcome. Every :class:`~assay.errors.AssayError` this function's
    own guard sequence can raise -- P02's two measurability guards, P03's
    ``EMPTY_COVERAGE`` guard, a broken coverage artifact
    (``FORMAT_MISMATCH``, ``UNREADABLE_ARTIFACT``), a git failure resolving
    the base or diffing it (``GIT_FAILED``), an ``evaluate_coverage``
    normalized-key collision, or an expected filesystem/Unicode failure
    reading a source file's text (``UNREADABLE_ARTIFACT``) -- is caught and
    returned as a complete R1 :class:`~assay.verdict.Claim` carrying that
    exact status and reason_code (P17 work item 6, closing three of sol's
    "permanently unreachable" pairs, A-O15/STATE.md; P20 work item 5 widens
    this to every EXPECTED failure after HEAD is known, not only the ones
    upstream of :func:`~assay.evaluate.evaluate_coverage`). NO ``coverage``
    payload accompanies any of them -- omitted, not zeroed (A-025 for the
    three ``NO_MEASUREMENT`` causes; A-136 for the ``ERROR`` ones, which are
    payload-free by construction regardless of cause). Only a
    non-:class:`AssayError` exception (a genuine programmer error) still
    propagates uncaught -- this function never invents a generic PASS/ERROR
    fallback for one.

    *lane.judge* must be fully resolved for R1 (every field
    ``JUDGE_FIELDS_BY_RIGOR["R1"]`` names) -- guaranteed by
    :mod:`assay.config`'s loader for any lane that actually declares R1
    rigor, which is the only way a caller should reach this function.
    *base* is the declared comparison ref, exactly as ``judge.base``
    declares it (a caller resolves nothing before passing it in).

    *profile* (P20): when given, it is the artifact :func:`run_lane` already
    consumed and parsed through its own single-owner reservation -- used
    directly and NEVER reread. ``None`` (the default; :mod:`assay.canary`'s
    existing call site relies on this) makes this function read it itself
    via :func:`assay.coverage.read_coverage_artifact` under the SAME bounded
    safe-I/O discipline, exactly as before.

    *on_base_resolved* (P17), if given, is called EXACTLY ONCE with the
    resolved full base commit (:attr:`~assay.measurability.ResolvedBase.
    base_rev`) the moment :func:`assay.measurability.check_base_is_head`
    produces it -- never on a path where that guard itself trips. This is
    how :func:`run_lane` builds ``judgment.resolved.base`` (V5-1 hoisted it out
    of ``judgment.r1``; P16's "the FULL
    resolved comparison commit, never the lane's own possibly-symbolic
    ``base`` ref") WITHOUT this function's own signature or return type
    changing: :mod:`assay.canary` calls this function directly today and
    expects a bare :class:`~assay.verdict.Claim` back, so a second return
    value is not available here, and re-resolving the base a second time
    the caller's own side would violate O2's "the comparison base resolves
    once" -- an additive, default-``None`` callback is the only channel
    that satisfies both constraints simultaneously.

    *on_added_resolved* (P18), the identical mechanism one field over: if
    given, called EXACTLY ONCE with the resolved
    :class:`~assay.diff.AddedLines` the moment
    :func:`assay.diff.parse_added_lines` produces it -- never on a path
    where an earlier guard (including the coverage-artifact-specific ones
    this function alone owns) trips first. This is how :func:`run_lane`
    lets a DECLARED R2 reuse R1's own resolved diff instead of diffing the
    same ``base``..``HEAD`` pair a second time, for the identical
    frozen-signature reason *on_base_resolved* exists: :mod:`assay.canary`
    already depends on this function returning a bare :class:`Claim`.

    **wave-1 §4/§5 (A-258/A-259/A-260).** ``judge.mode`` (absent means
    ``"changed_lines"``) dispatches to :func:`~assay.evaluate.
    evaluate_coverage` (unchanged shape) or to the whole-target mode's
    :func:`~assay.evaluate.evaluate_targets`, which has no base and no
    diff: neither *on_base_resolved* nor *on_added_resolved* is ever called
    on that path, and *base* itself is not read at all. ``judge.
    require_branch`` (absent means ``False``) is a MEASURABILITY guard --
    checked once, beside :func:`~assay.coverage.check_empty_coverage`,
    before mode dispatch, never inside either evaluate function -- so an
    artifact whose branch capability is ``"unavailable"`` renders
    ``NO_MEASUREMENT``/``BRANCH_UNAVAILABLE`` regardless of which mode was
    declared.
    """
    judge = lane.judge
    effective_mode = judge.mode if judge.mode is not None else "changed_lines"
    effective_require_branch = (
        judge.require_branch if judge.require_branch is not None else False
    )
    try:
        measurability.check_dirty_tree(repo, judge.source_root_paths, remaining=remaining)

        resolved = None
        if effective_mode == "changed_lines":
            resolved = measurability.check_base_is_head(repo, base, remaining=remaining)
            if on_base_resolved is not None:
                on_base_resolved(resolved.base_rev)

        if profile is None:
            profile = coverage.read_coverage_artifact(
                project_root, judge.coverage.artifact, declared_format=judge.coverage.format
            )
        coverage.check_empty_coverage(profile)

        if effective_require_branch and derive_branch_capability(profile) == "unavailable":
            raise AssayError(
                "judge.require_branch is true but the coverage artifact's "
                "branch capability is 'unavailable' -- this format cannot "
                "report branch arcs at all, so judging on lines alone would "
                "be exactly the silent rigor downgrade require_branch "
                "exists to refuse",
                outcome=Outcome.NO_MEASUREMENT,
                reason_code=ReasonCode.BRANCH_UNAVAILABLE,
            )

        repo_top = git.repo_top(repo, remaining=remaining)

        if effective_mode == "whole_target":
            result = evaluate_targets(
                profile=profile,
                adapter=adapter,
                repo_top=repo_top,
                project_root=project_root,
                targets=judge.targets or (),
                source_root_paths=judge.source_root_paths,
                fail_under=judge.fail_under,
                allow_excluded=judge.allow_excluded,
            )
        else:
            diff_text = git.run(
                repo, "diff", "--unified=0", resolved.base_rev, resolved.head_rev,
                remaining=remaining,
            )
            added = diff.parse_added_lines(diff_text)
            if on_added_resolved is not None:
                on_added_resolved(added)

            def read_source_text(path: str) -> str:
                return _read_bounded_source_text(repo_top, path, why="coverage classification")

            result = evaluate_coverage(
                added=added,
                profile=profile,
                adapter=adapter,
                repo_top=repo_top,
                project_root=project_root,
                source_root_paths=judge.source_root_paths,
                fail_under=judge.fail_under,
                allow_excluded=judge.allow_excluded,
                read_source_text=read_source_text,
            )
    except AssayError as exc:
        return Claim(
            rigor="R1",
            source="computed",
            status=exc.outcome,
            verified_by_assay=True,
            reason_code=exc.reason_code,
        )

    return Claim(
        rigor="R1",
        source="computed",
        status=result.outcome,
        verified_by_assay=True,
        reason_code=result.reason_code,
        coverage=Coverage(
            covered=result.covered,
            executable=result.executable,
            pct=result.pct,
            considered=result.considered,
            exclusion_capability=result.exclusion_capability,
            missing_lines=result.missing_lines,
            files_missing_coverage=result.files_missing_coverage,
            unclassified_lines=result.unclassified_lines,
            files_with_unclassified_lines=result.files_with_unclassified_lines,
            excluded_lines=result.excluded_lines,
            files_with_excluded_lines=result.files_with_excluded_lines,
            branches_covered=result.branches_covered,
            branches_total=result.branches_total,
            branch_capability=result.branch_capability,
            missing_branch_lines=result.missing_branch_lines,
            files_with_missing_branch_lines=result.files_with_missing_branch_lines,
        ),
    )


def _lane_declared_evidence(lane: Lane) -> tuple[EvidenceDeclaration, ...]:
    """The lane's own authoritative ordered evidence identities (P26/A-213) --
    derived from ``lane.judge.evidence``, never from a caller-supplied
    default. Empty exactly when the lane declares none at all.
    """
    judge = lane.judge
    if judge is None or judge.evidence is None:
        return ()
    return tuple(
        EvidenceDeclaration(source=item.source, key=item.key) for item in judge.evidence
    )


def _require_evidence_bound_to_lane(
    lane: Lane,
    declared_evidence: tuple[EvidenceDeclaration, ...],
    evidence: tuple[Evidence, ...],
) -> None:
    """Refuse before any Git/plan/adapter/command work (P26/A-213) unless
    BOTH *declared_evidence* and *evidence* are exactly the lane's own
    authoritative ordered identities.

    An unchecked empty-tuple default at a runner/assembly boundary is a
    shadowing default (AGENTS.md §4.2a): it can silently substitute for
    declarations the lane already names. Empty is legal only when the lane's
    own derived source is itself empty.
    """
    authoritative = [item.identity for item in _lane_declared_evidence(lane)]
    declared_identities = [item.identity for item in declared_evidence]
    if declared_identities != authoritative:
        raise AssayError(
            f"lane {lane.name!r} declares evidence {authoritative} but was "
            f"called with declared_evidence {declared_identities} -- refusing "
            f"before any work",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    resolved_identities = [item.identity for item in evidence]
    if resolved_identities != authoritative:
        raise AssayError(
            f"lane {lane.name!r} declares evidence {authoritative} but was "
            f"called with evidence {resolved_identities} -- refusing before "
            f"any work",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )


def assemble_verdict(
    *,
    lane: Lane,
    commit: str,
    result: CommandResult,
    claims: tuple[Claim, ...],
    assay_version: str,
    evidence: tuple[Evidence, ...] = (),
    declared_evidence: tuple[EvidenceDeclaration, ...] = (),
    mutation_claim: Claim | None = None,
    judgment: Judgment | None = None,
    ended: str | None = None,
    env_effective_incomplete: bool = False,
) -> Verdict:
    """Final verdict assembly (A-094): separable from :func:`execute_command`.

    Takes the WHOLE ``claims`` tuple (not just R0's), timing and the resolved
    plan from *result*. P05 inserts its own R1 evaluation between calling
    :func:`execute_command`/:func:`build_r0_claim` and calling this function,
    and passes ``claims=(r0_claim, r1_claim)`` -- no restructuring of this
    function required (A-094's whole point). P10 (this addition) is the same
    shape one level over: *evidence* and *declared_evidence* default to empty
    tuples, so every existing caller (through P09) is unaffected, and a
    caller that has resolved attested evidence (:func:`assay.attestation.
    load_attested_evidence`) passes it straight through without this
    function needing to know anything about HOW it was produced.

    Refuses (``ERROR``/``BAD_LANE_CONFIG``) BEFORE constructing a
    :class:`~assay.verdict.Verdict` if *claims* does not cover every level in
    ``lane.rigor``, or if *evidence* does not cover *declared_evidence*
    exactly -- this build (through P04) only ever passes an R0 claim, so a
    lane declaring ``rigor = ["R0", "R1"]`` is refused here rather than
    reaching :class:`~assay.verdict.Verdict`'s own internal invariant, which
    raises a bare ``ValueError`` no caller catches; the identical reasoning
    applies to a caller that declares evidence it never resolved (or resolves
    evidence it never declared). Deliberately placed here rather than in
    ``cli.py``: this module is in every later producer package's
    ``scope.touch`` (P05, P08, P09, P10...), so the guard self-obsoletes --
    once a package supplies the missing claim or evidence, ``missing`` is
    empty and this never fires for that identity again, with no file only
    this package could touch left to update.

    *mutation_claim* (P12, A-119) is the R2 wiring, the same shape one more
    level over: an optional :class:`~assay.verdict.Claim` -- built by
    :func:`assay.mutation.build_mutation_claim`, this function never
    constructs one itself, matching how *evidence*/*declared_evidence* are
    threaded through without this function owning their internals -- that,
    when given, is appended to *claims* before every check below runs, so a
    caller building ``claims=(r0_claim, r1_claim)`` does not also have to
    remember to fold the R2 claim into that tuple by hand. Passing a
    *mutation_claim* whose own ``rigor`` already appears in *claims* is
    refused the same way a duplicate rigor level is refused anywhere else in
    this project (:class:`~assay.verdict.Verdict`'s own
    ``_check_claims_cover_declared_rigor``) -- constructing the combined
    tuple and letting that existing check catch it, rather than a bespoke
    second one here.

    *judgment* (P16) is the resolved judge policy behind whichever claims
    rendered a real computed judgment -- built entirely by the CALLER (this
    function does not resolve source roots, coverage format, or a
    comparison commit itself) and passed straight through. ``scope`` and
    ``enforcement`` need no such parameter: both are already static *lane*
    attributes, so this function derives them from *lane* on every call,
    the same way ``argv_declared``/``env_declared`` already come from
    *result.plan*. When *claims* (after folding in *mutation_claim*)
    contains an R1 claim carrying a ``coverage`` payload but *judgment*
    supplies no ``r1`` policy, this function refuses
    (``ERROR``/``BAD_LANE_CONFIG``) before construction -- the identical
    reasoning as the missing-claims/missing-evidence guards above: a bare
    ``ValueError`` from :class:`~assay.verdict.Verdict` itself is not an
    ``AssayError`` and no caller catches it.

    The verdict's own ``outcome`` and ``reason_code`` are otherwise DERIVED
    from ``claims`` AND ``evidence`` together via :func:`~assay.verdict.rollup`
    (A-023), never chosen independently -- :class:`~assay.verdict.Verdict`
    would refuse a mismatch anyway, but deriving it here means this function
    cannot construct one. An ``ERROR``/``UNREADABLE_ARTIFACT`` evidence entry
    (a broken attestation, A-110) outranks every claim's own status the same
    way it would if it were a claim -- ``rollup`` does not distinguish which
    array a status came from, matching :class:`~assay.verdict.Verdict`'s own
    ``_check_outcome_agrees_with_rollup``.

    *ended* (P17, O2) overrides ``result.ended`` for the verdict's own
    ``ended`` timestamp -- ``started`` always stays ``result.started``.
    Every caller through P16 leaves it ``None`` (``result.ended`` is R0's
    own, and R0 was the only work done), so behaviour is unchanged for all
    of them. :func:`run_lane` passes the real timestamp taken AFTER R1
    evaluation completes: "verdict timing encloses command plus R1
    judgment" (O2) means the window a consumer sees must cover BOTH steps,
    not just R0's, and R1 has no started/ended of its own in schema v3 to
    carry that separately (there is exactly one ``started``/``ended`` pair
    per verdict) -- widening the ONE pair this function already emits is
    the only way to make that honest without a schema change P16 already
    closed the book on.
    """
    _require_evidence_bound_to_lane(lane, declared_evidence, evidence)
    claims = claims if mutation_claim is None else (*claims, mutation_claim)
    covered = {claim.rigor for claim in claims}
    missing = [level for level in lane.rigor if level not in covered]
    if missing:
        raise AssayError(
            f"lane {lane.name!r} declares rigor {list(lane.rigor)} but this "
            f"assay build only computed claims for {sorted(covered)} -- "
            f"{missing} evaluation lands in a later package. Refusing before "
            f"constructing an incomplete verdict.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    r1_claim = next((claim for claim in claims if claim.rigor == "R1"), None)
    r1_judged = r1_claim is not None and r1_claim.coverage is not None
    judgment_r1 = None if judgment is None else judgment.r1
    if r1_judged and judgment_r1 is None:
        raise AssayError(
            f"lane {lane.name!r} rendered an R1 claim carrying a coverage "
            f"payload, but no judgment.r1 policy was supplied -- an "
            f"independent consumer cannot re-derive R1's status from "
            f"coverage alone. Refusing before constructing an incomplete "
            f"verdict.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    statuses = [claim.status for claim in claims]
    statuses.extend(item.status for item in evidence)
    outcome = rollup(statuses)
    reason_code = None
    if outcome is not Outcome.PASS:
        reason_code = next(
            item.reason_code for item in (*claims, *evidence) if item.status is outcome
        )
    plan = result.plan
    return Verdict(
        lane=lane.name,
        commit=commit,
        outcome=outcome,
        reason_code=reason_code,
        started=result.started,
        ended=result.ended if ended is None else ended,
        assay_version=assay_version,
        declared_rigor=lane.rigor,
        declared_evidence=declared_evidence,
        argv_declared=plan.argv_declared,
        argv_appended=plan.argv_appended,
        argv_effective=plan.argv_effective,
        env_declared=plan.env_declared,
        env_effective=plan.env_effective,
        env_effective_incomplete=env_effective_incomplete,
        scope=lane.scope,
        enforcement=lane.enforcement,
        result_stdout_tail=result.stdout_tail,
        result_stderr_tail=result.stderr_tail,
        result_stdout_dropped_bytes=result.stdout_dropped_bytes,
        result_stderr_dropped_bytes=result.stderr_dropped_bytes,
        judgment=judgment,
        claims=claims,
        evidence=evidence,
        snapshot_policy=_verdict_snapshot_policy(lane),
    )


def _coverage_artifact_is_tracked(
    repo: Path, project_root: Path, artifact: str, *, remaining: git.Remaining | None = None
) -> bool:
    """True when the project-relative *artifact* is already tracked by git
    (P17 work item 3, closing sol finding 6's coverage-artifact half):
    measurement output has no business being committed, and this run is
    about to overwrite it (A-140's own consequence: the declared artifact
    must be git-ignored or absent).

    The symlink/non-regular-destination half of the OLD check is now owned
    by :func:`assay.safeio.reserve_output` itself (P20) -- constructing the
    reservation already refuses those before this function is even called,
    so this is the one remaining fact only git can answer.
    """
    artifact_path = project_root / artifact
    tracked = git.run(repo, "ls-files", "--", str(artifact_path), remaining=remaining)
    return bool(tracked.strip())


def refuse_lane(
    lane: Lane,
    *,
    commit: str,
    status: Outcome,
    reason_code: ReasonCode,
    argv_append: Sequence[str] = (),
    passthrough_source: Mapping[str, str] | None = None,
    project_prefix: PurePosixPath | None = None,
    infrastructure_source: Path | None = None,
    infrastructure_environment: Mapping[str, str] | None = None,
    assay_version: str,
    evidence: tuple[Evidence, ...] = (),
    declared_evidence: tuple[EvidenceDeclaration, ...] = (),
    clock: Clock = _utc_now,
) -> Verdict:
    """Refuse the WHOLE invocation before the lane's own command ever
    starts (work items 3/5): the command's own :class:`CommandPlan` is
    still resolved and recorded (A-036 -- a refused run is not an
    unrecorded one), but *process_runner* is never called. EVERY declared
    rigor level renders the exact SAME ``(status, reason_code)``: one root
    cause stopped the whole run, never several independently-derived
    stories about the same fact -- and none carries a payload, which a
    ``NO_MEASUREMENT`` claim (A-025) and an ``ERROR`` claim (A-136) both
    already require unconditionally.

    The claim set is built from ``lane.rigor`` itself rather than from a
    "which levels did this build wire up" flag (A-139): the ONE thing
    :func:`assemble_verdict` demands is a claim per DECLARED level, so
    deriving the tuple from the declaration is what makes this function
    total -- it can honestly refuse an ``R2``/``R3`` lane this build
    cannot evaluate, which is exactly the terminal path :mod:`assay.cli`
    used to let escape as a bare :class:`~assay.errors.AssayError` with no
    artifact at all even though ``HEAD`` was already known (work item 3's
    "every later terminal path must emit a complete artifact").

    Public, unlike the rest of this module's helpers, because
    :mod:`assay.cli` is the caller that owns the registry-capability
    refusals (work item 2) and must render them as artifacts here rather
    than re-deriving the shape itself.

    *project_prefix* (A-193) is the refusing caller's own repo-relative
    project identity, when it HAS one -- :func:`run_lane`'s direct pre-run
    dirty-tree refusal does, and passes it. It stays ``None`` by default
    precisely for :mod:`assay.cli`'s pre-adapter refusal, which happens
    before repository identity has entered that API at all: an honest
    absence, never an invented ``.``.

    *evidence*/*declared_evidence* (P26/A-213) are bound to
    ``lane.judge.evidence`` BEFORE the plan is even resolved: a refused run
    still owes every declared evidence identity a complete artifact entry.
    """
    _require_evidence_bound_to_lane(lane, declared_evidence, evidence)
    env_effective_incomplete = False
    try:
        plan = resolve_command_plan(
            lane,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            project_prefix=project_prefix,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
        )
    except AssayError:
        # (B025) This call exists only to RECORD the attempted plan on an
        # ALREADY-DECIDED refusal (A-036) -- every raise inside
        # `resolve_command_plan` is over its own infrastructure/
        # `env_passthrough` declarations (BAD_LANE_CONFIG), so a failure
        # here means the infrastructure declaration itself, not *status*/
        # *reason_code* above, is what's unresolvable. The caller's own
        # refusal cause must still produce an artifact, so this falls back
        # to what's always safe to record -- argv never touches
        # infrastructure -- and reports env_effective as exactly `lane.env`
        # (the one thing known for certain, never infrastructure or
        # `env_passthrough` values, neither of which could be safely
        # completed), flagged honestly incomplete rather than invented.
        argv_effective = tuple(lane.argv) + tuple(argv_append)
        plan = CommandPlan(
            argv_declared=tuple(lane.argv),
            argv_appended=tuple(argv_append),
            argv_effective=argv_effective,
            env_declared=MappingProxyType(dict(lane.env)),
            env_effective=MappingProxyType(dict(lane.env)),
            env_passthrough=tuple(lane.env_passthrough),
            allow_argv_append=lane.allow_argv_append,
            budget_seconds=lane.budget_seconds,
            project_prefix=project_prefix,
        )
        env_effective_incomplete = True
    return _refuse_lane_with_plan(
        lane,
        commit=commit,
        plan=plan,
        env_effective_incomplete=env_effective_incomplete,
        status=status,
        reason_code=reason_code,
        assay_version=assay_version,
        evidence=evidence,
        declared_evidence=declared_evidence,
        clock=clock,
    )


def _refuse_lane_with_plan(
    lane: Lane,
    *,
    commit: str,
    plan: CommandPlan,
    status: Outcome,
    reason_code: ReasonCode,
    assay_version: str,
    evidence: tuple[Evidence, ...] = (),
    declared_evidence: tuple[EvidenceDeclaration, ...] = (),
    clock: Clock = _utc_now,
    env_effective_incomplete: bool = False,
) -> Verdict:
    """The identical shape :func:`refuse_lane` builds, given an
    ALREADY-RESOLVED *plan* (P23/A-193): the higher-rigor path resolves its
    one immutable plan before any dirt/HEAD/snapshot work, so a pre-baseline
    refusal must record that exact plan rather than resolving a second one
    (O1's "no repeated unit receives lane... as a source of command truth").

    *env_effective_incomplete* (B025) is `refuse_lane`'s own signal that
    *plan* is the degraded fallback built when infrastructure resolution
    itself failed -- always `False` for every OTHER caller, whose *plan* is
    always a real, fully-resolved one.
    """
    started = clock()
    ended = clock()
    rigor_levels = tuple(lane.rigor)
    claims = tuple(
        Claim(
            rigor=level,
            source="computed",
            status=status,
            verified_by_assay=True,
            reason_code=reason_code,
        )
        for level in rigor_levels
    )
    result = CommandResult(
        plan=plan,
        outcome=status,
        reason_code=reason_code,
        returncode=None,
        started=iso_utc(started),
        ended=iso_utc(ended),
    )
    return assemble_verdict(
        lane=lane,
        commit=commit,
        result=result,
        claims=claims,
        assay_version=assay_version,
        evidence=evidence,
        declared_evidence=declared_evidence,
        env_effective_incomplete=env_effective_incomplete,
    )


# ---------------------------------------------------------------------------
# P23: exact reexecution integration -- the committed-snapshot state machine
# every lane declaring R1, R2, or R3 uses instead of the direct path above
# (A-189). isolation.py (P22) owns Git-object isolation/refusal; this module
# owns the plan/deadline/scratch seams and R0/R1's own orchestration,
# delegating R2 to mutation.py and R3 to canary.py -- both of which consume
# the ONE shared unit engine, :func:`_execute_snapshot_unit`, below.
# ---------------------------------------------------------------------------


def _resolved_project_prefix(repo_top: Path, project_root: Path) -> PurePosixPath:
    """*project_root*'s own normalized, repo-top-relative POSIX location --
    the ONE conversion the namespace map allows (``.`` at the repository
    root), written once and shared by BOTH dispatch states.

    Reviewer repair: the direct R0-only path resolved its plan without a
    prefix, so its :attr:`CommandPlan.project_prefix` read ``None`` -- the
    spelling A-193 reserves for "repository identity does not exist yet"
    (a pre-adapter :mod:`assay.cli` refusal, or a standalone helper). Direct
    :func:`run_lane` already knows both facts by the time it executes, so
    ``None`` there was a shadowing default in the exact sense AGENTS.md names:
    a literal standing in for a value with an authoritative source. Refusing
    a *project_root* outside its own repository is likewise a real check
    rather than an invented ``.``, and is the same containment the
    higher-rigor state machine's own step 1.2 already applies.
    """
    try:
        relative = project_root.resolve().relative_to(repo_top)
    except ValueError as exc:
        raise AssayError(
            f"the project root {project_root} is not contained by its own "
            f"repository top {repo_top}; a lane's command has no repo-relative "
            f"identity there",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        ) from exc
    return (
        PurePosixPath(".") if relative == Path(".") else PurePosixPath(relative.as_posix())
    )


def _snapshot_policy_for_lane(lane: Lane) -> IsolationConfig | None:
    """(B006a/A-269 WI-2, §3.3 item 1) The R0/R1+ isolation conditional,
    re-enforced HERE at the runner boundary.

    ``config.py``'s loader already enforces this identical rule
    (``_load_isolation_for_lane``) for every TOML-sourced lane; this is the
    SAME rule checked again because a caller may construct a public
    :class:`~assay.config.Lane` directly, bypassing the loader entirely
    (§3.5's own "a public directly-constructed Lane violates the R0/R1+
    isolation conditional"). Returns ``None`` -- never a fabricated
    repository default -- for a valid R0-only/no-isolation pair; returns
    ``lane.isolation`` BY IDENTITY (never copied or renormalised) for a
    valid R1+/isolation pair; raises ``ERROR``/``BAD_LANE_CONFIG`` for
    either impossible direct-constructor pairing.

    Called by :func:`run_lane` before it chooses between the direct R0-only
    path and :func:`_run_higher_rigor_lane`, and (WI-4) by
    ``assemble_verdict`` -- ONE helper, so the runner and the verdict
    producer can never disagree about which policy a lane carries.
    """
    higher_rigor = any(level != "R0" for level in lane.rigor)
    if higher_rigor:
        if lane.isolation is None:
            raise AssayError(
                f"lane {lane.name!r} declares rigor {list(lane.rigor)} but "
                f"isolation is None; R1/R2/R3 requires an explicit "
                f"snapshot_policy, and this runner never invents a "
                f"repository default for one",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        return lane.isolation
    if lane.isolation is not None:
        raise AssayError(
            f"lane {lane.name!r} declares rigor {list(lane.rigor)} (R0-only) "
            f"but isolation is {lane.isolation!r}; an R0-only lane runs no "
            f"snapshot and must not declare a policy for one",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )
    return None


def _verdict_snapshot_policy(lane: Lane) -> SnapshotPolicy | None:
    """(B006(a)/A-269 WI-4, §5.2) The WIRE ``snapshot_policy`` for *lane* --
    ``None`` exactly when :func:`_snapshot_policy_for_lane` returns
    ``None`` (an R0-only lane), and the closed-enum copy of
    ``lane.isolation`` otherwise. Called by :func:`assemble_verdict`, which
    is the SINGLE construction site every producer path -- normal
    completion, ``refuse_lane``, the attestation-timeout/adapter-refusal
    CLI branches, dirty-tree/HEAD-drift refusals, and the generic
    ``except AssayError`` mapping -- already funnels through, so this is
    the one place the runner and the verdict producer can never disagree
    about which policy a lane carries (the identical "one helper, not two
    copies that could drift" reasoning :func:`_snapshot_policy_for_lane`'s
    own docstring already gives).
    """
    policy = _snapshot_policy_for_lane(lane)
    if policy is None:
        return None
    return SnapshotPolicy(
        selection=policy.snapshot_selection,
        unsafe_symlink_omissions=(
            policy.unsafe_symlink_omissions
            if policy.snapshot_selection == "repository-minus-unsafe-symlinks"
            else None
        ),
    )


def _refuse_coverage_artifact_omission_collision(
    *,
    lane: Lane,
    snapshot_policy: IsolationConfig,
    project_prefix: PurePosixPath,
) -> None:
    """(B006a/A-269 WI-3, §3.4) **Public-API defence-in-depth** -- NOT a
    loadable-TOML preflight. A lane loaded from a real ``assay.toml`` cannot
    normally reach this branch at all: ``config._load_coverage`` already
    refuses a coverage artifact whose OWN spelling escapes the project root
    through a live symlink, at load time, long before any snapshot exists.
    This function exists for the shape that check cannot see -- a caller
    that builds the public frozen :class:`~assay.config.Lane` dataclass
    directly, bypassing the loader entirely, the same reachability §3.4
    grants ``_snapshot_policy_for_lane`` immediately above.

    Refuses ``ERROR``/``BAD_LANE_CONFIG`` when *lane*'s declared coverage
    artifact, converted to a repo-top-relative :class:`~pathlib.PurePosixPath`
    (never a string prefix, never a local :meth:`~pathlib.Path.resolve`
    against a commit namespace -- both would compare the wrong namespace or
    trust a filesystem fact this snapshot's own commit does not own), is
    equal to or beneath a declared unsafe-symlink omission. Without this
    check, B006(b)'s own missing-parent-chain creation could silently
    replace an omitted committed symlink with a generated ordinary
    directory the very first time the command's coverage artifact is
    reserved -- exactly the "ancestor of the coverage-artifact path" row
    §3.6's own outcome matrix names.

    Called ONCE per lane, by :func:`_run_higher_rigor_lane`, after
    ``isolation.prepare_snapshot`` has already returned (so every omission
    compared here already survived P22's own commit-tree validation: an
    unreadable declared target still fails ``GIT_FAILED`` and a declaration
    that turns out not to be a real unsafe symlink still fails
    ``BAD_LANE_CONFIG``, both from *inside* ``prepare_snapshot`` itself,
    never from this function) but BEFORE the first
    ``prepared.materialize()`` -- this is deliberately the very first thing
    that runs once a prepared snapshot exists, before any unit (baseline,
    mutant, or either canary half) ever touches a filesystem.

    A no-op in repository mode (there are no declared omissions to collide
    with) and for a lane with no declared coverage artifact at all -- an
    R2- or R3-only lane never reserves one.
    """
    if snapshot_policy.snapshot_selection != "repository-minus-unsafe-symlinks":
        return
    if lane.judge is None or lane.judge.coverage is None:
        return
    artifact_repo_path = PurePosixPath(
        (project_prefix / lane.judge.coverage.artifact).as_posix()
    )
    for omitted in snapshot_policy.unsafe_symlink_omissions:
        omitted_path = PurePosixPath(omitted)
        if artifact_repo_path.is_relative_to(omitted_path):
            raise AssayError(
                f"lane {lane.name!r}'s coverage artifact "
                f"{lane.judge.coverage.artifact!r} resolves to "
                f"{artifact_repo_path.as_posix()!r}, which is equal to or "
                f"beneath the declared unsafe-symlink omission {omitted!r}; "
                f"B006(b)'s coverage-artifact parent-directory creation "
                f"must never be allowed to silently replace an omitted "
                f"committed symlink with a generated directory",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )


def _relocate_source_roots(
    lane: Lane, *, project_root: Path, scratch_project_root: Path
) -> Lane:
    """*lane* with :attr:`~assay.config.JudgeConfig.source_root_paths`
    respelled against *scratch_project_root* instead of *project_root*
    (A-149) -- the ONE written copy of this respelling, shared by this
    module's own baseline/R2-target-discovery use and by
    :mod:`assay.canary`'s control/transform halves, rather than two
    independently drifting copies.

    ``source_root_paths`` are RESOLVED, ABSOLUTE directories under the
    CONSUMER's own project root; every judgement made inside a snapshot
    compares them against paths under THAT snapshot's own project root.
    Only ``source_root_paths`` needs respelling: every other path-bearing
    field a snapshot is judged through is already project-relative and
    resolved against whichever project root it is handed, and ``judge.base``
    is a revision, not a path.
    """
    judge = lane.judge
    relocated = tuple(
        scratch_project_root / root.relative_to(project_root)
        for root in judge.source_root_paths
    )
    return replace(lane, judge=replace(judge, source_root_paths=relocated))


@dataclass(frozen=True, kw_only=True)
class SnapshotUnitResult:
    """One executed higher-rigor unit (P23): the real :class:`CommandResult`,
    the post-run Git-state verdict (``None`` when the snapshot is still
    clean and still names its own commit), and -- only when coverage was
    requested -- either the freshly parsed profile or the
    :class:`~assay.errors.AssayError` its own reservation/consume/parse
    raised.

    *baseline_equivalence*/*equivalence_error* (P34/§3.6) are the identical
    shape one field over, for R2's declared ``equivalence_artifact`` -- only
    populated when a caller passed one in. Unlike coverage's *profile_error*
    (which can be a parse failure among other things), *equivalence_error*
    is set for exactly ONE fact this dataclass's own producer distinguishes
    from a genuine consume failure: the baseline declared the artifact and
    its own command did not write it (A-279's "never a comparison against
    None" -- :func:`~assay.mutation.run_mutation` refuses that outright, so
    this is where the caller must resolve real bytes or stop before it).
    """

    result: CommandResult
    post_reason: ReasonCode | None
    profile: CoverageProfile | None
    profile_error: AssayError | None
    baseline_equivalence: bytes | None = None
    equivalence_error: AssayError | None = None


def _execute_snapshot_unit(
    *,
    plan: CommandPlan,
    snapshot: "isolation.Snapshot",
    deadline: LaneDeadline,
    wants_coverage: bool,
    coverage_artifact: str | None,
    coverage_format: str | None,
    process_runner: ProcessRunner,
    clock: Clock,
    create_missing_parents: bool = False,
    equivalence_artifact: str | None = None,
    kill_signal_artifact: str | None = None,
) -> SnapshotUnitResult:
    """Run *plan* once inside *snapshot* -- the ONE engine the lane's own
    baseline, :mod:`assay.canary`'s control/transform halves, and (with its
    own copy of the post-run dirt/HEAD check, since a mutant additionally
    needs P22's replacement seam mid-flight) :mod:`assay.mutation`'s own
    workers all consume.

    Mirrors the direct path's own ordering one level down: reserve -> arm ->
    execute -> post-run dirt/HEAD check -> consume/parse. Reservation
    construction or a tracked-artifact check are PREREQUISITE failures and
    are RAISED -- the caller decides what "this unit could never even be
    attempted" means for its own claim scope. A post-run Git-state violation
    is reported via ``post_reason`` rather than raised, because only the
    caller knows whether an already-produced REAL R0 result (the lane's own
    baseline) must be preserved despite it (A-195, mirroring P20's own
    direct-path claim precedence one level down).

    *create_missing_parents* (B006(b)) defaults to ``False`` and is threaded
    straight to :func:`assay.safeio.reserve_output`'s own identically-named,
    identically-defaulted parameter -- this function's own default is never
    the thing that decides whether *snapshot*'s tracked-only checkout gets a
    generated parent directory. Every caller of this function DOES run
    entirely inside an ephemeral P22 snapshot (there is no other caller), so
    every one of them passes ``True`` explicitly; the default stays ``False``
    here anyway, rather than flipping it, so this function's own contract --
    read on its own, with no caller in view -- still says "does not create"
    until a caller affirmatively asks otherwise. A consumer's REAL worktree
    is never reachable through this function at all: *snapshot* is always
    :mod:`assay.isolation`'s own private, owned checkout.

    *equivalence_artifact*/*kill_signal_artifact* (P34/§3.6/§3.7) are the
    R2 baseline's own SQL-only artifact declarations, ``None`` for every
    other lane (identical disposition to *coverage_artifact* under
    ``wants_coverage``). Both get the SAME tracked-by-git refusal the
    coverage artifact already gets (§3.7's "either artifact tracked ...
    -> `runner._execute_snapshot_unit`"), checked here because a git-tracked
    status is a fact about the COMMIT, invariant across every later mutant's
    own single-blob-replacement snapshot of it -- one check at baseline time
    covers all of them. Only *equivalence_artifact* is reserved/armed/
    consumed: the baseline command is the lane's own ``apply && dump &&
    test`` run once (A-279), so its OWN dump is read here exactly like
    coverage's profile one field over; *kill_signal_artifact* names a file a
    PASSING baseline has no reason to write, so it gets the tracked check
    only, never a reservation.
    """
    reservation: safeio.OutputReservation | None = None
    if wants_coverage:
        assert coverage_artifact is not None and coverage_format is not None
        reservation = safeio.reserve_output(
            snapshot.project_root,
            coverage_artifact,
            limit=coverage.MAX_COVERAGE_ARTIFACT_BYTES,
            create_missing_parents=create_missing_parents,
        )
        if _coverage_artifact_is_tracked(
            snapshot.root, snapshot.project_root, coverage_artifact,
            remaining=deadline.remaining,
        ):
            reservation.close()
            raise AssayError(
                f"the declared coverage artifact {coverage_artifact!r} is "
                f"tracked by git inside the prepared snapshot -- "
                f"measurement output must not be committed",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        reservation.arm()

    equivalence_reservation: safeio.OutputReservation | None = None
    if equivalence_artifact is not None:
        equivalence_reservation = safeio.reserve_output(
            snapshot.project_root,
            equivalence_artifact,
            limit=mutation.MAX_EQUIVALENCE_ARTIFACT_BYTES,
            create_missing_parents=create_missing_parents,
        )
        if _coverage_artifact_is_tracked(
            snapshot.root, snapshot.project_root, equivalence_artifact,
            remaining=deadline.remaining,
        ):
            equivalence_reservation.close()
            if reservation is not None:
                reservation.close()
            raise AssayError(
                f"the declared equivalence_artifact {equivalence_artifact!r} "
                f"is tracked by git inside the prepared snapshot -- "
                f"measurement output must not be committed",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        equivalence_reservation.arm()

    if kill_signal_artifact is not None and _coverage_artifact_is_tracked(
        snapshot.root, snapshot.project_root, kill_signal_artifact,
        remaining=deadline.remaining,
    ):
        if equivalence_reservation is not None:
            equivalence_reservation.close()
        if reservation is not None:
            reservation.close()
        raise AssayError(
            f"the declared kill_signal_artifact {kill_signal_artifact!r} is "
            f"tracked by git inside the prepared snapshot -- measurement "
            f"output must not be committed",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )

    result = execute_plan(
        plan,
        cwd=snapshot.project_root,
        timeout=deadline.remaining(),
        process_runner=process_runner,
        clock=clock,
    )

    post_reason: ReasonCode | None = None
    if git.dirty_paths(snapshot.root, remaining=deadline.remaining):
        post_reason = ReasonCode.DIRTY_TREE
    elif git.head_rev(snapshot.root, remaining=deadline.remaining) != snapshot.commit:
        post_reason = ReasonCode.HEAD_CHANGED

    profile: CoverageProfile | None = None
    profile_error: AssayError | None = None
    if post_reason is None and wants_coverage:
        assert reservation is not None
        try:
            raw = reservation.consume()
            profile = coverage.parse_coverage_artifact(raw, declared_format=coverage_format)
        except AssayError as exc:
            profile_error = exc
    if reservation is not None:
        reservation.close()

    baseline_equivalence: bytes | None = None
    equivalence_error: AssayError | None = None
    if post_reason is None and equivalence_reservation is not None:
        try:
            baseline_equivalence = equivalence_reservation.consume()
        except AssayError as exc:
            equivalence_error = exc
        else:
            if baseline_equivalence is None:
                # A-279: never a comparison against None. The baseline
                # declared this artifact and its own command did not write
                # it -- no mutant is even attempted (§3.7's own row).
                equivalence_error = AssayError(
                    f"the baseline declared judge.mutation.equivalence_artifact "
                    f"{equivalence_artifact!r} but its own command did not "
                    f"write it -- R2 needs the baseline's own bytes to prove "
                    f"any mutant equivalent, so no mutant can even be "
                    f"attempted",
                    outcome=Outcome.ERROR,
                    reason_code=ReasonCode.EXEC_FAILED,
                )
    if equivalence_reservation is not None:
        equivalence_reservation.close()

    return SnapshotUnitResult(
        result=result,
        post_reason=post_reason,
        profile=profile,
        profile_error=profile_error,
        baseline_equivalence=baseline_equivalence,
        equivalence_error=equivalence_error,
    )


def _read_prepared_source_text(
    prepared: "isolation.SnapshotRepository", path: str, *, deadline: LaneDeadline
) -> str:
    """Read one repo-top-relative mutation-target source file through the
    prepared P22 seed (never a consumer/snapshot disk path, per the
    handoff's own "read source only through prepared.read_regular_file"),
    applying the identical 8 MiB ceiling and strict UTF-8 decode
    :func:`_read_bounded_source_text` already applies to the direct path.
    """
    raw = prepared.read_regular_file(PurePosixPath(path), timeout=deadline.remaining())
    if len(raw) > MAX_SOURCE_FILE_BYTES:
        raise AssayError(
            f"source file {path!r} is {len(raw)} bytes, exceeding the "
            f"{MAX_SOURCE_FILE_BYTES}-byte bound for mutation target resolution",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssayError(
            f"source file {path!r} is not valid UTF-8 and could not be read "
            f"for mutation target resolution: {exc}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        ) from exc


def _mutation_targets_from_diff(
    added: diff.AddedLines,
    *,
    prepared: "isolation.SnapshotRepository",
    deadline: LaneDeadline,
    adapter: LanguageAdapter,
    snapshot_repo_top: Path,
    source_root_paths: Sequence[Path],
) -> tuple[mutation.MutationTarget, ...]:
    """R2's per-file candidate list, from P18's own landed
    :func:`~assay.mutation.resolve_mutation_targets` -- the SAME four gates
    the direct path applied before P23 (under a declared source root, not
    inside one of the adapter's excluded directories, matching one of its
    ``source_globs``, and not one of its test paths).

    P23 changes only WHERE the bytes come from: *read_source_text* is bound
    to the prepared P22 seed (:func:`_read_prepared_source_text`) instead of
    the consumer's work tree, so the "never reopen a consumer path" rule
    holds while the selection policy stays exactly the landed one.

    Reviewer repair (phase 2). This function previously applied ONLY the
    source-root containment gate, on the reasoning that the other three were
    "R1's which-files-are-considered-for-coverage policy". They are not:
    :func:`~assay.mutation.resolve_mutation_targets`'s own docstring names
    the ``is_test_path`` exclusion as an ADDITION to R1's ``_is_considered``,
    made specifically for R2. Dropping them produced two reproduced defects
    on ordinary repositories:

    * a changed non-Python file under a declared source root (a ``src/
      NOTES.md``, a ``.json`` fixture, a ``.pyi`` stub) reached
      ``PythonAdapter.generate_mutation_sites``, which raises
      ``MutationDiscoveryError`` on unparseable text -- collapsing the whole
      verdict to ``ERROR``/``MUTATION_DISCOVERY_FAILED`` for a repository
      that measured normally before P23; and
    * a changed ``test_*.py`` under a declared source root became a real
      mutant identity in the R2 payload, so the suite "killed" a mutant by
      having its own test broken -- a false measurement, not a caught bug.

    Both are the false-refusal / false-evidence shapes A-016/A-035 name, and
    neither was reachable from the locked packet (whose only multi-file
    fixture is a single ``.py``).
    """

    def read_source_text(path: str) -> str:
        return _read_prepared_source_text(prepared, path, deadline=deadline)

    return mutation.resolve_mutation_targets(
        added,
        repo_top=snapshot_repo_top,
        source_root_paths=source_root_paths,
        adapter=adapter,
        read_source_text=read_source_text,
    )


def _mutation_targets_whole(
    *,
    prepared: "isolation.SnapshotRepository",
    snapshot_repo_top: Path,
    project_prefix: PurePosixPath,
    deadline: "LaneDeadline",
    adapter: LanguageAdapter,
    source_root_paths: Sequence[Path],
    targets: Sequence[str],
) -> tuple[mutation.MutationTarget, ...]:
    """Build whole-file R2 targets from the lane's declared target list.

    Every containment gate REFUSES ``ERROR``/``BAD_LANE_CONFIG`` naming the
    target and the gate it failed -- never a silent ``continue`` (B033/
    A-325). This is R1's own rule, one tier down: :func:`assay.evaluate.
    _resolve_whole_target` refuses the identical shapes, and its docstring
    states why -- "a directory target expanding to N files of which only one
    is measured would PASS while leaving the rest unjudged, which is
    precisely the vacuity hole this whole mode exists to close". R2 narrowing
    the same declaration silently is that hole reopened: ``judgment.r2``
    carries no ``targets`` field at all, so from the artifact alone a target
    that was judged and clean is indistinguishable from one that was never
    mutated. A partially-narrowed target set renders a real PASS/FAIL over
    scope the consumer never agreed to.

    The gates run in R1's own order -- containment, then existence, then
    excluded directory, then source globs, then test path -- so a consumer
    who moves one lane from R1 to R2 sees the same failure named first.
    Symlinks are refused one layer down, by
    :meth:`assay.isolation.SnapshotRepository.read_regular_file`, which this
    function reads every target through.

    A duplicate spelling is the one thing still collapsed rather than
    refused: two identical entries request exactly the same work, so
    de-duplicating narrows nothing.
    """
    resolved: list[mutation.MutationTarget] = []
    seen: set[str] = set()
    for raw_target in targets:
        path = (project_prefix / raw_target).as_posix()
        if path in seen:
            continue
        seen.add(path)
        abs_path = (snapshot_repo_top / path).resolve()
        if not any(abs_path.is_relative_to(root) for root in source_root_paths):
            raise AssayError(
                f"mutation target {raw_target!r} is outside judge.source_roots "
                f"{[str(root) for root in source_root_paths]}",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        if not abs_path.is_file():
            raise AssayError(
                f"mutation target {raw_target!r} does not exist as a regular "
                f"file at the judged commit (looked for {path!r} inside the "
                f"snapshot); a whole-target entry is always a regular file, "
                f"never a directory and never absent",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        if any(part in adapter.excluded_dir_names for part in PurePosixPath(path).parts[:-1]):
            raise AssayError(
                f"mutation target {raw_target!r} sits inside an excluded "
                f"directory ({sorted(adapter.excluded_dir_names)})",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        if not any(fnmatch.fnmatch(path, pattern) for pattern in adapter.source_globs):
            raise AssayError(
                f"mutation target {raw_target!r} is not adapter-recognised "
                f"source ({list(adapter.source_globs)})",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        if adapter.is_test_path(path):
            raise AssayError(
                f"mutation target {raw_target!r} is a test path per the "
                f"adapter's own convention",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        text = _read_prepared_source_text(prepared, path, deadline=deadline)
        # Every line the file actually has. A trailing newline TERMINATES the
        # last line rather than starting another, so `count("\n") + 2` (the
        # shipped spelling) claimed one line that does not exist for the
        # newline-terminated files essentially every source file is.
        line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
        lines = frozenset(range(1, line_count + 1))
        resolved.append(mutation.MutationTarget(path=path, text=text, lines=lines))
    return tuple(resolved)


@dataclass(frozen=True, kw_only=True)
class _PreparedOutcome:
    """The raw materials :func:`_run_higher_rigor_lane` needs to build the
    final :class:`~assay.verdict.Verdict` -- kept separate from an actually
    constructed ``Verdict`` so a cleanup-only failure AFTER an otherwise
    normal result can still replace exactly the highest declared
    higher-rigor claim/judgment tier (A-193/A-194's "outer scratch cleanup
    alone fails" rule) without re-deriving anything from a serialized
    artifact.
    """

    result: CommandResult
    claims: tuple[Claim, ...]
    judgment: Judgment | None
    ended: str


def _resolve_declared_base(
    repo: Path, base: str | None, *, remaining: git.Remaining | None = None
) -> str | None:
    """*base* resolved all the way to its MERGE-BASE commit against the
    CONSUMER's real repository (:func:`assay.git.resolve_base`), or ``None``
    when the lane declares none.

    A P22 snapshot carries only the reachable-commit CLOSURE of the resolved
    lane commit, never a branch/tag REF and never a DIVERGED commit's own
    unique history -- ``judge.base`` is commonly a symbolic name (a branch),
    and that branch's own tip may not be an ancestor of the resolved commit
    at all (a real fork-point comparison). Resolving all the way to the
    merge-base HERE, once, against the consumer's own full repository, is
    what makes the result an ANCESTOR of the resolved commit by construction
    -- and therefore always present inside the snapshot's own closure.
    :func:`~assay.measurability.check_base_is_head` still calls
    :func:`~assay.git.resolve_base` again, inside the snapshot, but merge-base
    is idempotent on an already-ancestor value, so that second call reproduces
    the identical OID rather than re-deriving a different one.
    """
    if base is None:
        return None
    return git.resolve_base(repo, base, remaining=remaining)


def _run_prepared_lane(
    lane: Lane,
    *,
    plan: CommandPlan,
    deadline: LaneDeadline,
    prepared: "isolation.SnapshotRepository",
    project_root: Path,
    project_prefix: PurePosixPath,
    adapter: LanguageAdapter | None,
    process_runner: ProcessRunner,
    clock: Clock,
    rigor_levels: tuple[str, ...],
    r1_declared: bool,
    r2_declared: bool,
    r3_declared: bool,
    resolved_base: str | None,
    base_resolution: str | None = None,
    resume: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
    progress_artifact: Path | None = None,
    diagnostics: "TextIO | None" = None,
) -> _PreparedOutcome:
    """Baseline, then R1/R2/R3 as declared -- entirely inside *prepared*'s
    still-live seed. Never lets an :class:`~assay.errors.AssayError` escape
    uncaught: every declared later claim is paid for one way or another
    before this returns, so the caller's own try/except around the whole
    ``prepared_snapshot``/scratch-root context exists only for a STRUCTURAL
    failure in one of those two context managers, never for anything this
    function does.
    """
    coverage_artifact = lane.judge.coverage.artifact if r1_declared else None
    coverage_format = lane.judge.coverage.format if r1_declared else None
    # P34/§3.6: `None` for every lane that does not declare R2 or whose
    # `judge.mutation` does not name them (every Python/Go lane through this
    # build, A-227) -- identical disposition to `coverage_artifact` above,
    # which is what makes the undeclared lane's baseline unit byte-identical
    # to before this item (O8).
    equivalence_artifact = (
        lane.judge.mutation.equivalence_artifact if r2_declared else None
    )
    kill_signal_artifact = (
        lane.judge.mutation.kill_signal_artifact if r2_declared else None
    )
    # B031/A-320. This used to be an UNCONDITIONAL
    # `Path(".assay") / f"{lane.name}.progress.jsonl"` for every R2 lane --
    # a CWD-relative path in the CONSUMER's live worktree, built by
    # interpolating an unvalidated lane name. Three defects in one line: it
    # broke B006(b)/A-292's "never the consumer's real worktree" rule (an R2
    # lane passed once and then refused forever with
    # `NO_MEASUREMENT`/`DIRTY_TREE`, reproduced), it let a lane named
    # `"../../../pwned/esc"` write NDJSON outside the repository, and it was
    # relative to the invoking CWD rather than to anything the verdict could
    # name. Progress is now OPT-IN and consumer-directed: `assay run
    # --progress PATH` (the same contract `--verdict-json` already has), and
    # `None` -- write nothing at all -- is the default for every lane.
    progress_path = progress_artifact if r2_declared else None
    # B006(b): `baseline_snapshot` is always an ephemeral, assay-owned P22
    # checkout -- never the consumer's real worktree -- so this is exactly
    # the "snapshot-running call site" that is allowed to opt in explicitly.
    with prepared.materialize(timeout=deadline.remaining()) as baseline_snapshot:
        unit = _execute_snapshot_unit(
            plan=plan,
            snapshot=baseline_snapshot,
            deadline=deadline,
            wants_coverage=r1_declared,
            coverage_artifact=coverage_artifact,
            coverage_format=coverage_format,
            process_runner=process_runner,
            clock=clock,
            create_missing_parents=True,
            equivalence_artifact=equivalence_artifact,
            kill_signal_artifact=kill_signal_artifact,
        )
        result = unit.result
        r0_claim = build_r0_claim(result)

        if unit.post_reason is not None:
            # A-195, mirroring P20's own direct-path claim precedence: the
            # REAL R0 claim stands; every OTHER declared level becomes the
            # unchanged payload-free pair.
            claims = tuple(
                r0_claim
                if level == "R0"
                else Claim(
                    rigor=level,
                    source="computed",
                    status=Outcome.NO_MEASUREMENT,
                    verified_by_assay=True,
                    reason_code=unit.post_reason,
                )
                for level in rigor_levels
            )
            return _PreparedOutcome(
                result=result, claims=claims, judgment=None, ended=iso_utc(clock())
            )

        claims: tuple[Claim, ...] = (r0_claim,)
        judgment_r1: JudgmentR1 | None = None
        added: diff.AddedLines | None = None
        targets: tuple[mutation.MutationTarget, ...] | None = None
        r2_early_claim: Claim | None = None
        if unit.equivalence_error is not None:
            # A-279: the baseline declared `equivalence_artifact` and its
            # own command did not write it -- ERROR/EXEC_FAILED, before any
            # mutant. Set HERE, before diff/target resolution below, so that
            # work is skipped entirely rather than merely discarded (the
            # `r2_early_claim is None` guard on that block).
            r2_early_claim = Claim(
                rigor="R2",
                source="computed",
                status=unit.equivalence_error.outcome,
                verified_by_assay=True,
                reason_code=unit.equivalence_error.reason_code,
            )

        if r1_declared:
            if unit.profile_error is not None:
                claims += (
                    Claim(
                        rigor="R1",
                        source="computed",
                        status=unit.profile_error.outcome,
                        verified_by_assay=True,
                        reason_code=unit.profile_error.reason_code,
                    ),
                )
            else:
                relocated_lane = _relocate_source_roots(
                    lane,
                    project_root=project_root,
                    scratch_project_root=baseline_snapshot.project_root,
                )
                resolved_base_holder: list[str] = []
                added_holder: list[diff.AddedLines] = []
                r1_claim = evaluate_r1(
                    relocated_lane,
                    repo=baseline_snapshot.root,
                    project_root=baseline_snapshot.project_root,
                    base=resolved_base,
                    adapter=adapter,
                    profile=unit.profile,
                    on_base_resolved=resolved_base_holder.append,
                    on_added_resolved=added_holder.append,
                    remaining=deadline.remaining,
                )
                claims += (r1_claim,)
                # wave-1 §6/A-264: R1 records its policy whenever R1 was
                # ATTEMPTED -- one case wider than "rendered a coverage
                # payload", mirroring A-183's identical R2 widening for
                # MUTATION_UNSUPPORTED. Without this, BRANCH_UNAVAILABLE and
                # TARGET_NOT_MEASURED could not record which target was
                # attempted or which floor was asked for.
                r1_attempted = r1_claim.coverage is not None or r1_claim.reason_code in (
                    ReasonCode.BRANCH_UNAVAILABLE,
                    ReasonCode.TARGET_NOT_MEASURED,
                )
                if r1_attempted:
                    # (P33/A-223f) v5 collapses two independently-resolved
                    # `base` values into ONE field, so which wins is stated
                    # rather than left to whoever writes the code next:
                    # `judgment.resolved.base` is the PRE-SNAPSHOT resolution
                    # against the consumer's own repository, and R1's
                    # in-snapshot resolution must EQUAL it. Merge-base is
                    # idempotent on an already-ancestor value, so the second
                    # call reproduces the first's OID -- and if it ever
                    # stops doing so, that is a real divergence between the
                    # commit assay measured and the one it recorded, which
                    # must be loud rather than silently resolved in favour
                    # of whichever value happened to be assigned last.
                    # wave-1 §5/A-260: a whole-target R1 never resolves a
                    # base at all (`on_base_resolved` is never called on
                    # that path, evaluate_r1's own docstring), so
                    # `resolved_base_holder` stays empty for it -- this
                    # comparison is therefore skipped, never a forced
                    # IndexError, whenever R1 resolved no base of its own.
                    if resolved_base_holder and resolved_base_holder[0] != resolved_base:
                        raise AssayError(
                            f"the comparison commit resolved inside the "
                            f"snapshot ({resolved_base_holder[0]}) differs "
                            f"from the one resolved against the consumer "
                            f"repository ({resolved_base}); a verdict cannot "
                            f"record one base for a diff measured against "
                            f"another",
                            outcome=Outcome.ERROR,
                            reason_code=ReasonCode.GIT_FAILED,
                        )
                    r1_effective_mode = (
                        lane.judge.mode if lane.judge.mode is not None else "changed_lines"
                    )
                    r1_effective_require_branch = (
                        lane.judge.require_branch
                        if lane.judge.require_branch is not None
                        else False
                    )
                    judgment_r1 = JudgmentR1(
                        coverage_format=lane.judge.coverage.format,
                        coverage_artifact=lane.judge.coverage.artifact,
                        fail_under=lane.judge.fail_under,
                        allow_excluded=lane.judge.allow_excluded,
                        mode=r1_effective_mode,
                        targets=lane.judge.targets,
                        require_branch=r1_effective_require_branch,
                    )
                if added_holder:
                    added = added_holder[0]
            ended = iso_utc(clock())

        if r2_declared and result.outcome is Outcome.PASS and r2_early_claim is None:
            whole_file_r2 = (
                lane.judge.mode == "whole_target"
                and lane.judge.targets is not None
            )
            if added is None and not whole_file_r2:
                try:
                    resolved = measurability.check_base_is_head(
                        baseline_snapshot.root, resolved_base, remaining=deadline.remaining
                    )
                    diff_text = git.run(
                        baseline_snapshot.root,
                        "diff",
                        "--unified=0",
                        resolved.base_rev,
                        resolved.head_rev,
                        remaining=deadline.remaining,
                    )
                    added = diff.parse_added_lines(diff_text)
                except AssayError as exc:
                    r2_early_claim = Claim(
                        rigor="R2",
                        source="computed",
                        status=exc.outcome,
                        verified_by_assay=True,
                        reason_code=exc.reason_code,
                    )
            if whole_file_r2:
                relocated_lane_r2 = _relocate_source_roots(
                    lane,
                    project_root=project_root,
                    scratch_project_root=baseline_snapshot.project_root,
                )
                try:
                    targets = _mutation_targets_whole(
                        prepared=prepared,
                        snapshot_repo_top=baseline_snapshot.root,
                        project_prefix=project_prefix,
                        deadline=deadline,
                        adapter=adapter,
                        source_root_paths=relocated_lane_r2.judge.source_root_paths,
                        targets=lane.judge.targets or (),
                    )
                except AssayError as exc:
                    # B033/A-325: the verdict stays as closed as it was --
                    # `judgment.r2` has no `targets` field and gains none
                    # (A-138/A-170) -- so WHICH declared target failed WHICH
                    # gate is written to the caller's diagnostics stream,
                    # exactly as A-322 does for a probe refusal. Without
                    # this, a whole-target R2 refusal is a bare
                    # `BAD_LANE_CONFIG` and the consumer has to guess which
                    # of N declared targets it names.
                    if diagnostics is not None:
                        print(
                            f"assay: lane {lane.name}: R2 whole-target "
                            f"resolution refused: {exc}",
                            file=diagnostics,
                        )
                    r2_early_claim = Claim(
                        rigor="R2",
                        source="computed",
                        status=exc.outcome,
                        verified_by_assay=True,
                        reason_code=exc.reason_code,
                    )
            elif added is not None:
                relocated_lane_r2 = _relocate_source_roots(
                    lane,
                    project_root=project_root,
                    scratch_project_root=baseline_snapshot.project_root,
                )
                try:
                    targets = _mutation_targets_from_diff(
                        added,
                        prepared=prepared,
                        deadline=deadline,
                        adapter=adapter,
                        snapshot_repo_top=baseline_snapshot.root,
                        source_root_paths=relocated_lane_r2.judge.source_root_paths,
                    )
                except AssayError as exc:
                    r2_early_claim = Claim(
                        rigor="R2",
                        source="computed",
                        status=exc.outcome,
                        verified_by_assay=True,
                        reason_code=exc.reason_code,
                    )
    # `baseline_snapshot` is closed from here on -- R2/R3 use `prepared`
    # directly, each materialising its own independent snapshot.

    judgment_r2: JudgmentR2 | None = None
    #: Reviewer repair: an :class:`~assay.errors.AssayError` ESCAPING
    #: :func:`~assay.mutation.run_mutation` is an ORCHESTRATION fault -- a P22
    #: worker failure, or a mutant that left its own snapshot dirty/moved
    #: (A-195) -- not a judged R2 outcome. The handoff's terminal table stops
    #: the lane on both ("P22 worker failure ... no R3"; "unit writes
    #: Git-visible support state ... no later unit"), so the pair is carried
    #: forward to every declared LATER level rather than letting R3 start two
    #: more snapshot units and render a judged canary verdict beside an R2
    #: claim that says the lane could not be measured. A judged R2 FAIL
    #: (MUTANTS_SURVIVED), an INCONCLUSIVE, a discovered-deadline
    #: LANE_TIMEOUT payload, or the max+1 sentinel are NOT such faults: they
    #: are real measurements and R3 still runs after them.
    r2_orchestration_fault: AssayError | None = None
    if r2_declared:
        if result.outcome is not Outcome.PASS:
            claims += (mutation.build_mutation_claim(result, None),)
        elif r2_early_claim is not None:
            claims += (r2_early_claim,)
        else:
            assert targets is not None
            try:
                mutation_result = mutation.run_mutation(
                    baseline=result,
                    prepared=prepared,
                    plan=plan,
                    deadline=deadline,
                    targets=targets,
                    adapter=adapter,
                    jobs=lane.judge.mutation.jobs,
                    max_mutants=lane.judge.mutation.max_mutants,
                    operators=lane.judge.mutation.operators,
                    process_runner=process_runner,
                    clock=clock,
                    equivalence_artifact=equivalence_artifact,
                    kill_signal_artifact=kill_signal_artifact,
                    baseline_equivalence=unit.baseline_equivalence,
                    budget_per_candidate_seconds=(
                        parse_duration(lane.judge.mutation.budget_per_candidate)
                        if lane.judge.mutation.budget_per_candidate is not None
                        else None
                    ),
                    progress_artifact=progress_path,
                    # A sharded run needs the same authoritative state root
                    # `run_mutation` requires for `resume` (its own guard:
                    # "an ephemeral snapshot cannot own resume evidence") --
                    # sharding a lane is exactly the long-running case this
                    # exists to make crash-resumable, so a bare `--shard`
                    # with no `--resume` still gets persisted candidate
                    # records, even though this particular invocation never
                    # reads them back.
                    state_project_root=(
                        project_root if (resume or shard_index is not None) else None
                    ),
                    resume=resume,
                    shard_index=shard_index,
                    shard_count=shard_count,
                )
            except AssayError as exc:
                r2_orchestration_fault = exc
                claims += (
                    Claim(
                        rigor="R2",
                        source="computed",
                        status=exc.outcome,
                        verified_by_assay=True,
                        reason_code=exc.reason_code,
                    ),
                )
            else:
                r2_claim = mutation.build_mutation_claim(result, mutation_result)
                claims += (r2_claim,)
                if r2_claim.mutation is not None:
                    # The EXECUTED shard, from the `--shard` CLI argument
                    # (this function's own parameter) -- not the lane's
                    # declared config, which selects nothing and would let a
                    # verdict claim a shard identity it never enforced.
                    judgment_r2 = _build_judgment_r2(
                        lane,
                        shard_index=shard_index,
                        shard_count=shard_count,
                    )
        ended = iso_utc(clock())

    judgment_r3: JudgmentR3 | None = None
    if r3_declared and r2_orchestration_fault is not None:
        # No canary unit is started at all: the lane already stopped.
        claims += (
            Claim(
                rigor="R3",
                source="computed",
                status=r2_orchestration_fault.outcome,
                verified_by_assay=True,
                reason_code=r2_orchestration_fault.reason_code,
            ),
        )
        ended = iso_utc(clock())
    elif r3_declared:
        # Deferred: `assay.canary` imports several names from THIS module at
        # its own module level, so a module-level import here would close a
        # genuine cycle -- the identical reasoning `assay.mutation`'s own
        # module docstring already gives for `execute_plan`.
        from .canary import build_canary_claim, run_isolated_canary

        canary_cfg = lane.judge.canary
        if result.outcome is not Outcome.PASS:
            canary_result = CanaryResult(
                mechanism=canary_cfg.mechanism,
                target=canary_cfg.target,
                description=(
                    "the lane's baseline command did not PASS -- a canary "
                    "control cannot be a known-good half of a failing lane"
                ),
                control_outcome=result.outcome,
            )
            claims += (build_canary_claim(canary_result),)
            judgment_r3 = JudgmentR3(mechanism=canary_cfg.mechanism, target=canary_cfg.target)
        elif canary_cfg.mechanism == "uncovered-line" and next(
            claim for claim in claims if claim.rigor == "R1"
        ).status is not Outcome.PASS:
            r1_status = next(claim for claim in claims if claim.rigor == "R1").status
            canary_result = CanaryResult(
                mechanism=canary_cfg.mechanism,
                target=canary_cfg.target,
                description=(
                    "the lane's baseline R1 coverage measurement did not "
                    "PASS -- an uncovered-line canary control has no "
                    "known-good coverage baseline"
                ),
                control_outcome=r1_status,
            )
            claims += (build_canary_claim(canary_result),)
            judgment_r3 = JudgmentR3(mechanism=canary_cfg.mechanism, target=canary_cfg.target)
        else:
            try:
                canary_result = run_isolated_canary(
                    lane,
                    prepared=prepared,
                    plan=plan,
                    deadline=deadline,
                    project_root=project_root,
                    resolved_base=resolved_base,
                    mechanism=canary_cfg.mechanism,
                    target=canary_cfg.target,
                    adapter=adapter,
                    process_runner=process_runner,
                    clock=clock,
                )
            except AssayError as exc:
                claims += (
                    Claim(
                        rigor="R3",
                        source="computed",
                        status=exc.outcome,
                        verified_by_assay=True,
                        reason_code=exc.reason_code,
                    ),
                )
            else:
                claims += (build_canary_claim(canary_result),)
                judgment_r3 = JudgmentR3(
                    mechanism=canary_cfg.mechanism, target=canary_cfg.target
                )
        ended = iso_utc(clock())

    judgment: Judgment | None = None
    if judgment_r1 is not None or judgment_r2 is not None or judgment_r3 is not None:
        # B033/A-325: "a tier ABOVE R0 ran" is not the same question as "a
        # tier that READS a base ran", and whole-target scope is exactly
        # where the two diverge. A whole-target R1 never resolves a base
        # (`on_base_resolved` is never called on that path), and a
        # whole-target R2 skips both `check_base_is_head` and the `git diff`
        # -- so on a whole-target lane NEITHER tier compares against a
        # comparison commit, and recording one would be the invented fact
        # `_build_judgment_resolved`'s own docstring forbids.
        whole_target_scope = lane.judge.mode == "whole_target"
        r1_reads_base = judgment_r1 is not None and not whole_target_scope
        r2_reads_base = judgment_r2 is not None and not (
            whole_target_scope and lane.judge.targets is not None
        )
        judgment = Judgment(
            resolved=_build_judgment_resolved(
                lane,
                resolved_base=resolved_base,
                base_resolution=base_resolution,
                compares_a_base=r1_reads_base or r2_reads_base,
            ),
            r1=judgment_r1,
            r2=judgment_r2,
            r3=judgment_r3,
        )

    return _PreparedOutcome(result=result, claims=claims, judgment=judgment, ended=ended)


def _build_judgment_resolved(
    lane: Lane,
    *,
    resolved_base: str | None,
    base_resolution: str | None = None,
    compares_a_base: bool,
) -> JudgmentResolved:
    """(P33/V5-1) the lane facts every computed tier above R0 consumes.

    Both provenance rules A-223(f) fixes are visible here:

    * ``source_roots`` comes from ``lane.judge``, the DECLARED
      project-relative spelling (A-049) -- never from the relocated lane
      ``_relocate_source_roots`` builds for a snapshot run, whose roots are
      absolute scratch paths (A-149). A consumer reading ``/tmp/...`` learns
      nothing about the project, and two prior packages got a copy-based
      orchestration wrong about paths in a way no fixture could see.
    * ``base`` is the PRE-SNAPSHOT resolution against the consumer's own
      repository, and it is present exactly when a tier that reads one is
      (A-223a/A-227). An ``R0,R3`` lane genuinely has no comparison commit:
      ``JUDGE_FIELDS_BY_RIGOR`` gives R3 none, so recording one would be an
      invented fact rather than a missing one. The same is true of a
      ``mode = "whole_target"`` lane at EVERY rigor (B033/A-325): whole-target
      scope replaces the diff outright, so neither R1 nor R2 reads a base
      there and ``judge.base`` is refused as inert config at load.
    """
    return JudgmentResolved(
        language=lane.judge.language,
        source_roots=lane.judge.source_roots,
        base=resolved_base if compares_a_base else None,
        base_resolution=base_resolution if compares_a_base else None,
    )


def _build_judgment_r2(
    lane: Lane, *, shard_index: int | None = None, shard_count: int | None = None
) -> JudgmentR2:
    """(P33/V5-4) the R2 policy, with ``kill_attribution`` DERIVED.

    A-223(b): the single declared source is
    ``judge.mutation.kill_signal_artifact`` -- present means ``declared``,
    absent means ``unattributed``. Never a second declared field, for A-036's
    reason: deriving it removes the whole class of artifact in which the two
    disagree.

    In THIS build the derivation has exactly one reachable answer, and that
    is deliberate rather than a shortcut (A-230d). ``config`` (P34/W4) now
    accepts ``kill_signal_artifact`` on a ``sql`` lane, but
    ``cli._built_in_registry`` does not register the SQL adapter until P34's
    own W6, so no build this package ships can resolve a ``sql`` lane past
    ``registry.get_adapter`` at all -- every real lane THIS build can run is
    still Python or Go, both of which ``config`` still refuses the field for,
    so every one of them still derives ``unattributed``. P33 therefore
    SPECIFIES the rule and enforces it for documents; it does not claim to
    witness it, and no construction seam exists here whose only purpose
    would be to make that claim testable.
    """
    mutation_config = lane.judge.mutation
    kill_signal_artifact = getattr(mutation_config, "kill_signal_artifact", None)
    return JudgmentR2(
        jobs=mutation_config.jobs,
        max_mutants=mutation_config.max_mutants,
        operators=mutation_config.operators,
        kill_attribution=(
            "declared" if kill_signal_artifact is not None else "unattributed"
        ),
        kill_signal_artifact=kill_signal_artifact,
        equivalence_artifact=getattr(mutation_config, "equivalence_artifact", None),
        shard_index=shard_index,
        shard_count=shard_count,
    )


def _replace_highest_higher_rigor_claim_with_git_failed(
    lane: Lane, outcome: _PreparedOutcome
) -> _PreparedOutcome:
    """A-193/A-194's "outer scratch cleanup alone fails" rule: replace the
    HIGHEST declared higher-rigor claim (R3 if declared, else R2, else R1)
    with the payload-free ``ERROR``/``GIT_FAILED`` pair and remove its
    matching judgment tier; every lower completed claim (including R0)
    remains exactly as it was.
    """
    target = next(level for level in ("R3", "R2", "R1") if level in lane.rigor)
    new_claims = tuple(
        Claim(
            rigor=target,
            source="computed",
            status=Outcome.ERROR,
            verified_by_assay=True,
            reason_code=ReasonCode.GIT_FAILED,
        )
        if claim.rigor == target
        else claim
        for claim in outcome.claims
    )
    new_judgment = outcome.judgment
    if new_judgment is not None:
        remaining = {
            "r1": new_judgment.r1,
            "r2": new_judgment.r2,
            "r3": new_judgment.r3,
        }
        remaining[target.lower()] = None
        new_judgment = None if not any(remaining.values()) else replace(new_judgment, **remaining)
    return _PreparedOutcome(
        result=outcome.result, claims=new_claims, judgment=new_judgment, ended=outcome.ended
    )


def _run_higher_rigor_lane(
    lane: Lane,
    *,
    commit: str,
    repo: Path,
    project_root: Path,
    adapter: LanguageAdapter | None,
    assay_version: str,
    argv_append: Sequence[str],
    passthrough_source: Mapping[str, str] | None,
    process_runner: ProcessRunner,
    clock: Clock,
    deadline: LaneDeadline,
    scratch_root_factory: ScratchRootFactory,
    r1_declared: bool,
    r2_declared: bool,
    r3_declared: bool,
    snapshot_policy: IsolationConfig,
    resume: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
    evidence: tuple[Evidence, ...] = (),
    declared_evidence: tuple[EvidenceDeclaration, ...] = (),
    infrastructure_source: Path | None = None,
    infrastructure_environment: Mapping[str, str] | None = None,
    progress_artifact: Path | None = None,
    diagnostics: "TextIO | None" = None,
) -> Verdict:
    """P23's committed-snapshot state machine (A-189): any lane declaring
    R1, R2, or R3 reaches here instead of the direct live-tree path in
    :func:`run_lane` below. One immutable :class:`CommandPlan` (with its
    exact, non-``None`` repo-relative ``project_prefix``) and the ONE
    CLI-started :class:`LaneDeadline` (P26/A-212, already begun before HEAD
    resolution by the caller) are shared across every unit;
    :mod:`assay.isolation` (P22) owns all Git-object isolation and refusal.

    *snapshot_policy* (B006a/A-269 WI-2) is :func:`run_lane`'s own
    ``_snapshot_policy_for_lane(lane)`` result -- always non-``None`` on this
    path, since the caller only reaches here once ``r1_declared or
    r2_declared or r3_declared``, which is exactly the pairing that helper
    never returns ``None`` for. Passed into the ONE :class:`~assay.isolation.
    SnapshotSpec` below BY IDENTITY, never re-derived from *lane* a second
    time here.
    """
    repo_top = git.repo_top(repo, remaining=deadline.remaining)
    project_prefix = _resolved_project_prefix(repo_top, project_root)
    try:
        plan = resolve_command_plan(
            lane,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            project_prefix=project_prefix,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
        )
    except AssayError as exc:
        # (B025) An unresolvable infrastructure declaration is refused here,
        # through the same `refuse_lane` every other post-HEAD-resolution
        # refusal in this module already uses (`missing_required`, the
        # bad-`--shard` refusal, adapter resolution) -- rather than
        # propagating uncaught to `main()`'s outer handler with no verdict
        # artifact at all. `refuse_lane`'s own second `resolve_command_plan`
        # call hits this identical failure and falls back to a degraded
        # plan (`env_effective_incomplete=True`, see its own docstring)
        # instead of raising again from inside the refusal path itself.
        return refuse_lane(
            lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            project_prefix=project_prefix,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=assay_version,
            evidence=evidence,
            declared_evidence=declared_evidence,
            clock=clock,
        )
    rigor_levels = tuple(lane.rigor)

    def refuse_all(status: Outcome, reason_code: ReasonCode) -> Verdict:
        return _refuse_lane_with_plan(
            lane,
            commit=commit,
            plan=plan,
            status=status,
            reason_code=reason_code,
            assay_version=assay_version,
            evidence=evidence,
            declared_evidence=declared_evidence,
            clock=clock,
        )

    if git.dirty_paths(repo, remaining=deadline.remaining):
        return refuse_all(Outcome.NO_MEASUREMENT, ReasonCode.DIRTY_TREE)
    if git.head_rev(repo, remaining=deadline.remaining) != commit:
        return refuse_all(Outcome.NO_MEASUREMENT, ReasonCode.HEAD_CHANGED)

    outcome_holder: list[_PreparedOutcome] = []
    try:
        # A P22 snapshot carries the resolved commit's full reachable OBJECT
        # closure, never a branch/tag REF -- a symbolic `judge.base` (the
        # common case) only the CONSUMER's own repository can resolve.
        # Resolved once, here, before any snapshot exists.
        resolved_base = _resolve_declared_base(
            repo, lane.judge.base if lane.judge is not None else None,
            remaining=deadline.remaining,
        )
        # (B008) Sibling to resolved_base, not derived from it -- a lane
        # declaring no base has nothing to classify, and this must reflect
        # HEAD's shape at the SAME pre-snapshot point resolved_base did.
        base_resolution = (
            git.base_resolution_mode(repo, remaining=deadline.remaining)
            if lane.judge is not None and lane.judge.base is not None
            else None
        )
        with scratch_root_factory() as scratch_root:
            spec = isolation.SnapshotSpec(
                repo_top=repo_top,
                commit=commit,
                project_prefix=project_prefix,
                scratch_root=scratch_root,
                snapshot_policy=snapshot_policy,
                limits=isolation.DEFAULT_SNAPSHOT_LIMITS,
            )
            with isolation.prepare_snapshot(spec, timeout=deadline.remaining()) as prepared:
                # (B006a/A-269 WI-3, §3.4) After `prepare_snapshot` has
                # already commit-validated `snapshot_policy`'s declared
                # omissions, but strictly BEFORE the first
                # `prepared.materialize()` call inside `_run_prepared_lane`
                # below -- so B006(b)'s own coverage-artifact parent-chain
                # creation can never get a chance to run first.
                _refuse_coverage_artifact_omission_collision(
                    lane=lane,
                    snapshot_policy=snapshot_policy,
                    project_prefix=project_prefix,
                )
                outcome_holder.append(
                    _run_prepared_lane(
                        lane,
                        plan=plan,
                        deadline=deadline,
                        prepared=prepared,
                        project_root=project_root,
                        project_prefix=project_prefix,
                        adapter=adapter,
                        process_runner=process_runner,
                        clock=clock,
                        rigor_levels=rigor_levels,
                        r1_declared=r1_declared,
                        r2_declared=r2_declared,
                        r3_declared=r3_declared,
                        resolved_base=resolved_base,
                        base_resolution=base_resolution,
                        resume=resume,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        progress_artifact=progress_artifact,
                        diagnostics=diagnostics,
                    )
                )
    except AssayError as exc:
        if outcome_holder:
            outcome = _replace_highest_higher_rigor_claim_with_git_failed(
                lane, outcome_holder[0]
            )
        else:
            return refuse_all(exc.outcome, exc.reason_code)
    except OSError:
        if outcome_holder:
            outcome = _replace_highest_higher_rigor_claim_with_git_failed(
                lane, outcome_holder[0]
            )
        else:
            return refuse_all(Outcome.ERROR, ReasonCode.GIT_FAILED)
    except RuntimeError:
        # `prepare_snapshot`'s own programmer-error leak detection. Only a
        # cleanup-time occurrence (after a normal result already exists) is
        # this module's problem; anything else is a genuine bug and must
        # propagate rather than be laundered into a claim.
        if outcome_holder:
            outcome = _replace_highest_higher_rigor_claim_with_git_failed(
                lane, outcome_holder[0]
            )
        else:
            raise
    else:
        outcome = outcome_holder[0]

    return assemble_verdict(
        lane=lane,
        commit=commit,
        result=outcome.result,
        claims=outcome.claims,
        assay_version=assay_version,
        evidence=evidence,
        declared_evidence=declared_evidence,
        judgment=outcome.judgment,
        ended=outcome.ended,
    )


def run_lane(
    lane: Lane,
    *,
    commit: str,
    repo: Path,
    project_root: Path,
    adapter: LanguageAdapter | None,
    assay_version: str,
    argv_append: Sequence[str] = (),
    passthrough_source: Mapping[str, str] | None = None,
    process_runner: ProcessRunner = default_process_runner,
    clock: Clock = _utc_now,
    monotonic: MonotonicClock = time.monotonic,
    scratch_root_factory: ScratchRootFactory = default_scratch_root,
    evidence: tuple[Evidence, ...] = (),
    declared_evidence: tuple[EvidenceDeclaration, ...] = (),
    deadline: LaneDeadline | None = None,
    resume: bool = False,
    shard: str | None = None,
    infrastructure_source: Path | None = None,
    infrastructure_environment: Mapping[str, str] | None = None,
    progress_artifact: Path | None = None,
    diagnostics: "TextIO | None" = None,
) -> Verdict:
    """``assay run``'s entry point (P17-P19; P23 two-state split A-189):
    dispatch on declared rigor, then either run the direct R0-only clean-tree
    path below or delegate whole to :func:`_run_higher_rigor_lane`.

    *adapter* is the ALREADY-RESOLVED :class:`~assay.adapters.base.
    LanguageAdapter` for ``lane.judge.language`` at whichever of R1/R2 is
    declared -- this function knows nothing about a
    :class:`~assay.registry.Registry`; resolving which adapter (or refusing
    an unsupported language/rigor pairing outright, before anything here runs
    at all) is :mod:`assay.cli`'s own job (work item 2). *adapter* is
    ``None`` exactly when NEITHER ``"R1"`` nor ``"R2"`` is in ``lane.rigor``.

    **The dispatch is a declared two-state policy, never a fallback
    (A-189).** ``rigor == ("R0",)`` retains this module's original direct
    live-tree execution below: no :mod:`assay.isolation` snapshot. Any lane
    declaring R1, R2 or R3 is instead handed whole to
    :func:`_run_higher_rigor_lane`, which owns the committed-snapshot state
    machine end to end (one resolved :class:`CommandPlan`, one shared
    :class:`LaneDeadline`, one prepared P22 seed, independent fresh
    snapshots per unit) -- see its own docstring and
    :mod:`assay.canary`/:mod:`assay.mutation` for R1/R2/R3's actual
    evaluation. A P22 refusal on that path is final; this function never
    catches it to retry against the live tree.

    **P26/A-212: one deadline, started once, reaches direct R0 too.**
    *deadline* is normally the single :class:`LaneDeadline` :mod:`assay.cli`
    started before HEAD resolution; ``None`` is a library convenience only
    (this function then starts and immediately enforces one from
    ``lane.budget_seconds``) -- never an omission of enforcement. Direct R0
    receives the ALREADY-ELAPSED remainder as its process timeout, never a
    fresh ``lane.budget_seconds``.

    **P26/A-213: evidence is bound to its lane source before any work.**
    *evidence*/*declared_evidence* must be exactly ``lane.judge.evidence``'s
    own ordered identities (empty when the lane declares none); see
    :func:`_require_evidence_bound_to_lane`.

    **P20 (A-175): the post-command repository check precedes the claim.**
    Immediately after the command returns, the whole Git-visible repository
    is compared against the commit this function itself resolved before the
    command ran -- never the caller's *commit* argument, which is an
    identity assay is asked to RECORD rather than one it has verified. The
    pre-run guard proves what existed BEFORE the command; this proves
    nothing was left behind (a tracked test/support file the command
    happened to modify, for instance).

    That comparison is TWO facts, not one (reviewer repair): the tree must
    still be clean, AND ``HEAD`` must still be the resolved pre-run commit.
    Dirt alone is not sufficient, because a command that COMMITS leaves a
    perfectly clean tree while moving what a higher-rigor lane would compare
    against. Dirt keeps precedence over a moved ``HEAD``: a command that both
    committed and left unrelated dirt has the stronger, unusable tree. Assay
    never cleans the consumer's tree to make a claim true.
    """
    _require_evidence_bound_to_lane(lane, declared_evidence, evidence)

    # (A-253/A-284, W3) The external-tool PATH preflight, at the TOP of this
    # function -- before ANY snapshot, command or Git work -- guarded on
    # *adapter* being resolved. There is no earlier "the adapter was just
    # resolved" position inside this function to slot into: `run_lane`
    # never resolves an adapter itself (see this function's own docstring
    # above -- that is `assay.cli`'s job), so `adapter` arrives already
    # resolved or not at all. `adapter is None` exactly when NEITHER `"R1"`
    # nor `"R2"` is declared, so an R0-only lane never reaches the loop
    # below; every lane that DOES carry an adapter is checked here before
    # either dispatch state (direct R0 or `_run_higher_rigor_lane`) begins,
    # which is the one insertion point that covers both.
    #
    # `refuse_lane` (never a bare `raise`): this function's other pre-work
    # refusals below build a complete refusal artifact the same way, and a
    # bare exception here would propagate past every later terminal path
    # this build already promises emits one (work item 3's "every later
    # terminal path must emit a complete artifact") -- `commit` is already
    # resolved by the time `run_lane` is called, so an un-auditable refusal
    # here would be exactly the shape P17 exists to remove.
    if adapter is not None:
        for tool in adapter.external_tools:
            if shutil.which(tool) is None:
                return refuse_lane(
                    lane,
                    commit=commit,
                    status=Outcome.NO_MEASUREMENT,
                    reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
                    argv_append=argv_append,
                    passthrough_source=passthrough_source,
                    infrastructure_source=infrastructure_source,
                    infrastructure_environment=infrastructure_environment,
                    assay_version=assay_version,
                    evidence=evidence,
                    declared_evidence=declared_evidence,
                    clock=clock,
                )

    if deadline is None:
        # A library convenience for a non-CLI caller only -- CLI always
        # passes the instance it started before HEAD resolution (A-212).
        deadline = LaneDeadline.start(
            budget_seconds=lane.budget_seconds, monotonic=monotonic
        )

    # (B010) A lane may declare WHERE its command is meaningful. Run the probe
    # in the INVOKING environment before any repository or snapshot work and
    # refuse loudly on a nonzero exit, rather than surfacing an unrelated
    # suite failure from the wrong dependency closure.
    if lane.environment_command is not None:
        # (B025) This resolution used to never forward *infrastructure_source*/
        # *infrastructure_environment* at all -- unconditionally crashing on
        # any lane pairing `environment_command` with a `derived:` fact,
        # resolvable or not (the one call site in this function that was
        # broken by a missing forward, not just a missing refusal). Both are
        # in scope here already; there is no reason for the probe's own plan
        # to see a different infrastructure world than the real command's.
        try:
            probe_env_effective = resolve_command_plan(
                lane,
                argv_append=(),
                passthrough_source=(
                    os.environ if passthrough_source is None else passthrough_source
                ),
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
            ).env_effective
        except AssayError as exc:
            # An unresolvable infrastructure declaration, refused the same
            # way every other post-HEAD-resolution refusal in this function
            # is -- `project_prefix` stays `None`, matching the
            # MISSING_EXTERNAL_TOOL refusal just above, which resolves no
            # repository identity at this point either.
            return refuse_lane(
                lane,
                commit=commit,
                status=exc.outcome,
                reason_code=exc.reason_code,
                argv_append=argv_append,
                passthrough_source=passthrough_source,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=assay_version,
                evidence=evidence,
                declared_evidence=declared_evidence,
                clock=clock,
            )
        # B032/A-322: `execute_plan` reads its `timeout=` ARGUMENT, never
        # `plan.budget_seconds` -- so the cap below was dead code and a hung
        # probe consumed the whole lane budget (measured: an
        # `environment_command` of `sleep 45` under `budget = "5m"` ran for
        # 45 s and then PASSED). The SAME value now reaches both, so the cap
        # is enforced where it is actually read.
        probe_timeout = min(PROBE_BUDGET_SECONDS, deadline.remaining())
        probe_plan = CommandPlan(
            argv_declared=tuple(lane.environment_command),
            argv_appended=(),
            argv_effective=tuple(lane.environment_command),
            env_declared=MappingProxyType(dict(lane.env)),
            env_effective=probe_env_effective,
            env_passthrough=lane.env_passthrough,
            allow_argv_append=False,
            budget_seconds=probe_timeout,
            project_prefix=None,
        )
        probe_result = execute_plan(
            probe_plan,
            cwd=project_root,
            timeout=probe_timeout,
            process_runner=process_runner,
            clock=clock,
        )
        if probe_result.outcome is not Outcome.PASS:
            probe_status, probe_reason = _probe_refusal_terminal(probe_result)
            _report_probe_refusal(
                lane,
                probe_result,
                status=probe_status,
                reason_code=probe_reason,
                diagnostics=diagnostics,
                probe_timeout=probe_timeout,
            )
            return refuse_lane(
                lane,
                commit=commit,
                status=probe_status,
                reason_code=probe_reason,
                argv_append=argv_append,
                passthrough_source=passthrough_source,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=assay_version,
                evidence=evidence,
                declared_evidence=declared_evidence,
                clock=clock,
            )

    # (A-254) The declared-environment precondition, checked BEFORE the
    # higher-rigor delegation below so one insertion point covers both paths.
    #
    # WHY this exists: `resolve_command_plan` copies a passthrough name only
    # `if name in source`, so an ABSENT one is silently dropped and the lane
    # runs anyway. A lane that passes through a provenance or instance
    # identity in order to RECORD it in `env_effective` therefore produces a
    # clean PASS carrying no identity at all when the variable is missing --
    # measured, not theorised: a lane declaring
    # `env_passthrough = ["CIU_IMAGE_REVISION", "CIU_INSTANCE_ID"]` with
    # neither set emits `env_effective` without them and PASSes. That is
    # A-025's rule broken at the environment layer: the absence of a value
    # read as the absence of a requirement.
    #
    # This is a PRE-REPOSITORY refusal -- decided before any Git work -- so
    # `project_prefix=None` is the honest value, which is exactly the case
    # A-193 sanctions for it.
    #
    # `ERROR`/`BAD_LANE_CONFIG` rather than a new reason code: the lane as
    # DECLARED cannot be honoured in this environment, which is what that
    # terminal already means for the tracked-coverage-artifact refusal. A
    # dedicated `NO_MEASUREMENT` code would be marginally more precise, and
    # would cost a closed-enum widening every consumer's schema copy would
    # then reject -- a price A-138/A-170 make deliberate, not incidental.
    missing_required = [
        name for name in lane.env_required
        if name not in (os.environ if passthrough_source is None else passthrough_source)
    ]
    if missing_required:
        return refuse_lane(
            lane,
            commit=commit,
            status=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=assay_version,
            evidence=evidence,
            declared_evidence=declared_evidence,
            clock=clock,
        )

    # (B006a/A-269 WI-2, §3.3 item 1) Resolved BEFORE the R0/higher-rigor
    # dispatch below, and unconditionally -- not only on the higher-rigor
    # branch -- so a directly constructed `Lane` that pairs R0-only rigor
    # with a declared isolation policy is refused here too, never silently
    # accepted because its rigor happened to route it past P22 entirely.
    snapshot_policy = _snapshot_policy_for_lane(lane)

    r1_declared = "R1" in lane.rigor
    r2_declared = "R2" in lane.rigor
    r3_declared = "R3" in lane.rigor

    shard_index: int | None = None
    shard_count: int | None = None
    if shard is not None:
        try:
            raw_index, raw_count = shard.split("/", 1)
            shard_index = int(raw_index)
            shard_count = int(raw_count)
            # Zero-based, matching config.py/the verdict schema/CONSUMERS.md
            # -- never `- 1`. Dry bounds check only (empty candidate tuple);
            # the real selection happens inside `mutation.run_mutation`.
            mutation.select_mutation_shard((), index=shard_index, count=shard_count)
        except (ValueError, AttributeError):
            # A-139: HEAD is already resolved above (this function's own
            # *commit* parameter) -- a bad `--shard` refusal here is one of
            # work item 3's "later terminal paths" and MUST emit a complete
            # artifact exactly like the `missing_required` refusal just
            # above, never an uncaught exception that reaches `main()` with
            # nothing to write.
            return refuse_lane(
                lane,
                commit=commit,
                status=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
                argv_append=argv_append,
                passthrough_source=passthrough_source,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=assay_version,
                evidence=evidence,
                declared_evidence=declared_evidence,
                clock=clock,
            )

    if r1_declared or r2_declared or r3_declared:
        # A-189: exact R0 alone stays on the direct live-tree path below;
        # every higher-rigor lane uses P22's committed-snapshot state
        # machine instead, with no live-tree fallback on its refusal.
        # `snapshot_policy` is never `None` here: `_snapshot_policy_for_lane`
        # only returns `None` for an R0-only lane, and this branch is
        # exactly `r1_declared or r2_declared or r3_declared`.
        return _run_higher_rigor_lane(
            lane,
            commit=commit,
            repo=repo,
            project_root=project_root,
            adapter=adapter,
            assay_version=assay_version,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            process_runner=process_runner,
            clock=clock,
            deadline=deadline,
            scratch_root_factory=scratch_root_factory,
            r1_declared=r1_declared,
            r2_declared=r2_declared,
            r3_declared=r3_declared,
            snapshot_policy=snapshot_policy,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            resume=resume,
            shard_index=shard_index,
            shard_count=shard_count,
            evidence=evidence,
            declared_evidence=declared_evidence,
            progress_artifact=progress_artifact,
            diagnostics=diagnostics,
        )

    # A-175 / work item 6: the post-command comparison is against the RESOLVED
    # pre-run commit, so that commit is read HERE, from git, at the same point
    # the pre-run cleanliness guard reads the tree -- never taken from the
    # caller's own `commit` label, which is an identity assay is asked to
    # record rather than a fact it has verified.
    pre_run_head = git.head_rev(repo, remaining=deadline.remaining)
    # A-193 (reviewer repair): "Direct `run_lane` already has repo/project
    # identity and must pass its exact prefix; only a pre-repository refusal
    # or standalone helper may honestly retain `None`." Read here, beside the
    # other two pre-run Git facts, so this path's own plan states the same
    # truth the higher-rigor path's does.
    direct_prefix = _resolved_project_prefix(
        git.repo_top(repo, remaining=deadline.remaining), project_root
    )

    if git.dirty_paths(repo, remaining=deadline.remaining):
        return refuse_lane(
            lane,
            commit=commit,
            status=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.DIRTY_TREE,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            project_prefix=direct_prefix,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=assay_version,
            evidence=evidence,
            declared_evidence=declared_evidence,
            clock=clock,
        )

    # P26/A-212: direct R0 executes the already-resolved plan with the
    # deadline's CURRENT remainder, never a fresh `lane.budget_seconds` --
    # evidence/HEAD/dirt work already spent from the same singular budget.
    try:
        plan = resolve_command_plan(
            lane,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            project_prefix=direct_prefix,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
        )
    except AssayError as exc:
        # (B025) The direct R0-only path's own plan resolution -- the third
        # of the sites an unresolvable infrastructure declaration could
        # crash from, alongside `_run_higher_rigor_lane`'s and
        # `refuse_lane`'s own (see those two sites' identical comments).
        # `docs/CONSUMERS.md`'s own B013 example lane declares `rigor =
        # ["R0"]`, so this is not a rare corner of the dispatch -- it is the
        # documented shape for the feature.
        return refuse_lane(
            lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            argv_append=argv_append,
            passthrough_source=passthrough_source,
            project_prefix=direct_prefix,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=assay_version,
            evidence=evidence,
            declared_evidence=declared_evidence,
            clock=clock,
        )
    result = execute_plan(
        plan,
        cwd=project_root,
        timeout=deadline.remaining(),
        process_runner=process_runner,
        clock=clock,
    )
    r0_claim = build_r0_claim(result)

    # A-175: post-command dirt precedes the final claim. The pre-run guard
    # above proves what existed BEFORE the command; it says nothing about
    # what the command left behind.
    #
    # Reviewer repair (work item 6, "compare the whole Git-visible repository
    # against the RESOLVED PRE-RUN COMMIT"): dirt alone is not that
    # comparison. A command that COMMITS leaves a perfectly clean tree, so a
    # dirt-only check passes -- while a higher-rigor lane's own base
    # resolution re-reads HEAD live and would then measure the NEW commit,
    # while the verdict records the pre-run one. Reproduced end to end
    # before the repair: a lane whose command committed away its own
    # uncovered line turned a real FAIL/UNCOVERED_LINES at 50.0% into PASS
    # at 100.0%, with the artifact still naming the commit that was never
    # measured. This is the same A-175 terminal `_execute_snapshot_unit`
    # checks one level down for every higher-rigor unit -- no new reason
    # code, no schema change.
    #
    # P21 work item 10 / A-178 replaces P20's single collapsed terminal. The
    # observation order is exactly one `dirty_paths` call, and `head_rev`
    # ONLY on the clean branch -- both because a second Git observation of the
    # same fact can disagree with the first, and because precedence must be
    # decided by the rule rather than by whichever call an `or` evaluated
    # first. Dirt keeps precedence: a command that both committed and left
    # unrelated dirt has an unusable tree, which is the stronger statement.
    # A clean tree whose HEAD moved is NOT dirty, and calling it DIRTY_TREE
    # was a false diagnosis for exactly the case P20 reproduced (a command
    # that commits away its own uncovered line).
    post_run_reason: ReasonCode | None = None
    if git.dirty_paths(repo, remaining=deadline.remaining):
        post_run_reason = ReasonCode.DIRTY_TREE
    elif git.head_rev(repo, remaining=deadline.remaining) != pre_run_head:
        post_run_reason = ReasonCode.HEAD_CHANGED
    if post_run_reason is not None:
        return assemble_verdict(
            lane=lane,
            commit=commit,
            result=result,
            claims=(
                Claim(
                    rigor="R0",
                    source="computed",
                    status=Outcome.NO_MEASUREMENT,
                    verified_by_assay=True,
                    reason_code=post_run_reason,
                ),
            ),
            assay_version=assay_version,
            evidence=evidence,
            declared_evidence=declared_evidence,
            ended=iso_utc(clock()),
        )

    return assemble_verdict(
        lane=lane,
        commit=commit,
        result=result,
        claims=(r0_claim,),
        assay_version=assay_version,
        evidence=evidence,
        declared_evidence=declared_evidence,
    )


def write_verdict(verdict: Verdict, destination: VerdictOutput) -> None:
    """Emit *verdict* through an ALREADY-RESERVED destination (P21/A-181).

    This function no longer interprets a path, chooses a temp name, or
    decides what an ``OSError`` means. All of that moved to
    :mod:`assay.output`, which owns the descriptor-held parent, the observed
    destination identity, and the single ``RESERVED -> EMITTED`` transition
    -- because those decisions have to be made BEFORE the lane's command
    runs, and this function is called after it.

    What remains here is the one thing this module owns: serialising the
    verdict. The reservation was taken by :mod:`assay.cli` before any HEAD,
    adapter, or consumer work, so by the time this is reached the only
    remaining question is whether the destination is still what was
    reserved -- and :meth:`assay.output.VerdictOutput.emit` answers it.
    """
    destination.emit(verdict.to_json())
