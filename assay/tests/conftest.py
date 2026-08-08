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
from types import MappingProxyType
from typing import Mapping

import pytest
from jsonschema import Draft202012Validator

from assay.config import CanaryConfig, CoverageConfig, JudgeConfig, Lane, MutationConfig

#: The `assay/` project directory, derived from this file's own location — the
#: one derivation AGENTS.md §4.2a explicitly blesses. Asserted, so a layout
#: change fails loudly here instead of silently scanning nothing later.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
assert (PROJECT_ROOT / "pyproject.toml").is_file(), (
    f"expected assay's project root at {PROJECT_ROOT}, but there is no "
    f"pyproject.toml there"
)

#: P09's canary fixture (`tests/fixtures/canary/python/`) is a real, committed
#: pytest project — `pkg/greet.py` + `tests/test_greet.py` — that
#: `assay.canary`'s Python orchestration materialises into a disposable
#: `tmp_path` and runs a GENUINE `pytest` subprocess against (A-107). It is
#: not part of assay's OWN test suite: pytest's default `test_*.py` discovery
#: would otherwise ALSO collect it directly from its committed location,
#: where `from pkg.greet import greet` cannot resolve (this fixture's `pkg`
#: is never on assay's own sys.path) — a collection ERROR that would break
#: assay's own gate run. A nested `conftest.py` doing this instead would
#: collide in `sys.modules['conftest']` with THIS file (pytest imports every
#: rootless `conftest.py` under the same bare name), so the ignore lives here.
#:
#: `fixtures/mutation_exec/python/` is P12's own committed real pytest
#: project (`pkg/checks.py` + `tests/test_checks.py`), the same shape and
#: the same reason for the same exclusion: its own `tests/test_checks.py`
#: would otherwise ALSO be collected directly from its committed location,
#: where `from pkg.checks import ...` cannot resolve.
collect_ignore_glob = ["fixtures/canary/python/**", "fixtures/mutation_exec/python/**"]

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
base = "main"

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


# --- real git state, materialised under tmp_path (P02, DESIGN-GUIDE §10) -----
#
# Runtime-materialised, never committed as a nested repository: "dirty-tree,
# base-is-HEAD and merge-commit cases are `git init`'d into `tmp_path` at test
# time. Committing a git repo inside a git repo is the alternative, and it is
# worse." ``GitRepo`` drives a REAL ``git`` binary for both setup and, where a
# test needs an independent expected value, verification — assay.git is a
# thin subprocess boundary, so the only honest oracle for it is git itself.


@dataclass(frozen=True)
class GitRepo:
    """A real git repository under ``tmp_path``."""

    path: Path

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, text=True
        )
        assert proc.returncode == 0, (
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr}"
        )
        return proc.stdout

    def write(self, rel: str, text: str) -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    """An initialised git repository with one committed file on ``main``.

    The seed commit guarantees ``HEAD`` always exists and gives every test a
    common ancestor to branch from, without any test having to special-case
    "the very first commit has no parent".
    """
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("README.md", "seed\n")
    repo.commit_all("seed")
    return repo


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


# --- the runner's R0 verdicts (P04) -------------------------------------------
#
# Additional hand-written artifacts for branches P04's runner is the FIRST
# producer of: an R0-only lane's own PASS, FAIL/COMMAND_FAILED, and two
# distinct ERROR/EXEC_FAILED shapes (missing executable vs. an argv append the
# lane never permitted). Kept OUT of `VERDICT_FIXTURES` deliberately: that dict
# is asserted 1:1 against `Outcome` by
# test_verdict_serialises.py::test_there_is_one_verdict_per_outcome_and_no_outcome_is_unproven,
# and these are additional EXAMPLES of outcomes already represented there, not
# new outcomes.

RUNNER_VERDICT_FIXTURES: dict[str, Path] = {
    "r0_pass": VERDICT_FIXTURE_DIR / "r0_pass.json",
    "r0_fail_command_failed": VERDICT_FIXTURE_DIR / "r0_fail_command_failed.json",
    "r0_error_exec_failed_missing_executable": (
        VERDICT_FIXTURE_DIR / "r0_error_exec_failed_missing_executable.json"
    ),
    "r0_error_argv_append_rejected": (
        VERDICT_FIXTURE_DIR / "r0_error_argv_append_rejected.json"
    ),
}


def runner_verdict_fixture(name: str) -> dict:
    """The hand-written expected artifact for one of P04's own R0 branches."""
    return json.loads(RUNNER_VERDICT_FIXTURES[name].read_text(encoding="utf-8"))


