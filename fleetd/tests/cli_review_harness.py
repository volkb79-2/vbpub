"""Hermetic runner behind the reviewed fleet CLI catalog (``docs/cli-review.toml``).

One invocation runs through the REAL ``fleetd.cli.build_cli`` registry (parser,
constraints and the library-owned verbs) in a sandbox: a throw-away ``HOME``
and a private, non-repository working directory, so ``plan`` pins no execution
context and every state directory is fresh.

Two vocabulary tokens keep the recorded invocations stable across machines and
temporary directories:

* ``@<name>`` is a file in ``tests/fixtures``;
* ``{tmp}`` is the invocation's private working directory.

The catalog therefore pins the GRAMMAR of every route (spellings, accepted
shapes, choices, exclusive groups and the library-owned refusals) plus the
library-owned ``skills`` verbs end to end; the behaviour behind each fleet verb
is pinned by that verb's own test module (see ``docs/SPEC.md``).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def short_id(case_id: str) -> str:
    """The stable pytest parameter id of a catalog case (its ``test_ids`` name it)."""

    route = case_id.removeprefix("case:route:entrypoint:fleet/")
    kind = next(
        (k for k in _KINDS if f"/{k}" in route),
        "case",
    )
    where = route.split(f"/{kind}", 1)[0].replace("/", "-") or route
    return f"{where}-{kind}-{hashlib.sha256(case_id.encode()).hexdigest()[:8]}"


_KINDS = (
    "argument-shape",
    "argument-choice",
    "option-spelling",
    "option-choice",
    "exclusive-member",
    "exclusive-conflict",
    "constraint-requires",
    "constraint-conflict",
    "minimum",
)


@contextlib.contextmanager
def sandbox():
    """Yield the private working directory for one invocation."""

    root = Path(tempfile.mkdtemp(prefix="fleet-review-"))
    home, work = root / "home", root / "work"
    home.mkdir()
    work.mkdir()
    previous = Path.cwd()
    environment = {"HOME": str(home), "XDG_STATE_HOME": str(home / ".local" / "state")}
    try:
        with mock.patch.dict(os.environ, environment):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            os.chdir(work)
            yield work
    finally:
        os.chdir(previous)
        shutil.rmtree(root, ignore_errors=True)


def resolve(argv: list[str], work: Path) -> list[str]:
    """Expand the catalog's ``@fixture`` and ``{tmp}`` vocabulary tokens."""

    out: list[str] = []
    for token in argv:
        if token.startswith("@") and not token.startswith("@" + os.sep):
            out.append(str(FIXTURES / token[1:]))
        elif token == "{tmp}" or token.startswith("{tmp}/"):
            out.append(str(work / token[len("{tmp}/") :]) if token != "{tmp}" else str(work))
        else:
            out.append(token)
    return out


def run(argv: list[str]) -> tuple[int, str, str]:
    """Run ``fleet <argv>`` once in a fresh sandbox; return status, stdout, stderr."""

    from fleetd.cli import build_cli

    stdout, stderr = io.StringIO(), io.StringIO()
    with sandbox() as work:
        status = build_cli().run(argv=resolve(list(argv), work), stdout=stdout, stderr=stderr)
    return status, stdout.getvalue(), stderr.getvalue()
