"""W5's v9 acceptance suite: the producer cut (B045 / B046 / B043 / B041(b)).

The six templates are the frozen W4 v8 documents migrated by bumping
`schema_version` to 9 and adding `judgment.r2.producer = "native"` to the two
that carry an r2 -- and `native` is not a choice among two possibilities for
those two: both record assay's own `jobs`/`max_mutants`/`operators`, which v9
FORBIDS under `ingested`, so the migration had exactly one legal value and it
is the producer those documents always described. They are committed, not
transformed at runtime, so this suite tests real bytes.

It carries forward W1's, W2's and W4's differential discipline unchanged:
every negative has an unmodified clean control that must verify clean in the
SAME test, so no negative here can pass merely because the whole document
became foreign.

What is NEW at v9, and what this suite exists to pin:

* `judgment.r2.producer` is REQUIRED, and it FORKS the object -- `native`
  requires assay's own policy and forbids the ingested record; `ingested` is
  the exact mirror and additionally forbids `equivalence_artifact`. This is
  the one rule of the cut with two failure directions, so it is tested in
  both;
* `mutation_operator` admits an OPEN `stryker:` namespace branch beside its
  three CLOSED enums -- and the closed ones stay closed;
* `judgment.r1.coverage_producer`, `cwd_declared` and
  `snapshot_policy.link_paths` are accepted where legal and refused where the
  grammar says they cannot go;
* `cwd_declared` is NOT a member of the lane-resolved group -- the property
  the whole of B043's recording depends on, and the one an implementer is
  most likely to get wrong by making it "consistent" with its neighbours.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
W1_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W1" / "expected"
W2_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W2" / "expected"
W4_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W4" / "expected"
P26_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected"

V9_TEMPLATES = [
    "missing-tool-v9-template.json",
    "sql-r2-v9-template.json",
    "ca1-r3-no-base-v9-template.json",
    "ca4-all-equivalent-v9-template.json",
]

P25_V9_TEMPLATES = ["p25-pass-v9-template.json", "p25-missing-v9-template.json"]

SUBS = {
    "@STARTED@": "2026-08-11T00:00:00+00:00",
    "@ENDED@": "2026-08-11T00:00:01+00:00",
}

#: A complete ingested `producer_tool`, in the shape a real
#: mutation-testing-report-schema report supplies it. Every string here is a
#: value that report would carry; none is a placeholder, because
#: `producer_tool` is `minLength: 1` on all three fields.
AN_INGESTED_TOOL = {
    "name": "StrykerJS",
    "version": "9.2.0",
    "report_schema_version": "2",
}


def load(path: Path) -> dict:
    text = path.read_text()
    for key, value in SUBS.items():
        text = text.replace(key, value)
    return json.loads(text)


def refuses_only_the_defect(verify_document, clean: dict, broken: dict, why: str):
    assert verify_document(clean) == [], (
        f"{why}: control document must verify clean under v9"
    )
    assert verify_document(broken), why


@pytest.fixture(scope="module")
def verify_document():
    from assay.verify import verify_document as fn

    return fn


# --- identity and hard-cut guards -------------------------------------------


def test_schema_identity_is_internally_consistent_under_v9():
    from assay import verdict as V

    schema = load(ROOT / "src" / "assay" / "schemas" / "verdict.schema.json")
    assert schema["$id"] == "urn:assay:schema:verdict:9"
    assert schema["properties"]["schema_version"]["const"] == 9
    assert V.VERDICT_SCHEMA_VERSION == 9


def test_shipped_schema_is_byte_identical_to_the_locked_v9_asset():
    """The guard this project has been bitten by TWICE. It is carried forward
    into every generation deliberately: whatever moves in the shipped schema
    must move in the frozen copy in the same commit, or this fails."""
    shipped = (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v9.json").read_bytes()
    assert shipped == locked


@pytest.mark.parametrize(
    "frozen",
    [
        pytest.param(path, id=f"{path.parent.parent.name}/{path.name}")
        for path in sorted(W1_EXPECTED.glob("*.json"))
        + sorted(W2_EXPECTED.glob("*.json"))
        + sorted(W4_EXPECTED.glob("*.json"))
    ],
)
def test_every_earlier_frozen_template_is_rejected_under_v9(verify_document, frozen):
    """A-170's hard cut, over ALL THREE earlier generations at once: v9
    rejects v6, v7 and v8 alike, with exactly one diagnostic and no downstream
    noise. W4 joins the sweep here for the reason W2 joined it at the v8 cut
    -- the generation that was live is now history, and the differential
    negative that matters is that its own documents are refused."""
    failures = verify_document(load(frozen))
    assert len(failures) == 1
    assert "is not this verifier's version 9" in failures[0], failures


def test_the_v8_refusal_is_worded_exactly_as_the_v7_one_was(verify_document):
    """The differential the dispatch asks for by name: v8 must be refused at
    v9 in the SAME shape v7 is, so the hard cut is one rule and not a special
    case for the version that happened to be previous."""
    v8_failures = verify_document(load(W4_EXPECTED / "sql-r2-v8-template.json"))
    v7_failures = verify_document(load(W2_EXPECTED / "sql-r2-v7-template.json"))
    assert len(v8_failures) == len(v7_failures) == 1
    assert v8_failures[0].replace(" 8 ", " N ") == v7_failures[0].replace(" 7 ", " N ")


# --- migrated v8 controls ----------------------------------------------------


@pytest.mark.parametrize("name", V9_TEMPLATES)
def test_locked_v9_template_is_accepted(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize("name", P25_V9_TEMPLATES)
def test_p25_v9_siblings_validate(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize(
    "name",
    [
        "current-v4-template.json",
        "stale-directory-v4-template.json",
        "independent-errors-v4-template.json",
        "attestation-timeout-v4-template.json",
    ],
)
def test_p26_attestation_shapes_survive_v9(verify_document, name):
    doc = load(P26_EXPECTED / name)
    doc["schema_version"] = 9
    assert verify_document(doc) == []


# --- B046: judgment.r2 says WHO computed it ---------------------------------


def test_an_r2_document_must_declare_its_producer(verify_document):
    """The whole of B046's wire contract in one assertion: `producer` is
    REQUIRED on `judgment.r2`, so no v9 document can be silent about whether
    assay's own engine produced its mutants or a foreign tool's report did.
    The north-star's "never conflate tiers" is what makes this required rather
    than defaulted on the wire."""
    clean = load(HERE / "expected" / "sql-r2-v9-template.json")
    broken = copy.deepcopy(clean)
    del broken["judgment"]["r2"]["producer"]
    refuses_only_the_defect(
        verify_document, clean, broken, "judgment.r2 without producer must be refused"
    )


def test_a_native_r2_document_may_not_carry_the_ingested_record(verify_document):
    """The first direction of the fork. These four fields are facts DERIVED
    FROM an ingested report; on a native document each would claim a
    computation that never happened."""
    clean = load(HERE / "expected" / "sql-r2-v9-template.json")
    assert clean["judgment"]["r2"]["producer"] == "native"
    for field, value in (
        ("producer_tool", AN_INGESTED_TOOL),
        ("survived_uncovered", [{"path": "pkg/mod.py", "lineno": 3}]),
        ("discarded", 0),
        ("lines_without_candidates", []),
    ):
        broken = copy.deepcopy(clean)
        broken["judgment"]["r2"][field] = value
        refuses_only_the_defect(
            verify_document,
            clean,
            broken,
            f"a native judgment.r2 carrying {field} must be refused",
        )


def test_an_ingested_r2_document_may_not_carry_assays_own_policy(verify_document):
    """The second direction, and the one the design fork was actually about
    (A-360). `jobs`, `max_mutants`, `operators` and `equivalence_artifact` are
    assay's OWN policy -- what assay chose. An ingested lane's policy is
    genuinely EMPTY, and filling these from the report would put the foreign
    tool's configuration on the wire under assay's name."""
    clean = load(HERE / "expected" / "sql-r2-v9-template.json")
    ingested = copy.deepcopy(clean)
    r2 = ingested["judgment"]["r2"]
    for field in ("jobs", "max_mutants", "operators", "equivalence_artifact"):
        r2.pop(field, None)
    r2["producer"] = "ingested"
    r2["producer_tool"] = AN_INGESTED_TOOL
    r2["survived_uncovered"] = []
    r2["discarded"] = 0
    r2["lines_without_candidates"] = []
    # The control here is the INGESTED document, so the assertion below is
    # about the forbidden field and not about the surrounding shape. It is
    # deliberately NOT asserted clean: this generation freezes no ingested
    # payload -- B046's own runner path lands after this cut -- so the
    # surrounding claim still carries native `sql:` operators. What IS pinned
    # is that each forbidden field is refused, differentially against a
    # document that differs by that field alone.
    for field, value in (
        ("jobs", 1),
        ("max_mutants", 50),
        ("operators", ["sql:drop-check"]),
        ("equivalence_artifact", ".assay/schema-dump.sql"),
    ):
        broken = copy.deepcopy(ingested)
        broken["judgment"]["r2"][field] = value
        extra = set(verify_document(broken)) - set(verify_document(ingested))
        assert extra, f"an ingested judgment.r2 carrying {field} must be refused"


def test_an_ingested_r2_document_must_carry_the_whole_ingested_record(verify_document):
    """"Required together" made mechanical, the way W4 did it for
    `judge_provenance`: dropping any ONE of the four is refused, so no
    document can carry three of them and read as a complete ingested record."""
    clean = load(HERE / "expected" / "sql-r2-v9-template.json")
    ingested = copy.deepcopy(clean)
    r2 = ingested["judgment"]["r2"]
    for field in ("jobs", "max_mutants", "operators", "equivalence_artifact"):
        r2.pop(field, None)
    r2["producer"] = "ingested"
    r2["producer_tool"] = AN_INGESTED_TOOL
    r2["survived_uncovered"] = []
    r2["discarded"] = 0
    r2["lines_without_candidates"] = []
    baseline = set(verify_document(ingested))
    for field in (
        "producer_tool",
        "survived_uncovered",
        "discarded",
        "lines_without_candidates",
    ):
        broken = copy.deepcopy(ingested)
        del broken["judgment"]["r2"][field]
        assert set(verify_document(broken)) - baseline, (
            f"an ingested judgment.r2 missing {field} must be refused"
        )


def test_an_unknown_producer_name_is_refused(verify_document):
    """The vocabulary is CLOSED at two values. A third would be a third trust
    story, and there are exactly two: assay ran the engine, or assay read a
    report a foreign tool wrote inside the snapshot."""
    clean = load(HERE / "expected" / "sql-r2-v9-template.json")
    for value in ("Native", "external", "", "stryker"):
        broken = copy.deepcopy(clean)
        broken["judgment"]["r2"]["producer"] = value
        refuses_only_the_defect(
            verify_document, clean, broken, f"producer {value!r} must be refused"
        )


# --- B046: the ingested operator namespace ----------------------------------


def test_the_locked_v9_schema_admits_the_ingested_namespace_and_keeps_the_rest_closed():
    """Asserted against the LOCKED artifact, at the level the claim is made
    about -- W4's own lesson for the withdrawn spellings, one cut on."""
    from jsonschema import Draft202012Validator

    schema = json.loads((HERE / "verdict.schema.v9.json").read_text())
    operator = Draft202012Validator(schema["$defs"]["mutation_operator"])

    # OPEN: a mutator name this build has never heard of validates, because a
    # foreign tool's mutator names are DATA assay records, not a catalogue
    # assay closes.
    for ingested in (
        "stryker:ArithmeticOperator",
        "stryker:BooleanLiteral",
        "stryker:AnOperatorInventedAfterThisRelease",
    ):
        assert operator.is_valid(ingested), ingested

    # ...but the namespace is exactly one namespace, and its suffix grammar is
    # real: neither a foreign namespace nor a malformed suffix validates.
    for refused in (
        "stryker:",
        "stryker:has-a-hyphen",
        "stryker:has_underscore",
        "mutmut:SomeOperator",
        "STRYKER:Operator",
        "stryker:a:b",
    ):
        assert not operator.is_valid(refused), refused

    # ...and the three language branches stay CLOSED: the withdrawn v7
    # spellings are still refused, and the surviving python four still pass,
    # so this cannot have passed by an enum being emptied or widened.
    for withdrawn in ("python:uuid-equality-swap", "python:enum-comparison-swap"):
        assert not operator.is_valid(withdrawn), withdrawn
    for kept in (
        "python:compare-swap",
        "python:boolop-swap",
        "python:bool-const-flip",
        "python:falsy-swap",
    ):
        assert operator.is_valid(kept), kept