# --- the runner's R0+R1 verdicts (P05) ----------------------------------------
#
# Full, independently hand-written artifacts for the branches
# assay.runner.evaluate_r1 is the FIRST producer of: R1 PASS, R1 FAIL under
# each of its two reason codes, and the three NO_MEASUREMENT guard branches
# (A-090/O4). Kept out of both VERDICT_FIXTURES and RUNNER_VERDICT_FIXTURES
# for the same reason those two stay separate from each other: additional
# EXAMPLES of outcomes already represented there, not new outcomes.

R1_VERDICT_FIXTURES: dict[str, Path] = {
    name: VERDICT_FIXTURE_DIR / f"{name}.json"
    for name in (
        "r1_pass",
        "r1_fail_uncovered_lines",
        "r1_fail_excluded_lines",
        "r1_no_measurement_dirty_tree",
        "r1_no_measurement_base_is_head",
        "r1_no_measurement_empty_coverage",
    )
}


# --- P07's own R1 verdicts: attribution's three terminal shapes --------------
#
# Full, independently hand-written artifacts for the three NEW terminal paths
# P07 adds to the R1 union: an attributed PASS, an attributed FAIL/
# UNCOVERED_LINES, and the wholly new FAIL/UNCLASSIFIED_LINES (A-100). Built
# directly from `Claim`/`Coverage`/`Verdict` in the test module that consumes
# them, NOT through `assay.runner.evaluate_r1` -- `runner.py` is outside this
# package's `scope.touch`, so these prove the claim payload P07 owns rather
# than the (unmodified, and for this package unreachable) runner wiring.

SPAN_VERDICT_FIXTURES: dict[str, Path] = {
    name: VERDICT_FIXTURE_DIR / f"{name}.json"
    for name in (
        "r1_pass_span_attributed",
        "r1_fail_uncovered_lines_span_attributed",
        "r1_fail_unclassified_lines",
    )
}


def span_verdict_fixture(name: str) -> dict:
    """The hand-written expected artifact for one of P07's own attribution
    branches."""
    return json.loads(SPAN_VERDICT_FIXTURES[name].read_text(encoding="utf-8"))


# --- P09's own R3 verdicts: the cause-sensitive canary's four terminal shapes --
#
# Full, independently hand-written artifacts for A-109's four named terminal
# paths: an attributed PASS, CANARY_SURVIVED via an unexpected pass,
# CANARY_SURVIVED via the wrong observed reason, and CANARY_INCONCLUSIVE.
# Built directly from `Claim`/`CanaryResult`/`Verdict` in the test module
# that consumes them (O2's own "independently written schema-valid
# artifact") -- kept out of the other *_VERDICT_FIXTURES dicts for the same
# reason SPAN_VERDICT_FIXTURES is: additional EXAMPLES of outcomes already
# represented in VERDICT_FIXTURES, not new outcomes.

CANARY_VERDICT_FIXTURES: dict[str, Path] = {
    name: VERDICT_FIXTURE_DIR / f"{name}.json"
    for name in (
        "r3_pass",
        "r3_fail_canary_survived_unexpected_pass",
        "r3_fail_canary_survived_wrong_reason",
        "r3_inconclusive_canary_inconclusive",
    )
}


def canary_verdict_fixture(name: str) -> dict:
    """The hand-written expected artifact for one of P09's own R3 branches."""
    return json.loads(CANARY_VERDICT_FIXTURES[name].read_text(encoding="utf-8"))


def r1_verdict_fixture(name: str) -> dict:
    """The hand-written expected artifact for one of P05's own R1 branches."""
    return json.loads(R1_VERDICT_FIXTURES[name].read_text(encoding="utf-8"))


# --- P10's own evidence verdicts: the four A-075/O4 terminal shapes ----------
#
# Full, independently hand-written artifacts distinguishing evidence that was
# never declared, declared but missing, current, and stale (O4). Built
# directly from `EvidenceDeclaration`/`Evidence`/`Claim`/`Verdict` in the test
# module that consumes them -- kept out of the other *_VERDICT_FIXTURES dicts
# for the same reason CANARY_VERDICT_FIXTURES is: additional EXAMPLES of
# outcomes already represented in VERDICT_FIXTURES, not new outcomes.

EVIDENCE_VERDICT_FIXTURES: dict[str, Path] = {
    name: VERDICT_FIXTURE_DIR / f"{name}.json"
    for name in (
        "evidence_never_declared",
        "evidence_declared_missing",
        "evidence_current",
        "evidence_stale",
    )
}


def evidence_verdict_fixture(name: str) -> dict:
    """The hand-written expected artifact for one of P10's own evidence
    branches."""
    return json.loads(EVIDENCE_VERDICT_FIXTURES[name].read_text(encoding="utf-8"))


