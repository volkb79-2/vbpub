"""W6's v10 acceptance suite: the INTEGRITY cut (B050 / B053 / B004 / B007 /
F015).

The seven inherited templates are W5's own v9 documents migrated in place, and
the migration is stated here so a reviewer can re-derive it rather than trust
it:

1. `schema_version` 9 -> 10, in all seven;
2. `judgment.r3` `target` -> a one-element `targets` array, and the flat
   `canary` body -> `{mechanism, attempts: [...]}` with `disposition:
   "attempted"`, in the one that carries an R3 claim (B007/A-432). A lane that
   declared ONE target records NO `aggregation`: with one probe `any` and
   `all` denote the same function, so recording one would record a policy the
   lane never stated;
3. `judgment.r2.fail_under = 100.0` added to the one INGESTED document
   (B050/A-427) -- and, exactly as `producer = "native"` was at the v9 cut,
   `100.0` is not a choice among possibilities: it is the floor the shipped
   loader forced for every ingested lane that could have produced that
   document, so the migration had one legal value.

Nothing else changed. They are committed, not transformed at runtime, so this
suite tests real bytes.

**Two templates are NEW and neither is migrated from anything**, because the
shapes they pin have no producer yet -- which is exactly why the drift guard
must carry them (the `MISSING_EXTERNAL_TOOL` reservation pattern, one layer
out):

* `multi-target-r3-v10-template.json` -- an `aggregation = "any"` R3 claim
  whose first probe was caught and whose second is recorded
  `not_attempted`/`short_circuited`. B007's implementation lands after the
  cut; without this document the whole plural branch of `$defs/canary` and
  `$defs/judgment_r3` would ship with zero frozen coverage, which is how a
  fork rots (W5 learned that about `producer` in its own fix round).
* `r4-red-first-v10-template.json` -- an `R4` claim carrying both recorded
  outcomes beside `judgment.r4`. F015's producer is PHASE 3; DA-R16 requires
  the shape pinned from the cut onward so phase 3 lands into a contract that
  already exists rather than one it defines as it goes.

Both are HAND-AUTHORED, and this suite says so rather than implying a run
behind them: they are schema-and-verifier-valid documents of a shape no
producer emits yet, and the moment a producer does, its own real output
replaces them here.

It carries forward W1's, W2's, W4's and W5's differential discipline
unchanged: every negative has an unmodified clean control that must verify
clean in the SAME test, so no negative here can pass merely because the whole
document became foreign.

What is NEW at v10, and what this suite exists to pin:

* `judgment.r2.fail_under` (B050/A-427) -- REQUIRED under `producer =
  "ingested"`, FORBIDDEN under `"native"`, so the R2 re-derivation reads the
  floor FROM the document instead of assuming a constant the loader forced;
* `claim.detail` / `claim.detail_dropped_bytes` (B053/A-428) -- non-PASS
  claims only, an all-or-nothing pair, and DECLARED-not-verified;
* `PROVENANCE_UNVERIFIED` in the `NO_MEASUREMENT` set and
  `RED_FIRST_UNPROVEN` in the `FAIL` set (B004/A-430; F015/A-433 as amended
  by A-434), both reserved here and rendered by their own producers later;
* the `evidence` narrowing B004 pays for: `source = "adjudicated"` now
  implies `verified_by_assay: false` in BOTH layers, where v9 left it an
  unconstrained boolean;
* `judgment.r3.targets`/`aggregation` and `canary.attempts[]` (B007/A-432),
  with the disposition fork and the closed `not_attempted_reason` vocabulary;
* `"R4"` in the rigor ladder, `judgment.r4` and the `red_first` claim payload
  (F015/A-433).

It also carries forward, unchanged, W5's own pins that v10 does not touch:
the `judgment.r2.producer` fork, the open `stryker:` operator namespace beside
three closed enums, `coverage_producer`/`cwd_declared`/`link_paths`, and the
fact that `cwd_declared` is NOT a member of the lane-resolved group.
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
W5_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W5" / "expected"
P26_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected"

V10_TEMPLATES = [
    "missing-tool-v10-template.json",
    "sql-r2-v10-template.json",
    "ca1-r3-no-base-v10-template.json",
    "ca4-all-equivalent-v10-template.json",
    # (fix round 1) The one shape every other template in this list is the
    # NATIVE counterpart of. Added because B046's whole new branch -- the five
    # conditionally-emitted `judgment.r2` fields, `producer_tool`, the
    # `stryker:` operator namespace -- had NO frozen document anywhere in the
    # corpus, so the drift guard covered the producer fork's native half only.
    # A guard that only guards one branch of a fork is how a fork rots.
    "ingested-r2-v10-template.json",
    # NEW at v10, and neither is migrated from anything -- see the module
    # docstring for why a shape with no producer yet is exactly what a drift
    # guard has to carry.
    "multi-target-r3-v10-template.json",
    "r4-red-first-v10-template.json",
]

#: The two documents this generation ADDS, by name, for the tests that are
#: about the plural-canary and red-first shapes specifically.
MULTI_TARGET_V10_TEMPLATE = "multi-target-r3-v10-template.json"
RED_FIRST_V10_TEMPLATE = "r4-red-first-v10-template.json"

#: The frozen ingested document, by name, for the tests that are about it
#: specifically rather than about "every v10 template verifies".
INGESTED_V10_TEMPLATE = "ingested-r2-v10-template.json"

P25_V10_TEMPLATES = ["p25-pass-v10-template.json", "p25-missing-v10-template.json"]

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
        f"{why}: control document must verify clean under v10"
    )
    assert verify_document(broken), why


@pytest.fixture(scope="module")
def verify_document():
    from assay.verify import verify_document as fn

    return fn


# --- identity and hard-cut guards -------------------------------------------


def test_schema_identity_is_internally_consistent_under_v10():
    from assay import verdict as V

    schema = load(ROOT / "src" / "assay" / "schemas" / "verdict.schema.json")
    assert schema["$id"] == "urn:assay:schema:verdict:10"
    assert schema["properties"]["schema_version"]["const"] == 10
    assert V.VERDICT_SCHEMA_VERSION == 10


def test_shipped_schema_is_byte_identical_to_the_locked_v10_asset():
    """The guard this project has been bitten by TWICE. It is carried forward
    into every generation deliberately: whatever moves in the shipped schema
    must move in the frozen copy in the same commit, or this fails."""
    shipped = (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v10.json").read_bytes()
    assert shipped == locked


@pytest.mark.parametrize(
    "frozen",
    [
        pytest.param(path, id=f"{path.parent.parent.name}/{path.name}")
        for path in sorted(W1_EXPECTED.glob("*.json"))
        + sorted(W2_EXPECTED.glob("*.json"))
        + sorted(W4_EXPECTED.glob("*.json"))
        + sorted(W5_EXPECTED.glob("*.json"))
    ],
)
def test_every_earlier_frozen_template_is_rejected_under_v10(verify_document, frozen):
    """A-170's hard cut, over ALL FOUR earlier generations at once: v10
    rejects v6, v7, v8 and v9 alike, with exactly one diagnostic and no
    downstream noise. W5 joins the sweep here for the reason W4 joined it at
    the v9 cut and W2 at the v8 cut -- the generation that was live is now
    history, and the differential negative that matters is that its own
    documents are refused."""
    failures = verify_document(load(frozen))
    assert len(failures) == 1
    assert "is not this verifier's version 10" in failures[0], failures


def test_the_v9_refusal_is_worded_exactly_as_the_v8_and_v7_ones_are(verify_document):
    """The differential the dispatch asks for by name: v9 must be refused at
    v10 in the SAME shape v8 and v7 are, so the hard cut is one rule and not a
    special case for the version that happened to be previous."""
    v9_failures = verify_document(load(W5_EXPECTED / "sql-r2-v9-template.json"))
    v8_failures = verify_document(load(W4_EXPECTED / "sql-r2-v8-template.json"))
    v7_failures = verify_document(load(W2_EXPECTED / "sql-r2-v7-template.json"))
    assert len(v9_failures) == len(v8_failures) == len(v7_failures) == 1
    assert (
        v9_failures[0].replace(" 9 ", " N ")
        == v8_failures[0].replace(" 8 ", " N ")
        == v7_failures[0].replace(" 7 ", " N ")
    )


# --- migrated v8 controls ----------------------------------------------------


@pytest.mark.parametrize("name", V10_TEMPLATES)
def test_locked_v10_template_is_accepted(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize("name", P25_V10_TEMPLATES)
def test_p25_v10_siblings_validate(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


# --- the frozen INGESTED document (fix round 1) -----------------------------
#
# `ingested-r2-v10-template.json` is a REAL verdict: a real run over the
# committed StrykerJS artifact (`tests/fixtures/mutation/
# mutation-report-json.probe-js-stryker.json`, 109 mutants over 6 files),
# frozen with only `started`/`ended` substituted. Its outcome is
# FAIL/MUTANTS_SURVIVED, which is the honest verdict for that artifact and is
# what makes it worth freezing -- a judged R2 claim with a real payload behind
# it, not a hand-built shell that happens to satisfy the schema.


def test_the_frozen_ingested_template_carries_the_WHOLE_ingested_record(
    verify_document,
):
    """The point of freezing it. `test_locked_v10_template_is_accepted` proves
    it verifies; this proves the document being verified is actually the
    ingested shape, so the guard cannot quietly start guarding a native
    document that happens to sit under the same filename."""
    document = load(HERE / "expected" / INGESTED_V10_TEMPLATE)
    r2 = document["judgment"]["r2"]
    assert r2["producer"] == "ingested"
    assert set(r2["producer_tool"]) == {"name", "version", "report_schema_version"}
    assert r2["producer_tool"]["name"] == "StrykerJS"
    # Required-and-possibly-empty under `ingested` (A-365), and non-empty here.
    assert r2["survived_uncovered"] and r2["lines_without_candidates"]
    assert isinstance(r2["discarded"], int)
    # Forbidden under `ingested` (A-360) -- assay declared no policy for a
    # discovery it did not perform.
    for forbidden in ("operators", "jobs", "max_mutants", "equivalence_artifact"):
        assert forbidden not in r2, forbidden
    # B043 rides along: this lane declared a `cwd`, so the frozen document
    # carries `cwd_declared` too.
    assert document["cwd_declared"] == "app"


def test_the_frozen_ingested_templates_operators_are_all_stryker_namespaced(
    verify_document,
):
    """A-362's namespace, frozen. Every operator in the payload is a
    `stryker:` name -- assay's native catalogue names a catalogue this run
    never used, and a template that mixed them would be recording a document
    `verify.py`'s own two-directional fork is supposed to refuse."""
    document = load(HERE / "expected" / INGESTED_V10_TEMPLATE)
    r2_claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    operators = {
        entry["operator"]
        for bucket in ("killed", "survived", "crashed", "budget_exceeded")
        for entry in r2_claim["mutation"].get(bucket, [])
    }
    assert operators, "the frozen payload records no mutants at all"
    assert all(name.startswith("stryker:") for name in operators), sorted(operators)


