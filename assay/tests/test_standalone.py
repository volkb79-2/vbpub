"""P13 — the SHIPPED WHEEL, not the source checkout, is a zero-runtime-
dependency executable that does real work (O1/O3).

The two-environment offline build/install recipe (A-070) already exists as
``conftest.py``'s session-scoped ``standalone`` fixture (A-123) and is
already exercised by ``test_dependency_purity.py``/
``test_verdict_schema_is_packaged.py`` for: zero ``Requires-Dist``, no
source-tree/``PYTHONPATH`` leakage, a venv holding only ``assay``, the
schema packaged and independently loadable from inside the venv, and the
console script resolving and running ``assay lanes``. This module fills in
what those two do NOT yet cover:

* **O1** — ``assay run`` through the INSTALLED console script producing a
  genuine R0 verdict (PASS and FAIL, both real subprocess outcomes, never
  constructed by hand), matched — modulo ``assay_version``/``commit``/
  ``started``/``ended``, which a real run cannot inject — against the
  hand-written shape in ``tests/fixtures/verdicts/r0_pass.json``.
* **O1's negative**, corrected by A-124: NOT "removing ``fallback_version``
  breaks the build" (independently confirmed unfalsifiable in this gate
  image — ``setuptools_scm`` is present in no interpreter here, so that code
  path never executes and the build is byte-identical either way). Instead:
  removing the package-data declaration or the console-script declaration
  from a COPY of ``pyproject.toml``, built and installed fresh (never the
  shared ``standalone`` fixture, which must stay unmutated for every other
  consumer), each still builds and installs cleanly but breaks a genuinely
  different downstream property — the schema fails to load, or there is no
  ``bin/assay`` to invoke at all. A third, related break (Work item 4,
  genuinely testable in this image): declaring a runtime dependency does not
  break the *build* (``--no-deps``), but does break the offline
  ``--no-index`` *install*, which is the property A-005 actually depends on
  holding at the wheel level, not only at the AST level
  (``test_dependency_purity.py``'s own tainted-copy check).
* **O3** — the installed wheel runs one COMMITTED Python fixture
  (``tests/fixtures/canary/python/``) through the FULL real pipeline (a
  genuine ``pytest`` subprocess via the installed ``assay run``), and proves
  the Go adapter SHIPS and is CALLABLE against real, committed Go source
  text (``tests/fixtures/canary/go/greet/greet.go``) — adapter-level only,
  per A-126: no Go toolchain exists anywhere in this devcontainer
  (A-042/A-087), so a genuine Go R0 run is categorically unavailable and
  scripting one would substitute a hand-picked result for a measurement.
  "Independently validate the packaged schema v2" (O3's remaining third) is
  proven here by validating the REAL emitted R0 artifact against it, rather
  than re-proving what ``test_verdict_schema_is_packaged.py`` already does.

**P17 added** the same proof one rigor level up: a real two-commit Python
fixture judged at R1 through the installed console script, compared as a
COMPLETE document.

**P18 (work item 7 / O4) adds R2**: real changed-line mutants, built and
executed by the installed wheel, rendered as complete documents for five of
the six terminal shapes O4 names -- killed, survived, no-mutants,
baseline-adverse, and budget-exceeded -- each with the shared source tree
hashed before and after. The sixth (a CRASHED mutant) is not producible
through a real lane at all; the section below says why, at the point where
its absence would otherwise look like an omission.

A-125: nothing here is a committed ``test_*.py`` under
``tests/fixtures/standalone/`` — every fixture used is either already
committed and already ``collect_ignore_glob``-excluded
(``tests/fixtures/canary/python/``, ``tests/fixtures/canary/go/greet/``) or
materialised as a literal string/temp file at test time.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from conftest import (
    GitRepo,
    PROJECT_ROOT,
    Standalone,
    _build_backend_home,
    _clean_env,
    runner_verdict_fixture,
    why_invalid,
)

GO_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "canary" / "go" / "greet" / "greet.go"
assert GO_FIXTURE.is_file(), f"expected the committed Go canary fixture at {GO_FIXTURE}"

PYTHON_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "canary" / "python"
assert (PYTHON_FIXTURE_DIR / "pkg" / "greet.py").is_file(), (
    f"expected the committed Python canary fixture at {PYTHON_FIXTURE_DIR}"
)

ORIGINAL_PYPROJECT = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")


# --- building a lane file for a real `assay run` invocation -------------------


def _write_lane_file(project_root: Path, lane_toml: str) -> Path:
    path = project_root / "assay.toml"
    path.write_text(lane_toml, encoding="utf-8")
    return path


def _r0_lane_toml(
    argv: list[str],
    *,
    env_line: str = "env = {}",
    env_passthrough: tuple[str, ...] = ("PATH",),
) -> str:
    """A single-lane, R0-only ``assay.toml`` — the eight required fields and
    nothing else (A-048), *argv* substituted in as a TOML string array."""
    argv_toml = ", ".join(json.dumps(token) for token in argv)
    passthrough_toml = ", ".join(json.dumps(name) for name in env_passthrough)
    return (
        "schema_version = 1\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0"]\n'
        'enforcement = "gate"\n'
        f"argv = [{argv_toml}]\n"
        f"{env_line}\n"
        f"env_passthrough = [{passthrough_toml}]\n"
        'budget = "2m"\n'
        "allow_argv_append = false\n"
    )


def _run_assay(standalone: Standalone, lane_file: Path) -> subprocess.CompletedProcess[str]:
    return standalone.run(
        "assay", "run", "package", "--file", str(lane_file), "--verdict-json", "-"
    )


# --- O1: a real `assay run` through the installed console script -------------


def test_a_real_pass_matches_the_documented_r0_pass_shape(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """The installed wheel, given the SAME lane r0_pass.json documents,
    produces the SAME artifact — modulo the four fields a real run cannot
    hand-inject (assay_version, commit, started, ended)."""
    lane_toml = _r0_lane_toml(
        ["/bin/sh", "-c", "exit 0"],
        env_line='env = { MOCK_MODE = "true" }',
        env_passthrough=(),
    )
    lane_file = _write_lane_file(git_repo.path, lane_toml)
    git_repo.commit_all("add r0-pass lane")

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 0, proc.stderr
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == [], "the real artifact is not schema-valid"

    expected = runner_verdict_fixture("r0_pass")
    volatile = {"assay_version", "commit", "started", "ended"}
    assert {k: v for k, v in real.items() if k not in volatile} == {
        k: v for k, v in expected.items() if k not in volatile
    }

    # A-069/A-124: setuptools_scm never loads in this gate image, so the
    # real installed wheel's version is the documented "0.0.0" fallback —
    # never r0_pass.json's own hand-written "0.1.0", which this real run
    # could never produce here.
    assert real["assay_version"] == "0.0.0"
    assert real["commit"] == git_repo.head()


def test_a_real_nonzero_exit_produces_a_genuine_fail_command_failed(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    lane_file = _write_lane_file(
        git_repo.path, _r0_lane_toml(["/bin/sh", "-c", "exit 3"])
    )
    git_repo.commit_all("add failing lane")

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 1, proc.stderr  # Outcome.FAIL.exit_code (A-021)
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    assert real["outcome"] == "FAIL"
    assert real["claims"] == [
        {
            "rigor": "R0",
            "source": "computed",
            "status": "FAIL",
            "reason_code": "COMMAND_FAILED",
            "verified_by_assay": True,
        }
    ]


# --- O3: a real committed Python fixture through the FULL real pipeline -------


def test_a_real_python_fixture_passes_through_the_installed_wheel(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """A-126's Python half: the FULL pipeline, through the installed
    console script's own subprocess — never assay's dev-tree import. The
    fixture's own ``pytest`` run is genuine (``sys.executable`` — the gate's
    own interpreter, which has the ``test`` extra installed — never the
    scratch venv, which deliberately has nothing but assay itself)."""
    shutil.copytree(PYTHON_FIXTURE_DIR / "pkg", git_repo.path / "pkg")
    shutil.copytree(PYTHON_FIXTURE_DIR / "tests", git_repo.path / "tests")
    # P20/A-175: a real pytest run leaves __pycache__/.pytest_cache behind --
    # untracked, non-ignored droppings the post-command dirty check correctly
    # refuses (unrelated to what this test measures).
    git_repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    lane_file = _write_lane_file(
        git_repo.path,
        _r0_lane_toml([sys.executable, "-m", "pytest", "tests", "-q"]),
    )
    git_repo.commit_all("add python fixture")

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 0, proc.stderr
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == [], "the real artifact is not schema-valid"
    assert real["outcome"] == "PASS"
    assert real["claims"] == [
        {"rigor": "R0", "source": "computed", "status": "PASS", "verified_by_assay": True}
    ]
    assert real["argv_effective"] == [sys.executable, "-m", "pytest", "tests", "-q"]


def test_a_real_r1_lane_passes_through_the_installed_wheel(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O1: the installed wheel runs a REAL two-commit Python fixture and
    emits exactly R0 and R1 claims matching a COMPLETE hand-written
    schema-v3 artifact. The coverage artifact is genuine ``pytest-cov``
    output (``--cov-report=json:...``), never hand-written -- the BASE
    commit is the fixture's own already-fully-tested control; the HEAD
    commit adds one new, fully-tested function, so the changed-line diff
    R1 measures is real and small enough to reason about by hand.

    Two things this asserts that a field-by-field version cannot:

    * **The whole document.** ``expected`` below is written out in full and
      compared with ``==`` (the established form -- see
      ``test_a_real_pass_matches_the_documented_r0_pass_shape`` and the
      eleven other complete comparisons across this suite), so a field the
      producer adds, drops, or fills in wrongly fails here even though no
      assertion names it. Only the three values a real run genuinely
      cannot hand-inject are excluded.
    * **``judge.base`` is RESOLVED, not echoed.** The lane declares a
      SYMBOLIC branch name; ``judgment.r1.base`` must be the 40-char commit
      it resolves to. Declaring a full SHA here -- as every other base
      assertion in the suite does -- makes resolved and declared
      indistinguishable, so ``base=judge.base`` would pass. It does not
      pass this one (A-141).
    """
    shutil.copytree(PYTHON_FIXTURE_DIR / "pkg", git_repo.path / "pkg")
    shutil.copytree(PYTHON_FIXTURE_DIR / "tests", git_repo.path / "tests")
    # P20/A-175: a real pytest+coverage run leaves __pycache__/.pytest_cache/
    # .coverage behind, and this lane's own cov.json is genuine output -- all
    # must be git-ignored or the post-command dirty check correctly refuses
    # the run.
    git_repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n.coverage\ncov.json\n")
    git_repo.commit_all("add control fixture")
    base_rev = git_repo.head()
    subprocess.run(
        ["git", "branch", "control-baseline", base_rev],
        cwd=git_repo.path,
        check=True,
        capture_output=True,
    )

    (git_repo.path / "pkg" / "farewell.py").write_text(
        'def farewell(name: str) -> str:\n    return f"Goodbye, {name}!"\n',
        encoding="utf-8",
    )
    (git_repo.path / "tests" / "test_farewell.py").write_text(
        "from pkg.farewell import farewell\n\n\n"
        "def test_farewell_returns_a_goodbye_message():\n"
        '    assert farewell("world") == "Goodbye, world!"\n',
        encoding="utf-8",
    )
    git_repo.commit_all("add farewell")

    lane_toml = (
        "schema_version = 1\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R1"]\n'
        'enforcement = "gate"\n'
        f"argv = {json.dumps([sys.executable, '-m', 'pytest', 'tests', '-q', '--cov=pkg', '--cov-report=json:cov.json'])}\n"
        # pytest writes .pyc caches under pkg/__pycache__ unless told not to
        # -- untracked files UNDER the declared source root, which would
        # otherwise trip evaluate_r1's own post-execution check_dirty_tree
        # guard (test_canary_python_pipeline.py hit this exact case first).
        'env = { PYTHONDONTWRITEBYTECODE = "1" }\n'
        # No passthrough at all, so `env_effective` is fully determined and
        # the complete comparison below stays honest -- the lane's argv is
        # an absolute interpreter path and needs nothing from PATH.
        "env_passthrough = []\n"
        'budget = "2m"\n'
        "allow_argv_append = false\n\n"
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["pkg"]\n'
        "fail_under = 100.0\n"
        "allow_excluded = false\n"
        'coverage = { format = "coverage-py-json", artifact = "cov.json" }\n'
        'base = "control-baseline"\n'
    )
    lane_file = _write_lane_file(git_repo.path, lane_toml)
    git_repo.commit_all("add assay.toml")

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 0, proc.stderr
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == [], "the real artifact is not schema-valid"

    argv = [sys.executable, "-m", "pytest", "tests", "-q", "--cov=pkg",
            "--cov-report=json:cov.json"]
    expected = {
        "schema_version": 3,
        "lane": "package",
        "commit": git_repo.head(),
        "outcome": "PASS",
        "exit_code": 0,
        "declared_rigor": ["R0", "R1"],
        "declared_evidence": [],
        "argv_declared": argv,
        "argv_appended": [],
        "argv_effective": argv,
        "argv_modified": False,
        "env_declared": {"PYTHONDONTWRITEBYTECODE": "1"},
        "env_effective": {"PYTHONDONTWRITEBYTECODE": "1"},
        "scope": "S1",
        "enforcement": "gate",
        "judgment": {
            "r1": {
                "language": "python",
                "source_roots": ["pkg"],
                "coverage_format": "coverage-py-json",
                "coverage_artifact": "cov.json",
                "fail_under": 100.0,
                "allow_excluded": False,
                # the SYMBOLIC `control-baseline` above, resolved.
                "base": base_rev,
            }
        },
        "claims": [
            {
                "rigor": "R0",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
            },
            {
                "rigor": "R1",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
                "coverage": {
                    # HEAD adds exactly `pkg/farewell.py`: one `def` line and
                    # one `return` line, both executable, both executed by
                    # the new test. `tests/test_farewell.py` is outside the
                    # declared source root, so it is not considered at all.
                    "considered": 1,
                    "changed_executable": 2,
                    "covered": 2,
                    "pct": 100.0,
                    "missing_lines": {},
                    "files_missing_coverage": [],
                    "unclassified_lines": {},
                    "files_with_unclassified_lines": [],
                    "excluded_lines": {},
                    "files_with_excluded_lines": [],
                },
            },
        ],
        "evidence": [],
    }
    # `assay_version` is deliberately NOT pinned here: A-069/A-124's "0.0.0"
    # claim is real but environment-specific, and
    # `test_a_real_pass_matches_the_documented_r0_pass_shape` already owns
    # it. Asserting it twice would make a SECOND test red outside the gate
    # image for a reason that has nothing to do with R1.
    volatile = {"assay_version", "started", "ended"}
    assert {k: v for k, v in real.items() if k not in volatile} == expected


