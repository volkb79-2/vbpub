"""W7's v11 acceptance suite: the DISCARDED-MUTANTS cut (B070).

**One change, one bump.** v11 exists for exactly one field:
`judgment.r2.discarded` stops being an integer COUNT and becomes a LIST of
the mutants the ingested report marked `CompileError`/`RuntimeError`, on
`survived_uncovered`'s own footing. Through v10 that field was DECLARED, NOT
VERIFIED by ruling (B051/DA-D4/DA-R26) because a count of mutants that are by
construction outside the document has nothing in the document to be a
difference OF -- the field set to `9999` on a real 109-mutant ingested
document verified clean (A-437), and every upper bound that caught that
equally refused the honest high-discard report the field exists to surface.
This generation is where that stops being true.

The NINE inherited templates are W6's own v10 documents migrated in place,
and the migration is stated here so a reviewer can re-derive it rather than
trust it:

1. `schema_version` 10 -> 11, in all nine;
2. in the one INGESTED document (`ingested-r2-v11-template.json`):
   `judgment.r2.discarded` `0` -> `[]`. Exactly as `fail_under = 100.0` was at
   the v10 cut, the empty array is not a choice among possibilities: that
   document is a real run over `tests/fixtures/mutation/
   mutation-report-json.probe-js-stryker.json`, which contains no
   `CompileError`/`RuntimeError` mutant at all, so `0` had one and only one
   legal v11 spelling. Its `mutation.candidate_count` is unchanged for the
   same reason -- with nothing discarded, `attempted + discarded` IS
   `attempted`.

Nothing else changed. No native document moves at all: `discarded` is
FORBIDDEN under `producer = "native"` and always was. The templates are
committed, not transformed at runtime, so this suite tests real bytes.

Two of the nine are still HAND-AUTHORED and this suite still says so rather
than implying a run behind them -- `multi-target-r3-v11-template.json` (the
plural-canary shape) and `r4-red-first-v11-template.json` (F015's phase-3
shape). Both are inherited from W6 unchanged apart from the version number.

It carries forward W1's, W2's, W4's, W5's and W6's differential discipline
unchanged: every negative has an unmodified clean control that must verify
clean in the SAME test, so no negative here can pass merely because the whole
document became foreign.

What is NEW at v11, and what this suite exists to pin:

* `judgment.r2.discarded` as an ARRAY of `mutant_outcome` records -- required
  possibly-empty under `producer = "ingested"`, forbidden under `"native"`,
  ascending and unique by the A-180 mutant identity, and carrying no
  `kill_signal` (nothing refused a mutant that never ran);
* the THREE re-derivations that array makes possible, none of which is an
  upper-bound clamp (route 3, rejected by DA-R26 because a clamp refuses the
  honest report too): identity DISJOINTNESS from all five mutation buckets,
  the LINE rule against `lines_without_candidates`, and the
  FIFTH-DISPOSITION arithmetic `candidate_count - total == len(discarded)`;
* the arithmetic rule itself. `Mutation._check_arithmetic` FORBADE
  `candidate_count != total` outside the limit sentinel -- a rule written when
  the buckets were the only dispositions a candidate could have. A residual is
  now legal in the payload and must be ATTRIBUTED one level up: to
  `judgment.r2.discarded`'s length under `ingested`, and to nothing at all
  under `native`, where the only unattempted residual is the pre-submission
  limit sentinel's own;
* **the A-437 reproduction, now a NAMED refusal** -- and, beside it, a
  truthful HIGH-DISCARD control that is ACCEPTED. Without that control the new
  bound would be indistinguishable from the clamp DA-R26 rejected, so the
  control is the point rather than a nicety.

It also carries forward, unchanged, W6's own pins that v11 does not touch:
`judgment.r2.fail_under` and both directions of the producer fork it rides,
`claim.detail` with its BYTE bound and its all-or-nothing dropped-byte pair,
the two reserved reason codes in both schema places AND in `assay.errors`
independently, the `adjudicated => verified_by_assay: false` narrowing,
`canary.attempts[]` with its disposition fork and pairwise target equality,
`R4` with `judgment.r4`/`red_first`, and W5's own `producer`-fork,
operator-namespace, `coverage_producer`/`cwd_declared`/`link_paths` pins.
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
W6_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W6" / "expected"
P26_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected"

V11_TEMPLATES = [
    "missing-tool-v11-template.json",
    "sql-r2-v11-template.json",
    "ca1-r3-no-base-v11-template.json",
    "ca4-all-equivalent-v11-template.json",
    # (fix round 1) The one shape every other template in this list is the
    # NATIVE counterpart of. Added because B046's whole new branch -- the five
    # conditionally-emitted `judgment.r2` fields, `producer_tool`, the
    # `stryker:` operator namespace -- had NO frozen document anywhere in the
    # corpus, so the drift guard covered the producer fork's native half only.
    # A guard that only guards one branch of a fork is how a fork rots -- and
    # at v11 this is the ONLY template the cut actually touches.
    "ingested-r2-v11-template.json",
    # Introduced at v10 and inherited here unchanged apart from the version
    # number -- see the module docstring for why a shape with no producer yet
    # is exactly what a drift guard has to carry.
    "multi-target-r3-v11-template.json",
    "r4-red-first-v11-template.json",
    # NEW at v11 (B070), and the only new document this cut adds -- a REAL
    # high-discard ingested verdict. See HIGH_DISCARD_TEMPLATE below.
    "high-discard-r2-v11-template.json",
]

#: (B070) The ONE document this generation ADDS, and the reason it is a real
#: run rather than a hand-authored shape: a truthful HIGH-DISCARD verdict, 88
#: candidates of which 40 were discarded, produced by a real StrykerJS 10.0.0
#: run with `@stryker-mutator/typescript-checker` enabled
#: (`tests/fixtures/mutation/mutation-report-json.probe-js-stryker-typecheck.
#: json`, recipe in that directory's PROVENANCE.md). It is the CONTROL that
#: proves v11's new arithmetic is a re-derivation and not an upper-bound clamp
#: -- route 3, which DA-R26 rejected precisely because a clamp refuses the
#: honest report too. The A-437 inflation negatives are built FROM this
#: document at runtime, differentially, exactly as every other negative in
#: this suite is built from its own clean control.
HIGH_DISCARD_TEMPLATE = "high-discard-r2-v11-template.json"

#: The two documents this generation ADDS, by name, for the tests that are
#: about the plural-canary and red-first shapes specifically.
MULTI_TARGET_V11_TEMPLATE = "multi-target-r3-v11-template.json"
RED_FIRST_V11_TEMPLATE = "r4-red-first-v11-template.json"

#: The frozen ingested document, by name, for the tests that are about it
#: specifically rather than about "every v11 template verifies".
INGESTED_V11_TEMPLATE = "ingested-r2-v11-template.json"

P25_V11_TEMPLATES = ["p25-pass-v11-template.json", "p25-missing-v11-template.json"]

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
        f"{why}: control document must verify clean under v11"
    )
    assert verify_document(broken), why


@pytest.fixture(scope="module")
def verify_document():
    from assay.verify import verify_document as fn

    return fn


# --- identity and hard-cut guards -------------------------------------------


def test_schema_identity_is_internally_consistent_under_v11():
    from assay import verdict as V

    schema = load(ROOT / "src" / "assay" / "schemas" / "verdict.schema.json")
    assert schema["$id"] == "urn:assay:schema:verdict:11"
    assert schema["properties"]["schema_version"]["const"] == 11
    assert V.VERDICT_SCHEMA_VERSION == 11


def test_shipped_schema_is_byte_identical_to_the_locked_v11_asset():
    """The guard this project has been bitten by TWICE. It is carried forward
    into every generation deliberately: whatever moves in the shipped schema
    must move in the frozen copy in the same commit, or this fails."""
    shipped = (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v11.json").read_bytes()
    assert shipped == locked


@pytest.mark.parametrize(
    "frozen",
    [
        pytest.param(path, id=f"{path.parent.parent.name}/{path.name}")
        for path in sorted(W1_EXPECTED.glob("*.json"))
        + sorted(W2_EXPECTED.glob("*.json"))
        + sorted(W4_EXPECTED.glob("*.json"))
        + sorted(W5_EXPECTED.glob("*.json"))
        + sorted(W6_EXPECTED.glob("*.json"))
    ],
)
def test_every_earlier_frozen_template_is_rejected_under_v11(verify_document, frozen):
    """A-170's hard cut, over ALL FIVE earlier generations at once: v11
    rejects v6, v7, v8, v9 and v10 alike, with exactly one diagnostic and no
    downstream noise. W6 joins the sweep here for the reason W5 joined it at
    the v10 cut, W4 at the v9 cut and W2 at the v8 cut -- the generation that
    was live is now history, and the differential negative that matters is
    that its own documents are refused."""
    failures = verify_document(load(frozen))
    assert len(failures) == 1
    assert "is not this verifier's version 11" in failures[0], failures


def test_the_v10_refusal_is_worded_exactly_as_the_v9_and_v8_ones_are(verify_document):
    """The differential the dispatch asks for by name: v10 must be refused at
    v11 in the SAME shape v9 and v8 are, so the hard cut is one rule and not a
    special case for the version that happened to be previous."""
    v10_failures = verify_document(load(W6_EXPECTED / "sql-r2-v10-template.json"))
    v9_failures = verify_document(load(W5_EXPECTED / "sql-r2-v9-template.json"))
    v8_failures = verify_document(load(W4_EXPECTED / "sql-r2-v8-template.json"))
    assert len(v10_failures) == len(v9_failures) == len(v8_failures) == 1
    assert (
        v10_failures[0].replace(" 10 ", " N ")
        == v9_failures[0].replace(" 9 ", " N ")
        == v8_failures[0].replace(" 8 ", " N ")
    )


# --- migrated v10 controls ---------------------------------------------------


@pytest.mark.parametrize("name", V11_TEMPLATES)
def test_locked_v11_template_is_accepted(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize("name", P25_V11_TEMPLATES)
def test_p25_v11_siblings_validate(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


# --- the frozen INGESTED document (fix round 1) -----------------------------
#
# `ingested-r2-v11-template.json` is a REAL verdict: a real run over the
# committed StrykerJS artifact (`tests/fixtures/mutation/
# mutation-report-json.probe-js-stryker.json`, 109 mutants over 6 files),
# frozen with only `started`/`ended` substituted. Its outcome is
# FAIL/MUTANTS_SURVIVED, which is the honest verdict for that artifact and is
# what makes it worth freezing -- a judged R2 claim with a real payload behind
# it, not a hand-built shell that happens to satisfy the schema.


def test_the_frozen_ingested_template_carries_the_WHOLE_ingested_record(
    verify_document,
):
    """The point of freezing it. `test_locked_v11_template_is_accepted` proves
    it verifies; this proves the document being verified is actually the
    ingested shape, so the guard cannot quietly start guarding a native
    document that happens to sit under the same filename."""
    document = load(HERE / "expected" / INGESTED_V11_TEMPLATE)
    r2 = document["judgment"]["r2"]
    assert r2["producer"] == "ingested"
    assert set(r2["producer_tool"]) == {"name", "version", "report_schema_version"}
    assert r2["producer_tool"]["name"] == "StrykerJS"
    # Required-and-possibly-empty under `ingested` (A-365), and non-empty here.
    assert r2["survived_uncovered"] and r2["lines_without_candidates"]
    # B070/v11: an ARRAY, empty here because the artifact behind this document
    # carries no CompileError/RuntimeError mutant. Empty is a statement, not a
    # silence -- `None` would be the native spelling, and this is not native.
    assert r2["discarded"] == []
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
    document = load(HERE / "expected" / INGESTED_V11_TEMPLATE)
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
def test_p26_attestation_shapes_survive_v11(verify_document, name):
    doc = load(P26_EXPECTED / name)
    doc["schema_version"] = 11
    assert verify_document(doc) == []


# --- B046: judgment.r2 says WHO computed it ---------------------------------


def test_an_r2_document_must_declare_its_producer(verify_document):
    """The whole of B046's wire contract in one assertion: `producer` is
    REQUIRED on `judgment.r2`, so no v9 document can be silent about whether
    assay's own engine produced its mutants or a foreign tool's report did.
    The north-star's "never conflate tiers" is what makes this required rather
    than defaulted on the wire."""
    clean = load(HERE / "expected" / "sql-r2-v11-template.json")
    broken = copy.deepcopy(clean)
    del broken["judgment"]["r2"]["producer"]
    refuses_only_the_defect(
        verify_document, clean, broken, "judgment.r2 without producer must be refused"
    )


def test_a_native_r2_document_may_not_carry_the_ingested_record(verify_document):
    """The first direction of the fork. These four fields are facts DERIVED
    FROM an ingested report; on a native document each would claim a
    computation that never happened."""
    clean = load(HERE / "expected" / "sql-r2-v11-template.json")
    assert clean["judgment"]["r2"]["producer"] == "native"
    for field, value in (
        ("producer_tool", AN_INGESTED_TOOL),
        ("survived_uncovered", [{"path": "pkg/mod.py", "lineno": 3}]),
        # B070/v11: an ARRAY now, and an EMPTY one is still forbidden on a
        # native document -- the fork is about which producer may state the
        # fact at all, not about whether the fact is interesting.
        ("discarded", []),
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
    clean = load(HERE / "expected" / "sql-r2-v11-template.json")
    ingested = copy.deepcopy(clean)
    r2 = ingested["judgment"]["r2"]
    for field in ("jobs", "max_mutants", "operators", "equivalence_artifact"):
        r2.pop(field, None)
    r2["producer"] = "ingested"
    r2["producer_tool"] = AN_INGESTED_TOOL
    r2["survived_uncovered"] = []
    r2["discarded"] = []
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
    clean = load(HERE / "expected" / "sql-r2-v11-template.json")
    ingested = copy.deepcopy(clean)
    r2 = ingested["judgment"]["r2"]
    for field in ("jobs", "max_mutants", "operators", "equivalence_artifact"):
        r2.pop(field, None)
    r2["producer"] = "ingested"
    r2["producer_tool"] = AN_INGESTED_TOOL
    r2["survived_uncovered"] = []
    r2["discarded"] = []
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
    clean = load(HERE / "expected" / "sql-r2-v11-template.json")
    for value in ("Native", "external", "", "stryker"):
        broken = copy.deepcopy(clean)
        broken["judgment"]["r2"]["producer"] = value
        refuses_only_the_defect(
            verify_document, clean, broken, f"producer {value!r} must be refused"
        )


# --- B046: the ingested operator namespace ----------------------------------


def test_the_locked_v11_schema_admits_the_ingested_namespace_and_keeps_the_rest_closed():
    """Asserted against the LOCKED artifact, at the level the claim is made
    about -- W4's own lesson for the withdrawn spellings, one cut on."""
    from jsonschema import Draft202012Validator

    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
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

    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
    branches = schema["$defs"]["mutation_operator"]["oneOf"]
    patterns = [branch["pattern"] for branch in branches if "pattern" in branch]
    assert patterns == [INGESTED_OPERATOR_RE.pattern]


