#!/usr/bin/env python3
"""Read the guest screen as text and wait for text on it (`console text`, `console wait-for-text`, `console mark`).

Three engines read one screen, strictly one after the other (QMP serves one client at a
time, so no connection is held while a subprocess that also uses QMP runs). ``auto`` tries them
in this order:

* ``serial``: exact text the guest printed to its serial console, read from ``<state>/serial.log``
  by ``vmscreen text --source serial``. This is the exact-text source of UEFI guests (their
  display is a graphical framebuffer). QEMU writes that log for EVERY guest, so its existence says
  nothing: ``auto`` uses ``serial`` only when ``runner.json`` records that the guest was started with
  ``--serial-console`` and the log has text (after the mark, if any). ``--engine serial`` is an
  explicit choice that works for any guest whose log exists.
  ``console mark`` prints a mark of the log's current end; a read with a mark sees only what was
  written after it.
* ``vmscreen``: exact text from the VGA text buffer, run as a subprocess: the ``vmscreen``
  executable on PATH, then ``python -m vmscreen`` if that probe fails. It must be installed (it
  does not run from a bare source tree) and is probed once per run. Applies only while the guest is
  in a VGA text mode.
* ``ocr``: a PNG screendump taken through the launcher's own QMP path, recognised by
  ``pwmcp ocr`` (needs pwmcp's OCR sidecar). Works for graphical screens.

Every external effect (subprocess runner, QMP call, clock, sleep, ``which``) is injected, so the
poll loop is testable without processes or time. ``vm_lab`` supplies the real ones.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from vm_lab_console import TOLERATED_CALL_ERRORS, ConsoleCallError, ConsoleError

EXIT_MATCHED = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 3
EXIT_FAIL_PATTERN = 4
EXIT_BOOT_LOOP = 5

EXIT_CODES_HELP = (
    "exit codes: 0 matched, 1 error, 2 usage, 3 timeout, "
    "4 a --fail-pattern matched, 5 boot loop detected"
)
TEXT_EXIT_CODES_HELP = "exit codes: 0 ok, 1 error (no engine could read the screen), 2 usage"
MARK_EXIT_CODES_HELP = "exit codes: 0 ok (the token is the only line on stdout), 1 error (no serial log), 2 usage"
SINCE_MARK_HELP = (
    "serial engine only: read only the text written after this mark (from `console mark`); "
    "the other engines ignore it with one stderr note"
)

ENGINES = ("auto", "serial", "vmscreen", "ocr")
SERIAL_LOG_FILENAME = "serial.log"
# A vmscreen mark: OFFSET.INODE.HEADLEN.HEADHASH.TAILLEN.TAILHASH (decimal numbers and hex hashes,
# as `vmscreen mark` prints it; vmscreen rejects any other shape with exit 2).
MARK_TOKEN_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9a-f]+\.[0-9]+\.[0-9a-f]+")
OCR_TEMP_FILENAME = ".vm-lab-console-text.png"
QEMU_STATE_DIR = "/state"
MAX_CONSECUTIVE_READ_FAILURES = 10
DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_LOOP_COUNT = 3
MIN_LOOP_COUNT = 2
VMSCREEN_TIMEOUT_SECONDS = 90.0
PWMCP_TIMEOUT_SECONDS = 120.0
QMP_TIMEOUT_SECONDS = 30.0
VMSCREEN_PROBE_TIMEOUT_SECONDS = 30.0
MIN_CALL_TIMEOUT_SECONDS = 1.0
# The option the VM tool passes that older vmscreen copies lack (added by VML-03a).
VMSCREEN_REQUIRED_OPTION = "--qemu-dump-dir"

OCR_HINT = "the OCR sidecar must be enabled (`pwmcp.ocr.enabled = true` in the pwmcp stack) and reachable"


class TextEngineError(ConsoleError):
    """A screen reader that cannot work at all (not a transient read failure)."""


class TextReadError(ConsoleCallError):
    """One failed read that a poll loop may skip."""


class TextNotApplicable(Exception):
    """The chosen engine does not apply to what the screen shows right now."""


class TextUsageError(ValueError):
    """Invalid command-line input, reported as exit 2 before anything connects."""


@dataclass(frozen=True)
class ScreenText:
    text: str
    engine: str
    exact: Any  # vmscreen: True or "partial"; ocr: None


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], Mapping[str, str], float], RunResult]


def bounded_timeout(default: float, budget: float | None) -> float:
    """``min(default, budget)``, but at least one second; ``budget=None`` means the default."""
    if budget is None:
        return default
    return max(MIN_CALL_TIMEOUT_SECONDS, min(default, budget))


def run_subprocess(argv: Sequence[str], env: Mapping[str, str], timeout: float) -> RunResult:
    """Run one command to completion; a timeout is a skippable read failure."""
    try:
        completed = subprocess.run(
            list(argv),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TextReadError(f"{Path(str(argv[0])).name} timed out after {timeout:g}s") from exc
    return RunResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def _short(message: str, limit: int = 800) -> str:
    message = " ".join(message.split())
    return message if len(message) <= limit else message[:limit] + "..."


def _stderr_note(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def validate_mark(token: str) -> str:
    """A mark is what ``console mark`` printed (six dot-separated fields); anything else is a usage error."""
    if not MARK_TOKEN_PATTERN.fullmatch(token):
        raise TextUsageError(
            f"--since-mark {token!r} is not a mark token; take one with `console mark` "
            "(OFFSET.INODE.HEADLEN.HEADHASH.TAILLEN.TAILHASH)"
        )
    return token


class ScreenReader:
    """Read the current screen text with the chosen engine; remembers what worked so far."""

    def __init__(
        self,
        *,
        state_dir: Path,
        vmscreen_qmp_path: Callable[[], Path],
        qmp_socket: Path,
        engine: str = "auto",
        qmp_call: Callable[..., Any],
        runner: Runner = run_subprocess,
        which: Callable[[str], str | None] = shutil.which,
        resolve_vmscreen: Callable[[], list[str]] | None = None,
        probe_vmscreen: Callable[[list[str], float], None] | None = None,
        environ: Mapping[str, str] | None = None,
        note: Callable[[str], None] = _stderr_note,
        qmp_timeout: float = QMP_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        since_mark: str | None = None,
        implicit_mark: bool = False,
    ) -> None:
        if engine not in ENGINES:
            raise TextUsageError(f"unknown engine {engine!r}")
        if since_mark is not None:
            validate_mark(since_mark)
        self.state_dir = Path(state_dir)
        self.vmscreen_qmp_path = vmscreen_qmp_path
        self.qmp_socket = Path(qmp_socket)
        self.engine = engine
        self.qmp_call = qmp_call
        self.runner = runner
        self.which = which
        self.resolve_vmscreen = resolve_vmscreen
        self.probe_vmscreen = probe_vmscreen or self._default_probe_vmscreen
        self.environ = os.environ if environ is None else environ
        self.note = note
        self.qmp_timeout = qmp_timeout
        self.clock = clock
        self._deadline: float | None = None
        self._worked: set[str] = set()
        self._vmscreen_unavailable: str | None = None
        self._vmscreen_command: list[str] | None = None
        # An explicit --since-mark applies to the serial engine only; the others say so once.
        self.since_mark = since_mark
        self._explicit_mark = since_mark is not None
        self._implicit_mark = implicit_mark
        self._mark_ignored_noted = False
        self._quiet_ocr_noted = False

    @property
    def serial_log(self) -> Path:
        return self.state_dir / SERIAL_LOG_FILENAME

    # -- engine dispatch -------------------------------------------------

    def read(self, budget: float | None = None) -> ScreenText:
        """Return the screen text; raise TextNotApplicable only for a forced engine that does not apply.

        ``budget`` is the time left to a caller's deadline. It becomes a deadline for this one
        read, and every external call (probe, vmscreen, screendump, pwmcp) gets
        ``min(its default, time left to that deadline)`` computed just before the call, at
        least one second, so a slow first call shrinks the allowance of the next.
        ``None`` keeps the defaults.
        """
        self._deadline = None if budget is None else self.clock() + budget
        if self.engine == "serial":
            return self._serial(forced=True)
        if self.engine == "ocr":
            self._note_mark_ignored()
            return self._ocr()
        if self.engine == "vmscreen":
            self._note_mark_ignored()
            if self._vmscreen_unavailable is not None:
                raise TextEngineError(f"vmscreen is not available: {self._vmscreen_unavailable}")
            try:
                return self._vmscreen()
            except _VmscreenUnavailable as exc:
                self._vmscreen_unavailable = str(exc)
                raise TextEngineError(f"vmscreen is not available: {exc}") from None
        # auto: serial, then the VGA text buffer, then OCR.
        quiet_serial: ScreenText | None = None
        if self.serial_in_auto() and self._vmscreen_unavailable is None:
            try:
                return self._serial(forced=False)
            except TextNotApplicable:
                # The log has no text (after the mark) yet: not a failure, the next engine tries.
                quiet_serial = ScreenText("", "serial", "stream")
            except _VmscreenUnavailable as exc:
                self._vmscreen_unavailable = str(exc)
                self.note(f"vm-lab: vmscreen is not available: {exc}; using OCR only")
        if quiet_serial is None:
            # The serial engine did not run: an explicit mark has nothing to apply to. A merely quiet
            # serial log (quiet_serial set) is not that: the mark is in use.
            self._note_mark_ignored()
        if self._vmscreen_unavailable is None:
            try:
                return self._vmscreen()
            except TextNotApplicable:
                pass
            except _VmscreenUnavailable as exc:
                self._vmscreen_unavailable = str(exc)
                self.note(f"vm-lab: vmscreen is not available: {exc}; using OCR only")
        try:
            return self._ocr()
        except TextEngineError as exc:
            if quiet_serial is None:
                raise
            # Serial is the source and has nothing new yet; OCR cannot work. Report the empty
            # serial text (a wait keeps polling) instead of failing the read.
            if not self._quiet_ocr_noted:
                self._quiet_ocr_noted = True
                self.note(f"vm-lab: the serial log has no text yet and OCR is unavailable: {exc}")
            return quiet_serial

    def serial_in_auto(self) -> bool:
        """``auto`` uses the serial log only for a guest that was started with --serial-console."""
        return recorded_serial_console(self.state_dir) and self.serial_log.is_file()

    def _note_mark_ignored(self) -> None:
        """One stderr note when an explicit --since-mark meets an engine that cannot use it."""
        if self._explicit_mark and not self._mark_ignored_noted:
            self._mark_ignored_noted = True
            self.note("vm-lab: --since-mark applies to the serial engine only; this engine ignores it")

    def _remaining(self) -> float | None:
        """Seconds left to this read's deadline, or ``None`` when the read has no deadline."""
        return None if self._deadline is None else self._deadline - self.clock()

    # -- vmscreen --------------------------------------------------------

    def _vmscreen_candidates(self) -> list[list[str]]:
        """The commands that may run vmscreen, in order: the executable on PATH, then ``python -m vmscreen``."""
        if self.resolve_vmscreen is not None:
            return [list(self.resolve_vmscreen())]
        module = [sys.executable, "-m", "vmscreen"]
        found = self.which("vmscreen")
        return [[found], module] if found else [module]

    def _default_probe_vmscreen(self, command: list[str], timeout: float) -> None:
        """``<command> text --help`` must exit 0 and list --qemu-dump-dir; else raise _VmscreenUnavailable."""
        label = " ".join(command)
        try:
            result = self.runner([*command, "text", "--help"], self.environ, timeout)
        except OSError as exc:
            raise _VmscreenUnavailable(f"cannot run {label}: {exc}") from exc
        except TextReadError as exc:
            raise _VmscreenUnavailable(f"{label} did not answer `text --help`: {exc}") from exc
        if result.returncode != 0:
            detail = _short(_last_line(result.stderr) or _last_line(result.stdout) or "no output", 300)
            raise _VmscreenUnavailable(
                f"{label} is not installed or not runnable (`text --help` exit {result.returncode}): {detail}"
            )
        if VMSCREEN_REQUIRED_OPTION not in result.stdout + result.stderr:
            raise _VmscreenUnavailable(
                f"{label} is too old: `text --help` lacks {VMSCREEN_REQUIRED_OPTION}; install libraries/vmscreen"
            )

    def _resolved_vmscreen(self) -> list[str]:
        """Resolve and probe vmscreen once per run; a failure is remembered by the caller.

        Each candidate command is probed in order and the first that passes is kept, so a
        broken ``vmscreen`` on PATH falls through to ``python -m vmscreen``. Only when every
        candidate fails is vmscreen unavailable, with every failure named.
        """
        if self._vmscreen_command is None:
            failures: list[str] = []
            for command in self._vmscreen_candidates():
                try:
                    self.probe_vmscreen(command, bounded_timeout(VMSCREEN_PROBE_TIMEOUT_SECONDS, self._remaining()))
                except _VmscreenUnavailable as exc:
                    failures.append(str(exc))
                    continue
                self._vmscreen_command = command
                break
            else:
                raise _VmscreenUnavailable("; then ".join(failures))
        return self._vmscreen_command

    def vmscreen_argv(self, command: list[str]) -> list[str]:
        return [
            *command,
            "text",
            "--source",
            "vga",
            "--json",
            "--qmp",
            str(self.vmscreen_qmp_path()),
            "--dump-dir",
            str(self.state_dir),
            "--qemu-dump-dir",
            QEMU_STATE_DIR,
        ]

    def _vmscreen(self) -> ScreenText:
        command = self._resolved_vmscreen()
        try:
            result = self.runner(
                self.vmscreen_argv(command),
                self.environ,
                bounded_timeout(VMSCREEN_TIMEOUT_SECONDS, self._remaining()),
            )
        except OSError as exc:
            raise TextReadError(f"cannot run {' '.join(command)}: {exc}") from exc
        code = result.returncode
        if code == 0:
            try:
                data = json.loads(result.stdout)
            except ValueError as exc:
                raise TextReadError(f"vmscreen printed output that is not JSON: {exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                raise TextReadError("vmscreen JSON has no text field")
            self._worked.add("vmscreen")
            exact = data.get("exact")
            if exact is True or exact == "partial":
                return ScreenText(data["text"], "vmscreen", exact)
            reason = data.get("exact_reason") or "the text is not verified against the screendump"
            raise TextNotApplicable(f"vmscreen text is not verified (exact={exact!r}): {reason}")
        if code == 4:
            self._worked.add("vmscreen")
            raise TextNotApplicable("vmscreen: no exact-text source applies to the screen")
        detail = _short(_json_error(result.stdout) or result.stderr or result.stdout or "no output")
        if code == 5:
            raise TextReadError(f"vmscreen QMP problem: {detail}")
        if code == 3:
            raise TextReadError(f"vmscreen timed out: {detail}")
        if code == 2:
            raise TextEngineError(f"vmscreen rejected the arguments of the VM tool (exit 2): {detail}")
        if "vmscreen" not in self._worked:
            raise TextEngineError(f"vmscreen failed (exit {code}) before it ever worked: {detail}")
        raise TextReadError(f"vmscreen failed (exit {code}): {detail}")

    # -- serial log --------------------------------------------------------

    def _serial_command(self, forced: bool) -> list[str]:
        """The probed vmscreen command for the serial engine (vmscreen runs the serial source too)."""
        if forced and self._vmscreen_unavailable is not None:
            raise TextEngineError(f"vmscreen is not available: {self._vmscreen_unavailable}")
        try:
            return self._resolved_vmscreen()
        except _VmscreenUnavailable as exc:
            if not forced:
                raise
            self._vmscreen_unavailable = str(exc)
            raise TextEngineError(f"vmscreen is not available: {exc}") from None

    def take_mark(self, command: list[str] | None = None) -> str:
        """Print-ready mark token of the serial log's current end (``vmscreen mark``)."""
        log = self.serial_log
        if not log.is_file():
            raise TextEngineError(f"no serial log at {log}; the guest has not been started in this state directory")
        command = self._serial_command(True) if command is None else command
        try:
            result = self.runner(
                [*command, "mark", "--serial-log", str(log), "--json"],
                self.environ,
                bounded_timeout(VMSCREEN_TIMEOUT_SECONDS, self._remaining()),
            )
        except (OSError, TextReadError) as exc:
            raise TextEngineError(f"cannot run `vmscreen mark`: {exc}") from exc
        if result.returncode != 0:
            detail = _short(_json_error(result.stdout) or result.stderr or result.stdout or "no output")
            raise TextEngineError(f"vmscreen mark failed (exit {result.returncode}): {detail}")
        try:
            data = json.loads(result.stdout)
        except ValueError as exc:
            raise TextEngineError(f"vmscreen mark printed output that is not JSON: {exc}") from exc
        token = data.get("mark") if isinstance(data, dict) else None
        if not isinstance(token, str) or not MARK_TOKEN_PATTERN.fullmatch(token):
            raise TextEngineError("vmscreen mark JSON has no usable mark field")
        return token

    def serial_argv(self, command: list[str], mark: str | None) -> list[str]:
        argv = [
            *command,
            "text",
            "--source",
            "serial",
            "--serial-log",
            str(self.serial_log),
            "--json",
            "--qmp",
            str(self.vmscreen_qmp_path()),
        ]
        if mark is not None:
            argv += ["--mark", mark]
        return argv

    def _serial(self, *, forced: bool) -> ScreenText:
        """Read the serial log. Forced: always a result (empty text is a quiet log). Auto: empty is "not yet"."""
        log = self.serial_log
        if not log.is_file():
            raise TextEngineError(f"no serial log at {log}; the guest has not been started in this state directory")
        command = self._serial_command(forced)
        if self.since_mark is None and self._implicit_mark:
            # A wait sees only text written after it began: the mark is taken once, before the first read.
            self.since_mark = self.take_mark(command)
        try:
            result = self.runner(
                self.serial_argv(command, self.since_mark),
                self.environ,
                bounded_timeout(VMSCREEN_TIMEOUT_SECONDS, self._remaining()),
            )
        except OSError as exc:
            raise TextReadError(f"cannot run {' '.join(command)}: {exc}") from exc
        code = result.returncode
        if code == 0:
            try:
                data = json.loads(result.stdout)
            except ValueError as exc:
                raise TextReadError(f"vmscreen printed output that is not JSON: {exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                raise TextReadError("vmscreen JSON has no text field")
            self._worked.add("serial")
            shot = ScreenText(data["text"], "serial", data.get("exact", "stream"))
            if not shot.text.strip() and not forced:
                raise TextNotApplicable("the serial log has no text yet")
            return shot
        detail = _short(_json_error(result.stdout) or result.stderr or result.stdout or "no output")
        if code == 4:
            if self.since_mark is not None and _stale_mark(result.stdout, result.stderr):
                # A mark that can never apply again: fail closed, never "does not apply".
                raise TextEngineError(STALE_MARK_MESSAGE)
            self._worked.add("serial")
            raise TextNotApplicable("vmscreen: the serial source does not apply")
        if code == 5:
            raise TextReadError(f"vmscreen QMP problem: {detail}")
        if code == 3:
            raise TextReadError(f"vmscreen timed out: {detail}")
        if code == 2:
            raise TextEngineError(f"vmscreen rejected the arguments of the VM tool (exit 2): {detail}")
        if "serial" not in self._worked:
            raise TextEngineError(f"vmscreen serial read failed (exit {code}) before it ever worked: {detail}")
        raise TextReadError(f"vmscreen serial read failed (exit {code}): {detail}")

    # -- OCR ---------------------------------------------------------------

    def _ocr(self) -> ScreenText:
        if self.which("pwmcp") is None:
            raise TextEngineError(f"OCR needs the pwmcp command, which is not on PATH; {OCR_HINT}")
        png = self.state_dir / OCR_TEMP_FILENAME
        if png.exists() or png.is_symlink():
            raise TextReadError(
                f"refusing to overwrite existing OCR screendump {png}; another reader may be "
                f"running on this guest, and if none is running the file is stale and may be deleted: {png}"
            )
        try:
            self.qmp_call(
                self.qmp_socket,
                "screendump",
                {"filename": f"{QEMU_STATE_DIR}/{OCR_TEMP_FILENAME}", "format": "png"},
                bounded_timeout(self.qmp_timeout, self._remaining()),
            )
            try:
                size = png.stat().st_size
            except OSError as exc:
                raise TextReadError(f"QEMU did not create the OCR screendump {png}: {exc}") from exc
            if size == 0:
                raise TextReadError(f"the OCR screendump {png} is empty")
            try:
                result = self.runner(
                    ["pwmcp", "ocr", "--image", str(png), "--json"],
                    self.environ,
                    bounded_timeout(PWMCP_TIMEOUT_SECONDS, self._remaining()),
                )
            except OSError as exc:
                raise TextEngineError(f"cannot run pwmcp: {exc}; {OCR_HINT}") from exc
        finally:
            png.unlink(missing_ok=True)
        if result.returncode != 0:
            message = _short(result.stderr or result.stdout or "no output")
            text = f"pwmcp ocr failed (exit {result.returncode}): {message}; {OCR_HINT}"
            if result.returncode == 2 or "ocr" not in self._worked:
                raise TextEngineError(text)
            raise TextReadError(text)
        try:
            data = json.loads(result.stdout)
        except ValueError as exc:
            raise TextReadError(f"pwmcp ocr printed output that is not JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise TextReadError("pwmcp ocr JSON has no text field")
        self._worked.add("ocr")
        return ScreenText(data["text"], "ocr", None)


class _VmscreenUnavailable(Exception):
    """vmscreen cannot be started at all (internal; becomes a note in auto, an error otherwise)."""


STALE_MARK_MESSAGE = (
    "the serial log was truncated or replaced since the mark was taken; the mark no longer applies "
    "(take a new one with `console mark`)"
)


def recorded_serial_console(state_dir: Path) -> bool:
    """Whether ``runner.json`` records that the guest was started with ``--serial-console``.

    No file, unreadable JSON, a missing key or anything but ``true`` means no: ``auto`` then never
    uses the serial log (QEMU writes it for every guest, so its existence proves nothing).
    """
    try:
        data = json.loads((Path(state_dir) / "runner.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("serial_console") is True


def _stale_mark(stdout: str, stderr: str) -> bool:
    """Does vmscreen's exit-4 output say the log was truncated or replaced since the mark?"""
    text = stdout + "\n" + stderr
    return "truncated or replaced" in text or "truncated or rotated" in text


def _json_error(stdout: str) -> str:
    try:
        data = json.loads(stdout)
    except ValueError:
        return ""
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        return data["error"]
    return ""


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def format_exact(exact: Any) -> str:
    if exact is None:
        return "n/a"
    if exact is True:
        return "true"
    return str(exact)


def report_error(message: str, *, as_json: bool) -> int:
    """One `vm-lab:` line on stderr (and with --json one error object on stdout); returns exit 1."""
    print(f"vm-lab: {message}", file=sys.stderr, flush=True)
    if as_json:
        print(json.dumps({"result": "error", "error": message}, ensure_ascii=False), flush=True)
    return EXIT_ERROR


# -- console mark ----------------------------------------------------------


def run_mark(reader: ScreenReader) -> int:
    """Print the mark token of the serial log's current end (one line, nothing else on stdout)."""
    print(reader.take_mark(), flush=True)
    return EXIT_MATCHED


# -- console text ----------------------------------------------------------


def run_text(reader: ScreenReader, *, as_json: bool) -> int:
    """Print the current screen text once."""
    try:
        shot = reader.read()
    except TextNotApplicable as exc:
        raise TextEngineError(str(exc)) from None
    if as_json:
        print(json.dumps({"text": shot.text, "engine": shot.engine, "exact": shot.exact}, ensure_ascii=False), flush=True)
    else:
        sys.stdout.write(shot.text if shot.text.endswith("\n") else shot.text + "\n")
        sys.stdout.flush()
        print(f"vm-lab: engine={shot.engine} exact={format_exact(shot.exact)}", file=sys.stderr, flush=True)
    return EXIT_MATCHED


# -- console wait-for-text -----------------------------------------------------


def loop_count_argument(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be an integer >= {MIN_LOOP_COUNT}") from None
    if number < MIN_LOOP_COUNT:
        raise argparse.ArgumentTypeError(f"must be an integer >= {MIN_LOOP_COUNT}")
    return number


@dataclass(frozen=True)
class WaitSpec:
    pattern: re.Pattern[str]
    pattern_text: str
    fail_patterns: tuple[re.Pattern[str], ...]
    loop_pattern: re.Pattern[str] | None
    loop_count: int
    timeout: float
    interval: float
    as_json: bool


def _compile(text: str, what: str, *, literal: bool = False) -> re.Pattern[str]:
    if text == "":
        raise TextUsageError(f"{what} must not be empty")
    try:
        return re.compile(re.escape(text) if literal else text, re.MULTILINE)
    except re.error as exc:
        raise TextUsageError(f"{what} is not a valid regular expression: {exc}") from exc


def _positive_finite(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise TextUsageError(f"{what} must be a finite number greater than zero")
    return float(value)


def wait_spec(args: Any) -> WaitSpec:
    """Validate the wait-for-text arguments; nothing connects before this passes."""
    pattern = _compile(args.pattern, "PATTERN", literal=bool(args.literal))
    fail_patterns = tuple(_compile(item, "--fail-pattern") for item in (args.fail_pattern or ()))
    loop_pattern = None
    if args.loop_pattern is not None:
        loop_pattern = _compile(args.loop_pattern, "--loop-pattern")
    if args.loop_count is not None and loop_pattern is None:
        raise TextUsageError("--loop-count needs --loop-pattern")
    loop_count = DEFAULT_LOOP_COUNT if args.loop_count is None else args.loop_count
    if isinstance(loop_count, bool) or not isinstance(loop_count, int) or loop_count < MIN_LOOP_COUNT:
        raise TextUsageError(f"--loop-count must be an integer >= {MIN_LOOP_COUNT}")
    return WaitSpec(
        pattern=pattern,
        pattern_text=args.pattern,
        fail_patterns=fail_patterns,
        loop_pattern=loop_pattern,
        loop_count=loop_count,
        timeout=_positive_finite(args.timeout, "--timeout"),
        interval=_positive_finite(args.interval, "--interval"),
        as_json=bool(args.json),
    )


def matching_lines(text: str, match: re.Match[str]) -> str:
    """The whole line(s) of ``text`` that the match touches."""
    start = text.rfind("\n", 0, match.start()) + 1
    last = max(match.end() - 1, match.start())
    end = text.find("\n", last)
    return text[start : len(text) if end == -1 else end]


@dataclass
class WaitOutcome:
    result: str  # matched | timeout | failed | boot-loop | error
    exit_code: int
    pattern: str
    matched_text: str | None
    loop_count: int
    last_text: str
    elapsed_s: float
    engine: str | None
    exact: Any
    error: str | None = None


def wait_loop(
    spec: WaitSpec,
    read: Callable[[float], ScreenText],
    *,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> WaitOutcome:
    """Poll the screen. Per poll: read, PATTERN, fail patterns, loop counter, timeout, sleep.

    ``read(budget)`` gets the seconds left to the deadline.

    ``clock`` and ``sleep`` default to ``time.monotonic`` and ``time.sleep``, resolved at call time.
    """
    clock = time.monotonic if clock is None else clock
    sleep = time.sleep if sleep is None else sleep
    start = clock()
    loops = 0
    loop_visible = False
    failures = 0
    last_text = ""
    last_engine: str | None = None
    last_exact: Any = None
    last_error = ""

    def outcome(result: str, code: int, pattern: str, matched: str | None, error: str | None = None) -> WaitOutcome:
        return WaitOutcome(
            result=result,
            exit_code=code,
            pattern=pattern,
            matched_text=matched,
            loop_count=loops,
            last_text=last_text,
            elapsed_s=round(clock() - start, 3),
            engine=last_engine,
            exact=last_exact,
            error=error,
        )

    while True:
        shot: ScreenText | None = None
        try:
            # Every external call of this read is bounded by the time left to the deadline.
            shot = read(spec.timeout - (clock() - start))
        except TextNotApplicable as exc:
            # A forced vmscreen on a screen it cannot read: no text, keep polling (not a failure).
            # Only a successfully read screen moves the loop state, so it stays as it was.
            last_error, failures = str(exc), 0
        except TOLERATED_CALL_ERRORS as exc:
            failures += 1
            last_error = str(exc) or type(exc).__name__
            if failures >= MAX_CONSECUTIVE_READ_FAILURES:
                return outcome(
                    "error",
                    EXIT_ERROR,
                    spec.pattern_text,
                    None,
                    f"{MAX_CONSECUTIVE_READ_FAILURES} consecutive screen reads failed: {last_error}",
                )
        else:
            failures, last_error = 0, ""
            last_text, last_engine, last_exact = shot.text, shot.engine, shot.exact

        if shot is not None:
            text = shot.text
            found = spec.pattern.search(text)
            if found:
                return outcome("matched", EXIT_MATCHED, spec.pattern_text, matching_lines(text, found))
            for fail in spec.fail_patterns:
                hit = fail.search(text)
                if hit:
                    return outcome("failed", EXIT_FAIL_PATTERN, fail.pattern, matching_lines(text, hit))
            if spec.loop_pattern is not None:
                seen = spec.loop_pattern.search(text)
                if seen and not loop_visible:
                    loops += 1
                loop_visible = bool(seen)
                if seen and loops >= spec.loop_count:
                    return outcome("boot-loop", EXIT_BOOT_LOOP, spec.loop_pattern.pattern, matching_lines(text, seen))

        remaining = spec.timeout - (clock() - start)
        if remaining <= 0:
            return outcome("timeout", EXIT_TIMEOUT, spec.pattern_text, None, last_error or None)
        sleep(min(spec.interval, remaining))


def report_outcome(spec: WaitSpec, out: WaitOutcome) -> int:
    """Print the outcome in the documented form and return its exit code."""
    if out.result == "error":
        return report_error(out.error or "error", as_json=spec.as_json)
    if spec.as_json:
        print(
            json.dumps(
                {
                    "result": out.result,
                    "engine": out.engine,
                    "exact": out.exact,
                    "elapsed_s": out.elapsed_s,
                    "pattern": out.pattern,
                    "matched_text": out.matched_text,
                    "loop_count": out.loop_count,
                    "last_text": out.last_text,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    elif out.result == "matched":
        print(out.matched_text, flush=True)
    if out.result == "matched":
        return out.exit_code
    reason = {
        "timeout": f"timeout after {out.elapsed_s:g}s: no screen matched {out.pattern!r}",
        "failed": f"fail pattern {out.pattern!r} matched after {out.elapsed_s:g}s",
        "boot-loop": f"boot loop: {out.pattern!r} appeared {out.loop_count} times in {out.elapsed_s:g}s",
    }[out.result]
    if out.result == "timeout" and out.error:
        reason += f" (last note: {out.error})"
    print(f"vm-lab: {reason}", file=sys.stderr, flush=True)
    if not spec.as_json:
        print(out.last_text, file=sys.stderr, flush=True)
    return out.exit_code


def run_wait(
    spec: WaitSpec,
    reader: ScreenReader,
    *,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    return report_outcome(spec, wait_loop(spec, reader.read, clock=clock, sleep=sleep))