# --- O4 (P18 work item 7): a real R2 lane through the installed wheel --------
#
# Every lane below declares its PATH explicitly (`env = { PATH = ... }`,
# `env_passthrough = []`) rather than passing the ambient one through: the
# commands genuinely need `grep`/`sleep`, and a passed-through PATH would put
# a machine-specific string into `env_effective` and make the complete
# document comparisons below dishonest.
#
# ONE of O4's six named terminal shapes is deliberately absent: a CRASHED
# mutant (`ERROR`/`EXEC_FAILED`). That bucket means the mutant's process
# could not be STARTED at all, and it is not producible through a real
# installed lane: `argv` is byte-identical for the baseline and every mutant
# (A-118, proven in `test_mutation_argv_fidelity.py`), the scratch tree is a
# faithful `copytree` of the project root, and only ONE source file's text
# differs -- so any argv that launches for the baseline launches for every
# mutant too, and any argv that fails to launch fails the baseline first,
# short-circuiting before a mutant is ever submitted. It is reachable only
# through an injected process boundary, where `test_mutation_isolation.py`
# and `test_mutation_judge.py` already drive it alongside the other three.
# Recorded here rather than silently omitted (A-147).


def _r2_lane_toml(
    *,
    script: str,
    base: str,
    operators: tuple[str, ...] = ("compare-swap",),
    jobs: int = 1,
    budget: str = "2m",
) -> str:
    operators_toml = ", ".join(json.dumps(name) for name in operators)
    return (
        "schema_version = 1\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R2"]\n'
        'enforcement = "gate"\n'
        f"argv = {json.dumps(['/bin/sh', '-c', script])}\n"
        'env = { PATH = "/usr/bin:/bin" }\n'
        "env_passthrough = []\n"
        f'budget = "{budget}"\n'
        "allow_argv_append = false\n\n"
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        f'base = "{base}"\n\n'
        "[lanes.package.judge.mutation]\n"
        f"jobs = {jobs}\n"
        f"operators = [{operators_toml}]\n"
    )


