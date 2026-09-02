"""The Go statement-position oracle is INSIDE the installed wheel.

B047 item 1 chose option (a) — ship the oracle's Go SOURCE inside the wheel and
invoke it with `go run` — over option (b), a separately built binary, on the
grounds that the source is then covered by `judge_provenance` and needs no
second artifact to pin. That reasoning is only true if the source is actually
in the wheel.

The negative this defends: *the helper does not reach the installed wheel, so
every in-tree test stays green while the installed judge cannot derive a
statement position at all.* That failure is silent in exactly the environments
that matter and invisible in exactly the environment tests run in, which is why
it gets an artifact-level check rather than a source-tree one.

**What this test does NOT claim, measured rather than assumed (A-396).** It does
not claim that `[tool.setuptools.package-data]` is what ships the helper. It is
not: built with that entire stanza deleted, the wheel still carries both this
helper and `assay/schemas/verdict.schema.json`, because `setuptools_scm`
installs a git file finder and setuptools' `include_package_data` defaults to
true under pyproject metadata, so every git-tracked file under the package
directory ships already. The declaration is kept for the git-metadata-absent
build `[tool.setuptools_scm]`'s own `fallback_version` anticipates, and this
test deliberately asserts the OUTCOME (the file is in the wheel and resolves
from the venv) rather than the mechanism — so it stays true whichever mechanism
delivers it, and stays red if none does.

That distinction matters because the sibling
`test_verdict_schema_is_packaged.py` states the mechanism claim as measured
fact in its own docstring, and that measurement no longer holds for the current
build configuration — filed as B056, not fixed here.

So, matching that file's three defences:

* the wheel's own zip namelist is read, so the claim is made against the
  ARTIFACT rather than against pip's behaviour;
* the helper is resolved **from inside the scratch venv**, in a subprocess with
  a clean environment, and the resolved path is asserted to be under the venv —
  resolving through `PROJECT_ROOT`, or leaving the gate's own `PYTHONPATH=src`
  in the child's environment, would find the source-tree copy and pass against
  an empty wheel (A-067's vacuity, in this package's shape);
* the bytes that come back out of the venv are compared with the source file,
  so "a file with the right name is present" cannot stand in for "the oracle
  is".

This suite needs no Go toolchain and runs no Go: it asserts that the source
SHIPS, not what it computes. What it computes is proven against real toolchain
output in `test_statement_attribution_go_witnesses.py` and
`carve-assets/P27-recarve/PROVENANCE.md` (A-042/A-043: this devcontainer has no
Go, and the gate container has none either).
"""

from __future__ import annotations

import zipfile

from conftest import PROJECT_ROOT, Standalone

HELPER_DIR = "helpers/go/stmtpos"
HELPER_SOURCE_MEMBER = f"assay/{HELPER_DIR}/stmtpos.go"
HELPER_GOMOD_MEMBER = f"assay/{HELPER_DIR}/go.mod"

_SOURCE_DIR = PROJECT_ROOT / "src" / "assay" / "helpers" / "go" / "stmtpos"


# --- the source tree ----------------------------------------------------------


def test_the_helper_exists_in_the_source_tree():
    """The trivial half, kept explicit so a failure below is unambiguous:
    a red artifact test means a PACKAGING defect, not a deleted file."""
    assert (_SOURCE_DIR / "stmtpos.go").is_file()
    assert (_SOURCE_DIR / "go.mod").is_file()


def test_the_helper_module_declares_no_requirements():
    """`GOPROXY=off` under `--network=none` is only satisfiable because the
    oracle is stdlib-only. A `require` line would make the helper undownloadable
    in the gate container and the failure would surface as a confusing network
    error at judgment time, not here."""
    # Parse DIRECTIVES, never substring-match the file: `go.mod`'s comments
    # are prose, and this check's first draft matched the word "requirements"
    # inside its own explanatory comment. A test that reads comments as
    # directives is testing the documentation.
    directives = [
        line.strip()
        for line in (_SOURCE_DIR / "go.mod").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("//")
    ]
    requires = [line for line in directives if line.split(" ")[0] == "require"]
    assert requires == [], (
        f"the helper's go.mod declares {requires}; it must stay stdlib-only "
        f"so it resolves under GOPROXY=off with no module cache"
    )
    assert sum(1 for line in directives if line.startswith("module ")) == 1
    assert any(line.startswith("go ") for line in directives)


