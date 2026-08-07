"""O5 — zero runtime dependencies (A-005), proven by an AST walk (A-060).

**Never grep.** P01's original O5 was verified VACUOUS: ``grep -rn`` prefixes
every line with a path containing ``assay``, and the ``-v`` alternation
contained ``assay``, so every line was filtered regardless of content — it
passed clean on a file importing ``requests``, ``flask`` and a function-level
``boto3``. Two further defects survived even fixing the path issue: the ``re``
alternative matched any line containing that substring, and ``^import`` missed
indented imports.

So the check here parses each file, walks the tree, and takes the ROOT name of
every ``Import`` / ``ImportFrom``, which is immune to all four defects: it sees
indented imports because it walks rather than line-matches, it sees
``from x import y`` because ``ImportFrom`` is a node type rather than a spelling,
and it cannot be confused by a path because it never looks at one.

And because *an oracle that cannot fail is worse than no oracle*, the check is
itself run against a deliberately tainted copy of the package and required to
catch all three shapes.
"""

from __future__ import annotations

import ast
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path

from conftest import PROJECT_ROOT, Standalone

PACKAGE_DIR = PROJECT_ROOT / "src" / "assay"

#: Everything the walk is allowed to see: the standard library of the running
#: interpreter, plus assay's own name for absolute self-imports.
ALLOWED_ROOTS = set(sys.stdlib_module_names) | {"assay"}

TAINTED_MODULE = '''\
"""A module that is deliberately not pure, used to prove the check can fail."""

import os.path
import requests
from concurrent.futures import ThreadPoolExecutor
from flask import Flask

from . import errors
from .config import Lane


def handler():
    import boto3  # indented: the shape the original ^import grep missed

    return boto3, requests, Flask, ThreadPoolExecutor, errors, Lane, os.path
'''