#: The command every kill/survive lane below runs: PASS iff the live
#: ``src/mod.py`` still contains the literal ``x > 0``. The baseline does;
#: the ``Gt->GtE`` mutant of line 2 does not, so it is genuinely KILLED,
#: while the mutant of line 6 leaves line 2 untouched and SURVIVES.
_GREP_LINE_2 = "grep -q 'x > 0' src/mod.py"

_MOD_AT_BASE = "def f(x):\n    return 0\n\n\ndef g(y):\n    return 0\n"
_MOD_AT_HEAD = "def f(x):\n    return x > 0\n\n\ndef g(y):\n    return y > 1\n"

_ARGV_KEYS = ("argv_declared", "argv_effective")


def _tracked_file_hashes(root: Path) -> dict[str, str]:
    """Every file in the worktree except git's own bookkeeping, by sha256 --
    O4's "shared source bytes remain unchanged" half. ``.git`` is excluded
    because a real `assay run` legitimately refreshes the index's stat cache
    while checking cleanliness; nothing under it is source."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _seed_two_compare_swap_sites(git_repo: GitRepo) -> str:
    """A real two-commit history introducing exactly TWO ``compare-swap``
    sites (lines 2 and 6), with a BRANCH NAME left pointing at the base --
    the lane declares that symbolic ref, never the 40-char SHA."""
    (git_repo.path / "src").mkdir()
    (git_repo.path / "src" / "mod.py").write_text(_MOD_AT_BASE, encoding="utf-8")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.git("branch", "control-baseline", base_rev)
    (git_repo.path / "src" / "mod.py").write_text(_MOD_AT_HEAD, encoding="utf-8")
    git_repo.commit_all("introduce two compare-swap sites")
    return base_rev


def _expected_r2_artifact(
    *,
    git_repo: GitRepo,
    script: str,
    outcome: str,
    reason_code: str,
    exit_code: int,
    r2_claim: dict,
    jobs: int = 1,
    operators: list[str] | None = None,
    r0_claim: dict | None = None,
) -> dict:
    """The COMPLETE expected document, written out in full so a field the
    producer adds, drops, or fills in wrongly fails even though no assertion
    names it (the established form -- a dozen other comparisons in this
    suite are written the same way). ``judgment`` is omitted entirely when
    *operators* is ``None``, which is what a run that never rendered a
    mutation payload must emit."""
    argv = ["/bin/sh", "-c", script]
    document = {
        "schema_version": 3,
        "lane": "package",
        "commit": git_repo.head(),
        "outcome": outcome,
        # the ROLLUP's own cause, stated independently rather than copied
        # off *r2_claim* -- an artifact whose top-level reason disagreed
        # with the claim it came from would otherwise pass unnoticed.
        "reason_code": reason_code,
        "exit_code": exit_code,
        "declared_rigor": ["R0", "R2"],
        "declared_evidence": [],
        "argv_declared": argv,
        "argv_appended": [],
        "argv_effective": argv,
        "argv_modified": False,
        "env_declared": {"PATH": "/usr/bin:/bin"},
        "env_effective": {"PATH": "/usr/bin:/bin"},
        "scope": "S1",
        "enforcement": "gate",
        "claims": [
            r0_claim
            or {
                "rigor": "R0",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
            },
            r2_claim,
        ],
        "evidence": [],
    }
    if operators is not None:
        document["judgment"] = {"r2": {"jobs": jobs, "operators": operators}}
    return document


def _assert_complete(real: dict, expected: dict) -> None:
    # `assay_version`/`started`/`ended` are the three values a real run
    # genuinely cannot hand-inject; everything else is compared.
    volatile = {"assay_version", "started", "ended"}
    assert {k: v for k, v in real.items() if k not in volatile} == expected


def test_a_real_r2_lane_kills_one_mutant_and_lets_another_survive_through_the_wheel(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O4's first two terminal shapes in ONE artifact, through the installed
    console script: two independently enumerated changed-line mutants, one
    genuinely killed by a real subprocess and one genuinely surviving it.

    A universal-killed bug (O4's own first negative) fails here, because the
    survivor's own identity -- path, lineno, operator, description -- is in
    the expected document; so does an omitted unattempted identity, because
    ``total`` and the bucket are compared together. And the shared source
    tree is hashed before and after: live-tree mutation (the third negative)
    changes ``src/mod.py``'s digest.
    """
    _seed_two_compare_swap_sites(git_repo)
    lane_file = _write_lane_file(
        git_repo.path,
        _r2_lane_toml(script=_GREP_LINE_2, base="control-baseline"),
    )
    git_repo.commit_all("add assay.toml")
    before = _tracked_file_hashes(git_repo.path)

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 1, proc.stderr  # Outcome.FAIL.exit_code
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == [], "the real R2 artifact is not schema-valid"
    _assert_complete(
        real,
        _expected_r2_artifact(
            git_repo=git_repo,
            script=_GREP_LINE_2,
            outcome="FAIL",
            reason_code="MUTANTS_SURVIVED",
            exit_code=1,
            operators=["compare-swap"],
            r2_claim={
                "rigor": "R2",
                "source": "computed",
                "status": "FAIL",
                "verified_by_assay": True,
                "reason_code": "MUTANTS_SURVIVED",
                "mutation": {
                    "total": 2,
                    "killed": 1,
                    # line 2's `x > 0` -> `x >= 0` is what the lane's own
                    # grep looks for, so THAT one dies; line 6's `y > 1` is
                    # unobserved by the command and lives.
                    "survived": [
                        {
                            "path": "src/mod.py",
                            "lineno": 6,
                            "operator": "compare-swap",
                            "description": "Gt->GtE",
                        }
                    ],
                    "crashed": [],
                    "budget_exceeded": [],
                },
            },
        ),
    )
    assert _tracked_file_hashes(git_repo.path) == before, (
        "every mutant runs in its own copy -- the shared tree must be "
        "byte-identical afterwards"
    )


