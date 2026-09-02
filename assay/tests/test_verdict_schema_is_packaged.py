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

**What this test does NOT claim, corrected 2026-09-02 (B056/DA-D13 → A-412).**
Through 4.1.0 this docstring stated a measurement — *"with
`[tool.setuptools.package-data]` the wheel carries
`assay/schemas/verdict.schema.json`; without it the wheel carries only
`assay/__init__.py`, `assay/cli.py`, `assay/config.py`, `assay/errors.py` and
`assay/verdict.py`. Both sides are real."* — that a re-run refutes. Built from
the current tree with that entire stanza deleted, the wheel still carries the
schema, and 47 members in total rather than five: `setuptools_scm` installs a
git file finder and setuptools' `include_package_data` defaults to true under
pyproject metadata, so every git-TRACKED file under the package directory
ships regardless of the stanza (A-396's measurement).

So this file makes no claim about WHICH mechanism ships the schema. It
asserts the OUTCOME — the schema is in the wheel, and resolves from inside a
clean venv — which stays true whichever mechanism delivers it and stays red
if none does. That is the shape its sibling `test_go_helper_is_packaged.py`
already had, and the two are now consistent by construction rather than by
coincidence. The `package-data` declaration is KEPT (not dropped, DA-D13's
third option) because A-029 is a consumer-facing guarantee that should not
rest on git tracking, and because it is what ships the schema in the
git-metadata-absent build `[tool.setuptools_scm]`'s own `fallback_version`
anticipates.
"""

from __future__ import annotations

import json
import tomllib
import zipfile

from conftest import PROJECT_ROOT, SCHEMA_PATH, Standalone, verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.verdict import SCHEMA_RESOURCE, VERDICT_SCHEMA_VERSION
from assay.vocabulary import (
    INGESTED_OPERATOR_RE,
    MUTATION_OPERATORS,
    MUTATION_OPERATORS_BY_LANGUAGE,
    is_ingested_operator,
)

WHEEL_MEMBER = "assay/schemas/verdict.schema.json"


def test_the_shipped_schema_enumerates_exactly_the_vocabulary_module_declares():
    """(P33/V5-2) The drift guard between two independently maintained
    artifacts, in the same shape `test_verdict_reason_codes.py` already uses
    for the reason vocabulary.

    v5 makes this necessary rather than merely tidy: the operator catalogue
    is now THREE per-language enums in the schema against one per-language
    map in :mod:`assay.vocabulary`, and nothing else in the suite compares
    them. A language whose enum was added to one and not the other would
    make the config loader and the shipped schema disagree about what a
    lane may declare -- exactly the model/schema/verifier mismatch P21's own
    work item 2 deleted for the flat four-value list.

    ORDER is asserted too, not just membership: `judgment.r2.operators`
    records a lane's own order-preserving selection, and the module
    docstring calls its tuple order normative for these branches.

    **B046 (schema v9) adds a FOURTH branch that is deliberately not an
    enum** -- the `^stryker:[A-Za-z0-9]+$` ingested namespace. It is
    separated out here rather than folded in, because folding it in is
    exactly the drift this test exists to catch: an open pattern compared
    against a closed vocabulary would either force `MUTATION_OPERATORS` to
    grow names no adapter implements, or quietly stop asserting anything.
    The two halves are checked by their own rules -- the enums against
    `MUTATION_OPERATORS_BY_LANGUAGE`, the pattern against
    `INGESTED_OPERATOR_RE` -- and the count is pinned so a fifth branch
    appearing in either shape fails here first.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    branches = schema["$defs"]["mutation_operator"]["oneOf"]
    enum_branches = [branch for branch in branches if "enum" in branch]
    pattern_branches = [branch for branch in branches if "pattern" in branch]
    assert len(enum_branches) + len(pattern_branches) == len(branches)
    assert len(pattern_branches) == 1

    shipped = [branch["enum"] for branch in enum_branches]
    declared = [list(operators) for operators in MUTATION_OPERATORS_BY_LANGUAGE.values()]
    assert shipped == declared

    # B046: the ingested branch's pattern is the SAME source string
    # `assay.vocabulary` compiles, not a hand-transcribed twin -- the schema
    # and the predicate cannot disagree about which names are ingested.
    assert pattern_branches[0]["pattern"] == INGESTED_OPERATOR_RE.pattern
    # ...and it really is open where the others are closed: a name matching
    # it is legal without appearing in any vocabulary the module ships.
    assert is_ingested_operator("stryker:ArithmeticOperator")
    assert "stryker:ArithmeticOperator" not in MUTATION_OPERATORS

    # Every ENUM branch is single-language, so a `oneOf` really does partition
    # the catalogue rather than merely covering it -- two branches sharing a
    # name would make a document ambiguous under `oneOf` and reject a valid
    # operator. The pattern branch cannot collide with them: no
    # language-qualified name assay ships starts with the ingested namespace.
    for branch, (language, operators) in zip(
        enum_branches, MUTATION_OPERATORS_BY_LANGUAGE.items()
    ):
        assert {name.split(":", 1)[0] for name in branch["enum"]} == {language}
        assert len(set(operators)) == len(operators)
    assert not [name for name in MUTATION_OPERATORS if is_ingested_operator(name)]

    # ...and the flat tuple the model and config close against is exactly the
    # union of what the schema ships in its CLOSED branches.
    assert set(MUTATION_OPERATORS) == {
        name for branch in enum_branches for name in branch["enum"]
    }


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
    assert proc.stdout.split()[0] == "urn:assay:schema:verdict:10"
