"""Turn a container's own log lines into phase marks — driver tier only.

A container often announces its own transitions ("world loaded", "steam
update complete") far more precisely than post-hoc changepoint detection can
infer them, and a human reading the log already trusts those lines. This
module tails one container's log with the Docker CLI and turns matching
lines into the same `marks.jsonl` entries `cgprofile mark` produces.

**This is driver-tier, not collector-tier.** The collector runs inside the
privileged helper container with `--network=none` and no Docker socket
(DESIGN.md §1, `access.HelperSpec.docker_args`); log tailing needs both, so
it runs in the driver process alongside `_start_run`/`_stop_run`, never
inside `cmd_collect`.

**The pattern syntax is not invented here.** It reuses exactly what
`docker.per_server_slices`' `WINGS_CG_PHASE_EVENTS` already established
across this estate (`game_stuff/soulmask/egg-soulmask-rcon-ksm-cgroups.json`):
one `name=pattern` per line, where `pattern` is a literal substring unless it
starts with the literal prefix ``regex:``, in which case the rest is a Python
regular expression searched (not anchored) against the line. An operator who
already writes `WINGS_CG_STEADY_MATCH` values does not have to learn a second
grammar to point this tool at the same log.

**Timestamps come from the log line, never from when we happened to read
it.** `docker logs -t` prefixes every line with its own RFC3339 timestamp;
that is what gets resolved to `emit_mark`'s `when=`. Container log delivery
buffers — sometimes by seconds — so a mark timestamped at arrival time would
be systematically late relative to the 250 ms sample series it exists to be
correlated against. A line whose timestamp cannot be parsed still produces a
mark (missing a boundary because of a formatting quirk is worse than a mark
a few hundred milliseconds off); `meta["approx_time"]` records that the
fallback (`time.time()`) was used, so a report can say so.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import phases as phases_mod

REGEX_PREFIX = "regex:"
_REPEAT_SUFFIX = "@repeat"

# One timestamp token (no embedded whitespace — RFC3339 has none) followed by
# the rest of the line. `docker logs -t` always emits this shape; a line that
# does not match it (or whose token fails to parse) is matched whole instead
# of guessing which prefix might have been a timestamp.
_TOKEN_RE = re.compile(r"^(\S+)[ \t](.*)$", re.DOTALL)
_FRACTIONAL_SECONDS_RE = re.compile(r"\.\d+")


class LogPatternError(ValueError):
    """A ``name=pattern`` line that cannot be parsed."""


@dataclass
class LogPattern:
    """One named thing to watch for. ``literal`` and ``regex`` are mutually
    exclusive; exactly one is set, matching whichever form ``raw`` used.
    """

    name: str
    raw: str
    regex: Optional[re.Pattern] = None
    literal: Optional[str] = None
    repeat: bool = False

    def matches(self, line: str) -> bool:
        if self.regex is not None:
            return self.regex.search(line) is not None
        return self.literal is not None and self.literal in line


def parse_pattern(text: str) -> LogPattern:
    """``name=substring`` / ``name=regex:<python regex>`` / ``name@repeat=…``.

    The estate's own grammar (see the module docstring) is ``name=pattern``
    with no room for a third field, so the opt-in "record every match, not
    just the first" flag is spelled the way every other spec in this package
    spells a per-item option — an ``@opt`` suffix on the identifying half,
    here the name — rather than inventing a new separator. A file written
    against the estate's plain two-field convention parses identically; only
    a caller who wants repeats has anything new to write.
    """
    raw = text
    text = text.strip()
    if not text:
        raise LogPatternError("empty pattern line")
    if "=" not in text:
        raise LogPatternError(f"pattern needs name=pattern, got {raw!r}")
    name, _, pattern = text.partition("=")
    name = name.strip()
    repeat = False
    if name.endswith(_REPEAT_SUFFIX):
        name = name[: -len(_REPEAT_SUFFIX)].strip()
        repeat = True
    if not name:
        raise LogPatternError(f"pattern needs a name, got {raw!r}")
    if not pattern:
        raise LogPatternError(f"pattern {name!r} has no match text, got {raw!r}")
    if pattern.startswith(REGEX_PREFIX):
        expr = pattern[len(REGEX_PREFIX):]
        try:
            compiled = re.compile(expr)
        except re.error as exc:
            raise LogPatternError(f"pattern {name!r}: invalid regex {expr!r}: {exc}") from exc
        return LogPattern(name=name, raw=raw, regex=compiled, repeat=repeat)
    return LogPattern(name=name, raw=raw, literal=pattern, repeat=repeat)


def load_patterns(path: str) -> List[LogPattern]:
    """Every ``name=pattern`` line in ``path``. ``#`` comments and blank
    lines are skipped; an empty or comment-only file yields ``[]`` — that is
    a valid "nothing to watch for here" configuration, not an error.
    """
    patterns: List[LogPattern] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(parse_pattern(line))
    return patterns


def _rfc3339_to_epoch(token: str) -> Optional[float]:
    """Best-effort RFC3339 → epoch seconds, or ``None`` if ``token`` is not
    one. ``datetime.fromisoformat`` tops out at microsecond precision;
    `docker logs -t` emits nanoseconds, so the fractional part is truncated
    rather than rejected — this tool never needs sub-microsecond accuracy.
    """
    if "T" not in token:
        return None
    value = token[:-1] + "+00:00" if token.endswith("Z") else token
    value = _FRACTIONAL_SECONDS_RE.sub(lambda m: m.group(0)[:7], value, count=1)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _split_timestamp(text: str) -> Tuple[Optional[float], str]:
    """``(epoch, rest)`` for one `docker logs -t` line.

    Falls back to ``(None, text)`` — the *whole* line, not a guessed
    substring — whenever the leading token is missing or unparseable, so a
    line that happens not to carry a real timestamp still gets matched
    against its full text rather than silently losing its first word.
    """
    match = _TOKEN_RE.match(text)
    if not match:
        return None, text
    token, rest = match.groups()
    epoch = _rfc3339_to_epoch(token)
    if epoch is None:
        return None, text
    return epoch, rest


def _default_docker_bin() -> str:
    from . import access

    return access.docker_bin() or "docker"


class LogTailer:
    """Tails one container's log in a background thread, turning matching
    lines into phase marks in ``run_path``'s ``marks.jsonl``.

    Never takes the run down: every failure mode short of a programming bug
    in this class itself (the container not existing, ``docker`` not being
    on PATH, the process dying mid-stream, invalid bytes on the wire) is
    caught inside the background thread and simply ends the tail early —
    a profiling run must finish and report on what it did collect, not die
    because an optional log-derived annotation source misbehaved.
    """

    def __init__(
        self,
        container: str,
        patterns: Sequence[LogPattern],
        run_path: str,
        label: Optional[str] = None,
        docker: Optional[str] = None,
        kind: str = "phase",
    ) -> None:
        self.container = container
        self.patterns = list(patterns)
        self.run_path = run_path
        self.label = label or container
        self.docker = docker or _default_docker_bin()
        self.kind = kind
        self.matched: List[Tuple[float, str]] = []
        self.error: Optional[str] = None

        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_requested = threading.Event()
        self._matched_names: set = set()

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"logtail-{self.label}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Always reaps the child, even if the read loop is blocked in it.

        Terminating the ``docker logs`` process closes its stdout from the
        other end, which is what unblocks a ``readline()`` the background
        thread is sitting in — there is no other way to interrupt a blocking
        read on a pipe from outside its thread in the standard library.
        """
        self._stop_requested.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    # ── the tail loop ────────────────────────────────────────────────────

    def _run(self) -> None:
        since = datetime.now(timezone.utc).isoformat()
        command = [self.docker, "logs", "-f", "-t", "--since", since, self.container]
        try:
            self._proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self.error = f"could not start `docker logs` for {self.container}: {exc}"
            return
        try:
            stdout = self._proc.stdout
            assert stdout is not None
            for raw_line in iter(stdout.readline, b""):
                if self._stop_requested.is_set():
                    break
                self._handle_line(raw_line)
        except Exception as exc:  # noqa: BLE001 - an optional annotation source must never crash the run
            self.error = f"log tail for {self.container} stopped early: {exc}"
        finally:
            # self._proc is always set here: the only way into this try/
            # finally is after Popen() above already succeeded (the OSError
            # branch returns before reaching it), so there is no "proc is
            # still None" case to guard against.
            proc = self._proc
            try:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - best-effort reap on the way out
                pass

    def _handle_line(self, raw_line: bytes) -> None:
        # errors="replace" is load-bearing, not decorative: this estate runs
        # a server that logs CJK text, and a single mis-decoded byte must not
        # take the tailer (and every other pattern still to match) down with
        # it — replace, don't raise.
        text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not text:
            return
        epoch, rest = _split_timestamp(text)
        approx = epoch is None
        when = epoch if epoch is not None else time.time()

        for pattern in self.patterns:
            if not pattern.repeat and pattern.name in self._matched_names:
                continue
            if not pattern.matches(rest):
                continue
            self._matched_names.add(pattern.name)
            self.matched.append((when, pattern.name))
            meta: Dict[str, Any] = {
                "source_container": self.label,
                "line": rest[:200],
            }
            if approx:
                meta["approx_time"] = True
            phases_mod.emit_mark(
                self.run_path, pattern.name, kind=self.kind, meta=meta, when=when, source="log",
            )