def test_a_real_r2_lane_diffs_the_resolved_merge_base_not_the_declared_ref(
    standalone: Standalone, git_repo: GitRepo
):
    """A-143's shape, one rigor level over and made genuinely
    discriminating. ``judgment.r2`` records no base, so a symbolic ref alone
    proves nothing here -- instead the declared ref is a branch that has
    DIVERGED, so ``base..HEAD`` and ``merge-base(base, HEAD)..HEAD`` name
    different changed lines and therefore a different number of mutants.

    ``control-baseline`` already carries line 2's own ``x > 0``, so diffing
    against the branch TIP would see only line 6 change and build ONE
    mutant. Diffing against the fork point -- what ``git.resolve_base``
    actually does -- sees both and builds TWO. The count in the artifact is
    the discriminator.
    """
    (git_repo.path / "src").mkdir()
    (git_repo.path / "src" / "mod.py").write_text(_MOD_AT_BASE, encoding="utf-8")
    git_repo.commit_all("the fork point")

    git_repo.git("checkout", "-q", "-b", "control-baseline")
    (git_repo.path / "src" / "mod.py").write_text(
        "def f(x):\n    return x > 0\n\n\ndef g(y):\n    return 0\n", encoding="utf-8"
    )
    git_repo.commit_all("diverge: line 2 already at its HEAD value")

    git_repo.git("checkout", "-q", "main")
    (git_repo.path / "src" / "mod.py").write_text(_MOD_AT_HEAD, encoding="utf-8")
    git_repo.commit_all("introduce two compare-swap sites")

    lane_file = _write_lane_file(
        git_repo.path,
        _r2_lane_toml(script=_GREP_LINE_2, base="control-baseline"),
    )
    git_repo.commit_all("add assay.toml")

    proc = _run_assay(standalone, lane_file)

    real = json.loads(proc.stdout)
    mutation = real["claims"][1]["mutation"]
    assert mutation["total"] == 2, (
        "two mutants means the fork point was diffed; one would mean the "
        "declared branch tip was used verbatim"
    )
    assert [entry["lineno"] for entry in mutation["survived"]] == [6]