def import_roots(source: str) -> set[str]:
    """Every root module name imported by *source*, however it is spelled."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # `from . import x` / `from .config import Lane` — intra-package
                # by construction, so there is no root name to check.
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def scan_package(package_dir: Path) -> tuple[list[Path], dict[str, set[str]]]:
    """Return (files scanned, {third-party root: files that import it})."""
    files = sorted(
        p for p in package_dir.rglob("*.py") if "__pycache__" not in p.parts
    )
    offenders: dict[str, set[str]] = {}
    for path in files:
        for root in import_roots(path.read_text(encoding="utf-8")):
            if root not in ALLOWED_ROOTS:
                offenders.setdefault(root, set()).add(path.name)
    return files, offenders


# --- the check itself can fail ------------------------------------------------


def test_the_check_catches_every_import_shape_in_a_tainted_copy(tmp_path: Path):
    tainted = tmp_path / "src" / "assay"
    shutil.copytree(
        PACKAGE_DIR, tainted, ignore=shutil.ignore_patterns("__pycache__")
    )
    (tainted / "tainted.py").write_text(TAINTED_MODULE, encoding="utf-8")

    files, offenders = scan_package(tainted)

    assert set(offenders) == {"requests", "flask", "boto3"}, (
        "the purity check failed to catch an injected third-party import; "
        f"it reported {sorted(offenders)}"
    )
    assert offenders["boto3"] == {"tainted.py"}, "function-level import missed"
    assert offenders["flask"] == {"tainted.py"}, "from-import missed"
    assert len(files) > 1


def test_the_check_is_not_fooled_by_the_word_assay_in_the_path(tmp_path: Path):
    # A-060's actual defect: every scanned path contains "assay", and the
    # original grep filtered on exactly that.
    tainted = tmp_path / "assay" / "src" / "assay"
    tainted.mkdir(parents=True)
    (tainted / "impure.py").write_text("import requests\n", encoding="utf-8")

    files, offenders = scan_package(tainted)

    assert all("assay" in str(p) for p in files)
    assert set(offenders) == {"requests"}


def test_relative_imports_are_not_reported_as_third_party():
    assert import_roots("from . import errors\nfrom .config import Lane\n") == set()


def test_dotted_stdlib_imports_reduce_to_their_root():
    assert import_roots("import xml.etree.ElementTree as ET\n") == {"xml"}
    assert import_roots("from importlib.metadata import version\n") == {"importlib"}


# --- and it passes on the real package ----------------------------------------


def test_the_package_imports_nothing_outside_the_stdlib():
    files, offenders = scan_package(PACKAGE_DIR)

    assert offenders == {}, f"non-stdlib imports found: {offenders}"
    # Guard the guard: an empty result must mean "scanned and clean", never
    # "the glob found nothing".
    assert len(files) >= 4, f"expected the whole package, scanned {files}"
    assert {p.name for p in files} >= {
        "__init__.py",
        "cli.py",
        "config.py",
        "errors.py",
        "verdict.py",
    }


def test_every_scanned_file_parses_and_declares_at_least_one_import():
    files, _ = scan_package(PACKAGE_DIR)
    with_imports = [p for p in files if import_roots(p.read_text(encoding="utf-8"))]
    assert with_imports, "no file declared an import — is the walk seeing anything?"


# --- the declaration matches the code ----------------------------------------


def load_pyproject() -> dict:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_declares_zero_runtime_dependencies():
    assert load_pyproject()["project"]["dependencies"] == []


def test_pyproject_declares_a_test_extra_with_the_validator():
    extra = load_pyproject()["project"]["optional-dependencies"]["test"]
    names = {spec.split(">")[0].split("=")[0].split("[")[0] for spec in extra}
    # A-056: A-005 constrains RUNTIME dependencies only, so a validator is
    # legitimate here — P01b's schema tests need one.
    assert {"pytest", "jsonschema"} <= names


def test_pyproject_sets_a_setuptools_scm_fallback_version(tmp_path: Path):
    # A-057, verified empirically in the gate image: with `root = ".."` and no
    # `assay-v*` tag in the repo, a build from a copied tree fails with
    # `LookupError: setuptools-scm was unable to detect version` without this.
    scm = load_pyproject()["tool"]["setuptools_scm"]
    assert scm["root"] == ".."
    assert scm["fallback_version"]


def test_gitignore_covers_the_build_residue_an_install_leaves():
    # A-057: an in-place `pip install .` writes build/ and src/assay.egg-info/
    # into the bind-mounted worktree, which would perturb P02's DIRTY_TREE
    # tests and P11's self-hosting run.
    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert "build/" in ignored
    assert "src/*.egg-info/" in ignored


# --- a venv containing only assay ---------------------------------------------


# The `standalone` fixture (wheel build + offline install, A-070's two
# environments) now lives in `conftest.py`, session-scoped: P01b's packaging
# oracle needs the same venv, and a subtle procedure that nine more packages
# would each copy is exactly the four-copies shape this project exists to
# remove. The tests below are unchanged.


def test_the_built_wheel_declares_no_runtime_requirement(standalone: Standalone):
    """The closure, read straight off the artifact rather than off pip's mood.

    Extras are fine — every ``Requires-Dist`` must be guarded by an ``extra``
    marker, so nothing is pulled in by a plain ``pip install assay``.
    """
    with zipfile.ZipFile(standalone.wheel) as archive:
        name = next(
            n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(name).decode("utf-8")

    requires = [
        line for line in metadata.splitlines() if line.startswith("Requires-Dist:")
    ]
    unconditional = [line for line in requires if "extra ==" not in line]
    assert unconditional == [], f"assay declares runtime dependencies: {unconditional}"


def test_assay_imports_in_a_venv_that_contains_only_itself(standalone: Standalone):
    proc = standalone.run(
        "python",
        "-c",
        "import assay, assay.cli, assay.config, assay.errors, assay.verdict;"
        " print(assay.__file__)",
    )

    assert proc.returncode == 0, proc.stderr
    assert str(standalone.venv) in proc.stdout.strip(), (
        "assay was imported from outside the venv, so this proves nothing: "
        f"{proc.stdout.strip()}"
    )


def test_the_venv_holds_no_third_party_distribution(standalone: Standalone):
    proc = standalone.run(
        "python",
        "-c",
        "from importlib.metadata import distributions;"
        "print(' '.join(sorted((d.metadata['Name'] or '').lower()"
        " for d in distributions())))",
    )

    assert proc.returncode == 0, proc.stderr
    installed = set(proc.stdout.split())
    assert "assay" in installed
    # pip/setuptools/wheel are venv bootstrap, not assay's closure.
    assert installed - {"assay", "pip", "setuptools", "wheel"} == set()


def test_the_installed_console_script_runs_against_a_fixture_project(
    standalone: Standalone, tmp_path: Path
):
    lane_file = tmp_path / "assay.toml"
    lane_file.write_text(
        '\n'.join(
            [
                "schema_version = 1",
                "",
                "[lanes.package]",
                'scope = "S1"',
                'rigor = ["R0"]',
                'enforcement = "gate"',
                'argv = ["pytest", "-q"]',
                "env = {}",
                'env_passthrough = ["PATH"]',
                'budget = "5m"',
                "allow_argv_append = false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    proc = standalone.run("assay", "lanes", "--file", str(lane_file))

    assert proc.returncode == 0, proc.stderr
    assert "package" in proc.stdout