# --- B043: cwd_declared -----------------------------------------------------


def test_a_verdict_may_name_the_directory_its_command_ran_in(verify_document):
    """The additive half. A declared cwd is accepted; its ABSENCE stays
    accepted too, because a lane that declares none ran at the snapshot's
    project root and must not invent a `"."`."""
    clean = load(HERE / "expected" / "p25-pass-v11-template.json")
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

    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
    matrix = schema.get("dependentRequired", {})
    assert "cwd_declared" not in matrix
    for required in matrix.values():
        assert "cwd_declared" not in required


def test_a_cwd_declared_that_is_not_a_repository_tree_path_is_refused(verify_document):
    """One path grammar (A-271). Absent means "the project root"; `"."` is not
    a synonym for it, and neither is an escape or an absolute path."""
    clean = load(HERE / "expected" / "p25-pass-v11-template.json")
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
    clean = load(HERE / "expected" / "p25-pass-v11-template.json")
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
    clean = load(HERE / "expected" / "p25-pass-v11-template.json")
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
    clean = load(HERE / "expected" / "p25-pass-v11-template.json")
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
# Inherited from v10, and re-run here against v11 bytes. Every negative below
# carries its own unmodified clean control.
# ============================================================================


# --- B050/A-427: judgment.r2.fail_under, the floor the document states -------


