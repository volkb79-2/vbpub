"""Shared wrapper for CMRU's process-run keyword contract.

``argv`` is copied to a list. ``capture=True`` passes ``capture_output=True``;
``capture=False`` inherits output streams and passes no capture keyword.
``text`` is forwarded when supplied. ``cwd``, ``env``, ``timeout``, and
``input`` are forwarded only when not ``None``; captured calls use
``cli_extended.procio.run_capture`` so a lingering pipe holder cannot hang them
and a timeout reaps the child's process group. ``check`` has the same outcome
as ``subprocess.run``. ``stdin`` accepts only ``None`` or
``subprocess.DEVNULL``. This module never enables a shell, retries, logs,
mutates the environment, or catches subprocess exceptions.
"""

from __future__ import annotations

import subprocess
from typing import Sequence

from cli_extended import procio


def run_process(
    argv: Sequence[str],
    *,
    cwd=None,
    env=None,
    timeout=None,
    check=False,
    capture=True,
    text=True,
    input=None,
    stdin=None,
) -> subprocess.CompletedProcess:
    """Run one argument-vector command with CMRU's shared subprocess policy."""
    argv_list = list(argv)
    if capture:
        result = procio.run_capture(
            argv_list,
            cwd=cwd,
            env=env,
            input=input,
            stdin=stdin,
            timeout=timeout,
            text=text,
        )
        if check and result.returncode:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result

    kwargs = {"check": check}
    if text is not None:
        kwargs["text"] = text
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    if timeout is not None:
        kwargs["timeout"] = timeout
    if input is not None:
        kwargs["input"] = input
    if stdin is not None:
        kwargs["stdin"] = stdin
    return subprocess.run(argv_list, **kwargs)
