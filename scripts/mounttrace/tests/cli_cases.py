"""Shared harness for the reviewed mounttrace CLI cases."""

from __future__ import annotations

import io
from pathlib import Path
import tomllib
from contextlib import ExitStack
from unittest.mock import patch

from cli_extended import CliIdentity

import mounttrace_core
from mounttrace_cli.cli import build_cli


CATALOG = Path(__file__).resolve().parents[1] / "docs" / "cli-review.toml"
IDENTITY = CliIdentity(
    "mounttrace",
    "3.2.0",
    "read-only mount namespace analyzer",
    "mounttrace",
)


def load_cases() -> list[dict]:
    catalog = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
    return [case for case in catalog["cases"] if case.get("state") == "active"]


def slug(case_id: str) -> str:
    return case_id.removeprefix("case:route:entrypoint:mounttrace/").replace("/", ".")


def fake_tables() -> dict:
    parse = mounttrace_core.parse_mountinfo_line
    root = parse("1 1 0:1 / / rw - ext4 root rw")
    return {
        "host": [root],
        "pids": {42: [parse("10 10 0:1 / / rw master:10 - ext4 root rw")]},
        "state": None,
    }


def execute(argv: list[str]) -> tuple[int, str, str]:
    """Run one invocation against synthetic mount tables, never the live host."""
    stdout, stderr = io.StringIO(), io.StringIO()

    def tables(_args):
        return fake_tables()

    def interrupted(_seconds):
        raise KeyboardInterrupt

    cli = build_cli(IDENTITY, core_module=mounttrace_core)
    with ExitStack() as stack:
        stack.enter_context(patch.object(mounttrace_core, "read_tables", tables))
        if "--watch" in argv:
            stack.enter_context(
                patch.object(mounttrace_core.time, "sleep", interrupted)
            )
        code = cli.run(argv=list(argv), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()