def test_the_locked_schemas_ingested_pattern_is_the_modules_own_source_string():
    """The drift guard that makes the open branch safe. Two independently
    maintained artifacts -- the shipped schema and `assay.vocabulary` -- must
    hold ONE string, so adding a second namespace cannot be done in one and
    forgotten in the other."""
    from assay.vocabulary import INGESTED_OPERATOR_RE

    schema = json.loads((HERE / "verdict.schema.v9.json").read_text())
    branches = schema["$defs"]["mutation_operator"]["oneOf"]
    patterns = [branch["pattern"] for branch in branches if "pattern" in branch]
    assert patterns == [INGESTED_OPERATOR_RE.pattern]


# --- B043: cwd_declared -----------------------------------------------------


def test_a_verdict_may_name_the_directory_its_command_ran_in(verify_document):
    """The additive half. A declared cwd is accepted; its ABSENCE stays
    accepted too, because a lane that declares none ran at the snapshot's
    project root and must not invent a `"."`."""
    clean = load(HERE / "expected" / "p25-pass-v9-template.json")
    assert "cwd_declared" not in clean
    assert verify_document(clean) == []

    rooted = copy.deepcopy(clean)
    rooted["cwd_declared"] = "applications/webapp-ui-react"
    assert verify_document(rooted) == []