def test_an_ingested_r2_must_state_the_floor_it_judged_against(verify_document):
    """B050's whole point. Up to v9 the loader FORCED `fail_under = 100.0` on
    every ingested lane so that `verify.py` could assume it; a document that
    does not state the floor leaves the re-derivation partial, so `fail_under`
    is REQUIRED under `producer = "ingested"`."""
    clean = load(HERE / "expected" / INGESTED_V11_TEMPLATE)
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
    clean = load(HERE / "expected" / "sql-r2-v11-template.json")
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
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
    r1 = schema["$defs"]["judgment_r1"]["properties"]["fail_under"]
    r2 = schema["$defs"]["judgment_r2"]["properties"]["fail_under"]
    assert {k: v for k, v in r1.items() if k != "description"} == {
        k: v for k, v in r2.items() if k != "description"
    }


# --- B053/A-428: claim.detail, the refusing sentence on the wire -------------


def test_a_non_pass_claim_may_carry_the_refusing_sentence(verify_document):
    clean = load(HERE / "expected" / "missing-tool-v11-template.json")
    assert verify_document(clean) == []
    refusing = next(c for c in clean["claims"] if c["status"] != "PASS")

    detailed = copy.deepcopy(clean)
    target = next(c for c in detailed["claims"] if c["status"] == refusing["status"])
    target["detail"] = "the 'go' adapter needs the external tool 'go'"
    target["detail_dropped_bytes"] = 0
    assert verify_document(detailed) == []