def test_a_real_r2_lane_with_no_declared_operator_site_is_inconclusive(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O4's ``no-mutants`` shape. The one changed line IS a real mutation
    site -- a ``bool-const-flip`` -- but the lane declares only
    ``compare-swap``, so the operator filter empties the job list and the
    honest answer is ``INCONCLUSIVE``/``NO_MUTANTS`` with a real, empty
    payload. Never PASS: nothing was measured."""
    (git_repo.path / "src").mkdir()
    (git_repo.path / "src" / "mod.py").write_text(
        "def f():\n    return 0\n", encoding="utf-8"
    )
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.git("branch", "control-baseline", base_rev)
    (git_repo.path / "src" / "mod.py").write_text(
        "def f():\n    return True\n", encoding="utf-8"
    )
    git_repo.commit_all("change the one line to a bool-const site")
    lane_file = _write_lane_file(
        git_repo.path, _r2_lane_toml(script="exit 0", base="control-baseline")
    )
    git_repo.commit_all("add assay.toml")

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 5, proc.stderr  # Outcome.INCONCLUSIVE.exit_code
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    _assert_complete(
        real,
        _expected_r2_artifact(
            git_repo=git_repo,
            script="exit 0",
            outcome="INCONCLUSIVE",
            reason_code="NO_MUTANTS",
            exit_code=5,
            operators=["compare-swap"],
            r2_claim={
                "rigor": "R2",
                "source": "computed",
                "status": "INCONCLUSIVE",
                "verified_by_assay": True,
                "reason_code": "NO_MUTANTS",
                "mutation": {
                    "total": 0,
                    "killed": 0,
                    "survived": [],
                    "crashed": [],
                    "budget_exceeded": [],
                },
            },
        ),
    )


def test_a_real_r2_lane_propagates_an_adverse_baseline_verbatim(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O4's ``baseline-adverse`` shape, and work item 4's own contract read
    end to end: the lane's command fails, so no mutant is ever built and the
    R2 claim reuses R0's own ``(outcome, reason_code)`` VERBATIM with NO
    mutation payload -- and no ``judgment.r2``, because no policy was ever
    applied to anything."""
    _seed_two_compare_swap_sites(git_repo)
    lane_file = _write_lane_file(
        git_repo.path, _r2_lane_toml(script="exit 7", base="control-baseline")
    )
    git_repo.commit_all("add assay.toml")

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 1, proc.stderr  # Outcome.FAIL.exit_code
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    _assert_complete(
        real,
        _expected_r2_artifact(
            git_repo=git_repo,
            script="exit 7",
            outcome="FAIL",
            reason_code="COMMAND_FAILED",
            exit_code=1,
            operators=None,
            r0_claim={
                "rigor": "R0",
                "source": "computed",
                "status": "FAIL",
                "verified_by_assay": True,
                "reason_code": "COMMAND_FAILED",
            },
            r2_claim={
                "rigor": "R2",
                "source": "computed",
                "status": "FAIL",
                "verified_by_assay": True,
                "reason_code": "COMMAND_FAILED",
            },
        ),
    )


def test_a_real_r2_mutant_that_outlives_the_lane_budget_is_its_own_bucket(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O4's ``budget-exceeded`` shape, through a real timeout rather than an
    injected one -- the only one of the six that a real lane can only reach
    by actually waiting.

    The command is content-dependent in the opposite direction to the kill
    test: it sleeps only when ``x > 0`` is ABSENT, which is true for line
    2's mutant and false for the baseline and for line 6's. So the baseline
    and one mutant finish immediately, and exactly one mutant is stopped by
    the lane's own budget -- never conflated with a kill (a timeout exits
    non-zero too, which is precisely the conflation A-122 refuses).

    The margin is deliberate, not tuned: 5s of budget against a 300s sleep,
    with the passing paths doing one `grep` on a six-line file. This cannot
    flip on a slow machine without the whole suite already having failed
    for a different reason (AUTHORING.md §3b.A's own reading -- a slow-host
    red here would be a TRUE red).
    """
    script = "grep -q 'x > 0' src/mod.py || sleep 300"
    _seed_two_compare_swap_sites(git_repo)
    lane_file = _write_lane_file(
        git_repo.path,
        _r2_lane_toml(script=script, base="control-baseline", jobs=2, budget="5s"),
    )
    git_repo.commit_all("add assay.toml")
    before = _tracked_file_hashes(git_repo.path)

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 4, proc.stderr  # Outcome.BUDGET_EXCEEDED.exit_code
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    _assert_complete(
        real,
        _expected_r2_artifact(
            git_repo=git_repo,
            script=script,
            outcome="BUDGET_EXCEEDED",
            reason_code="LANE_TIMEOUT",
            exit_code=4,
            jobs=2,
            operators=["compare-swap"],
            r2_claim={
                "rigor": "R2",
                "source": "computed",
                "status": "BUDGET_EXCEEDED",
                "verified_by_assay": True,
                "reason_code": "LANE_TIMEOUT",
                "mutation": {
                    "total": 2,
                    # line 6's mutant leaves `x > 0` in place, so the grep
                    # succeeds, nothing sleeps, and it SURVIVES.
                    "killed": 0,
                    "survived": [
                        {
                            "path": "src/mod.py",
                            "lineno": 6,
                            "operator": "compare-swap",
                            "description": "Gt->GtE",
                        }
                    ],
                    "crashed": [],
                    "budget_exceeded": [
                        {
                            "path": "src/mod.py",
                            "lineno": 2,
                            "operator": "compare-swap",
                            "description": "Gt->GtE",
                        }
                    ],
                },
            },
        ),
    )
    assert _tracked_file_hashes(git_repo.path) == before


# --- P19 (work item 6 / O2-O3) adds R3: an isolated canary, through the ------
# installed wheel, that copies the consumer's own repository into scratch
# state and proves it never touches the real one.
#
# Work item 6 names six shapes: PASS, broken control, survivor, wrong
# cause, no-op, and malformed configuration. All but ONE are below, and
# PASS is here TWICE -- once per mechanism, because the two mechanisms
# reach their expected reason through different halves of the pipeline
# and only one of them can be proved by an R3-alone lane (A-149).
#
# The single absent shape is a genuine NO-OP transform, for the same class
# of reason O4's own CRASHED-mutant shape is absent (P18's own LOG, "What
# could not be honored as written"): both `_inject_import_break` and
# `_inject_uncovered_line` ALWAYS change the text they are given (an
# unconditional insert/append, never a conditional one), so no input to
# the real PythonAdapter produces one. The existing unit-level proof
# (`test_canary_python_pipeline.py::test_a_transform_that_produces_no_
# change_is_inconclusive`) needs a deliberately no-op-returning FAKE
# adapter subclass, which the installed CLI has no way to inject
# (`cli._built_in_registry` resolves the one real `PythonAdapter`,
# always). Recorded here rather than silently omitted (A-072/A-147).
#
# "Wrong cause" is NOT in that category, contrary to a first reading of
# the fixed mechanism/expected-reason pairing: the pairing fixes what is
# EXPECTED, never what a real run OBSERVES. `import-break` injected into a
# module the lane's own tests never import leaves R0 passing and is caught
# by R1 instead, so the observed reason is genuinely `UNCOVERED_LINES`
# against an expected `COMMAND_FAILED` -- a real wrong cause, real
# adapter, real registry.  (`_MislabeledAdapter` in
# `test_canary_python_pipeline.py` remains the unit-level version of the
# same shape; it is no longer the ONLY way to reach it.)


def _r3_lane_toml(*, script: str, mechanism: str, target: str) -> str:
    return (
        "schema_version = 1\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R3"]\n'
        'enforcement = "gate"\n'
        f"argv = {json.dumps(['/bin/sh', '-c', script])}\n"
        'env = { PATH = "/usr/bin:/bin", PYTHONDONTWRITEBYTECODE = "1" }\n'
        "env_passthrough = []\n"
        'budget = "2m"\n'
        "allow_argv_append = false\n\n"
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["pkg"]\n\n'
        "[lanes.package.judge.canary]\n"
        f'mechanism = "{mechanism}"\n'
        f'target = "{target}"\n'
    )


_PKG_MOD = "def f():\n    return 1\n"
_PKG_TEST_PASS = "from pkg.mod import f\n\n\ndef test_f():\n    assert f() == 1\n"
_PKG_TEST_BROKEN = "from pkg.mod import f\n\n\ndef test_f():\n    assert f() == 'WRONG'\n"

_IMPORT_BREAK_DESCRIPTION = (
    'inserted `raise AssertionError("assay-canary-import-break")` at line 1'
)
_UNCOVERED_LINE_DESCRIPTION = (
    "appended never-called `def _assay_canary_unreached` "
    "(2 uncovered lines) at end of file"
)


def _seed_pkg(git_repo: GitRepo, *, test_body: str = _PKG_TEST_PASS) -> None:
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", _PKG_MOD)
    git_repo.write("tests/test_mod.py", test_body)
    git_repo.commit_all("add pkg")


def _expected_r3_artifact(
    *,
    git_repo: GitRepo,
    script: str,
    mechanism: str,
    target: str,
    outcome: str,
    reason_code: str | None,
    exit_code: int,
    r3_claim: dict,
    r0_claim: dict | None = None,
) -> dict:
    """The COMPLETE expected document -- the established form this whole
    module already uses for R1/R2 (``_expected_r2_artifact``'s own
    docstring)."""
    argv = ["/bin/sh", "-c", script]
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    document = {
        "schema_version": 3,
        "lane": "package",
        "commit": git_repo.head(),
        "outcome": outcome,
        "exit_code": exit_code,
        "declared_rigor": ["R0", "R3"],
        "declared_evidence": [],
        "argv_declared": argv,
        "argv_appended": [],
        "argv_effective": argv,
        "argv_modified": False,
        "env_declared": env,
        "env_effective": env,
        "scope": "S1",
        "enforcement": "gate",
        "judgment": {"r3": {"mechanism": mechanism, "target": target}},
        "claims": [
            r0_claim
            or {
                "rigor": "R0",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
            },
            r3_claim,
        ],
        "evidence": [],
    }
    if reason_code is not None:
        document["reason_code"] = reason_code
    return document


def test_a_real_r3_lane_proves_the_canary_and_passes_through_the_wheel(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O2/O3's PASS shape: a genuine control run, a genuine import-break
    transform committed into an independently-owned scratch COPY, and a
    genuine transformed run that fails for EXACTLY the expected reason --
    through the installed console script, with the consumer's own
    repository (HEAD, index, and every tracked file's bytes) unchanged."""
    _seed_pkg(git_repo)
    script = f"{sys.executable} -m pytest tests -q"
    lane_file = _write_lane_file(
        git_repo.path,
        _r3_lane_toml(script=script, mechanism="import-break", target="pkg/mod.py"),
    )
    git_repo.commit_all("add assay.toml")
    head_before = git_repo.head()

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 0, proc.stderr
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == [], "the real R3 artifact is not schema-valid"
    _assert_complete(
        real,
        _expected_r3_artifact(
            git_repo=git_repo,
            script=script,
            mechanism="import-break",
            target="pkg/mod.py",
            outcome="PASS",
            reason_code=None,
            exit_code=0,
            r3_claim={
                "rigor": "R3",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
                "canary": {
                    "mechanism": "import-break",
                    "description": _IMPORT_BREAK_DESCRIPTION,
                    "control_outcome": "PASS",
                    "transformed_outcome": "FAIL",
                    "expected_reason_code": "COMMAND_FAILED",
                    "observed_reason_code": "COMMAND_FAILED",
                },
            },
        ),
    )
    # O2: the scratch copy's own control/transform commits never reach the
    # shared, tracked source tree -- git-aware, so a real pytest run's own
    # self-ignoring cache directories (`.pytest_cache/`, `.hypothesis/`,
    # each carrying its own `.gitignore`) are correctly not mistaken for
    # pollution the way a raw filesystem walk would be.
    assert git_repo.head() == head_before
    assert git_repo.git("status", "--porcelain") == ""


def test_a_real_r3_lane_with_a_broken_control_is_inconclusive_through_the_wheel(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O1's own negative, made concrete: the KNOWN-GOOD control is not
    actually good (a real, genuine assertion failure) -- so nothing about
    the transform's own cause can be concluded, and the R3 CLAIM itself is
    INCONCLUSIVE, never a silent PASS. The transformed half still genuinely
    runs (the real orchestration never short-circuits on a broken control
    -- only a malformed/no-op transform does), and fails too, just not for
    a reason this claim's own status depends on.

    The OVERALL verdict is FAIL, not INCONCLUSIVE: R3's own control is a
    copy of the SAME committed state R0 itself runs against, so a broken
    control means R0's own real execution against the real repository
    fails too -- correctly dominating the rollup (FAIL outranks
    INCONCLUSIVE), with R3's own finding still fully recorded underneath
    it rather than replacing it.
    """
    _seed_pkg(git_repo, test_body=_PKG_TEST_BROKEN)
    script = f"{sys.executable} -m pytest tests -q"
    lane_file = _write_lane_file(
        git_repo.path,
        _r3_lane_toml(script=script, mechanism="import-break", target="pkg/mod.py"),
    )
    git_repo.commit_all("add assay.toml")
    head_before = git_repo.head()

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 1, proc.stderr  # Outcome.FAIL.exit_code
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    _assert_complete(
        real,
        _expected_r3_artifact(
            git_repo=git_repo,
            script=script,
            mechanism="import-break",
            target="pkg/mod.py",
            outcome="FAIL",
            reason_code="COMMAND_FAILED",
            exit_code=1,
            r0_claim={
                "rigor": "R0",
                "source": "computed",
                "status": "FAIL",
                "verified_by_assay": True,
                "reason_code": "COMMAND_FAILED",
            },
            r3_claim={
                "rigor": "R3",
                "source": "computed",
                "status": "INCONCLUSIVE",
                "verified_by_assay": True,
                "reason_code": "CANARY_INCONCLUSIVE",
                "canary": {
                    "mechanism": "import-break",
                    "description": _IMPORT_BREAK_DESCRIPTION,
                    "control_outcome": "FAIL",
                    "transformed_outcome": "FAIL",
                    "expected_reason_code": "COMMAND_FAILED",
                    "observed_reason_code": "COMMAND_FAILED",
                },
            },
        ),
    )
    assert git_repo.head() == head_before
    assert git_repo.git("status", "--porcelain") == ""


def test_a_real_r3_lane_whose_bad_case_unexpectedly_passes_survives_through_the_wheel(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O1's negative, the concrete case its own text names: an R3-only
    lane's command never inspects changed-line coverage at all, so it
    sails right past a valid, test-neutral, uncovered addition -- CANARY
    survived via an unexpected PASS, never accepted as success."""
    _seed_pkg(git_repo)
    script = f"{sys.executable} -m pytest tests -q"
    lane_file = _write_lane_file(
        git_repo.path,
        _r3_lane_toml(script=script, mechanism="uncovered-line", target="pkg/mod.py"),
    )
    git_repo.commit_all("add assay.toml")
    head_before = git_repo.head()

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 1, proc.stderr  # Outcome.FAIL.exit_code
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    _assert_complete(
        real,
        _expected_r3_artifact(
            git_repo=git_repo,
            script=script,
            mechanism="uncovered-line",
            target="pkg/mod.py",
            outcome="FAIL",
            reason_code="CANARY_SURVIVED",
            exit_code=1,
            r3_claim={
                "rigor": "R3",
                "source": "computed",
                "status": "FAIL",
                "verified_by_assay": True,
                "reason_code": "CANARY_SURVIVED",
                "canary": {
                    "mechanism": "uncovered-line",
                    "description": _UNCOVERED_LINE_DESCRIPTION,
                    "control_outcome": "PASS",
                    "transformed_outcome": "PASS",
                    "expected_reason_code": "UNCOVERED_LINES",
                },
            },
        ),
    )
    assert git_repo.head() == head_before
    assert git_repo.git("status", "--porcelain") == ""


def test_a_malformed_canary_configuration_renders_no_artifact_through_the_wheel(
    standalone: Standalone, git_repo: GitRepo
):
    """Work item 6's sixth shape: a lane-config failure, refused before
    ``HEAD`` is even resolved -- there is no commit to attach a verdict
    to, so this renders no artifact at all (never an ``R3`` claim with a
    manufactured status), matching the "Carried in from P16" section's
    own ruling in the P19 handoff."""
    _seed_pkg(git_repo)
    lane_file = _write_lane_file(
        git_repo.path,
        _r3_lane_toml(
            script=f"{sys.executable} -m pytest tests -q",
            mechanism="not-a-real-mechanism",
            target="pkg/mod.py",
        ),
    )
    git_repo.commit_all("add assay.toml")

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 2, proc.stderr  # Outcome.ERROR.exit_code
    assert proc.stdout == ""
    assert "judge.canary.mechanism" in proc.stderr


# --- R1 declared ALONGSIDE R3: the copy's own R1 half has to be real ---------


def _r1_r3_lane_toml(*, mechanism: str, target: str, base: str) -> str:
    """An R0+R1+R3 lane -- the ONLY shape in which ``uncovered-line`` can
    reach its own expected reason, since ``UNCOVERED_LINES`` is produced by
    R1 and by nothing else. Every other R3 lane in this module declares
    R0+R3 alone, where that mechanism can only ever be reported as having
    SURVIVED."""
    argv = [
        sys.executable, "-m", "pytest", "tests", "-q",
        "--cov=pkg", "--cov-report=json:cov.json",
    ]
    return (
        "schema_version = 1\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R1", "R3"]\n'
        'enforcement = "gate"\n'
        f"argv = {json.dumps(argv)}\n"
        'env = { PYTHONDONTWRITEBYTECODE = "1" }\n'
        "env_passthrough = []\n"
        'budget = "5m"\n'
        "allow_argv_append = false\n\n"
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["pkg"]\n'
        "fail_under = 100.0\n"
        "allow_excluded = false\n"
        'coverage = { format = "coverage-py-json", artifact = "cov.json" }\n'
        f'base = "{base}"\n\n'
        "[lanes.package.judge.canary]\n"
        f'mechanism = "{mechanism}"\n'
        f'target = "{target}"\n'
    )


def _seed_measurable_history(git_repo: GitRepo, *, lonely: bool = False) -> str:
    """Two real commits whose HEAD ADDS a fully-covered ``pkg/mod.py`` -- so
    ``base..HEAD`` has genuinely changed source lines to measure and the R1
    half of BOTH the lane and the canary's control really measures them.
    Returns the base revision.

    *lonely* additionally seeds ``pkg/lonely.py`` AT THE BASE COMMIT: real
    source, under the declared source root, never imported by any test and
    never part of ``base..HEAD``. It is the canary target for the
    wrong-cause case -- transforming a module nothing imports leaves R0
    passing, so the failure the transform DOES cause arrives from R1
    instead of from the command.
    """
    git_repo.write(".gitignore", "cov.json\n.coverage\n")
    git_repo.write("pkg/__init__.py", "")
    if lonely:
        git_repo.write("pkg/lonely.py", "def unused():\n    return 7\n")
    base_rev = git_repo.commit_all("add package skeleton")
    git_repo.write("pkg/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.write(
        "tests/test_mod.py",
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f(1)\n",
    )
    git_repo.commit_all("add mod.py")
    return base_rev


def _r1_r3_expected(
    *,
    git_repo: GitRepo,
    base_rev: str,
    mechanism: str,
    target: str,
    outcome: str,
    reason_code: str | None,
    exit_code: int,
    r1_claim: dict,
    r3_claim: dict,
) -> dict:
    argv = [
        sys.executable, "-m", "pytest", "tests", "-q",
        "--cov=pkg", "--cov-report=json:cov.json",
    ]
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    document = {
        "schema_version": 3,
        "lane": "package",
        "commit": git_repo.head(),
        "outcome": outcome,
        "exit_code": exit_code,
        "declared_rigor": ["R0", "R1", "R3"],
        "declared_evidence": [],
        "argv_declared": argv,
        "argv_appended": [],
        "argv_effective": argv,
        "argv_modified": False,
        "env_declared": env,
        "env_effective": env,
        "scope": "S1",
        "enforcement": "gate",
        "judgment": {
            "r1": {
                "language": "python",
                "source_roots": ["pkg"],
                "coverage_format": "coverage-py-json",
                "coverage_artifact": "cov.json",
                "fail_under": 100.0,
                "allow_excluded": False,
                "base": base_rev,
            },
            "r3": {"mechanism": mechanism, "target": target},
        },
        "claims": [
            {
                "rigor": "R0",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
            },
            r1_claim,
            r3_claim,
        ],
        "evidence": [],
    }
    if reason_code is not None:
        document["reason_code"] = reason_code
    return document


def test_a_real_r3_lane_proves_the_uncovered_line_canary_through_the_wheel(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """O3's second PASS shape, and the ONLY end-to-end place the scratch
    copy's own R1 half is load-bearing: ``uncovered-line``'s expected
    ``UNCOVERED_LINES`` can only be produced by a real coverage evaluation
    run INSIDE the copy, against the copy's own paths.

    A-149's regression oracle at the installed-product level. The lane's
    ``source_roots`` resolve to absolute paths under the CONSUMER's project
    root; the canary runs somewhere else entirely. Judge the copy by the
    consumer's roots and every changed file falls outside every root --
    ``considered`` 0, ``pct`` a vacuous 100.0 -- so the transformed half
    PASSes too and this lane reports ``CANARY_SURVIVED`` instead, with an
    R1 claim that still says PASS.
    """
    base_rev = _seed_measurable_history(git_repo)
    lane_file = _write_lane_file(
        git_repo.path,
        _r1_r3_lane_toml(
            mechanism="uncovered-line", target="pkg/mod.py", base=base_rev
        ),
    )
    git_repo.commit_all("add assay.toml")
    head_before = git_repo.head()

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 0, proc.stderr
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    _assert_complete(
        real,
        _r1_r3_expected(
            git_repo=git_repo,
            base_rev=base_rev,
            mechanism="uncovered-line",
            target="pkg/mod.py",
            outcome="PASS",
            reason_code=None,
            exit_code=0,
            r1_claim={
                "rigor": "R1",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
                "coverage": {
                    # HEAD adds `pkg/mod.py` alone: two executable lines,
                    # both covered by the new test. `tests/test_mod.py` is
                    # outside the declared source root.
                    "considered": 1,
                    "changed_executable": 2,
                    "covered": 2,
                    "pct": 100.0,
                    "missing_lines": {},
                    "files_missing_coverage": [],
                    "unclassified_lines": {},
                    "files_with_unclassified_lines": [],
                    "excluded_lines": {},
                    "files_with_excluded_lines": [],
                },
            },
            r3_claim={
                "rigor": "R3",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
                "canary": {
                    "mechanism": "uncovered-line",
                    "description": _UNCOVERED_LINE_DESCRIPTION,
                    "control_outcome": "PASS",
                    "transformed_outcome": "FAIL",
                    "expected_reason_code": "UNCOVERED_LINES",
                    "observed_reason_code": "UNCOVERED_LINES",
                },
            },
        ),
    )
    assert git_repo.head() == head_before
    assert git_repo.git("status", "--porcelain") == ""


def test_a_real_r3_lane_whose_bad_case_fails_for_the_wrong_cause_survives(
    standalone: Standalone, git_repo: GitRepo, validator: Draft202012Validator
):
    """Work item 6's "wrong cause" shape, through the real, un-mocked
    adapter and registry: ``import-break`` expects ``COMMAND_FAILED``, and
    the transformed half genuinely FAILS -- but ``UNCOVERED_LINES``,
    because the lane's own tests never import the broken module and R1
    catches the injected line instead. Accepting any transformed non-PASS
    (O3's own negative) makes this green.

    The control still has to PASS for the comparison to mean anything,
    which is why the canary target and the measured change are DIFFERENT
    files: ``base..HEAD`` adds a fully-covered ``pkg/mod.py``, so the
    control's own R1 half passes, while the transform lands in
    ``pkg/lonely.py`` -- already present at the base commit, under the
    declared source root, and imported by nothing.
    """
    base_rev = _seed_measurable_history(git_repo, lonely=True)
    lane_file = _write_lane_file(
        git_repo.path,
        _r1_r3_lane_toml(
            mechanism="import-break", target="pkg/lonely.py", base=base_rev
        ),
    )
    git_repo.commit_all("add assay.toml")
    head_before = git_repo.head()

    proc = _run_assay(standalone, lane_file)

    assert proc.returncode == 1, proc.stderr  # Outcome.FAIL.exit_code
    real = json.loads(proc.stdout)
    assert why_invalid(validator, real) == []
    _assert_complete(
        real,
        _r1_r3_expected(
            git_repo=git_repo,
            base_rev=base_rev,
            mechanism="import-break",
            target="pkg/lonely.py",
            outcome="FAIL",
            reason_code="CANARY_SURVIVED",
            exit_code=1,
            r1_claim={
                "rigor": "R1",
                "source": "computed",
                "status": "PASS",
                "verified_by_assay": True,
                "coverage": {
                    # `base..HEAD` adds `pkg/mod.py` alone and its new test
                    # covers both lines. `pkg/lonely.py` -- the canary
                    # target -- is real source under the same root, but it
                    # is not part of this diff, so R1 never considers it.
                    "considered": 1,
                    "changed_executable": 2,
                    "covered": 2,
                    "pct": 100.0,
                    "missing_lines": {},
                    "files_missing_coverage": [],
                    "unclassified_lines": {},
                    "files_with_unclassified_lines": [],
                    "excluded_lines": {},
                    "files_with_excluded_lines": [],
                },
            },
            r3_claim={
                "rigor": "R3",
                "source": "computed",
                "status": "FAIL",
                "verified_by_assay": True,
                "reason_code": "CANARY_SURVIVED",
                "canary": {
                    "mechanism": "import-break",
                    "description": _IMPORT_BREAK_DESCRIPTION,
                    "control_outcome": "PASS",
                    "transformed_outcome": "FAIL",
                    "expected_reason_code": "COMMAND_FAILED",
                    "observed_reason_code": "UNCOVERED_LINES",
                },
            },
        ),
    )
    assert git_repo.head() == head_before
    assert git_repo.git("status", "--porcelain") == ""


def test_the_installed_wheel_ships_and_exposes_the_go_adapter(standalone: Standalone):
    """A-126's Go half: ADAPTER-LEVEL only. ``GoAdapter`` is imported from
    INSIDE the scratch venv (proving the wheel actually ships
    ``assay.adapters.go``, not merely ``cli``/``config``/``errors``/
    ``verdict`` the way an unpackaged-data wheel would) and its real,
    narrow-lexer methods are called against real, committed Go source text.
    Never a genuine ``go test`` run — no Go toolchain exists anywhere in
    this devcontainer (A-042/A-087)."""
    go_source = GO_FIXTURE.read_text(encoding="utf-8")
    code = (
        "from assay.adapters.go import GoAdapter\n"
        "adapter = GoAdapter(module_path='example.com/greet')\n"
        f"text = {go_source!r}\n"
        "print(adapter.has_executable_code('greet/greet.go', text))\n"
        "print(adapter.normalize_coverage_key('example.com/greet/greet/greet.go'))\n"
        "print(adapter.is_test_path('greet/greet_test.go'))\n"
        "print(adapter.is_test_path('greet/greet.go'))\n"
        "print(adapter.name, adapter.requires_span_attribution)\n"
    )

    proc = standalone.run("python", "-c", code)

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == "True", "Greet() has a real top-level function body"
    assert lines[1] == "greet/greet.go", "the declared module_path was not stripped"
    assert lines[2] == "True", "a _test.go suffix must be recognised as a test path"
    assert lines[3] == "False", "greet.go itself must not be misclassified as a test"
    assert lines[4] == "go False"


# --- O1's negative, corrected (A-124): package data / console entry point ----


def _drop_toml_table(text: str, header: str) -> str:
    """Remove the whole TOML table beginning at the line *header*
    (verbatim, e.g. ``"[project.scripts]"``) up to (but not including) the
    next top-level ``[...]`` header or end of file.

    The table-level analogue of ``conftest.py``'s own ``drop_key``: raises
    if *header* is not present exactly once, so a rename upstream fails
    THIS loudly instead of quietly building the unmutated wheel and passing
    for the wrong reason (A-067).
    """
    lines = text.splitlines(keepends=True)
    start: int | None = None
    end = len(lines)
    for i, line in enumerate(lines):
        if start is None:
            if line.rstrip("\n") == header:
                start = i
            continue
        if line.startswith("["):
            end = i
            break
    if start is None:
        raise AssertionError(f"pyproject.toml has no table header {header!r} to drop")
    return "".join(lines[:start] + lines[end:])


def _build_and_install_mutant(
    tmp_path: Path, pyproject_text: str
) -> tuple[
    subprocess.CompletedProcess[str], subprocess.CompletedProcess[str] | None, Path
]:
    """The SAME two-environment offline recipe as ``conftest.standalone``
    (A-070/A-123) — reused via ``_build_backend_home``/``_clean_env``, not
    reimplemented — but built fresh from a MUTATED ``pyproject.toml`` copy.

    Never reuses the shared session-scoped ``standalone`` fixture: that venv
    is consumed by every other test module in this suite and must stay the
    real, unmutated build (A-123's whole point). Returns
    ``(build_result, install_result_or_None, venv)`` — *install_result* is
    ``None`` when the build itself failed, so a build-time failure is never
    mistaken for an install-time one.
    """
    source = tmp_path / "assay-src"
    source.mkdir()
    (source / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")
    shutil.copytree(
        PROJECT_ROOT / "src",
        source / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )

    venv = tmp_path / "venv"
    base = Path(sys.base_prefix) / "bin" / "python3"
    creator = str(base if base.exists() else sys.executable)
    subprocess.run([creator, "-m", "venv", str(venv)], check=True, capture_output=True)
    python = venv / "bin" / "python"
    assert python.exists(), "the fresh scratch venv has no interpreter"

    wheels = tmp_path / "wheels"
    build_env = _clean_env()
    build_env["PYTHONPATH"] = str(_build_backend_home())
    built = subprocess.run(
        [
            str(python), "-m", "pip", "wheel",
            "--no-build-isolation", "--no-deps",
            "--wheel-dir", str(wheels), str(source),
        ],
        capture_output=True, text=True, env=build_env,
    )
    if built.returncode != 0:
        return built, None, venv

    candidates = sorted(wheels.glob("assay-*.whl"))
    assert len(candidates) == 1, f"expected one assay wheel, got {candidates}"
    installed = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", str(candidates[0])],
        capture_output=True, text=True, env=_clean_env(),
    )
    return built, installed, venv


def test_removing_package_data_ships_a_wheel_whose_schema_cannot_load(tmp_path: Path):
    mutated = _drop_toml_table(ORIGINAL_PYPROJECT, "[tool.setuptools.package-data]")
    parsed = tomllib.loads(mutated)
    assert "package-data" not in parsed.get("tool", {}).get("setuptools", {}), (
        "the mutation left package-data declared -- this negative would prove "
        "nothing"
    )

    built, installed, venv = _build_and_install_mutant(tmp_path, mutated)
    assert built.returncode == 0, (
        f"a missing package-data stanza must not break the BUILD itself -- "
        f"only later use of the missing file:\n{built.stdout}\n{built.stderr}"
    )
    assert installed is not None and installed.returncode == 0, (
        f"a missing package-data stanza must not break the offline INSTALL "
        f"itself:\n{installed.stdout if installed else ''}\n"
        f"{installed.stderr if installed else ''}"
    )

    proc = subprocess.run(
        [
            str(venv / "bin" / "python"), "-c",
            "from assay.verdict import load_schema; load_schema()",
        ],
        capture_output=True, text=True, env=_clean_env(),
    )
    assert proc.returncode != 0, (
        "the installed wheel loaded its schema even without a package-data "
        "declaration -- this negative no longer proves anything"
    )
    assert "FileNotFoundError" in proc.stderr, proc.stderr


def test_removing_the_console_script_ships_a_wheel_with_no_invocable_binary(
    tmp_path: Path,
):
    mutated = _drop_toml_table(ORIGINAL_PYPROJECT, "[project.scripts]")
    parsed = tomllib.loads(mutated)
    assert "scripts" not in parsed.get("project", {}), (
        "the mutation left the console entry point declared -- this negative "
        "would prove nothing"
    )

    built, installed, venv = _build_and_install_mutant(tmp_path, mutated)
    assert built.returncode == 0, (
        f"a missing [project.scripts] must not break the BUILD itself:\n"
        f"{built.stdout}\n{built.stderr}"
    )
    assert installed is not None and installed.returncode == 0, (
        f"a missing [project.scripts] must not break the offline INSTALL "
        f"itself:\n{installed.stdout if installed else ''}\n"
        f"{installed.stderr if installed else ''}"
    )

    assay_bin = venv / "bin" / "assay"
    assert not assay_bin.exists(), (
        "assay is still invocable without a declared console entry point -- "
        "this negative no longer proves anything"
    )
    with pytest.raises(FileNotFoundError):
        subprocess.run(
            [str(assay_bin), "lanes"], capture_output=True, text=True, env=_clean_env()
        )


def test_declaring_a_runtime_dependency_breaks_the_offline_scratch_install(
    tmp_path: Path,
):
    """Work item 4's other genuinely-testable break (A-005/O2), proven at
    the WHEEL level rather than only the AST level
    (``test_dependency_purity.py``'s own tainted-copy self-check):
    ``--no-deps`` lets the BUILD succeed regardless of what is declared, but
    ``--no-index`` against a clean venv has nothing to satisfy a real
    ``Requires-Dist`` with, so the offline INSTALL — the property A-005
    actually needs to hold — fails."""
    original_line = "dependencies = []"
    assert ORIGINAL_PYPROJECT.count(original_line) == 1, (
        "pyproject.toml no longer matches the expected dependencies line"
    )
    mutated = ORIGINAL_PYPROJECT.replace(original_line, 'dependencies = ["requests>=2"]', 1)
    parsed = tomllib.loads(mutated)
    assert parsed["project"]["dependencies"] == ["requests>=2"], (
        "the mutation did not actually declare a runtime dependency"
    )

    built, installed, _venv = _build_and_install_mutant(tmp_path, mutated)
    assert built.returncode == 0, (
        f"--no-deps must let the BUILD succeed regardless of what is "
        f"declared:\n{built.stdout}\n{built.stderr}"
    )
    assert installed is not None, "the install step should have run"
    assert installed.returncode != 0, (
        "a declared runtime dependency installed cleanly with --no-index and "
        "no local index available -- this negative no longer proves anything"
    )
