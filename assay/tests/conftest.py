"""Shared fixtures and lane-file templates.

House style, set here for the nine packages that follow P01a:

* **One canonical artefact per shape, mutated for the negative direction.**
  ``R0_LANE`` and ``R1_LANE`` below are the *same* text the ACCEPT tests load
  and the *same* text the REJECT tests mutate with :func:`drop_key` /
  :func:`set_key`. A loader that rejects everything therefore fails the ACCEPT
  half of every module, which is the whole defence against a hollow suite.
* **Mutation helpers assert they mutated something.** A typo'd key name would
  otherwise silently produce a no-op mutant and a test that proves nothing.
* **Fixtures build real directories.** ``source_roots`` are validated against
  the filesystem, so a fixture that fakes the tree would exercise a different
  code path than production.
* **No wall-clock assertions anywhere.** A verdict must never depend on how
  fast the machine is.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

#: The `assay/` project directory, derived from this file's own location — the
#: one derivation AGENTS.md §4.2a explicitly blesses. Asserted, so a layout
#: change fails loudly here instead of silently scanning nothing later.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
assert (PROJECT_ROOT / "pyproject.toml").is_file(), (
    f"expected assay's project root at {PROJECT_ROOT}, but there is no "
    f"pyproject.toml there"
)

#: A complete, minimal R0 lane: the eight required top-level fields and nothing
#: else. An R0-only lane has NO [judge] table (A-048).
R0_LANE = """\
schema_version = 1

[lanes.package]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["pytest", "tests/unit", "-q"]
env = { MOCK_MODE = "true" }
env_passthrough = ["HOME", "TMPDIR"]
budget = "5m"
allow_argv_append = false
"""

#: A complete R1 lane: the eight, plus all five conditionally-required `judge`
#: fields, plus a `[…where]` table assay must carry and never interpret.
#: `source_roots` name directories the `project` fixture really creates.
R1_LANE = """\
schema_version = 1

[lanes.package]
scope = "S2"
rigor = ["R0", "R1"]
enforcement = "advisory"
argv = ["pytest", "tests", "-q", "--cov-report=json:cov.json"]
env = { MOCK_MODE = "true", TZ = "UTC" }
env_passthrough = []
budget = "1h30m"
allow_argv_append = true

[lanes.package.judge]
language = "python"
source_roots = ["src", "scripts"]
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "cov.json" }

[lanes.package.where]
service = "test-runner"
instance = "worktree"
"""

#: The directories `R1_LANE` declares as source roots.
R1_SOURCE_ROOTS = ("src", "scripts")


def drop_key(text: str, key: str) -> str:
    """Return *text* with the ``key = …`` line removed.

    Raises if the key was not there: a silent no-op would turn the REJECT test
    that uses it into a test of nothing.
    """
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    lines = text.splitlines(keepends=True)
    kept = [line for line in lines if not pattern.match(line)]
    if len(kept) == len(lines):
        raise AssertionError(f"template has no top-of-line key {key!r} to drop")
    return "".join(kept)


def set_key(text: str, key: str, value: str) -> str:
    """Return *text* with the ``key = …`` line rewritten to ``key = value``.

    *value* is raw TOML, so quote strings yourself. Raises if the key was not
    there, for the same reason :func:`drop_key` does.
    """
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    lines = text.splitlines(keepends=True)
    replaced = False
    out = []
    for line in lines:
        if pattern.match(line):
            out.append(f"{key} = {value}\n")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        raise AssertionError(f"template has no top-of-line key {key!r} to set")
    return "".join(out)


def lane_table(text: str, name: str = "package") -> dict:
    """The lane's table exactly as ``tomllib`` sees it — the round-trip oracle.

    Deliberately not assay's own parse: comparing assay's output against assay's
    input would prove nothing.
    """
    return tomllib.loads(text)["lanes"][name]


@dataclass(frozen=True)
class Project:
    """A project directory: the thing that contains ``assay.toml``."""

    root: Path

    def write(self, text: str, *, name: str = "assay.toml") -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def dir(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def file(self, rel: str, text: str = "") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A project root with the directories ``R1_LANE`` declares."""
    proj = Project(root=tmp_path / "proj")
    proj.root.mkdir()
    for name in R1_SOURCE_ROOTS:
        proj.dir(name)
    return proj