@pytest.mark.parametrize(
    "name",
    [
        "current-v4-template.json",
        "stale-directory-v4-template.json",
        "independent-errors-v4-template.json",
        "attestation-timeout-v4-template.json",
    ],
)
def test_p26_attestation_shapes_survive_v10(verify_document, name):
    doc = load(P26_EXPECTED / name)
    doc["schema_version"] = 10
    assert verify_document(doc) == []


# --- B046: judgment.r2 says WHO computed it ---------------------------------


def test_an_r2_document_must_declare_its_producer(verify_document):
    """The whole of B046's wire contract in one assertion: `producer` is
    REQUIRED on `judgment.r2`, so no v9 document can be silent about whether
    assay's own engine produced its mutants or a foreign tool's report did.
    The north-star's "never conflate tiers" is what makes this required rather
    than defaulted on the wire."""
    clean = load(HERE / "expected" / "sql-r2-v10-template.json")
    broken = copy.deepcopy(clean)
    del broken["judgment"]["r2"]["producer"]
    refuses_only_the_defect(
        verify_document, clean, broken, "judgment.r2 without producer must be refused"
    )


def test_a_native_r2_document_may_not_carry_the_ingested_record(verify_document):
    """The first direction of the fork. These four fields are facts DERIVED
    FROM an ingested report; on a native document each would claim a
    computation that never happened."""
    clean = load(HERE / "expected" / "sql-r2-v10-template.json")
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
    clean = load(HERE / "expected" / "sql-r2-v10-template.json")
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
    clean = load(HERE / "expected" / "sql-r2-v10-template.json")
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
    clean = load(HERE / "expected" / "sql-r2-v10-template.json")
    for value in ("Native", "external", "", "stryker"):
        broken = copy.deepcopy(clean)
        broken["judgment"]["r2"]["producer"] = value
        refuses_only_the_defect(
            verify_document, clean, broken, f"producer {value!r} must be refused"
        )


