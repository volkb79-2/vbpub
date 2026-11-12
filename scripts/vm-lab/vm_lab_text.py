#!/usr/bin/env python3
"""Read the guest screen as text and wait for text on it (`console text`, `console wait-for-text`).

Two engines read one screen, strictly one after the other (QMP serves one client at a
time, so no connection is held while a subprocess that also uses QMP runs):

* ``vmscreen``: exact text from the VGA text buffer, run as ``python -m vmscreen`` from the
  repository source (no wheel dependency for this script). Applies only while the guest is
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

ENGINES = ("auto", "vmscreen", "ocr")
OCR_TEMP_FILENAME = ".vm-lab-console-text.png"
QEMU_STATE_DIR = "/state"
MAX_CONSECUTIVE_READ_FAILURES = 10
DEFAULT_INTERVAL_SECONDS = 2.0
DEFAULT_LOOP_COUNT = 3
MIN_LOOP_COUNT = 2
VMSCREEN_TIMEOUT_SECONDS = 90.0
PWMCP_TIMEOUT_SECONDS = 120.0
QMP_TIMEOUT_SECONDS = 30.0

# A vmscreen run that dies like this never started: the module or a dependency cannot be imported
# (``cli_extended`` missing, or the source tree is not an installed distribution).
_STARTUP_FAILURE = re.compile(
    r"ModuleNotFoundError|ImportError|No module named|VersionLookupError|is not installed|cannot import name"
)

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


def vmscreen_source_dir(module_file: str | os.PathLike[str] = __file__) -> Path:
    """``<repo>/libraries/vmscreen/src``, found from this script's own location."""
    return Path(module_file).resolve().parent.parent.parent / "libraries" / "vmscreen" / "src"


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


class ScreenReader:
    """Read the current screen text with the chosen engine; remembers what worked so far."""

    def __init__(
        self,
        *,
        state_dir: Path,
        vmscreen_qmp_path: Callable[[], Path],
        qmp_socket: Path,
        engine: str = "auto",
        vmscreen_src: Path | None = None,
        qmp_call: Callable[..., Any],
        runner: Callable[[Sequence[str], Mapping[str, str], float], RunResult] = run_subprocess,
        which: Callable[[str], str | None] = shutil.which,
        environ: Mapping[str, str] | None = None,
        note: Callable[[str], None] = _stderr_note,
        qmp_timeout: float = QMP_TIMEOUT_SECONDS,
    ) -> None:
        if engine not in ENGINES:
            raise TextUsageError(f"unknown engine {engine!r}")
        self.state_dir = Path(state_dir)
        self.vmscreen_qmp_path = vmscreen_qmp_path
        self.qmp_socket = Path(qmp_socket)
        self.engine = engine
        self.vmscreen_src = vmscreen_source_dir() if vmscreen_src is None else Path(vmscreen_src)
        self.qmp_call = qmp_call
        self.runner = runner
        self.which = which
        self.environ = os.environ if environ is None else environ
        self.note = note
        self.qmp_timeout = qmp_timeout
        self._worked: set[str] = set()
        self._vmscreen_unavailable: str | None = None

    # -- engine dispatch -------------------------------------------------

    def read(self) -> ScreenText:
        """Return the screen text; raise TextNotApplicable only for a forced engine that does not apply."""
        if self.engine == "ocr":
            return self._ocr()
        if self.engine == "vmscreen":
            if self._vmscreen_unavailable is not None:
                raise TextEngineError(f"vmscreen is not available: {self._vmscreen_unavailable}")
            try:
                return self._vmscreen()
            except _VmscreenUnavailable as exc:
                self._vmscreen_unavailable = str(exc)
                raise TextEngineError(f"vmscreen is not available: {exc}") from None
        if self._vmscreen_unavailable is None:
            try:
                return self._vmscreen()
            except TextNotApplicable:
                pass
            except _VmscreenUnavailable as exc:
                self._vmscreen_unavailable = str(exc)
                self.note(f"vm-lab: vmscreen is not available: {exc}; using OCR only")
        return self._ocr()

    # -- vmscreen --------------------------------------------------------

    def vmscreen_argv(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "vmscreen",
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

    def vmscreen_env(self) -> dict[str, str]:
        env = dict(self.environ)
        inherited = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(self.vmscreen_src) + (os.pathsep + inherited if inherited else "")
        return env

    def _vmscreen(self) -> ScreenText:
        try:
            result = self.runner(self.vmscreen_argv(), self.vmscreen_env(), VMSCREEN_TIMEOUT_SECONDS)
        except OSError as exc:
            raise _VmscreenUnavailable(f"cannot run {sys.executable}: {exc}") from exc
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
        if code == 1 and _STARTUP_FAILURE.search(result.stderr + "\n" + result.stdout):
            raise _VmscreenUnavailable(_short(_last_line(result.stderr) or detail, 300))
        if "vmscreen" not in self._worked:
            raise TextEngineError(f"vmscreen failed (exit {code}) before it ever worked: {detail}")
        raise TextReadError(f"vmscreen failed (exit {code}): {detail}")

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
                self.qmp_timeout,
            )
            try:
                size = png.stat().st_size
            except OSError as exc:
                raise TextReadError(f"QEMU did not create the OCR screendump {png}: {exc}") from exc
            if size == 0:
                raise TextReadError(f"the OCR screendump {png} is empty")
            try:
                result = self.runner(
                    ["pwmcp", "ocr", "--image", str(png), "--json"], self.environ, PWMCP_TIMEOUT_SECONDS
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
    read: Callable[[], ScreenText],
    *,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> WaitOutcome:
    """Poll the screen. Per poll: read, PATTERN, fail patterns, loop counter, timeout, sleep.

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
            shot = read()
        except TextNotApplicable as exc:
            # A forced vmscreen on a screen it cannot read: no text, keep polling (not a failure).
            # The loop pattern is no longer on the screen, so its next sighting counts again.
            last_text, last_error, failures, loop_visible = "", str(exc), 0, False
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
        print(f"vm-lab: {out.error}", file=sys.stderr, flush=True)
        return out.exit_code
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