def test_cwd_declared_is_not_a_member_of_the_lane_resolved_group(verify_document):
    """**The property B043's whole recording depends on**, and the one an
    implementer is most likely to break by making this field "consistent" with
    the ten it sits beside. Those ten are all-present-or-all-absent with a
    full dependentRequired cross-matrix; if `cwd_declared` had joined it,
    every verdict from every lane that declares no `cwd` -- nearly all of them
    -- would be schema-invalid. Asserted from BOTH sides: the lane-resolved
    ten with no cwd is clean, and adding a cwd requires nothing else."""
    from assay.verdict import LANE_RESOLVED_FIELDS

    assert "cwd_declared" not in LANE_RESOLVED_FIELDS

    schema = json.loads((HERE / "verdict.schema.v9.json").read_text())
    matrix = schema.get("dependentRequired", {})
    assert "cwd_declared" not in matrix
    for required in matrix.values():
        assert "cwd_declared" not in required


def test_a_cwd_declared_that_is_not_a_repository_tree_path_is_refused(verify_document):
    """One path grammar (A-271). Absent means "the project root"; `"."` is not
    a synonym for it, and neither is an escape or an absolute path."""
    clean = load(HERE / "expected" / "p25-pass-v9-template.json")
    clean["cwd_declared"] = "applications/webapp-ui-react"
    for value in (".", "..", "../elsewhere", "/abs/path", "a/../b", ".git/hooks", ""):
        broken = copy.deepcopy(clean)
        broken["cwd_declared"] = value
        refuses_only_the_defect(
            verify_document, clean, broken, f"cwd_declared {value!r} must be refused"
        )


