"""O5 — the schema is INSIDE the installed wheel, not only in the source tree.

The wheel half of the former ``test_verdict_schema_is_packaged.py`` (W4, B123):
it builds and installs the wheel, so it is a tooling test. The judge half (the
schema against the vocabulary, and the resource path) stays at
``tests/core/test_verdict_schema_is_packaged.py``.

The negative this defends: *the schema is not declared as package data, so it
exists in the source tree and vanishes on install — silently breaking A-029 for
every consumer while every in-tree test stays green.*

That negative names the hollowness precisely, so the defences are aimed at it:

* the schema is resolved **from inside the scratch venv**, in a subprocess with
  a clean environment, and the resolved path is asserted to be under the venv.
  Resolving it through `PROJECT_ROOT` — or leaving `PYTHONPATH=src` in the
  child's environment, which the gate exports — would find the source-tree copy
  and pass against an empty wheel. That is A-067's original vacuity, in this
  package's shape.
* the wheel's own zip namelist is read, so the claim is made against the
  artifact rather than against pip's behaviour.
* the text that comes back out of the venv is compared with the source file, so
  "a file with the right name is present" cannot stand in for "the schema is".

**What this test does NOT claim, corrected 2026-09-02 (B056/DA-D13 → A-412).**
Built from the current tree with the whole `[tool.setuptools.package-data]`
stanza deleted, the wheel still carries the schema: `setuptools_scm` installs a
git file finder and setuptools' `include_package_data` defaults to true under
pyproject metadata, so every git-TRACKED file under the package directory ships
regardless of the stanza (A-396's measurement).

So this file makes no claim about WHICH mechanism ships the schema. It
asserts the OUTCOME — the schema is in the wheel, and resolves from inside a
clean venv — which stays true whichever mechanism delivers it and stays red
if none does. The `package-data` declaration is KEPT (not dropped, DA-D13's
third option) because A-029 is a consumer-facing guarantee that should not
rest on git tracking, and because it is what ships the schema in the
git-metadata-absent build `[tool.setuptools_scm]`'s own `fallback_version`
anticipates.
"""

from __future__ import annotations

import json
import tomllib
import zipfile

from jsonschema import Draft202012Validator

from assay.verdict import VERDICT_SCHEMA_VERSION
from gate.tests.support import PROJECT_ROOT, SCHEMA_PATH, Standalone, verdict_fixture, why_invalid

WHEEL_MEMBER = "assay/schemas/verdict.schema.json"


# --- the declaration ----------------------------------------------------------


def test_pyproject_declares_the_schema_as_package_data():
    """The declaration is KEPT, and this records why — not the refuted claim
    that deleting it would drop the schema from the wheel (B056/A-412).

    It is the belt to the git file finder's braces: A-029 is a
    consumer-facing guarantee, and resting it on "every file under the
    package directory happens to be git-tracked" would make an untracked or
    generated schema vanish silently. It is also what ships the schema in the
    git-metadata-absent build `[tool.setuptools_scm]`'s own
    `fallback_version` anticipates, where the finder cannot run at all.

    The OUTCOME — the schema is really in the wheel — is asserted separately,
    against the artifact, by `test_the_schema_is_inside_the_built_wheel`.
    That is the check that goes red if the schema stops shipping, by any
    mechanism or the loss of all of them.
    """
    pyproject = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "schemas/*.json" in package_data["assay"], (
        "the declaration was removed. It is not the only thing that ships the "
        "schema today (setuptools_scm's git file finder does too), so the "
        "wheel may still carry it — but a build with no git metadata, or a "
        "schema that is not git-tracked, then silently drops it and breaks "
        "A-029 for every consumer"
    )


# --- the artifact -------------------------------------------------------------


def test_the_schema_is_inside_the_built_wheel(standalone: Standalone):
    """Read off the artifact, not off pip's mood."""
    with zipfile.ZipFile(standalone.wheel) as archive:
        names = archive.namelist()
        assert WHEEL_MEMBER in names, (
            f"the wheel does not ship the schema; it contains "
            f"{[n for n in names if not n.startswith('assay-')]}"
        )
        shipped = archive.read(WHEEL_MEMBER).decode("utf-8")

    assert shipped == SCHEMA_PATH.read_text(encoding="utf-8"), (
        "a file with the right name is present, but it is not the schema"
    )


# --- and it resolves from the installed package --------------------------------


def test_the_installed_package_resolves_the_schema_from_inside_the_venv(
    standalone: Standalone,
):
    proc = standalone.run(
        "python",
        "-c",
        "from importlib.resources import files;"
        "p = files('assay').joinpath('schemas/verdict.schema.json');"
        "print(p); print(p.read_text(), end='')",
    )

    assert proc.returncode == 0, proc.stderr
    location, _, text = proc.stdout.partition("\n")
    assert str(standalone.venv) in location, (
        "the schema was resolved from OUTSIDE the venv, so this proves nothing "
        f"about the installed package: {location}"
    )
    assert text == SCHEMA_PATH.read_text(encoding="utf-8")


def test_the_installed_schema_still_rejects_a_malformed_verdict(
    standalone: Standalone,
):
    """The end-to-end form of A-029: a consumer holding only the installed file
    can gate on it. Validation happens HERE, because the scratch venv contains
    only assay — no jsonschema — which is the whole point of A-005."""
    proc = standalone.run(
        "python",
        "-c",
        "from importlib.resources import files;"
        "print(files('assay').joinpath('schemas/verdict.schema.json').read_text(), end='')",
    )
    assert proc.returncode == 0, proc.stderr

    installed = Draft202012Validator(json.loads(proc.stdout))

    good = verdict_fixture("NO_MEASUREMENT")
    assert why_invalid(installed, good) == []

    good["claims"][1]["coverage"] = {
        "covered": 0,
        "changed_executable": 0,
        "pct": 100.0,
        "considered": 0,
    }
    assert not installed.is_valid(good), (
        "the installed schema accepted a NO_MEASUREMENT verdict carrying "
        "pct: 100.0 — the shipped file is not the one this suite tests"
    )


def test_the_installed_package_exposes_the_verdict_model(standalone: Standalone):
    proc = standalone.run(
        "python",
        "-c",
        "import assay;"
        "print(assay.VERDICT_SCHEMA_VERSION, assay.Verdict.__name__,"
        " assay.Claim.__name__, assay.Coverage.__name__)",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == [
        str(VERDICT_SCHEMA_VERSION),
        "Verdict",
        "Claim",
        "Coverage",
    ]


def test_load_schema_works_from_the_installed_package(standalone: Standalone):
    proc = standalone.run(
        "python",
        "-c",
        "from assay.verdict import load_schema;"
        "s = load_schema(); print(s['$id'], len(s['$defs']))",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split()[0] == "urn:assay:schema:verdict:14"
