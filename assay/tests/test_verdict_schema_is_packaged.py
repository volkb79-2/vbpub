"""O5 — the schema is INSIDE the installed wheel, not only in the source tree.

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

Measured in the gate image while writing this: with
`[tool.setuptools.package-data]` the wheel carries
`assay/schemas/verdict.schema.json`; without it the wheel carries only
`assay/__init__.py`, `assay/cli.py`, `assay/config.py`, `assay/errors.py` and
`assay/verdict.py`. Both sides are real.
"""

from __future__ import annotations

import json
import tomllib
import zipfile

from conftest import PROJECT_ROOT, SCHEMA_PATH, Standalone, verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.verdict import SCHEMA_RESOURCE, VERDICT_SCHEMA_VERSION

WHEEL_MEMBER = "assay/schemas/verdict.schema.json"


# --- the declaration ----------------------------------------------------------


def test_pyproject_declares_the_schema_as_package_data():
    pyproject = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "schemas/*.json" in package_data["assay"], (
        "without this the schema exists in the source tree and vanishes on "
        "install — A-029 broken for every consumer, with the suite still green"
    )


def test_the_resource_path_the_code_uses_matches_where_the_file_is():
    assert SCHEMA_RESOURCE == "schemas/verdict.schema.json"
    assert (PROJECT_ROOT / "src" / "assay" / SCHEMA_RESOURCE).is_file()


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
    assert proc.stdout.split()[0] == "urn:assay:schema:verdict:1"