def test_a_pass_claim_carries_no_detail(verify_document):
    """A-428: `detail` is the refusing sentence, and a pass refused nothing."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v11-template.json")
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
    clean = load(HERE / "expected" / "missing-tool-v11-template.json")
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
    clean = load(HERE / "expected" / "missing-tool-v11-template.json")
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
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
    detail = schema["$defs"]["claim"]["properties"]["detail"]
    assert detail["maxLength"] == 2048
    assert detail["minLength"] == 1


# --- B004/A-430: the reserved code and the evidence narrowing ---------------


def test_the_locked_schema_reserves_both_new_reason_codes_in_both_places():
    """A-430's "four places, none of them optional", asserted over the frozen
    schema: the flat `reason_code` enum AND the per-outcome block. A code in
    one and not the other is a document the two layers disagree about."""
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
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

    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
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
    clean["schema_version"] = 11
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
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
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
    clean = load(HERE / "expected" / MULTI_TARGET_V11_TEMPLATE)
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
    clean = load(HERE / "expected" / MULTI_TARGET_V11_TEMPLATE)

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
    clean = load(HERE / "expected" / MULTI_TARGET_V11_TEMPLATE)

    broken = copy.deepcopy(clean)
    broken["judgment"]["r3"]["aggregation"] = "all"
    assert verify_document(broken), (
        "'short_circuited' under aggregation 'all' must be refused"
    )


def test_one_declared_target_records_no_aggregation(verify_document):
    """A-432: with one probe `any` and `all` denote the same function, so
    recording one would record a policy the lane never stated. Its ABSENCE is
    the checkable statement."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v11-template.json")
    assert clean["judgment"]["r3"]["targets"] == ["pkg/greet.py"]
    assert "aggregation" not in clean["judgment"]["r3"]

    broken = copy.deepcopy(clean)
    broken["judgment"]["r3"]["aggregation"] = "all"
    refuses_only_the_defect(
        verify_document, clean, broken, "a single-target lane recording an aggregation"
    )


def test_a_not_attempted_entry_carries_no_run_field(verify_document):
    clean = load(HERE / "expected" / MULTI_TARGET_V11_TEMPLATE)

    broken = copy.deepcopy(clean)
    broken["claims"][1]["canary"]["attempts"][1]["control_outcome"] = "PASS"
    refuses_only_the_defect(
        verify_document, clean, broken, "a not_attempted entry with a control run"
    )


def test_the_locked_schema_bounds_the_target_list_at_the_measured_number():
    """A-432's bound is MEASURED (~2.76 s of materialisation per target
    against the smallest documented lane budget), and the same number binds
    both objects that carry the list."""
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
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
    clean = load(HERE / "expected" / RED_FIRST_V11_TEMPLATE)
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
    clean = load(HERE / "expected" / RED_FIRST_V11_TEMPLATE)

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
    clean = load(HERE / "expected" / RED_FIRST_V11_TEMPLATE)

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
    clean = load(HERE / "expected" / RED_FIRST_V11_TEMPLATE)

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
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
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


# ============================================================================
# What is NEW at v11 (B070): judgment.r2.discarded LISTS the invalid mutants,
# and the raw layer re-derives it. Every negative below is differential
# against an unmodified clean control, and the control is a REAL run.
# ============================================================================


def _fabricated_discards(count: int) -> list[dict]:
    """`count` well-formed, ascending, unique discarded records that no
    payload contains.

    Deliberately well-formed: the point of the A-437 reproduction is that an
    inflated `discarded` is refused by the ARITHMETIC, not by a grammar
    accident. A list of malformed entries would be refused for the wrong
    reason and would prove nothing about the bound.
    """
    return [
        {
            "path": "app/src/format.ts",
            "lineno": 1 + index % 30,
            "start_byte": 100_000 + index * 8,
            "end_byte": 100_004 + index * 8,
            "replacement_sha256": f"{index:064x}",
            "operator": "stryker:Fabricated",
            "description": "a mutant this payload does not contain",
        }
        for index in range(count)
    ]


