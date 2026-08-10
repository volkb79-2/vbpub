"""Compiling P26 reference for the generic Git process boundary.

This is carver-owned construction material, not production code and not a
patch to apply blindly.  Adapt ``run_bounded_reference`` inside
``assay.git._run_bounded`` while retaining that module's existing executable,
repository, argv, and replacement-environment owners.
"""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
from collections.abc import Callable, Mapping

from assay.errors import AssayError, Outcome, ReasonCode

Remaining = Callable[[], float]


def _git_failed(message: str) -> AssayError:
    return AssayError(
        message,
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.GIT_FAILED,
    )


def _sample(remaining: Remaining | None) -> float | None:
    if remaining is None:
        return None
    value = remaining()
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"remaining callback returned {value!r}, expected positive finite seconds")
    return float(value)


def _kill_owned_group(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort cleanup that never depends on direct-child liveness."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        # Cleanup must not replace the original timeout/overflow/read error.
        # The group is same-user and session-owned, so this is only a fallback
        # for an abnormal platform/process-table condition.
        try:
            proc.kill()
        except OSError:
            pass


def _close_registered(selector: selectors.BaseSelector) -> None:
    for key in tuple(selector.get_map().values()):
        try:
            selector.unregister(key.fileobj)
        except (KeyError, ValueError):
            pass
        try:
            key.fileobj.close()
        except OSError:
            pass


def run_bounded_reference(
    argv: list[str],
    *,
    env: Mapping[str, str],
    stdout_limit: int,
    stderr_limit: int,
    remaining: Remaining | None = None,
) -> tuple[int, bytes, bytes]:
    """Run one owned child with bounded pipes and one optional absolute budget.

    A raised ``remaining()`` exception is retained and re-raised as the same
    object after group termination, pipe closure, and direct-child reap.
    Overflow/read failures use ``ERROR/GIT_FAILED``.  A normal nonzero exit is
    returned to the narrow Git helper that owns its interpretation.
    """
    _sample(remaining)
    try:
        proc = subprocess.Popen(
            argv,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise _git_failed(f"could not start Git boundary: {exc}") from exc

    if proc.stdout is None or proc.stderr is None:
        _kill_owned_group(proc)
        proc.wait()
        raise AssertionError("Popen did not create the requested pipes")

    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    completed = False

    try:
        try:
            os.set_blocking(proc.stdout.fileno(), False)
            os.set_blocking(proc.stderr.fileno(), False)
            selector.register(
                proc.stdout, selectors.EVENT_READ, ("stdout", stdout_limit)
            )
            selector.register(
                proc.stderr, selectors.EVENT_READ, ("stderr", stderr_limit)
            )
        except OSError as exc:
            raise _git_failed(f"could not prepare bounded Git pipes: {exc}") from exc
        while selector.get_map():
            timeout = _sample(remaining)
            try:
                ready = selector.select(timeout)
            except OSError as exc:
                raise _git_failed(f"could not wait on bounded Git pipes: {exc}") from exc
            for key, _ in ready:
                stream, limit = key.data
                try:
                    chunk = os.read(key.fd, 65_536)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    raise _git_failed(f"could not read Git {stream}: {exc}") from exc
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                buffers[stream] += chunk
                if len(buffers[stream]) > limit:
                    del buffers[stream][:]
                    raise _git_failed(f"Git {stream} exceeded its {limit}-byte bound")
        while proc.poll() is None:
            timeout = _sample(remaining)
            if timeout is None:
                proc.wait()
                break
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # The callback owns expiry and its exact typed exception. A
                # wait timeout only means it must be sampled again.
                continue
        completed = True
    finally:
        if not completed:
            _kill_owned_group(proc)
        _close_registered(selector)
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
        try:
            proc.wait()
        except (OSError, subprocess.SubprocessError):
            # Never mask an active deadline/overflow/read exception.  A wait
            # failure after an otherwise normal pump is itself structural.
            if completed:
                raise

    return proc.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