# --- B041(b): snapshot_policy.link_paths ------------------------------------


def test_a_snapshot_policy_may_record_what_was_linked_into_it(verify_document):
    """A verdict states plainly that its snapshot was not purely committed
    objects. Accepted under `repository` selection with no pairing to it --
    linking content IN is orthogonal to the unsafe-symlink omission policy
    (A-366)."""
    clean = load(HERE / "expected" / "p25-pass-v9-template.json")
    assert clean["snapshot_policy"]["selection"] == "repository"
    assert "link_paths" not in clean["snapshot_policy"]
    assert verify_document(clean) == []

    linked = copy.deepcopy(clean)
    linked["snapshot_policy"]["link_paths"] = ["applications/webapp-ui-react/node_modules"]
    assert verify_document(linked) == []


def test_link_paths_obeys_the_omissions_grammar_it_sits_beside(verify_document):
    """Same grammar as `unsafe_symlink_omissions`: 1..64 entries, tree paths
    with no `.git` component, unique and strictly ascending. Empty is refused
    rather than treated as "none", because a lane that declared none records
    NONE (A-051) -- `[]` would assert a known-empty fact under a key whose
    absence already says it."""
    clean = load(HERE / "expected" / "p25-pass-v9-template.json")
    clean["snapshot_policy"]["link_paths"] = ["a/node_modules", "b/node_modules"]
    for value in (
        [],
        ["../outside"],
        ["/abs"],
        [".git/hooks"],
        ["b/node_modules", "a/node_modules"],
        ["a/node_modules", "a/node_modules"],
    ):
        broken = copy.deepcopy(clean)
        broken["snapshot_policy"]["link_paths"] = value
        refuses_only_the_defect(
            verify_document, clean, broken, f"link_paths {value!r} must be refused"
        )


# --- B045: judgment.r1.coverage_producer ------------------------------------


def test_a_judgment_r1_may_name_the_producer_of_its_coverage_artifact(verify_document):
    """B045's wire half. Optional, because it is optional in the lane file for
    every format but `coverage-istanbul-json`; present-and-empty is refused,
    because absence is already the spelling of "the lane declared none"."""
    clean = load(HERE / "expected" / "p25-pass-v9-template.json")
    assert "coverage_producer" not in clean["judgment"]["r1"]
    assert verify_document(clean) == []

    declared = copy.deepcopy(clean)
    declared["judgment"]["r1"]["coverage_producer"] = "coverage.py"
    assert verify_document(declared) == []

    broken = copy.deepcopy(clean)
    broken["judgment"]["r1"]["coverage_producer"] = ""
    refuses_only_the_defect(
        verify_document, clean, broken, "an empty coverage_producer must be refused"
    )