def test_the_high_discard_template_is_a_real_run_with_forty_discarded_mutants(
    verify_document,
):
    """The witness DA-D4 asked for and DA-R26 waived while the field was
    merely declared: a real report that really discarded mutants. 88
    candidates, 48 attempted, 40 discarded -- StrykerJS 10.0.0 with its
    TypeScript checker, not a hand-authored status."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    r2 = clean["judgment"]["r2"]
    payload = next(c for c in clean["claims"] if c["rigor"] == "R2")["mutation"]

    assert r2["producer"] == "ingested"
    assert r2["producer_tool"]["name"] == "StrykerJS"
    assert len(r2["discarded"]) == 40
    assert payload["candidate_count"] == 88
    assert payload["total"] == 48
    assert payload["candidate_count"] - payload["total"] == len(r2["discarded"])
    for entry in r2["discarded"]:
        assert entry["operator"].startswith("stryker:")
        assert entry["end_byte"] > entry["start_byte"]
        assert len(entry["replacement_sha256"]) == 64
        assert "kill_signal" not in entry


def test_a_truthful_high_discard_document_is_ACCEPTED(verify_document):
    """**The control, and the whole reason this generation adds a document.**
    Without it the new arithmetic would be indistinguishable from an upper
    bound (`discarded <= total`), which DA-R26 rejected precisely because it
    refuses the honest report as readily as the inflated one. Here 40
    discarded mutants stand beside 48 attempted, and the document verifies
    with an empty failure list."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    assert len(clean["judgment"]["r2"]["discarded"]) == 40
    assert verify_document(clean) == []


def test_the_A437_reproduction_now_refuses_BY_NAME(verify_document):
    """**A-437, inverted.** That row recorded, as a deliberately accepted gap,
    that `judgment.r2.discarded = 9999` on the frozen 109-mutant ingested
    document verified clean. The v11 spelling of that exact forgery is a
    9999-entry list, and it is now refused with a message that names both
    numbers -- the list's length and the payload's own residual."""
    clean = load(HERE / "expected" / INGESTED_V11_TEMPLATE)
    payload = next(c for c in clean["claims"] if c["rigor"] == "R2")["mutation"]
    assert clean["judgment"]["r2"]["discarded"] == []
    assert payload["candidate_count"] == payload["total"] == 109
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["judgment"]["r2"]["discarded"] = _fabricated_discards(9999)
    assert 9999 > payload["total"], (
        "the reproduction must inflate BEYOND the whole payload, or it is not "
        "the case A-437 recorded"
    )
    failures = verify_document(broken)
    assert any(
        "judgment.r2.discarded lists 9999 mutant(s)" in failure
        and "a residual of 0" in failure
        for failure in failures
    ), failures


def test_the_inflation_is_caught_on_the_HIGH_DISCARD_document_too(verify_document):
    """The pair that makes the bound a re-derivation rather than a threshold:
    the same document that legitimately carries 40 discarded mutants refuses a
    41st. Nothing about "how many" is being judged -- only whether the list
    and the payload beside it agree."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    broken = copy.deepcopy(clean)
    broken["judgment"]["r2"]["discarded"] = sorted(
        broken["judgment"]["r2"]["discarded"] + _fabricated_discards(1),
        key=lambda e: (
            e["path"],
            e["start_byte"],
            e["end_byte"],
            e["replacement_sha256"],
            e["operator"],
        ),
    )
    refuses_only_the_defect(
        verify_document, clean, broken, "a 41st discarded mutant with no candidate"
    )


def test_deleting_a_discarded_mutant_is_refused_in_the_same_words(verify_document):
    """The other direction, and the one a producer that wanted to LOOK better
    would take: dropping entries understates how much of the report was
    invalid. The residual is an equality, so it catches both."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    broken = copy.deepcopy(clean)
    del broken["judgment"]["r2"]["discarded"][0]
    refuses_only_the_defect(
        verify_document, clean, broken, "a dropped discarded mutant"
    )


def test_shrinking_candidate_count_to_match_a_forged_list_is_refused(
    verify_document,
):
    """The third corner: make the arithmetic agree by moving the OTHER side.
    `candidate_count` is bounded from below by `total` -- a mutant that was
    attempted was first observed -- so a payload cannot pay for a shorter
    discarded list by claiming it saw fewer candidates than it ran."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    broken = copy.deepcopy(clean)
    r2_claim = next(c for c in broken["claims"] if c["rigor"] == "R2")
    r2_claim["mutation"]["candidate_count"] = 40
    refuses_only_the_defect(
        verify_document, clean, broken, "a candidate_count below total"
    )


def test_a_discarded_mutant_may_not_also_be_in_a_bucket(verify_document):
    """Disjointness. Without it the arithmetic alone could be satisfied by
    listing a mutant twice -- once as caught, once as invalid -- which is a
    strictly better-looking document than the truth."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    broken = copy.deepcopy(clean)
    killed = next(c for c in broken["claims"] if c["rigor"] == "R2")["mutation"][
        "killed"
    ][0]
    broken["judgment"]["r2"]["discarded"] = sorted(
        broken["judgment"]["r2"]["discarded"][1:] + [copy.deepcopy(killed)],
        key=lambda e: (
            e["path"],
            e["start_byte"],
            e["end_byte"],
            e["replacement_sha256"],
            e["operator"],
        ),
    )
    failures = verify_document(broken)
    assert verify_document(clean) == []
    assert any(
        "which the R2 payload also records in one of its five buckets" in failure
        for failure in failures
    ), failures


