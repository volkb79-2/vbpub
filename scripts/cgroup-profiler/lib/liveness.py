"""Liveness, stall policy and the watch state machine — contract §8.2/§8.4.

RG-55 P6 C7 (CP-8; design A1/D-27, D-17, D-22). The daemon is the estate's
singleton watcher: run-gate AUTHORS the policy at ``ctl start`` and the
daemon judges — which means the judgement has to be a pure function of
readings this module can be handed, not something welded into the sampler.
So everything here is side-effect free except :func:`read_progress_stream`
(one bounded read of one file) and :func:`subtree_cpu_seconds`/
:func:`cgroup_io_bytes` (the same, of ``/proc``/cgroup files). The tracker
itself never touches the filesystem and never sleeps: a test drives it with
a fake clock and fake readings through EVERY state transition, which is the
only way the transitions get covered at all — the real ones take minutes.

**The two clocks, and why there are two.** Contract §8.4 compresses D-17's
three layers into one paragraph, and read carelessly its ``stalled`` and
``runaway`` states are mutually unreachable: if CPU growth counts as
"activity" (it does — the P6 handoff spells the activity definition out:
"any of a new progress-stream line, cpu growth >= 1 s over the trailing
30 s, io bytes growth") then it resets the idle clock, and "idle bound
exceeded WITH CPU growth" can never happen. D-17 itself is unambiguous
about what was meant, and names them separately:

    ``stalled`` (no liveness for N s; last event ...), ``runaway``
    (alive; no event within the cadence hint)

— two different clocks against one bound:

* the **activity clock** (`idle_for_seconds`, the §8.4 field) is reset by
  any of the three signals; exceeding the idle bound means nothing at all
  is moving -> ``stalled``;
* the **cadence clock** (internal, `_silent_for`) is reset ONLY by a new
  progress-stream line; exceeding the idle bound while the activity clock
  is still being reset by CPU growth means the lane is busy but silent ->
  ``runaway`` (D-17 layer 2's busy loop: "100 % alive, going nowhere").

With no ``--progress-stream`` configured there is no cadence signal to be
silent on, so ``runaway`` is deliberately unreachable for such a session:
without a progress stream a busy loop and real work are genuinely
indistinguishable from the outside, and inventing a verdict there would be
a guess. That is a judgement this session made and recorded in the P6 LOG,
not a rule taken from the contract.

Both clocks pause together while gates-slice or host memory ``full avg10``
exceeds :data:`PAUSE_PSI_FULL_AVG10` (§8.4), so estate-wide memory pressure
— the one condition under which everything legitimately stops moving — can
never be read as this lane stalling.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from . import metrics, util

# ── policy vocabulary (§8.4) ────────────────────────────────────────────

AUTO = "auto"
ON_STALL_VALUES = ("kill", "report")
DEFAULT_ON_STALL = "report"

#: `--idle-bound auto` floor and multiplier (D-22: `max(300, 3 x hint)`).
IDLE_BOUND_FLOOR_SECONDS = 300.0
CADENCE_MULTIPLIER = 3.0
#: `--ceiling auto` = 3 x `meta.expected.duration_s` when known, else none.
CEILING_MULTIPLIER = 3.0

#: CPU growth counts as activity at >= 1 s over the trailing 30 s (§8.4).
CPU_WINDOW_SECONDS = 30.0
CPU_GROWTH_SECONDS = 1.0
#: A terminal stream event plus a subtree still alive this long after -> `hung`.
HUNG_GRACE_SECONDS = 30.0
#: Memory `full avg10` above this pauses both clocks (§8.4).
PAUSE_PSI_FULL_AVG10 = 5.0
#: The leaf's own `memory.pressure full avg10` above this, with `memory.high`
#: applied, is `throttled` (§8.4) — never killed, always reported.
THROTTLED_PSI_FULL_AVG10 = 20.0

#: Bounded read: the last 64 KiB of the progress stream, last complete line.
STREAM_TAIL_BYTES = 65536
#: A stream event with one of these names means the lane says it is finished.
TERMINAL_STREAM_EVENTS = frozenset({"verdict", "end", "done", "summary"})
#: assay B091's own cadence hint field, carried by the `plan` event (D-17).
CADENCE_HINT_FIELD = "expect_next_event_within_s"

#: `--watch-interval` (§8.2): default 30 s, clamped to [5, 300].
WATCH_INTERVAL_DEFAULT = 30.0
WATCH_INTERVAL_MIN = 5.0
WATCH_INTERVAL_MAX = 300.0

STATE_OK = "ok"
STATE_STALLED = "stalled"
STATE_HUNG = "hung"
STATE_RUNAWAY = "runaway"
STATE_THROTTLED = "throttled"
STATE_OVER_CEILING = "over_ceiling"
STATES = (
    STATE_OK, STATE_STALLED, STATE_HUNG, STATE_RUNAWAY, STATE_THROTTLED, STATE_OVER_CEILING,
)
#: The states `--on-stall kill` acts on. `runaway` is NOT one of them —
#: §8.4: "killed only when over ceiling", and a runaway that outlives its
#: ceiling is reported as `over_ceiling`, which IS here. `throttled` is
#: never killed by the contract's own words ("never killed, reported").
KILL_STATES = frozenset({STATE_STALLED, STATE_HUNG, STATE_OVER_CEILING})

VERDICT_NONE = "none"
VERDICT_REPORTED = "reported"
VERDICT_KILLED = "killed"


class PolicyError(ValueError):
    """An unparsable policy option — contract §8.8's ``bad-policy``: exit 2
    and the session is NOT started (a lane whose stall policy the daemon
    misread is worse than a lane with no daemon at all)."""


@dataclass(frozen=True)
class Policy:
    """The four §8.4 options as the daemon holds them.

    ``idle_bound``/``ceiling`` keep the caller's own word — the literal
    ``"auto"`` or a number — because ``auto`` resolves against inputs that
    are not known at ``start`` time (the cadence hint arrives with the
    stream's `plan` event, possibly minutes in).
    """

    progress_stream: Optional[str] = None
    idle_bound: Union[str, float] = AUTO
    ceiling: Union[str, float, None] = AUTO
    on_stall: str = DEFAULT_ON_STALL

    def idle_bound_seconds(self, cadence_hint_seconds: Optional[float]) -> float:
        """D-22: an authored number wins; ``auto`` is ``max(300, 3 x hint)``
        and falls back to the 300 s floor while no hint is known."""
        if self.idle_bound != AUTO:
            return float(self.idle_bound)
        if cadence_hint_seconds is None:
            return IDLE_BOUND_FLOOR_SECONDS
        return max(IDLE_BOUND_FLOOR_SECONDS, CADENCE_MULTIPLIER * float(cadence_hint_seconds))

    def ceiling_seconds(self, expected_duration_seconds: Optional[float]) -> Optional[float]:
        """``auto`` = ``3 x meta.expected.duration_s`` when the consumer
        declared one, else NO ceiling at all (§8.4) — never a constant."""
        if self.ceiling is None:
            return None
        if self.ceiling != AUTO:
            return float(self.ceiling)
        if expected_duration_seconds is None:
            return None
        return CEILING_MULTIPLIER * float(expected_duration_seconds)


def _parse_bound(raw: Any, *, option: str, allow_none: bool) -> Union[str, float, None]:
    """``auto``/a positive number/(optionally) ``null``, or ``PolicyError``.

    A numeric STRING is accepted because the exec carrier's ``--idle-bound``
    is a string option (it has to be: ``auto`` is a legal value), and §8.1
    rule 2 makes the in-image client a literal translator — it forwards what
    it was given rather than deciding what is parsable. That decision is the
    daemon's, so both carriers get the identical ``bad-policy`` refusal.
    """
    if raw is None:
        return None if allow_none else AUTO
    if isinstance(raw, str):
        text = raw.strip()
        if text == AUTO:
            return AUTO
        try:
            value = float(text)
        except ValueError:
            raise PolicyError(
                f"--{option} must be 'auto' or a positive number of seconds, got {raw!r}"
            ) from None
    elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PolicyError(
            f"--{option} must be 'auto' or a positive number of seconds, got {raw!r}"
        )
    else:
        value = float(raw)
    if not value > 0.0:
        raise PolicyError(f"--{option} must be greater than 0, got {raw!r}")
    return value


def parse_policy(args: Dict[str, Any]) -> Policy:
    """Build a :class:`Policy` from a §8.1 ``args`` object (the wire names
    are the long options with the dashes stripped — `docs/PROTOCOL.md`).
    Every refusal is a :class:`PolicyError`; the caller turns it into
    ``bad-policy`` before the session exists."""
    stream = args.get("progress_stream")
    if stream is not None:
        if not isinstance(stream, str) or not stream.strip():
            raise PolicyError("--progress-stream must be a non-empty path")
        stream = stream.strip()
        if not stream.startswith("/"):
            # The daemon resolves it as `/proc/<pid>/root/<path>`; a relative
            # path has no meaning there (the lane's cwd is not the daemon's,
            # and by the time the daemon reads the file the lane may have
            # chdir'd anyway).
            raise PolicyError(
                f"--progress-stream must be absolute AS THE LANE SEES IT, got {stream!r}"
            )
    idle_bound = _parse_bound(args.get("idle_bound"), option="idle-bound", allow_none=False)
    ceiling = _parse_bound(args.get("ceiling"), option="ceiling", allow_none=False)
    on_stall = args.get("on_stall")
    if on_stall is None:
        on_stall = DEFAULT_ON_STALL
    if on_stall not in ON_STALL_VALUES:
        raise PolicyError(
            f"--on-stall must be one of {list(ON_STALL_VALUES)}, got {on_stall!r}"
        )
    return Policy(
        progress_stream=stream, idle_bound=idle_bound, ceiling=ceiling, on_stall=on_stall,
    )


def clamp_watch_interval(raw: Any) -> float:
    """§8.2's ``--watch-interval``: default 30, clamped to [5, 300]. Out of
    range is CLAMPED, not refused (the contract says "clamped"); a
    non-number is a caller error and raises ``ValueError`` — the `watch`
    verb turns that into ``bad-argument``, not ``bad-policy``: it is a
    request argument, not a stall policy, and no session is at stake."""
    if raw is None:
        return WATCH_INTERVAL_DEFAULT
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"--watch-interval must be a number, got {raw!r}")
    return util.clamp(float(raw), WATCH_INTERVAL_MIN, WATCH_INTERVAL_MAX)


# ── the progress stream (§8.4 `--progress-stream`) ──────────────────────

@dataclass(frozen=True)
class StreamSample:
    """One bounded read of the lane's progress NDJSON.

    ``identity`` is what "a NEW line appeared" is decided on: the file's
    size plus a digest of its last complete line. Size alone misses a
    rewritten same-length line; the digest alone misses a duplicate event
    appended twice. Together they change whenever the producer wrote
    anything at all, which is exactly the activity signal §8.4 asks for.
    """

    path: str
    present: bool
    identity: Optional[str] = None
    last_event: Optional[str] = None
    cadence_hint_seconds: Optional[float] = None


def resolve_stream_path(proc_root: str, pid: int, lane_path: str) -> str:
    """``/proc/<pid>/root/<path as the lane sees it>`` (§8.4: "no extra
    mount"). The daemon runs ``--pid=host``, so the lane's mount namespace
    is reachable through its own ``/proc`` entry whether the lane is a
    container, an exec into one, or a bare-host process."""
    return os.path.join(proc_root, str(pid), "root", lane_path.lstrip("/"))


def _cadence_hint(obj: Dict[str, Any]) -> Optional[float]:
    value = obj.get(CADENCE_HINT_FIELD)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def read_progress_stream(path: str, *, previous: Optional[StreamSample] = None) -> StreamSample:
    """Tail ``path`` — the last :data:`STREAM_TAIL_BYTES`, last COMPLETE
    JSON line — and report what the lane last said.

    Bounded by construction: one ``stat`` plus at most 64 KiB read plus (on
    the first read only, to catch a `plan` header that has already scrolled
    out of the tail) at most 64 KiB from the head. A lane that writes a
    gigabyte of progress events costs the daemon the same as one that writes
    ten. Every failure mode — absent file, a directory, a permission error,
    a half-written last line, invalid UTF-8, a line that is not JSON at all
    — reads back as "nothing new", never an exception: this runs on the
    sampler thread of a daemon that must outlive the lane it watches.
    """
    carried_hint = previous.cadence_hint_seconds if previous is not None else None
    try:
        # The lane controls this path. A FIFO with no writer blocks a plain
        # open forever, freezing its sampler and the enforcement clock. Open
        # nonblocking, then judge the opened object (not a prior path stat)
        # before reading. Regular files ignore O_NONBLOCK.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode):
                return StreamSample(path=path, present=False, cadence_hint_seconds=carried_hint)
            size = info.st_size
            if size > STREAM_TAIL_BYTES:
                fh.seek(size - STREAM_TAIL_BYTES)
                partial = True
            else:
                partial = False
            blob = fh.read(STREAM_TAIL_BYTES)
            head = b""
            if previous is None and size > STREAM_TAIL_BYTES:
                fh.seek(0)
                head = fh.read(STREAM_TAIL_BYTES)
    except OSError:
        return StreamSample(path=path, present=False, cadence_hint_seconds=carried_hint)

    lines = blob.decode("utf-8", errors="replace").split("\n")
    if partial and lines:
        lines = lines[1:]  # the first line is a fragment of a line we cut
    # The last line is complete only if the blob ended with a newline; a
    # producer caught mid-write must never be parsed (its truncated JSON
    # would read as "no event", losing the PREVIOUS complete one).
    complete = [line for line in lines[:-1] if line.strip()]

    last_event: Optional[str] = None
    hint = carried_hint
    identity: Optional[str] = None
    for line in reversed(complete):
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if identity is None:
            digest = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()[:16]
            identity = f"{size}:{digest}"
            event = obj.get("event")
            last_event = event if isinstance(event, str) else None
        found = _cadence_hint(obj)
        if found is not None:
            hint = found
            break
    if hint is None and head:
        for line in head.decode("utf-8", errors="replace").split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                found = _cadence_hint(obj)
                if found is not None:
                    hint = found
                    break
    if identity is None:
        # The file exists but holds no complete JSON object yet (a lane that
        # has only just created it). Size still moves as it fills, and that
        # IS a new-line signal, so identity tracks size alone here.
        identity = f"{size}:"
    return StreamSample(
        path=path, present=True, identity=identity, last_event=last_event,
        cadence_hint_seconds=hint,
    )


# ── readings the tracker is fed ─────────────────────────────────────────

def subtree_cpu_seconds(pids: List[int], proc_root: str) -> Optional[float]:
    """CPU seconds summed over ``pids`` from ``/proc/<pid>/stat``.

    Used instead of the cgroup's own ``cpu.stat usage_usec`` whenever the
    session has a token: in scope ``container-shared`` the cgroup is the
    whole devcontainer (permanently busy — the IDE and the agents live
    there), so cgroup CPU says nothing about the LANE. The subtree does.

    The sum is not monotone across ticks (an exited pid takes its accrued
    time with it); :class:`LivenessTracker` accumulates only the positive
    deltas, so a shrinking subtree reads as "no growth this tick", never as
    negative activity.
    """
    if not pids:
        return None
    total_usec = 0
    seen = False
    for pid in pids:
        utime, stime = metrics._proc_cpu_usec(os.path.join(proc_root, str(pid), "stat"))
        if utime is None or stime is None:
            continue
        seen = True
        total_usec += utime + stime
    return (total_usec / 1_000_000.0) if seen else None


def cgroup_io_bytes(abs_path: str) -> Optional[int]:
    """Total bytes read+written by the cgroup, from ``io.stat``.

    ``io.stat`` is deliberately NOT in ``summary._TARGET_GROUPS`` (the
    Summary has no use for it), so this reads the one file it needs rather
    than widening every sample record — which would change `samples.jsonl`
    for every session, golden fixtures included, to feed one boolean.
    """
    stat = util.read_flat_keyed(os.path.join(abs_path, "io.stat"))
    if not stat:
        return None
    total = 0
    for fields in stat.values():
        for key in ("rbytes", "wbytes"):
            value = fields.get(key)
            try:
                total += int(value)
            except (TypeError, ValueError):
                continue
    return total


@dataclass
class LivenessSample:
    """Everything one tick of the state machine needs, already read.

    The server fills this in :meth:`SessionServer._on_session_sample`; a
    test fills it by hand. Nothing in :class:`LivenessTracker` reaches past
    it, which is what makes every state transition testable with a fake
    clock instead of a real minute.
    """

    mono: float
    at: str
    elapsed_seconds: float
    cpu_seconds_total: Optional[float] = None
    io_bytes_total: Optional[int] = None
    stream: Optional[StreamSample] = None
    host_psi_full_avg10: Optional[float] = None
    slice_psi_full_avg10: Optional[float] = None
    # C8 (CP-9 placement) fills these; until a leaf exists `throttled` is
    # unreachable in production — see the class docstring.
    leaf_psi_full_avg10: Optional[float] = None
    leaf_memory_high_applied: bool = False
    subtree_alive: bool = True


class LivenessTracker:
    """The §8.4 state machine for ONE session.

    Pure: :meth:`observe` is the only way state changes, it takes a
    :class:`LivenessSample` and returns the new state when (and only when)
    the state CHANGED — which is exactly §8.2's "verdict lines only on a
    state change", so the watch stream and the session record cannot
    disagree about when a transition happened.

    ``throttled`` is computed only when the caller supplies leaf readings.
    Until CP-9 (C8) creates a leaf there are none, so in production today
    this state is unreachable — deliberately implemented and tested now
    (the reading plumbing is C8's only remaining work for it) rather than
    left as a hole in the machine.
    """

    def __init__(
        self,
        policy: Policy,
        *,
        started_at: str,
        expected_duration_seconds: Optional[float] = None,
    ) -> None:
        self.policy = policy
        self.expected_duration_seconds = expected_duration_seconds
        self.readings = 0
        self.state = STATE_OK
        self.verdict = VERDICT_NONE
        self.reason: Optional[str] = None
        self.enforced = False

        self.last_activity_at = started_at
        self.idle_for_seconds = 0.0
        self.paused_for_seconds = 0.0
        self.pause_reason: Optional[str] = None
        self.cpu_seconds = 0.0
        self.cpu_seconds_recent = 0.0
        self.io_bytes = 0
        self.stream: Optional[StreamSample] = None
        self.stream_last_event_at: Optional[str] = None

        self._silent_for = 0.0
        self._prev_mono: Optional[float] = None
        self._prev_cpu_total: Optional[float] = None
        self._prev_io_total: Optional[int] = None
        self._cpu_window: List[Any] = []  # [(mono, cumulative cpu seconds)]
        self._terminal_seen_mono: Optional[float] = None

    # -- the tick ---------------------------------------------------------

    def observe(self, sample: LivenessSample) -> Optional[str]:
        self.readings += 1
        dt = 0.0 if self._prev_mono is None else max(0.0, sample.mono - self._prev_mono)
        self._prev_mono = sample.mono

        cpu_growing = self._observe_cpu(sample)
        io_activity = self._observe_io(sample)
        stream_activity = self._observe_stream(sample)

        paused, reason = self._pause(sample)
        if stream_activity or io_activity or cpu_growing:
            self.last_activity_at = sample.at
            self.idle_for_seconds = 0.0
            self.paused_for_seconds = 0.0
            self.pause_reason = None
        elif paused:
            self.paused_for_seconds += dt
            self.pause_reason = reason
        else:
            self.idle_for_seconds += dt
            self.pause_reason = None
        # The cadence clock (see the module docstring) is reset by a stream
        # line ALONE, and pauses with the activity clock.
        if stream_activity:
            self._silent_for = 0.0
        elif not paused:
            self._silent_for += dt

        return self._evaluate(sample, cpu_growing)

    def _observe_cpu(self, sample: LivenessSample) -> bool:
        total = sample.cpu_seconds_total
        if total is not None:
            if self._prev_cpu_total is not None:
                self.cpu_seconds += max(0.0, total - self._prev_cpu_total)
            self._prev_cpu_total = total
        self._cpu_window.append((sample.mono, self.cpu_seconds))
        cutoff = sample.mono - CPU_WINDOW_SECONDS
        while len(self._cpu_window) > 1 and self._cpu_window[0][0] < cutoff:
            self._cpu_window.pop(0)
        self.cpu_seconds_recent = self.cpu_seconds - self._cpu_window[0][1]
        return self.cpu_seconds_recent >= CPU_GROWTH_SECONDS

    def _observe_io(self, sample: LivenessSample) -> bool:
        total = sample.io_bytes_total
        if total is None:
            return False
        grew = self._prev_io_total is not None and total > self._prev_io_total
        self._prev_io_total = total
        self.io_bytes = max(self.io_bytes, total)
        return grew

    def _observe_stream(self, sample: LivenessSample) -> bool:
        stream = sample.stream
        if stream is None or not stream.present:
            return False
        previous = self.stream
        self.stream = stream
        new_line = previous is None or previous.identity != stream.identity
        if new_line:
            # The daemon's OWN observation time, not a timestamp parsed out
            # of the producer's event: `last_event_at` sits beside
            # `last_activity_at` in the same block, and a consumer comparing
            # the two needs them on one clock.
            self.stream_last_event_at = sample.at
        if (
            stream.last_event in TERMINAL_STREAM_EVENTS
            and self._terminal_seen_mono is None
        ):
            self._terminal_seen_mono = sample.mono
        return new_line

    def _pause(self, sample: LivenessSample) -> Any:
        """(paused, reason). The gates slice is checked first: it is the
        more specific object (the lane's own capacity pool) and names the
        cause a consumer can act on, where host PSI only says "the box"."""
        slice_psi = sample.slice_psi_full_avg10
        if slice_psi is not None and slice_psi > PAUSE_PSI_FULL_AVG10:
            return True, "slice-psi"
        host_psi = sample.host_psi_full_avg10
        if host_psi is not None and host_psi > PAUSE_PSI_FULL_AVG10:
            return True, "host-psi"
        return False, None

    # -- the state machine ------------------------------------------------

    def _evaluate(self, sample: LivenessSample, cpu_growing: bool) -> Optional[str]:
        if self.enforced:
            # A kill (or a refused kill) is terminal: the verdict a consumer
            # already read must not be rewritten by later ticks of a subtree
            # that is now dying.
            return None
        idle_bound = self.idle_bound_seconds()
        ceiling = self.ceiling_seconds()
        state = STATE_OK
        reason: Optional[str] = None

        if ceiling is not None and sample.elapsed_seconds > ceiling:
            state = STATE_OVER_CEILING
            reason = (
                f"elapsed {sample.elapsed_seconds:.1f}s is over the ceiling of {ceiling:.1f}s"
            )
        elif (
            self._terminal_seen_mono is not None
            and (sample.mono - self._terminal_seen_mono) >= HUNG_GRACE_SECONDS
            and sample.subtree_alive
        ):
            last = self.stream.last_event if self.stream is not None else None
            state = STATE_HUNG
            reason = (
                f"the progress stream reported {last!r} "
                f"{sample.mono - self._terminal_seen_mono:.1f}s ago and the pid subtree is "
                f"still alive"
            )
        elif (
            sample.leaf_psi_full_avg10 is not None
            and sample.leaf_memory_high_applied
            and sample.leaf_psi_full_avg10 > THROTTLED_PSI_FULL_AVG10
        ):
            state = STATE_THROTTLED
            reason = (
                f"the leaf's memory.pressure full avg10 is {sample.leaf_psi_full_avg10:.1f} "
                f"(> {THROTTLED_PSI_FULL_AVG10:.0f}) with memory.high applied"
            )
        elif self.idle_for_seconds >= idle_bound:
            last = self.stream.last_event if self.stream is not None else None
            state = STATE_STALLED
            reason = (
                f"no activity for {self.idle_for_seconds:.1f}s (idle bound {idle_bound:.1f}s); "
                f"cpu grew {self.cpu_seconds_recent:.1f}s over the trailing "
                f"{CPU_WINDOW_SECONDS:.0f}s; last stream event {last!r}"
            )
        elif self.policy.progress_stream is not None and self._silent_for >= idle_bound:
            if cpu_growing:
                state = STATE_RUNAWAY
                reason = (
                    f"no progress-stream event for {self._silent_for:.1f}s "
                    f"(idle bound {idle_bound:.1f}s) while cpu grew "
                    f"{self.cpu_seconds_recent:.1f}s over the trailing "
                    f"{CPU_WINDOW_SECONDS:.0f}s"
                )

        if state == self.state:
            self.reason = reason
            return None
        self.state = state
        self.reason = reason
        self.verdict = VERDICT_NONE if state == STATE_OK else VERDICT_REPORTED
        return state

    # -- enforcement (the server performs it; the tracker records it) -----

    @property
    def kill_requested(self) -> bool:
        """Whether ``--on-stall kill`` says to act on the CURRENT state."""
        return (
            self.policy.on_stall == "kill"
            and not self.enforced
            and self.state in KILL_STATES
        )

    def record_kill(self, pids: List[int], *, via: Optional[str] = None) -> None:
        """``via`` names HOW the lane was killed, because CP-9 gave the server
        a second way to do it: a placed session dies by one write to its
        leaf's ``cgroup.kill`` (atomic), an unplaced one by SIGKILL to each
        pid the resolver found (the default phrasing). The verdict is
        ``killed`` either way — the distinction is in the reason a consumer
        prints, never in what it does."""
        self.enforced = True
        self.verdict = VERDICT_KILLED
        how = via if via is not None else f"SIGKILL sent to {len(pids)} pid(s) of the token subtree"
        self.reason = f"{self.reason}; {how}"

    def record_kill_refused(self, why: str) -> None:
        """``--on-stall kill`` was asked for and the daemon would not do it
        (there is nothing it can safely kill). The verdict stays
        ``reported`` — claiming ``killed`` for a lane that is still running
        would be a lie a consumer acts on."""
        self.enforced = True
        self.verdict = VERDICT_REPORTED
        self.reason = f"{self.reason}; kill-refused:{why}"

    # -- the blocks (§8.4 / §8.7) -----------------------------------------

    def idle_bound_seconds(self) -> float:
        hint = self.stream.cadence_hint_seconds if self.stream is not None else None
        return self.policy.idle_bound_seconds(hint)

    def ceiling_seconds(self) -> Optional[float]:
        return self.policy.ceiling_seconds(self.expected_duration_seconds)

    def liveness_block(self) -> Dict[str, Any]:
        stream_block: Optional[Dict[str, Any]] = None
        if self.policy.progress_stream is not None:
            stream_block = {
                "path": self.policy.progress_stream,
                "last_event_at": self.stream_last_event_at,
                "last_event": self.stream.last_event if self.stream is not None else None,
                "cadence_hint_seconds": (
                    self.stream.cadence_hint_seconds if self.stream is not None else None
                ),
            }
        return {
            "last_activity_at": self.last_activity_at,
            "idle_for_seconds": round(self.idle_for_seconds, 3),
            "cpu_seconds": round(self.cpu_seconds, 3),
            "cpu_seconds_recent": round(self.cpu_seconds_recent, 3),
            "io_bytes": self.io_bytes,
            "stream": stream_block,
            "paused_for_seconds": round(self.paused_for_seconds, 3),
            "pause_reason": self.pause_reason,
        }

    def watch_block(self) -> Dict[str, Any]:
        ceiling = self.ceiling_seconds()
        return {
            "state": self.state,
            "verdict": self.verdict,
            "reason": self.reason,
            "readings": self.readings,
            "policy": {
                "idle_bound_s": round(self.idle_bound_seconds(), 3),
                "ceiling_s": None if ceiling is None else round(ceiling, 3),
                "on_stall": self.policy.on_stall,
                "progress_stream": self.policy.progress_stream,
            },
        }