# --- B046: the ingested operator namespace ----------------------------------


def test_the_locked_v10_schema_admits_the_ingested_namespace_and_keeps_the_rest_closed():
    """Asserted against the LOCKED artifact, at the level the claim is made
    about -- W4's own lesson for the withdrawn spellings, one cut on."""
    from jsonschema import Draft202012Validator

    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
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

    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    branches = schema["$defs"]["mutation_operator"]["oneOf"]
    patterns = [branch["pattern"] for branch in branches if "pattern" in branch]
    assert patterns == [INGESTED_OPERATOR_RE.pattern]


# --- B043: cwd_declared -----------------------------------------------------


def test_a_verdict_may_name_the_directory_its_command_ran_in(verify_document):
    """The additive half. A declared cwd is accepted; its ABSENCE stays
    accepted too, because a lane that declares none ran at the snapshot's
    project root and must not invent a `"."`."""
    clean = load(HERE / "expected" / "p25-pass-v10-template.json")
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

    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    matrix = schema.get("dependentRequired", {})
    assert "cwd_declared" not in matrix
    for required in matrix.values():
        assert "cwd_declared" not in required


def test_a_cwd_declared_that_is_not_a_repository_tree_path_is_refused(verify_document):
    """One path grammar (A-271). Absent means "the project root"; `"."` is not
    a synonym for it, and neither is an escape or an absolute path."""
    clean = load(HERE / "expected" / "p25-pass-v10-template.json")
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
    clean = load(HERE / "expected" / "p25-pass-v10-template.json")
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
    clean = load(HERE / "expected" / "p25-pass-v10-template.json")
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
    clean = load(HERE / "expected" / "p25-pass-v10-template.json")
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


