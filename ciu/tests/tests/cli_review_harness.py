"""Hermetic runner behind the reviewed CLI catalog ``docs/cli-review.toml`` (AC-17, AC-22).

One invocation runs through the REAL ``ciu.cli.main`` (registry, parser, constraints,
library verbs) in a sandbox: an empty non-repository cwd, a throw-away ``HOME``, a ``PATH``
holding only ``git`` and stub ``docker``/``ssh``/``fio`` that log their argv and exit 97, no
CIU/REPO environment, and the implementation layer replaced by a recorder
(``cli._run_legacy``), so a verb that ACCEPTS its arguments reaches the recorder and exits 0
without touching Docker, SSH, the network or the working tree.

The catalog therefore pins the GRAMMAR of every route (spellings, shapes, choices, exclusive
groups, declared constraints) and the library-owned verbs end to end; the behaviour behind a
verb is pinned by that verb's own suites.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock


def short_id(case_id: str) -> str:
    """The stable pytest parameter id of a catalog case (its ``test_ids`` name it)."""
    import hashlib

    route = case_id.removeprefix("case:route:entrypoint:ciu/")
    stem = route.split("/", 1)[0] if "/" in route else route
    kind = next(
        (k for k in (
            "argument-shape", "argument-choice", "option-spelling", "option-choice",
            "exclusive-member", "exclusive-conflict", "constraint-requires",
            "constraint-conflict", "minimum",
        ) if f"/{k}" in route),
        "case",
    )
    where = route.split(f"/{kind}", 1)[0].replace("/", "-") or stem
    return f"{where}-{kind}-{hashlib.sha256(case_id.encode()).hexdigest()[:8]}"


def _stub(path: Path) -> None:
    path.write_text('#!/bin/sh\nexit 97\n')
    path.chmod(0o755)


@contextlib.contextmanager
def sandbox():
    """Yield ``(workdir, recorded_tokens)`` for one invocation."""
    from ciu import cli

    keep = {"LANG", "TERM"}
    root = Path(tempfile.mkdtemp(prefix="ciu-review-"))
    home, work, bindir = root / "home", root / "work", root / "bin"
    for directory in (home, work, bindir):
        directory.mkdir()
    for name in ("docker", "ssh", "ssh-keygen", "ssh-keyscan", "fio"):
        _stub(bindir / name)
    git = shutil.which("git")
    if git:
        os.symlink(git, bindir / "git")
    recorded: list[list[str]] = []
    environment = {key: value for key, value in os.environ.items() if key in keep}
    environment.update({"HOME": str(home), "PATH": str(bindir), "CIU_TEST_SUITE": "1"})
    cwd = os.getcwd()
    # Keep the stand-in ``ciu`` distribution (tests/conftest.py) resolvable in the sandbox.
    if os.environ.get("PYTHONPATH"):
        environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
    try:
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            cli, "_run_legacy", lambda tokens: recorded.append(list(tokens)) or 0,
        ):
            os.chdir(work)
            yield work, recorded
    finally:
        os.chdir(cwd)
        shutil.rmtree(root, ignore_errors=True)


def run(argv: list[str]) -> tuple[int, str, str, list[list[str]]]:
    """Run ``ciu <argv>`` once; ``(exit status, stdout, stderr, implementation calls)``."""
    from ciu import cli

    out, err = io.StringIO(), io.StringIO()
    with sandbox() as (_work, recorded):
        with mock.patch.object(sys, "argv", ["ciu", *argv]), contextlib.redirect_stdout(
            out
        ), contextlib.redirect_stderr(err):
            try:
                cli.main()
                status = 0
            except SystemExit as stop:
                code = stop.code
                status = code if isinstance(code, int) else (0 if code is None else 1)
    return status, out.getvalue(), err.getvalue(), recorded
