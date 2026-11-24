"""Subprocess discipline for CIU.

Bounded reads are shared cli-extended behaviour, not a private ciu loop.
``run_cmd(capture=True)`` delegates to :func:`cli_extended.procio.run_capture`;
``run_streaming`` is used directly by the Compose execution boundary. The
remaining direct subprocess sites are explicit exceptions in the CIU-220 guard
test.

Rules
-----
- ``run_cmd`` MUST NOT call ``sys.exit``; callers decide what to do on failure.
- On ``check=True``, raises ``subprocess.CalledProcessError`` enriched with a
  human-readable message containing the command, returncode, and the last few
  lines of stderr.
- ``FileNotFoundError`` (command not found) and ``subprocess.TimeoutExpired``
  propagate to the caller with their established public shapes.
"""
from __future__ import annotations

import codecs
import io
import signal
import subprocess
import sys
import threading
from typing import IO, Any

from cli_extended import procio


_CALLER_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def _decode_output(value: bytes | str | None) -> str | None:
    """Preserve CIU's UTF-8 replacement and universal-newline text contract."""
    if isinstance(value, bytes):
        decoder = io.IncrementalNewlineDecoder(
            codecs.getincrementaldecoder("utf-8")("replace"),
            translate=True,
        )
        return decoder.decode(value, final=True)
    return value


def _caller_has_python_signal_handler() -> bool:
    """Whether procio's main-thread signal scope would replace a ciu handler."""
    return any(callable(signal.getsignal(signum)) for signum in _CALLER_SIGNALS)


def _run_capture_without_replacing_callers_signals(**kwargs: Any) -> procio.CaptureResult:
    """Run procio off-main-thread, leaving ciu's forwarding handlers armed (CLI-EXT-51)."""
    outcome: dict[str, Any] = {}

    def capture() -> None:
        try:
            outcome["result"] = procio.run_capture(**kwargs)
        except BaseException as exc:  # transferred to the main thread below
            outcome["exception"] = exc

    worker = threading.Thread(target=capture, name="ciu-run-capture")
    worker.start()
    worker.join()
    exception = outcome.get("exception")
    if exception is not None:
        raise exception
    return outcome["result"]


def run_cmd(
    cmd: list[str],
    *,
    timeout: float | None = None,
    check: bool = False,
    env: dict | None = None,
    cwd: str | None = None,
    capture: bool = True,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """Run *cmd* as a subprocess and return the ``CompletedProcess``.

    Parameters
    ----------
    cmd:
        Argument list for the child process (must not be empty).
    timeout:
        Optional wall-clock timeout in seconds.  On expiry,
        ``subprocess.TimeoutExpired`` propagates to the caller.
    check:
        When *True* and the process exits with a non-zero code, raise a
        ``subprocess.CalledProcessError`` whose ``stderr`` attribute and
        ``str()`` include the command, returncode, and the last 20 lines of
        captured stderr.
    env:
        Optional environment dict for the child process.  *None* inherits the
        current process environment.
    cwd:
        Optional working directory for the child process.
    capture:
        When *True* (the default), capture both stdout and stderr
        (``capture_output=True``).  When *False*, both streams are passed
        through to the parent's stdout/stderr.
    input:
        Optional text written to the child's stdin (e.g. a SQL script piped
        to ``psql -f -``). *None* (the default) leaves stdin untouched —
        identical to every pre-existing caller that never passed this.

    Returns
    -------
    subprocess.CompletedProcess
        Always in text mode (``encoding='utf-8'``, ``errors='replace'``).

    Raises
    ------
    subprocess.CalledProcessError
        When ``check=True`` and the child exits non-zero.  The exception is
        enriched with a clear message including cmd, returncode and stderr tail.
    FileNotFoundError
        When the executable is not found on PATH.  Propagates unchanged.
    subprocess.TimeoutExpired
        When *timeout* is exceeded. ``output`` and ``stderr`` are text strings
        (``""`` for a stream with no captured bytes), never bytes or ``None``.
    """
    if capture:
        stdin_input = input.encode("utf-8") if input is not None else None
        capture_arguments = {
            "cwd": cwd,
            "env": env,
            "input": stdin_input,
            "timeout": timeout,
            "text": False,
        }
        try:
            if (
                threading.current_thread() is threading.main_thread()
                and _caller_has_python_signal_handler()
            ):
                captured = _run_capture_without_replacing_callers_signals(
                    argv=cmd, **capture_arguments
                )
            else:
                captured = procio.run_capture(cmd, **capture_arguments)
        except subprocess.TimeoutExpired as exc:
            raise subprocess.TimeoutExpired(
                exc.cmd,
                exc.timeout,
                output=_decode_output(exc.output),
                stderr=_decode_output(exc.stderr),
            ) from exc
        if captured.warning is not None:
            print(f"[WARN] {captured.warning}", file=sys.stderr, flush=True)
        result = subprocess.CompletedProcess(
            captured.args,
            captured.returncode,
            _decode_output(captured.stdout),
            _decode_output(captured.stderr),
        )
    else:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=cwd,
            input=input,
        )

    if check and result.returncode != 0:
        stderr_text = result.stderr or ""
        # Include up to the last 20 lines of stderr for context.
        tail_lines = stderr_text.splitlines()[-20:]
        tail = "\n".join(tail_lines)
        cmd_str = " ".join(cmd)
        message = (
            f"Command failed (exit {result.returncode}): {cmd_str}"
            + (f"\nstderr:\n{tail}" if tail else "")
        )
        exc = subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )
        # Attach the enriched message as a string representation aid.
        exc.args = (message,)
        raise exc

    return result


def docker(args: list[str], **kw: Any) -> subprocess.CompletedProcess:
    """Thin wrapper: run ``docker <args>`` via :func:`run_cmd`.

    All keyword arguments are forwarded to :func:`run_cmd` unchanged
    (``timeout``, ``check``, ``env``, ``cwd``, ``capture``, ``input``).
    """
    return run_cmd(["docker", *args], **kw)


def popen(
    cmd: list[str],
    *,
    env: dict | None = None,
    cwd: str | None = None,
    stdin: int | IO | None = None,
    stdout: int | IO | None = None,
    stderr: int | IO | None = None,
    text: bool = True,
    bufsize: int = -1,
    start_new_session: bool = False,
) -> subprocess.Popen:
    """Start a long-lived child whose lifecycle the caller observes.

    This is the streaming companion to :func:`run_cmd`; it exists for CIU's
    foreground one-shot lifecycle, where the controller must inspect and
    signal a container while Compose remains attached to it.
    """
    return subprocess.Popen(
        cmd,
        env=env,
        cwd=cwd,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=text,
        bufsize=bufsize,
        start_new_session=start_new_session,
    )