def test_a_discarded_mutants_line_may_not_be_reported_as_barren(verify_document):
    """The exact converse of `lines_without_candidates`' own rule. The tool
    DID produce a candidate on that line; it merely produced an invalid one,
    and "no candidate here" would be a false statement about the same
    document."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    broken = copy.deepcopy(clean)
    entry = broken["judgment"]["r2"]["discarded"][0]
    barren = broken["judgment"]["r2"]["lines_without_candidates"]
    barren.append({"path": entry["path"], "lineno": entry["lineno"]})
    barren.sort(key=lambda p: (p["path"], p["lineno"]))
    failures = verify_document(broken)
    assert verify_document(clean) == []
    assert any(
        "judgment.r2.discarded records a mutant starting on that exact line"
        in failure
        for failure in failures
    ), failures


def test_an_out_of_order_or_duplicated_discarded_list_is_refused(verify_document):
    """Array ORDER is not expressible in draft 2020-12, so the schema says it
    "is checked by the model and the raw verifier" -- and this asserts the RAW
    layer's own wording, which is deliberately different from the model's, so
    a passing test cannot be the model's witness counted twice."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)

    reordered = copy.deepcopy(clean)
    entries = reordered["judgment"]["r2"]["discarded"]
    assert len(entries) >= 2
    entries[0], entries[1] = entries[1], entries[0]
    failures = verify_document(reordered)
    assert any(
        "judgment.r2.discarded must be strictly ascending" in failure
        for failure in failures
    ), failures

    duplicated = copy.deepcopy(clean)
    entries = duplicated["judgment"]["r2"]["discarded"]
    entries.insert(1, copy.deepcopy(entries[0]))
    failures = verify_document(duplicated)
    assert any(
        "judgment.r2.discarded must be strictly ascending" in failure
        for failure in failures
    ), failures


def test_a_discarded_entry_may_not_carry_a_kill_signal(verify_document):
    """A kill signal names the mechanism that refused a mutant, and nothing
    refused one that never ran -- the same rule the four non-killed buckets
    already carry, applied to the fifth disposition."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    broken = copy.deepcopy(clean)
    broken["judgment"]["r2"]["discarded"][0]["kill_signal"] = "constraint-violation"
    refuses_only_the_defect(
        verify_document, clean, broken, "a discarded mutant naming a kill mechanism"
    )


def test_an_integer_discarded_is_refused_with_the_v10_shape_named(verify_document):
    """The migration's own diagnostic. A consumer that regenerates a document
    with a v10 producer gets the version refusal; a consumer that hand-edits
    one gets this, which says what the field became and why."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    broken = copy.deepcopy(clean)
    broken["judgment"]["r2"]["discarded"] = 40
    failures = verify_document(broken)
    assert any("must be an ARRAY of the mutants" in failure for failure in failures), (
        failures
    )


def test_an_empty_discarded_list_and_an_absent_one_say_different_things(
    verify_document,
):
    """A-051's empty-vs-absent line, which this field now sits on: EMPTY is
    the ingested path's positive statement that it looked and found none;
    ABSENT is the native producer's statement that it has no such concept.
    Both frozen documents are real, and each is refused if given the other's
    spelling."""
    ingested = load(HERE / "expected" / INGESTED_V11_TEMPLATE)
    assert ingested["judgment"]["r2"]["discarded"] == []
    native = load(HERE / "expected" / "sql-r2-v11-template.json")
    assert "discarded" not in native["judgment"]["r2"]

    stripped = copy.deepcopy(ingested)
    del stripped["judgment"]["r2"]["discarded"]
    refuses_only_the_defect(
        verify_document, ingested, stripped, "an ingested r2 with no discarded list"
    )


def test_the_locked_v11_schema_types_discarded_as_a_mutant_outcome_array():
    """Asserted against the LOCKED artifact, at the level the claim is made
    about. A `type: integer` surviving here would mean the shipped schema and
    this generation's frozen copy had drifted -- the guard this project has
    been bitten by twice."""
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
    discarded = schema["$defs"]["judgment_r2"]["properties"]["discarded"]
    assert discarded["type"] == "array"
    assert discarded["uniqueItems"] is True
    # The DOCUMENT ceiling, not `max_mutants`' -- see
    # `test_the_locked_v11_schema_bounds_both_at_the_document_ceiling` for why
    # the two are different numbers with different owners.
    assert discarded["maxItems"] == 100000
    branches = discarded["items"]["allOf"]
    assert {"$ref": "#/$defs/mutant_outcome"} in branches
    assert {"not": {"required": ["kill_signal"]}} in branches


def test_the_locked_v11_schema_still_forks_discarded_on_the_producer():
    """B046's fork, unchanged in substance by the reshape: `discarded` is
    required under `ingested` and forbidden under `native`. A reshape that
    quietly made the field optional on both sides would pass every test above
    and lose the whole tier statement."""
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
    fork = [
        clause
        for clause in schema["$defs"]["judgment_r2"]["allOf"]
        if isinstance(clause, dict)
        and clause.get("if", {}).get("properties", {}).get("producer")
        == {"const": "native"}
    ]
    assert len(fork) == 1, fork
    assert {"not": {"required": ["discarded"]}} in fork[0]["then"]["allOf"]
    assert "discarded" in fork[0]["else"]["required"]


def test_the_arithmetic_rule_admits_the_residual_only_where_it_is_attributed():
    """The model half of the fifth disposition, asserted directly because a
    document cannot exhibit it: `Mutation` alone now ACCEPTS a residual (it
    cannot see what accounts for it), and the layer that can see both objects
    is what refuses an unattributed one."""
    from assay.verdict import Mutation, MutantOutcome

    one = MutantOutcome(
        path="pkg/mod.py",
        lineno=3,
        start_byte=10,
        end_byte=14,
        replacement_sha256="a" * 64,
        operator="python:compare-swap",
        description="< -> <=",
    )

    # Legal at the payload level: a residual awaiting attribution. Through v10
    # this raised; it is the exact rule B070 had to revise.
    payload = Mutation(candidate_count=10, total=1, killed=(one,))
    assert payload.candidate_count - payload.total == 9

    # Never legal: fewer candidates than attempted. A mutant that was
    # attempted was first observed as a candidate.
    with pytest.raises(ValueError, match="candidate_count is never below total"):
        Mutation(candidate_count=0, total=1, killed=(one,))