# ============================================================================
# What is NEW at v10. Every one of these is a rule that did not exist at v9,
# and every negative below carries its own unmodified clean control.
# ============================================================================


# --- B050/A-427: judgment.r2.fail_under, the floor the document states -------


def test_an_ingested_r2_must_state_the_floor_it_judged_against(verify_document):
    """B050's whole point. Up to v9 the loader FORCED `fail_under = 100.0` on
    every ingested lane so that `verify.py` could assume it; a document that
    does not state the floor leaves the re-derivation partial, so `fail_under`
    is REQUIRED under `producer = "ingested"`."""
    clean = load(HERE / "expected" / INGESTED_V10_TEMPLATE)
    assert clean["judgment"]["r2"]["fail_under"] == 100.0
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    del broken["judgment"]["r2"]["fail_under"]
    refuses_only_the_defect(
        verify_document, clean, broken, "an ingested r2 without a floor must be refused"
    )


def test_a_native_r2_must_not_state_a_floor_it_never_applied(verify_document):
    """The other direction, and the reason the field forks rather than being
    optional on both sides: a NATIVE R2 has no floor at all -- `judge_mutation`
    fails on any survivor whatsoever -- so a native document carrying one
    would record a policy nothing applied, which is B050's own un-auditable
    claim inverted."""
    clean = load(HERE / "expected" / "sql-r2-v10-template.json")
    assert clean["judgment"]["r2"]["producer"] == "native"
    assert "fail_under" not in clean["judgment"]["r2"]

    broken = copy.deepcopy(clean)
    broken["judgment"]["r2"]["fail_under"] = 100.0
    refuses_only_the_defect(
        verify_document, clean, broken, "a native r2 carrying a floor must be refused"
    )


