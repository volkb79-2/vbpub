"""Subprocess discipline for CIU v2.

Single subprocess entry-point for all CIU code (groundwork for SPEC S7.3
exit-semantics: no sys.exit in helpers).

Rules
-----
- ``run_cmd`` is the **only** place CIU spawns child processes.
- ``run_cmd`` MUST NOT call ``sys.exit``; callers decide what to do on failure.
- On ``check=True``, raises ``subprocess.CalledProcessError`` enriched with a
  human-readable message containing the command, returncode, and the last few
  lines of stderr.
- ``FileNotFoundError`` (command not found) and ``subprocess.TimeoutExpired``
  propagate to the caller unchanged.
"""
from __future__ import annotations

import subprocess
from typing import Any


def run_cmd(
    cmd: list[str],
    *,
    timeout: float | None = None,
    check: bool = False,
    env: dict | None = None,
    capture: bool = True,
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
    capture:
        When *True* (the default), capture both stdout and stderr
        (``capture_output=True``).  When *False*, both streams are passed
        through to the parent's stdout/stderr.

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
        When *timeout* is exceeded.  Propagates unchanged.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
        timeout=timeout,
        env=env,
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
    (``timeout``, ``check``, ``env``, ``capture``).
    """
    return run_cmd(["docker", *args], **kw)
