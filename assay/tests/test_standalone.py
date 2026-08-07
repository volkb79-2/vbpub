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

A-125: nothing here is a committed ``test_*.py`` under
``tests/fixtures/standalone/`` — every fixture used is either already
committed and already ``collect_ignore_glob``-excluded
(``tests/fixtures/canary/python/``, ``tests/fixtures/canary/go/greet/``) or
materialised as a literal string/temp file at test time.
"""

from __future__ import annotations

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