def test_the_floor_is_spelled_exactly_as_judgment_r1s_own():
    """A-427: the same quantity at a different tier, and two spellings of one
    policy number is how they drift. Asserted against the LOCKED schema, so a
    later edit to one and not the other is a red test here."""
    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    r1 = schema["$defs"]["judgment_r1"]["properties"]["fail_under"]
    r2 = schema["$defs"]["judgment_r2"]["properties"]["fail_under"]
    assert {k: v for k, v in r1.items() if k != "description"} == {
        k: v for k, v in r2.items() if k != "description"
    }


# --- B053/A-428: claim.detail, the refusing sentence on the wire -------------


def test_a_non_pass_claim_may_carry_the_refusing_sentence(verify_document):
    clean = load(HERE / "expected" / "missing-tool-v10-template.json")
    assert verify_document(clean) == []
    refusing = next(c for c in clean["claims"] if c["status"] != "PASS")

    detailed = copy.deepcopy(clean)
    target = next(c for c in detailed["claims"] if c["status"] == refusing["status"])
    target["detail"] = "the 'go' adapter needs the external tool 'go'"
    target["detail_dropped_bytes"] = 0
    assert verify_document(detailed) == []


def test_a_pass_claim_carries_no_detail(verify_document):
    """A-428: `detail` is the refusing sentence, and a pass refused nothing."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v10-template.json")
    broken = copy.deepcopy(clean)
    broken["claims"][0]["detail"] = "nothing to explain"
    broken["claims"][0]["detail_dropped_bytes"] = 0
    refuses_only_the_defect(
        verify_document, clean, broken, "a PASS claim carrying a detail must be refused"
    )


def test_detail_and_its_dropped_byte_count_are_an_all_or_nothing_pair(
    verify_document,
):
    """B014's truncation convention, restated: a silently truncated sentence
    is worse than no sentence, so the reader can never mistake a short message
    for a cut one."""
    clean = load(HERE / "expected" / "missing-tool-v10-template.json")
    refusing_index = next(
        i for i, c in enumerate(clean["claims"]) if c["status"] != "PASS"
    )

    lonely_detail = copy.deepcopy(clean)
    lonely_detail["claims"][refusing_index]["detail"] = "a cause"
    refuses_only_the_defect(
        verify_document, clean, lonely_detail, "detail without its byte count"
    )

    lonely_count = copy.deepcopy(clean)
    lonely_count["claims"][refusing_index]["detail_dropped_bytes"] = 7
    refuses_only_the_defect(
        verify_document, clean, lonely_count, "a byte count without its detail"
    )


def test_detail_is_bounded_in_bytes_not_merely_in_characters(verify_document):
    """A-428 splits the two bounds DELIBERATELY: JSON Schema's `maxLength`
    counts CHARACTERS, so a 2048-character string of 3-byte codepoints
    satisfies the document and must still be refused by the verifier, which is
    where the real bound lives."""
    clean = load(HERE / "expected" / "missing-tool-v10-template.json")
    refusing_index = next(
        i for i, c in enumerate(clean["claims"]) if c["status"] != "PASS"
    )

    broken = copy.deepcopy(clean)
    broken["claims"][refusing_index]["detail"] = "中" * 2048
    broken["claims"][refusing_index]["detail_dropped_bytes"] = 0
    assert len(broken["claims"][refusing_index]["detail"]) == 2048
    refuses_only_the_defect(
        verify_document, clean, broken, "a 6144-byte detail must be refused"
    )


def test_the_locked_schema_bounds_detail_in_characters_and_says_so():
    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    detail = schema["$defs"]["claim"]["properties"]["detail"]
    assert detail["maxLength"] == 2048
    assert detail["minLength"] == 1


# --- B004/A-430: the reserved code and the evidence narrowing ---------------


def test_the_locked_schema_reserves_both_new_reason_codes_in_both_places():
    """A-430's "four places, none of them optional", asserted over the frozen
    schema: the flat `reason_code` enum AND the per-outcome block. A code in
    one and not the other is a document the two layers disagree about."""
    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    flat = set(schema["$defs"]["reason_code"]["enum"])
    by_outcome = schema["$defs"]["reason_codes"]

    assert "PROVENANCE_UNVERIFIED" in flat
    assert "PROVENANCE_UNVERIFIED" in by_outcome["NO_MEASUREMENT"]["enum"]
    assert "RED_FIRST_UNPROVEN" in flat
    # A-434/DA-R18: a judged FAIL, not a NO_MEASUREMENT.
    assert "RED_FIRST_UNPROVEN" in by_outcome["FAIL"]["enum"]
    assert "RED_FIRST_UNPROVEN" not in by_outcome["NO_MEASUREMENT"]["enum"]


def test_the_two_layers_agree_about_the_new_codes():
    """A-182: `assay.errors` states the pairing INDEPENDENTLY of the schema,
    so the frozen copy and the enum must agree member for member."""
    from assay.errors import REASON_CODES, Outcome

    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    for outcome in Outcome:
        if outcome is Outcome.PASS:
            continue
        assert {c.value for c in REASON_CODES[outcome]} == set(
            schema["$defs"]["reason_codes"][outcome.value]["enum"]
        ), outcome


def test_adjudicated_evidence_can_no_longer_claim_to_be_computed(verify_document):
    """B004/A-430's narrowing, which v10 pays for because it is the bump that
    carries `PROVENANCE_UNVERIFIED`. Up to v9 the `adjudicated` branch left
    `verified_by_assay` an unconstrained boolean, so a Tier-2 result could
    ship `true` and be legal in BOTH layers, reading as computed."""
    clean = load(P26_EXPECTED / "current-v4-template.json")
    clean["schema_version"] = 10
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["evidence"] = [
        {
            "source": "adjudicated",
            "key": "image-provenance",
            "status": "PASS",
            "verified_by_assay": True,
        }
    ]
    broken["declared_evidence"] = [
        {"source": "adjudicated", "key": "image-provenance"}
    ]
    assert verify_document(broken), (
        "adjudicated evidence claiming verified_by_assay=true must be refused"
    )


def test_the_locked_schema_pins_the_adjudicated_narrowing():
    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    branches = schema["$defs"]["evidence"]["allOf"]
    narrowing = [
        clause
        for clause in branches
        if isinstance(clause, dict)
        and clause.get("else", {}).get("properties", {}).get("verified_by_assay")
        == {"const": False}
    ]
    assert narrowing, "the adjudicated `else` branch must constrain verified_by_assay"


# --- B007/A-432: the ordered target list and its bookkeeping -----------------


def test_the_multi_target_template_is_the_plural_branchs_only_frozen_witness(
    verify_document,
):
    clean = load(HERE / "expected" / MULTI_TARGET_V10_TEMPLATE)
    assert verify_document(clean) == []
    r3 = clean["judgment"]["r3"]
    assert r3["targets"] == ["pkg/greet.py", "pkg/farewell.py"]
    assert r3["aggregation"] == "any"
    attempts = clean["claims"][1]["canary"]["attempts"]
    assert [a["disposition"] for a in attempts] == ["attempted", "not_attempted"]
    assert attempts[1]["not_attempted_reason"] == "short_circuited"


def test_the_attempts_must_be_the_declared_targets_in_the_declared_order(
    verify_document,
):
    """A-432 generalises P21/A-152's single equality to a PAIRWISE, IN-ORDER
    one. A reordered array would let a SURVIVING probe be reported under a
    caught probe's name, which is the whole failure this equality prevents."""
    clean = load(HERE / "expected" / MULTI_TARGET_V10_TEMPLATE)

    reordered = copy.deepcopy(clean)
    reordered["judgment"]["r3"]["targets"] = ["pkg/farewell.py", "pkg/greet.py"]
    refuses_only_the_defect(
        verify_document, clean, reordered, "a reordered targets list must be refused"
    )

    short = copy.deepcopy(clean)
    del short["claims"][1]["canary"]["attempts"][1]
    refuses_only_the_defect(
        verify_document, clean, short, "an attempt list shorter than targets"
    )