# --------------------------------------------------------------------------
# B070 fix round 1: the WHOLLY-DISCARDED ingested document
#
# `total 0`, five empty buckets, a positive `candidate_count` -- byte-identical
# to a NATIVE pre-submission limit sentinel, and reachable for real by an
# ingested report whose in-scope mutants were all invalid. Round 1 of the
# review deleted the subtraction that tells them apart and watched the whole
# local suite stay green; these tests are the artifact-level half of what
# makes that impossible. Built from the committed high-discard template, so no
# new fixture is needed and the control is a real run.
# --------------------------------------------------------------------------


def _wholly_discarded(clean: dict) -> dict:
    """The real 40-discard document with its buckets emptied: every one of its
    candidates accounted for as discarded, nothing attempted.

    `survived_uncovered` empties with the `survived` bucket it is a subset of
    -- keeping it would be a document contradicting itself for a second
    reason, and a negative that fails for two reasons proves neither.
    """
    document = copy.deepcopy(clean)
    r2 = next(c for c in document["claims"] if c["rigor"] == "R2")
    discarded = document["judgment"]["r2"]["discarded"]
    for bucket in ("killed", "survived", "crashed", "budget_exceeded", "equivalent"):
        r2["mutation"][bucket] = []
    r2["mutation"]["total"] = 0
    r2["mutation"]["candidate_count"] = len(discarded)
    r2["status"] = "INCONCLUSIVE"
    r2["reason_code"] = "NO_MUTANTS"
    document["judgment"]["r2"]["survived_uncovered"] = []
    document["outcome"] = "INCONCLUSIVE"
    document["reason_code"] = "NO_MUTANTS"
    document["exit_code"] = 5
    return document


def test_an_ingested_report_whose_candidates_were_ALL_discarded_is_NO_MUTANTS(
    verify_document,
):
    """The honest document, ACCEPTED. An ingested lane declares no candidate
    cap (A-360) and assay declined nothing — it read a report that attempted
    no mutant, which is `INCONCLUSIVE`/`NO_MUTANTS`."""
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    document = _wholly_discarded(clean)

    payload = next(c for c in document["claims"] if c["rigor"] == "R2")["mutation"]
    assert payload["total"] == 0
    assert payload["candidate_count"] == 40
    assert len(document["judgment"]["r2"]["discarded"]) == 40

    assert verify_document(document) == [], (
        "a report that discarded every candidate it had is a real, honest "
        "shape; refusing it would put B070 back where DA-R26 found it"
    )


def test_the_SAME_document_claiming_a_limit_refusal_is_REFUSED(verify_document):
    """The lie, refused, differentially against the honest document above.
    These bytes are what a NATIVE pre-submission refusal looks like, and
    reporting them that way would name a candidate cap the lane never declared
    and a refusal assay never made.

    Two layers hold this rule and either would catch it alone. The message
    asserted here is the MODEL's, because reconstruction runs before
    `_check_r2_rederivation` can be reached and a refused reconstruction ends
    the read — which is exactly what "the model states it too" means. The raw
    layer's own independent witness is `judge_mutation`'s subtraction inside
    `_check_r2_rederivation`; it is what caught this shape in round 1, when
    the model still accepted it, and it is asserted directly in
    `tests/test_mutation_judge.py`.
    """
    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)
    honest = _wholly_discarded(clean)
    assert verify_document(honest) == []

    lie = copy.deepcopy(honest)
    r2 = next(c for c in lie["claims"] if c["rigor"] == "R2")
    r2["status"] = "BUDGET_EXCEEDED"
    r2["reason_code"] = "MUTANT_LIMIT_EXCEEDED"
    lie["outcome"] = "BUDGET_EXCEEDED"
    lie["reason_code"] = "MUTANT_LIMIT_EXCEEDED"
    lie["exit_code"] = 4

    failures = verify_document(lie)
    assert failures, "the wholly-discarded latent lie must be refused"
    assert any(
        "the honest terminal is INCONCLUSIVE/NO_MUTANTS" in failure
        for failure in failures
    ), failures