def test_the_helper_imports_only_the_standard_library():
    """The same claim one layer down, against the source rather than the
    manifest: a stdlib-only import block is what makes the empty `go.mod`
    honest. Checked by shape (no dotted domain in any import path), because a
    third-party module path always carries one."""
    source = (_SOURCE_DIR / "stmtpos.go").read_text(encoding="utf-8")
    block = source.partition("import (")[2].partition(")")[0]
    imported = [
        line.strip().strip('"')
        for line in block.splitlines()
        if line.strip() and not line.strip().startswith("//")
    ]
    assert imported, "the import block was not found; this test is not reading the source"
    non_stdlib = [path for path in imported if "." in path.split("/")[0]]
    assert non_stdlib == [], (
        f"the oracle imports non-stdlib package(s) {non_stdlib}; it must stay "
        f"stdlib-only (B047 item 1)"
    )


# --- the artifact -------------------------------------------------------------


def test_the_helper_is_inside_the_built_wheel(standalone: Standalone):
    """Read off the artifact, not off pip's mood."""
    with zipfile.ZipFile(standalone.wheel) as archive:
        names = archive.namelist()
        for member in (HELPER_SOURCE_MEMBER, HELPER_GOMOD_MEMBER):
            assert member in names, (
                f"the wheel does not ship {member}; it contains "
                f"{[n for n in names if not n.startswith('assay-')]}"
            )
        shipped_source = archive.read(HELPER_SOURCE_MEMBER).decode("utf-8")
        shipped_gomod = archive.read(HELPER_GOMOD_MEMBER).decode("utf-8")

    assert shipped_source == (_SOURCE_DIR / "stmtpos.go").read_text(
        encoding="utf-8"
    ), "a file with the right name is present, but it is not the oracle"
    assert shipped_gomod == (_SOURCE_DIR / "go.mod").read_text(encoding="utf-8")


# --- and it resolves from the installed package -------------------------------


def test_the_installed_package_resolves_the_helper_from_inside_the_venv(
    standalone: Standalone,
):
    """`go run` is handed a path, so the path must resolve out of the INSTALLED
    package. Asserting the resolved location is under the venv is what stops
    this passing against the source tree."""
    proc = standalone.run(
        "python",
        "-c",
        "from importlib.resources import files;"
        f"p = files('assay').joinpath('{HELPER_DIR}/stmtpos.go');"
        "print(p); print(p.read_text(), end='')",
    )

    assert proc.returncode == 0, proc.stderr
    location, _, text = proc.stdout.partition("\n")
    assert str(standalone.venv) in location, (
        "the helper was resolved from OUTSIDE the venv, so this proves nothing "
        f"about the installed package: {location}"
    )
    assert text == (_SOURCE_DIR / "stmtpos.go").read_text(encoding="utf-8")


def test_the_installed_helper_directory_is_a_usable_go_run_target(
    standalone: Standalone,
):
    """`go run .` needs the module file and the source in ONE directory — a
    real constraint met while building this (`go run` refuses files spread
    across directories: "named files must all be in one directory"). So the
    packaged layout, not just the packaged bytes, is what has to hold."""
    proc = standalone.run(
        "python",
        "-c",
        "from importlib.resources import files;"
        f"d = files('assay').joinpath('{HELPER_DIR}');"
        "print(sorted(p.name for p in d.iterdir()))",
    )

    assert proc.returncode == 0, proc.stderr
    assert "'stmtpos.go'" in proc.stdout and "'go.mod'" in proc.stdout, proc.stdout