# --- P12's own R2 verdicts: mutation execution's terminal shapes -------------
#
# Full, independently hand-written artifacts for A-116/A-117's terminal
# mutation-execution cases: a killed-only PASS, FAIL/MUTANTS_SURVIVED,
# INCONCLUSIVE/NO_MUTANTS, BUDGET_EXCEEDED/LANE_TIMEOUT, and the two
# genuinely different ERROR/EXEC_FAILED shapes A-116 itself calls out (a
# crashed BASELINE, `mutation=None`, versus a crashed MUTANT, `mutation`
# present with a non-empty `crashed` bucket). Built directly from
# `Claim`/`Mutation`/`MutantOutcome`/`Verdict` in the test module that
# consumes them, kept out of the other *_VERDICT_FIXTURES dicts for the
# same reason CANARY_VERDICT_FIXTURES is: additional EXAMPLES of outcomes
# already represented in VERDICT_FIXTURES, not new outcomes.

MUTATION_VERDICT_FIXTURES: dict[str, Path] = {
    name: VERDICT_FIXTURE_DIR / f"{name}.json"
    for name in (
        "r2_pass",
        "r2_fail_mutants_survived",
        "r2_inconclusive_no_mutants",
        "r2_budget_exceeded_lane_timeout",
        "r2_error_exec_failed_mutant_crashed",
        "r2_error_exec_failed_baseline_crashed",
    )
}


def mutation_verdict_fixture(name: str) -> dict:
    """The hand-written expected artifact for one of P12's own R2 branches."""
    return json.loads(MUTATION_VERDICT_FIXTURES[name].read_text(encoding="utf-8"))


# --- Lane objects built directly, bypassing assay.toml/tomllib (P04) ---------
#
# Runner-level unit tests need exact control over a Lane's fields (a specific
# commit-matching argv, a specific budget, allow_argv_append toggled) without
# the ceremony of writing and loading a TOML file through config.py — that
# ceremony belongs to config.py's OWN tests, which already prove the loader.
# Bypassing it here only proves assay.runner, which is what the module under
# test actually is.


def make_lane(
    *,
    name: str = "package",
    scope: str = "S1",
    rigor: tuple[str, ...] = ("R0",),
    enforcement: str = "gate",
    argv: tuple[str, ...] = ("/bin/sh", "-c", "exit 0"),
    env: Mapping[str, str] = MappingProxyType({}),
    env_passthrough: tuple[str, ...] = (),
    budget: str = "5m",
    budget_seconds: float = 300.0,
    allow_argv_append: bool = False,
    judge: "JudgeConfig | None" = None,
) -> Lane:
    return Lane(
        name=name,
        scope=scope,
        rigor=rigor,
        enforcement=enforcement,
        argv=argv,
        env=MappingProxyType(dict(env)),
        env_passthrough=env_passthrough,
        budget=budget,
        budget_seconds=budget_seconds,
        allow_argv_append=allow_argv_append,
        judge=judge,
        where=None,
    )


# --- R1 evaluation harness (P05) ----------------------------------------------
#
# make_r1_judge/FakeAdapter give runner- and evaluate-level tests exact control
# over a JudgeConfig and a synthetic, non-Python language, the same way
# make_lane bypasses assay.toml/tomllib for Lane — this proves assay.evaluate
# and the relevant slice of assay.runner, not assay.config's own loader, which
# already has its own suite.


def make_r1_judge(
    *,
    language: str = "zzz",
    source_root_paths: tuple[Path, ...],
    fail_under: float = 100.0,
    allow_excluded: bool = False,
    coverage_format: str = "coverage-py-json",
    coverage_artifact: str = "cov.json",
    base: str = "main",
    mutation: "MutationConfig | None" = None,
) -> JudgeConfig:
    """A fully-resolved R1 ``JudgeConfig`` — every field
    ``JUDGE_FIELDS_BY_RIGOR["R1"]`` names — built directly rather than
    through ``assay.toml``, mirroring :func:`make_lane`. *base* is a
    placeholder string here: callers that exercise real base RESOLUTION
    (``runner.evaluate_r1``'s own tests) pass their own resolved/declared
    ref straight to that function, not through this field -- only
    ``runner.run_lane`` reads ``judge.base`` itself. *mutation* (P18)
    lets a caller build a combined R1+R2 judge without a second
    constructor -- ``None`` (the default) is unchanged R1-only behaviour."""
    return JudgeConfig(
        language=language,
        source_roots=tuple(str(p) for p in source_root_paths),
        source_root_paths=source_root_paths,
        fail_under=fail_under,
        allow_excluded=allow_excluded,
        coverage=CoverageConfig(format=coverage_format, artifact=coverage_artifact),
        mutation=mutation,
        canary=None,
        base=base,
    )