def test_a_short_circuit_recorded_under_all_is_refused(verify_document):
    """The bookkeeping check: only `any` short-circuits, so a document whose
    bookkeeping contradicts its own aggregation is refused."""
    clean = load(HERE / "expected" / MULTI_TARGET_V10_TEMPLATE)

    broken = copy.deepcopy(clean)
    broken["judgment"]["r3"]["aggregation"] = "all"
    assert verify_document(broken), (
        "'short_circuited' under aggregation 'all' must be refused"
    )


def test_one_declared_target_records_no_aggregation(verify_document):
    """A-432: with one probe `any` and `all` denote the same function, so
    recording one would record a policy the lane never stated. Its ABSENCE is
    the checkable statement."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v10-template.json")
    assert clean["judgment"]["r3"]["targets"] == ["pkg/greet.py"]
    assert "aggregation" not in clean["judgment"]["r3"]

    broken = copy.deepcopy(clean)
    broken["judgment"]["r3"]["aggregation"] = "all"
    refuses_only_the_defect(
        verify_document, clean, broken, "a single-target lane recording an aggregation"
    )


def test_a_not_attempted_entry_carries_no_run_field(verify_document):
    clean = load(HERE / "expected" / MULTI_TARGET_V10_TEMPLATE)

    broken = copy.deepcopy(clean)
    broken["claims"][1]["canary"]["attempts"][1]["control_outcome"] = "PASS"
    refuses_only_the_defect(
        verify_document, clean, broken, "a not_attempted entry with a control run"
    )


def test_the_locked_schema_bounds_the_target_list_at_the_measured_number():
    """A-432's bound is MEASURED (~2.76 s of materialisation per target
    against the smallest documented lane budget), and the same number binds
    both objects that carry the list."""
    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    assert schema["$defs"]["judgment_r3"]["properties"]["targets"]["maxItems"] == 8
    assert schema["$defs"]["canary"]["properties"]["attempts"]["maxItems"] == 8
    assert schema["$defs"]["judgment_r3"]["properties"]["aggregation"]["enum"] == [
        "any",
        "all",
    ]
    assert schema["$defs"]["canary_attempt"]["properties"][
        "not_attempted_reason"
    ]["enum"] == [
        "short_circuited",
        "budget_exhausted",
        "earlier_target_terminal",
    ]


# --- F015/A-433 (amended by A-434): R4, the red-first rung -------------------


def test_the_red_first_template_pins_the_shape_before_its_producer_exists(
    verify_document,
):
    clean = load(HERE / "expected" / RED_FIRST_V10_TEMPLATE)
    assert verify_document(clean) == []
    assert clean["declared_rigor"] == ["R0", "R4"]
    payload = clean["claims"][1]["red_first"]
    assert payload["before_outcome"] == "FAIL"
    assert payload["after_outcome"] == "PASS"
    assert clean["judgment"]["r4"]["broken_commit_source"] == "declared"


def test_r4_passes_only_when_the_test_failed_before_and_passes_after(
    verify_document,
):
    """A-433's re-derivation, hand-transcribed in `verify.py`: PASS iff
    `before_outcome != PASS` and `after_outcome == PASS`. Anything else is a
    judged FAIL/RED_FIRST_UNPROVEN (A-434/DA-R18)."""
    clean = load(HERE / "expected" / RED_FIRST_V10_TEMPLATE)

    still_passing = copy.deepcopy(clean)
    still_passing["claims"][1]["red_first"]["after_outcome"] = "FAIL"
    refuses_only_the_defect(
        verify_document,
        clean,
        still_passing,
        "a PASS R4 claim whose HEAD run did not pass",
    )


def test_a_test_that_passed_at_the_broken_commit_ends_the_claim(verify_document):
    """A-433: `after_outcome` is absent EXACTLY when the before-run already
    ended the claim -- the declared test PASSED at the broken commit, so a
    HEAD run answers a question already closed."""
    clean = load(HERE / "expected" / RED_FIRST_V10_TEMPLATE)

    broken = copy.deepcopy(clean)
    broken["claims"][1]["status"] = "FAIL"
    broken["claims"][1]["reason_code"] = "RED_FIRST_UNPROVEN"
    broken["claims"][1]["red_first"]["before_outcome"] = "PASS"
    del broken["claims"][1]["red_first"]["after_outcome"]
    broken["outcome"] = "FAIL"
    broken["reason_code"] = "RED_FIRST_UNPROVEN"
    broken["exit_code"] = 1
    assert verify_document(broken) == [], (
        "the judged FAIL half of red-first must itself be a valid document"
    )

    contradiction = copy.deepcopy(broken)
    contradiction["claims"][1]["red_first"]["after_outcome"] = "PASS"
    assert verify_document(contradiction), (
        "a PASS at the broken commit beside a recorded HEAD run must be refused"
    )


def test_red_first_unproven_belongs_to_the_r4_claim(verify_document):
    """The binding `_check_r1_only_reason_codes` applies one tier down: the
    code is read off R4's own two recorded outcomes and nowhere else."""
    clean = load(HERE / "expected" / RED_FIRST_V10_TEMPLATE)

    broken = copy.deepcopy(clean)
    broken["claims"][0]["status"] = "FAIL"
    broken["claims"][0]["reason_code"] = "RED_FIRST_UNPROVEN"
    broken["outcome"] = "FAIL"
    broken["reason_code"] = "RED_FIRST_UNPROVEN"
    broken["exit_code"] = 1
    assert verify_document(broken), (
        "RED_FIRST_UNPROVEN on an R0 claim must be refused"
    )


def test_the_locked_schema_carries_r4_in_the_ladder_and_its_two_objects():
    schema = json.loads((HERE / "verdict.schema.v10.json").read_text())
    assert schema["$defs"]["rigor"]["enum"] == ["R0", "R1", "R2", "R3", "R4"]
    assert schema["$defs"]["judgment_r4"]["required"] == [
        "tests",
        "broken_commit",
        "broken_commit_source",
    ]
    assert schema["$defs"]["red_first"]["required"] == [
        "broken_commit",
        "tests",
        "before_outcome",
    ]
    # B006(a)/A-269 §5.1's own enum has to track the ladder: R4 resolves two
    # materialisations, so it is a higher-rigor level like every other rung.
    higher_rigor = [
        clause
        for clause in schema["allOf"]
        if isinstance(clause, dict)
        and clause.get("then", {}).get("required") == ["snapshot_policy"]
    ]
    assert higher_rigor, "the snapshot_policy conditional must still be here"
    assert higher_rigor[0]["if"]["properties"]["declared_rigor"]["contains"][
        "enum"
    ] == ["R1", "R2", "R3", "R4"]