def test_the_MODEL_alone_refuses_both_halves_of_the_sentinel_disposition():
    """The two-independent-witnesses half. `assay verify` catches both shapes
    through its raw checks and its re-derivation; the MODEL must state the
    rule itself, or `Verdict._check_discarded_disposition` is entirely
    shadowed and its removal would go unnoticed (round-1 review measured
    exactly that: the whole method stubbed out, 4257 passed).

    Reconstructed through `assay.verify._reconstruct_verdict`, which is the
    real model constructor `verify_document` uses, so this asserts the model
    layer and not a hand-built object.
    """
    from assay.verify import _reconstruct_verdict

    clean = load(HERE / "expected" / HIGH_DISCARD_TEMPLATE)

    # (a) the INGESTED half: a wholly discarded report may not claim the
    #     native limit refusal.
    lie = _wholly_discarded(clean)
    r2 = next(c for c in lie["claims"] if c["rigor"] == "R2")
    r2["status"] = "BUDGET_EXCEEDED"
    r2["reason_code"] = "MUTANT_LIMIT_EXCEEDED"
    lie["outcome"] = "BUDGET_EXCEEDED"
    lie["reason_code"] = "MUTANT_LIMIT_EXCEEDED"
    lie["exit_code"] = 4
    with pytest.raises(ValueError, match="the honest terminal is INCONCLUSIVE"):
        _reconstruct_verdict(lie)

    # ...and the honest one really does reconstruct, so (a) is not passing
    # because the surrounding document became foreign.
    assert _reconstruct_verdict(_wholly_discarded(clean)) is not None

    # (b) the NATIVE half: a real limit sentinel may not be relabelled with
    #     the milder ingested terminal. The `Claim` rule had to admit that
    #     pairing for (a)'s sake; this is where it is taken back.
    native = load(HERE / "expected" / "sql-r2-v11-template.json")
    assert native["judgment"]["r2"]["producer"] == "native"
    sentinel = copy.deepcopy(native)
    claim = next(c for c in sentinel["claims"] if c["rigor"] == "R2")
    for bucket in ("killed", "survived", "crashed", "budget_exceeded", "equivalent"):
        claim["mutation"][bucket] = []
    claim["mutation"]["total"] = 0
    claim["mutation"]["candidate_count"] = (
        sentinel["judgment"]["r2"]["max_mutants"] + 1
    )
    claim["status"] = "BUDGET_EXCEEDED"
    claim["reason_code"] = "MUTANT_LIMIT_EXCEEDED"
    sentinel["outcome"] = "BUDGET_EXCEEDED"
    sentinel["reason_code"] = "MUTANT_LIMIT_EXCEEDED"
    sentinel["exit_code"] = 4
    assert _reconstruct_verdict(sentinel) is not None, (
        "the honest native sentinel must reconstruct, or the negative below "
        "proves nothing"
    )

    relabelled = copy.deepcopy(sentinel)
    claim = next(c for c in relabelled["claims"] if c["rigor"] == "R2")
    claim["status"] = "INCONCLUSIVE"
    claim["reason_code"] = "NO_MUTANTS"
    relabelled["outcome"] = "INCONCLUSIVE"
    relabelled["reason_code"] = "NO_MUTANTS"
    relabelled["exit_code"] = 5
    with pytest.raises(ValueError, match="PRE-SUBMISSION limit refusal"):
        _reconstruct_verdict(relabelled)


# --------------------------------------------------------------------------
# B070 fix round 1: the ingested size bound, at the boundary in both
# directions
#
# `candidate_count = attempted + discarded` put a truthful high-discard report
# under a ceiling built for a NATIVE declared cap. The ceiling is now
# producer-aware; these pin the new bound so a future edit cannot quietly
# reintroduce the narrowing DA-R26 ruled against.
# --------------------------------------------------------------------------


def test_an_ingested_payload_is_bounded_by_the_DOCUMENT_ceiling_not_max_mutants():
    """`MAX_CANDIDATE_CEILING` (10,001) is `max_mutants + 1` and defends
    against a malicious DECLARED cap — a native concern. An ingested lane
    declares none (A-360), so the bound that applies is the one on reading a
    report at all."""
    from assay.verdict import MAX_CANDIDATE_CEILING, Mutation, MutantOutcome
    from assay.vocabulary import MAX_INGESTED_MUTANTS
    from assay.mutation_parsers.mutation_report_json import (
        MAX_INGESTED_MUTANTS as PARSER_BOUND,
    )

    # One value, two modules -- the drift guard the operator namespace already
    # has. A ceiling the parser and the model disagreed about would refuse a
    # report one of them was willing to read.
    assert MAX_INGESTED_MUTANTS == PARSER_BOUND == 100_000
    assert MAX_CANDIDATE_CEILING == 10_001

    one = MutantOutcome(
        path="app/src/format.ts",
        lineno=3,
        start_byte=10,
        end_byte=14,
        replacement_sha256="a" * 64,
        operator="stryker:ArithmeticOperator",
        description="+ -> -",
    )

    # The shape the review reproduced: 48 attempted, and enough invalid
    # mutants to carry the payload past the NATIVE ceiling. Accepted.
    over_the_native_ceiling = Mutation(
        candidate_count=MAX_CANDIDATE_CEILING + 1, total=1, killed=(one,)
    )
    assert over_the_native_ceiling.candidate_count == 10_002

    # ACCEPTED right at the document ceiling...
    at_the_bound = Mutation(
        candidate_count=MAX_INGESTED_MUTANTS, total=1, killed=(one,)
    )
    assert at_the_bound.candidate_count == 100_000

    # ...and REFUSED one past it, by name.
    with pytest.raises(ValueError, match="exceeds the document ceiling"):
        Mutation(candidate_count=MAX_INGESTED_MUTANTS + 1, total=1, killed=(one,))


def test_the_native_ceiling_still_binds_where_a_declared_cap_exists(verify_document):
    """The other direction, and the reason moving the bound is not the same as
    dropping it: under `producer = "native"` a payload over `max_mutants + 1`
    is still refused — one level up, where the producer is visible."""
    clean = load(HERE / "expected" / "sql-r2-v11-template.json")
    assert clean["judgment"]["r2"]["producer"] == "native"
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    claim = next(c for c in broken["claims"] if c["rigor"] == "R2")
    claim["mutation"]["candidate_count"] = 10_002
    failures = verify_document(broken)
    assert failures, "a native payload over the product ceiling must be refused"


def test_the_locked_v11_schema_bounds_both_at_the_document_ceiling():
    """Asserted against the LOCKED artifact. A `maximum: 10001` surviving here
    would mean the shipped schema still refuses the honest high-discard report
    even though the model no longer does."""
    schema = json.loads((HERE / "verdict.schema.v11.json").read_text())
    assert schema["$defs"]["mutation"]["properties"]["candidate_count"][
        "maximum"
    ] == 100000
    assert schema["$defs"]["judgment_r2"]["properties"]["discarded"][
        "maxItems"
    ] == 100000
