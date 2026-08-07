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

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

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
env_passthrough = ["HOME", "TMPDIR", "PATH"]
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
env_passthrough = ["PATH"]
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


# --- the verdict artifact (P01b) ---------------------------------------------
#
# The six fixture files under `fixtures/verdicts/` are HAND-WRITTEN JSON, not
# generated from `assay.verdict`. That is the point: A-041 makes the independent
# oracle pytest over expected-verdict artifacts, and A-067 requires a property
# test to be checked against something that is not the thing under test. A
# round-trip asserted against assay's own serialiser would prove only that the
# serialiser agrees with itself, exactly as a `tomllib` round-trip is what keeps
# the config tests honest.

#: The shipped schema, resolved as a FILE from the package — never a dict
#: literal, because A-029's claim is that a consumer validates against a file
#: without importing assay.
SCHEMA_PATH = PROJECT_ROOT / "src" / "assay" / "schemas" / "verdict.schema.json"
assert SCHEMA_PATH.is_file(), f"the shipped schema is missing at {SCHEMA_PATH}"

VERDICT_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "verdicts"

#: One hand-written artifact per outcome. Keyed by outcome name so a test can
#: assert the set is exactly `Outcome`'s — an outcome with no fixture is an
#: outcome nothing proves anything about.
VERDICT_FIXTURES: dict[str, Path] = {
    "PASS": VERDICT_FIXTURE_DIR / "pass.json",
    "FAIL": VERDICT_FIXTURE_DIR / "fail.json",
    "ERROR": VERDICT_FIXTURE_DIR / "error.json",
    "NO_MEASUREMENT": VERDICT_FIXTURE_DIR / "no_measurement.json",
    "BUDGET_EXCEEDED": VERDICT_FIXTURE_DIR / "budget_exceeded.json",
    "INCONCLUSIVE": VERDICT_FIXTURE_DIR / "inconclusive.json",
}


def verdict_fixture(outcome: str) -> dict:
    """The hand-written expected artifact for *outcome*, freshly parsed.

    Freshly, so a test that mutates its copy cannot leak the mutation into the
    next test — the ACCEPT half of every REJECT test depends on the canonical
    document really being canonical.
    """
    return json.loads(VERDICT_FIXTURES[outcome].read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def schema() -> dict:
    """The shipped JSON Schema, read from the file on disk."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def validator(schema: dict) -> Draft202012Validator:
    """A validator that is NOT assay's. `jsonschema` is the independent oracle.

    Hand-rolling a checker would mean every schema test proved the hand-rolled
    checker rather than the schema (A-056).
    """
    return Draft202012Validator(schema)


def why_invalid(validator: Draft202012Validator, instance: dict) -> list[str]:
    """Every validation message, for asserting a rejection is the INTENDED one.

    A rejection test that only asserts "invalid" passes when the instance is
    malformed for an unrelated reason, which would leave the real defect
    accepted. Assert against this, not against a bare boolean.
    """
    return [error.message for error in validator.iter_errors(instance)]


# --- assay, built and installed with nothing else present ---------------------
#
# Hoisted here from tests/test_dependency_purity.py so P01b's packaging oracle
# and P01a's purity oracle share ONE build; nine more packages would otherwise
# each inherit a copy of a subtle two-environment procedure (A-070).


def _build_backend_home() -> Path:
    """Locate an importable setuptools, by DERIVATION from this interpreter.

    The scratch venv is built with ``--no-build-isolation --no-index`` so that
    nothing is fetched from a network. That needs the build backend to be
    importable from somewhere, and the honest way to find it is to ask the
    interpreters we already have rather than to hardcode a container path.
    """
    probe = "import setuptools, pathlib; print(pathlib.Path(setuptools.__file__).parent.parent)"
    candidates = [Path(sys.executable), Path(sys.base_prefix) / "bin" / "python3"]
    for exe in candidates:
        if not exe.exists():
            continue
        proc = subprocess.run([str(exe), "-c", probe], capture_output=True, text=True)
        if proc.returncode == 0:
            return Path(proc.stdout.strip())
    raise AssertionError(
        f"no interpreter among {candidates} can import setuptools, so the "
        f"offline scratch-venv install cannot be built"
    )


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


@dataclass(frozen=True)
class Standalone:
    """assay, built and installed with nothing else present."""

    venv: Path
    wheel: Path

    def run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.venv / "bin" / argv[0]), *argv[1:]],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )


@pytest.fixture(scope="session")
def standalone(tmp_path_factory) -> Standalone:
    """Build assay's wheel and install it into a venv that has nothing else.

    Build and install are two subprocesses with two different environments, on
    purpose (A-070). The build needs ``setuptools`` on ``PYTHONPATH``; if that
    ``PYTHONPATH`` were also present for the *install*, pip would resolve
    requirements against whatever else happens to live in that directory and a
    declared runtime dependency could be silently considered satisfied — the
    venv would no longer contain "only assay" in the sense the claim needs.
    So the install runs with a clean environment and ``--no-index``: nothing to
    fetch from, nothing to leak in.

    The copied source tree is deliberately ``pyproject.toml`` + ``src/`` only,
    with no MANIFEST and no VCS plugin available, so a data file reaches the
    wheel ONLY if ``[tool.setuptools.package-data]`` puts it there. That is what
    makes the packaging oracle able to fail.
    """
    tmp = tmp_path_factory.mktemp("standalone")
    source = tmp / "assay-src"
    source.mkdir()
    shutil.copy(PROJECT_ROOT / "pyproject.toml", source / "pyproject.toml")
    shutil.copytree(
        PROJECT_ROOT / "src",
        source / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )

    venv = tmp / "venv"
    base = Path(sys.base_prefix) / "bin" / "python3"
    creator = str(base if base.exists() else sys.executable)
    subprocess.run([creator, "-m", "venv", str(venv)], check=True, capture_output=True)
    python = venv / "bin" / "python"
    assert python.exists(), "the fresh venv has no interpreter"

    wheels = tmp / "wheels"
    build_env = _clean_env()
    build_env["PYTHONPATH"] = str(_build_backend_home())
    built = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheels),
            str(source),
        ],
        capture_output=True,
        text=True,
        env=build_env,
    )
    assert built.returncode == 0, f"wheel build failed:\n{built.stdout}\n{built.stderr}"
    candidates = sorted(wheels.glob("assay-*.whl"))
    assert len(candidates) == 1, f"expected one assay wheel, got {candidates}"

    installed = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", str(candidates[0])],
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert installed.returncode == 0, (
        "offline install failed — with zero runtime dependencies there is "
        f"nothing to resolve:\n{installed.stdout}\n{installed.stderr}"
    )
    return Standalone(venv=venv, wheel=candidates[0])
