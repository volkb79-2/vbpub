"""Shared wrapper for CMRU's ``subprocess.run`` keyword contract.

``argv`` is copied to a list. ``capture=True`` passes ``capture_output=True``;
``capture=False`` inherits output streams and passes no capture keyword.
``text`` is forwarded when supplied. ``cwd``, ``env``, ``timeout``, and
``input`` are forwarded only when not ``None``; ``check`` is always forwarded
with its given value. This module is the single place where CMRU passes these
process keywords to ``subprocess.run``. It never enables a shell, retries,
logs, mutates the environment, or catches subprocess exceptions.
"""

from __future__ import annotations

import subprocess
from typing import Sequence


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
) -> subprocess.CompletedProcess:
    """Run one argument-vector command with CMRU's shared subprocess policy."""
    kwargs = {"check": check}
    if capture:
        kwargs["capture_output"] = True
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
    return subprocess.run(list(argv), **kwargs)