def make_r2_judge(
    *,
    language: str = "zzz",
    source_root_paths: tuple[Path, ...],
    base: str = "main",
    mutation: MutationConfig,
) -> JudgeConfig:
    """A fully-resolved R2-ONLY ``JudgeConfig`` (P18) — language,
    source_roots, mutation and base, and nothing else: R1's coverage-floor
    fields stay ``None``, matching a real ``assay.toml`` lane that
    declares ``rigor = ["R0", "R2"]`` without R1 alongside it (A-062: a
    real loader would refuse ``fail_under``/``coverage`` here as inert
    config for an undeclared level)."""
    return JudgeConfig(
        language=language,
        source_roots=tuple(str(p) for p in source_root_paths),
        source_root_paths=source_root_paths,
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=mutation,
        canary=None,
        base=base,
    )


def make_r3_judge(
    *,
    language: str = "zzz",
    source_root_paths: tuple[Path, ...],
    canary: CanaryConfig,
) -> JudgeConfig:
    """A fully-resolved R3-ONLY ``JudgeConfig`` (P19) -- language,
    source_roots and canary, and nothing else: R1's coverage-floor fields
    and R2's mutation policy stay ``None``, matching a real ``assay.toml``
    lane that declares ``rigor = ["R0", "R3"]`` without R1/R2 alongside it
    (A-062: a real loader would refuse ``fail_under``/``mutation`` here as
    inert config for an undeclared level). ``base`` stays ``None`` too --
    R3 alone does not require it (``JUDGE_FIELDS_BY_RIGOR["R3"]``)."""
    return JudgeConfig(
        language=language,
        source_roots=tuple(str(p) for p in source_root_paths),
        source_root_paths=source_root_paths,
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=None,
        canary=canary,
        base=None,
    )


@dataclass(frozen=True, kw_only=True)
class FakeAdapter:
    """A synthetic, deliberately non-Python :class:`~assay.adapters.base.
    LanguageAdapter` (A-097): P05's O2 exists to prove the evaluation core
    reaches the identical result for a language that is not Python, using
    an extension (``.zzz``) and a classification rule (a literal
    ``NO-CODE``/test-path marker in the text) that could not possibly be
    satisfied by ``ast`` or a hardcoded ``.py`` filter.

    ``key_prefix`` stands in for a real adapter's module-path strip (Go's
    own case, DESIGN-GUIDE §11): :meth:`normalize_coverage_key` removes it,
    proving the core consults the adapter's own hook rather than assuming
    the coverage artifact's keys already match ``git diff`` paths.
    """

    name: str = "zzz"
    source_globs: tuple[str, ...] = ("*.zzz",)
    excluded_dir_names: frozenset[str] = frozenset({"vendor"})
    requires_span_attribution: bool = False
    external_tools: tuple[str, ...] = ()
    key_prefix: str = ""
    test_marker: str = "_test.zzz"
    no_code_marker: str = "NO-CODE"

    def is_test_path(self, rel_path: str) -> bool:
        return self.test_marker in rel_path

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return self.no_code_marker not in text

    def normalize_coverage_key(self, key: str) -> str:
        return key.removeprefix(self.key_prefix) if self.key_prefix else key


def write_coverage_json(path: Path, files: Mapping[str, Mapping[str, list]]) -> None:
    """Write a minimal coverage.py-JSON-shaped artifact at *path*.

    *files* maps a coverage-artifact key to a record with any of
    ``executed_lines``/``missing_lines``/``excluded_lines`` (omitted means
    empty) — deliberately the SAME format
    :mod:`assay.coverage_parsers.coverage_py_json` parses, reused here for a
    fake, non-Python LANGUAGE to demonstrate format and language are
    independent axes (DESIGN-GUIDE §11): a synthetic ``.zzz`` file can be
    "measured" by a real, already-proven coverage FORMAT parser.
    """
    document = {
        "files": {
            key: {
                "executed_lines": list(record.get("executed_lines", [])),
                "missing_lines": list(record.get("missing_lines", [])),
                "excluded_lines": list(record.get("excluded_lines", [])),
            }
            for key, record in files.items()
        }
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def fixed_clock(*moments):
    """A ``runner.Clock`` stub returning *moments* in order, one per call.

    Raises ``StopIteration`` if called more times than there are moments — a
    silent extra call (e.g. a stray third timestamp) would otherwise go
    unnoticed rather than failing the test that used it (AUTHORING.md §3b.A:
    no real clock, and no test that could pass regardless of correctness).
    """
    iterator = iter(moments)
    return lambda: next(iterator)


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
