"""P21 phase-2 review: the raw verifier is a SECOND independent witness, and
every payload-free R2 terminal a producer can reach is verify-clean.

Two defects this module exists to keep closed, both found by feeding real
producer output back through ``assay verify`` rather than by reading the diff:

1. **A producer terminal its own verifier rejected.** ``run_lane`` renders a
   payload-free R2 claim from a caught ``AssayError`` for six distinct
   reasons while R0 PASSed. ``_check_r2_rederivation`` compared every one of
   them against that passing baseline (``judge_mutation``'s ``mutation is
   None`` branch propagates the baseline verbatim), so ``assay run`` emitted
   artifacts ``assay verify`` refused. The reachable-today instance is P21's
   own ``ERROR``/``MUTATION_DISCOVERY_FAILED`` (work item 4 / A-171).

2. **A cross-object rule with only one witness.** The raw sweep binding
   ``claim.mutation``'s operators to ``judgment.r2.operators`` visited
   ``survived``/``crashed``/``budget_exceeded`` but not ``killed`` — the one
   bucket P21 work item 3 exists to bring under that binding (A-180). The
   model caught it through reconstruction, so ``verify_document`` still said
   no; the INDEPENDENCE A-182 assigns separately to model and raw verifier
   did not.

Defect 2 is why several tests here call ``verify._check_judgment_matches_
claims`` DIRECTLY. ``verify_document`` appends reconstruction failures to the
same list as raw failures, so a passing ``verify_document`` assertion cannot
distinguish "the raw layer caught it" from "only the model did" — which is
exactly how the omission survived a green suite and 28 green locked cases.
"""

from __future__ import annotations

import copy
import io
import json
import subprocess
from pathlib import Path

import pytest
from conftest import GitRepo

from assay import verify
from assay.cli import main
from assay.errors import REASON_CODES, Outcome, ReasonCode

#: (P33/V5-3) FIVE buckets since v5. `equivalent` joins the raw operator
#: sweep for the same reason `killed` did in P21: a bucket the sweep does not
#: visit is a bucket in which an undeclared operator is structurally
#: unobservable.
_BUCKETS = ("killed", "survived", "crashed", "budget_exceeded", "equivalent")


def _mutant(operator: str = "python:compare-swap", start: int = 25) -> dict:
    return {
        "path": "src/m.py",
        "lineno": 2,
        "start_byte": start,
        "end_byte": start + 1,
        "replacement_sha256": (
            "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"
        ),
        "operator": operator,
        "description": "Lt->LtE",
    }


def _r2_document(*, bucket: str, operator: str) -> dict:
    """A complete v4 artifact whose R2 payload records ONE outcome in
    *bucket*, produced by *operator*, against a policy declaring only
    ``compare-swap``."""
    payload = {"candidate_count": 1, "total": 1, **{name: [] for name in _BUCKETS}}
    payload[bucket] = [_mutant(operator)]
    status, reason = {
        "killed": ("PASS", None),
        "survived": ("FAIL", "MUTANTS_SURVIVED"),
        "crashed": ("ERROR", "EXEC_FAILED"),
        "budget_exceeded": ("BUDGET_EXCEEDED", "LANE_TIMEOUT"),
        # P33/A-223d: killed + survived == 0 with a non-empty `equivalent`.
        "equivalent": ("INCONCLUSIVE", "ALL_MUTANTS_EQUIVALENT"),
    }[bucket]
    claim = {
        "rigor": "R2",
        "source": "computed",
        "status": status,
        "verified_by_assay": True,
        "mutation": payload,
    }
    if reason is not None:
        claim["reason_code"] = reason
    outcome = Outcome(status)
    document = {
        "schema_version": 5,
        "assay_version": "0.1.0",
        "lane": "package",
        "commit": "4" * 40,
        "outcome": status,
        "exit_code": outcome.exit_code,
        "started": "2026-08-09T11:00:00+00:00",
        "ended": "2026-08-09T11:00:01+00:00",
        "declared_rigor": ["R0", "R2"],
        "declared_evidence": [],
        "argv_declared": ["pytest", "-q"],
        "argv_appended": [],
        "argv_effective": ["pytest", "-q"],
        "argv_modified": False,
        "env_declared": {},
        "env_effective": {},
        "scope": "S1",
        "enforcement": "gate",
        "judgment": {
            # P33/V5-1: an R0,R2 lane records what it judged. This is the
            # exact shape v4 could not express -- no language, no source
            # roots, no comparison commit -- while R2 scopes mutation to
            # changed lines against precisely that commit.
            "resolved": {
                "language": "python",
                "source_roots": ["pkg"],
                "base": "a" * 40,
            },
            "r2": {
                "jobs": 1,
                "max_mutants": 50,
                "operators": ["python:compare-swap"],
                "kill_attribution": "unattributed",
                # P33/V5-3: the `equivalent` bucket is paired
                # both-present-or-both-absent with this declaration (A-209's
                # pattern), because equivalence is established by comparing
                # this artifact's bytes and nothing else may be inferred
                # from. Declared only for the bucket that uses it, so the
                # other four rows still exercise the absent half.
                **(
                    {"equivalence_artifact": ".assay/schema-dump.sql"}
                    if bucket == "equivalent"
                    else {}
                ),
            },
        },
        "claims": [
            {"rigor": "R0", "source": "computed", "status": "PASS", "verified_by_assay": True},
            claim,
        ],
        "evidence": [],
    }
    if reason is not None:
        document["reason_code"] = reason
    return document


def _raw_failures(document: dict) -> list[str]:
    """ONLY the raw, pre-reconstruction cross-field layer.

    Deliberately not `verify_document`: that merges these with the model's
    own reconstruction failures, and the whole point of the rule under test
    is that this layer speaks independently.
    """
    failures: list[str] = []
    verify._check_judgment_matches_claims(document, failures)
    return failures


# ---------------------------------------------------------------------------
# Defect 2 — the raw operator-policy sweep covers every bucket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bucket", _BUCKETS)
def test_the_raw_layer_alone_rejects_an_operator_outside_the_declared_policy(bucket):
    # `boolop-swap` is a real member of the closed catalogue, so neither the
    # schema enum nor `MutantOutcome`'s own closure objects: the ONLY thing
    # wrong with this artifact is that the recorded policy never selected it.
    # That makes it exactly the cross-object rule the raw layer must own.
    document = _r2_document(bucket=bucket, operator="python:boolop-swap")
    failures = _raw_failures(document)
    assert failures, (
        f"the raw layer accepted an undeclared operator in {bucket!r}; before "
        f"this repair `killed` was omitted from the sweep and only the model "
        f"objected"
    )
    assert any("python:boolop-swap" in item and "never declared" in item for item in failures)


@pytest.mark.parametrize("bucket", _BUCKETS)
def test_the_raw_layer_accepts_the_same_bucket_when_the_policy_declares_it(bucket):
    # The negative's control: identical artifact, operator inside the policy.
    # Without this, a raw check that rejected everything would pass above.
    document = _r2_document(bucket=bucket, operator="python:compare-swap")
    assert _raw_failures(document) == []


def test_verify_document_also_rejects_the_undeclared_killed_operator_end_to_end():
    document = _r2_document(bucket="killed", operator="python:boolop-swap")
    failures = verify.verify_document(document)
    assert failures
    # Both witnesses must speak, not just reconstruction. The raw message is
    # worded differently from the model's on purpose (P16's own finding), so
    # their presence is separately observable.
    assert any("never declared" in item for item in failures)
    assert any(item.startswith("schema: ") and "outside judgment.r2" in item for item in failures)


# ---------------------------------------------------------------------------
# Defect 1 — payload-free R2 terminals a producer actually reaches
# ---------------------------------------------------------------------------

#: Every reason `run_lane` can render as a payload-free R2 claim for R2's OWN
#: cause, independently of the baseline, with where it comes from.
INDEPENDENT_TERMINALS: dict[str, str] = {
    "MUTATION_DISCOVERY_FAILED": "run_mutation's discovery boundary (A-171)",
    "DIRTY_TREE": "post-command refusal and R2's own measurability guard",
    "HEAD_CHANGED": "post-command clean HEAD move (A-178)",
    "BASE_IS_HEAD": "R2's own measurability guard",
    "GIT_FAILED": "the diff R2 resolves targets from",
    "UNREADABLE_ARTIFACT": "the bounded source read during target resolution",
}


def _payload_free_r2(status: str, reason: str, *, r0_status: str = "PASS") -> dict:
    document = _r2_document(bucket="survived", operator="python:compare-swap")
    document["outcome"] = status
    document["reason_code"] = reason
    document["exit_code"] = Outcome(status).exit_code
    del document["judgment"]
    document["claims"] = [
        {
            "rigor": "R0",
            "source": "computed",
            "status": r0_status,
            "verified_by_assay": True,
            **({} if r0_status == "PASS" else {"reason_code": "COMMAND_FAILED"}),
        },
        {
            "rigor": "R2",
            "source": "computed",
            "status": status,
            "reason_code": reason,
            "verified_by_assay": True,
        },
    ]
    return document


@pytest.mark.parametrize("reason", sorted(INDEPENDENT_TERMINALS))
def test_a_payload_free_terminal_reached_for_r2s_own_reason_is_verify_clean(reason):
    # The baseline PASSed and R2 still refused, for its own cause. Comparing
    # such a claim against the baseline is what made `assay run` emit
    # artifacts `assay verify` rejected.
    owner = next(o for o, codes in REASON_CODES.items() if ReasonCode(reason) in codes)
    document = _payload_free_r2(owner.value, reason)
    assert verify.verify_document(document) == [], INDEPENDENT_TERMINALS[reason]


@pytest.mark.parametrize("reason", sorted(INDEPENDENT_TERMINALS))
def test_a_payload_free_terminal_paired_with_a_foreign_outcome_is_rejected(reason):
    # The repair re-derives the STATUS from the closed vocabulary rather than
    # trusting the document, so it is not a blanket accept: every outcome the
    # vocabulary does not bind to this reason stays refused.
    owner = next(o for o, codes in REASON_CODES.items() if ReasonCode(reason) in codes)
    for other in Outcome:
        if other is owner or other is Outcome.PASS:
            continue  # PASS is separately unconstructible for a bare R2 claim.
        document = _payload_free_r2(other.value, reason)
        failures = verify.verify_document(document)
        assert failures, f"{other.value}/{reason} was accepted"


def test_baseline_propagation_still_decides_reasons_outside_that_set():
    # `COMMAND_FAILED` is NOT an independent terminal: a payload-free R2 claim
    # carrying it can only have come from a baseline that did not pass, so the
    # original comparison is the correct one and must survive the repair.
    good = _payload_free_r2("FAIL", "COMMAND_FAILED", r0_status="FAIL")
    good["claims"][1]["reason_code"] = "COMMAND_FAILED"
    assert verify.verify_document(good) == []

    # ... and the mismatch it exists to catch is still caught: R0 says the
    # command failed, R2 claims a different propagated pair.
    forged = copy.deepcopy(good)
    forged["claims"][1]["status"] = "ERROR"
    forged["claims"][1]["reason_code"] = "EXEC_FAILED"
    assert any("disagrees" in item for item in verify.verify_document(forged))


# ---------------------------------------------------------------------------
# The producer-to-verify round trip, through the real installed entry point
# ---------------------------------------------------------------------------


def test_assay_run_emits_a_discovery_failure_artifact_its_own_verify_accepts(
    git_repo: GitRepo, tmp_path: Path
):
    """The end-to-end witness. A real R2 lane whose changed file does not
    parse must produce the discovery terminal AND an artifact `assay verify`
    accepts — the two halves of one product disagreeing about one document is
    what this repair closes."""
    repo = git_repo
    repo.write("src/m.py", "x = 1\n")
    repo.write(".gitignore", "verdict.json\n")
    repo.write(
        "assay.toml",
        'schema_version = 1\n'
        "[lanes.package]\n"
        'scope = "S1"\nrigor = ["R0", "R2"]\nenforcement = "gate"\n'
        'argv = ["/bin/true"]\nenv = {}\nenv_passthrough = ["PATH"]\n'
        'budget = "1m"\nallow_argv_append = false\n'
        "[lanes.package.judge]\n"
        'language = "python"\nsource_roots = ["src"]\nbase = "HEAD^"\n'
        "[lanes.package.judge.mutation]\n"
        'jobs = 1\nmax_mutants = 5\noperators = ["python:compare-swap"]\n',
    )
    repo.commit_all("lane")
    repo.write("src/m.py", "def broken(\n")
    repo.commit_all("unparseable change")

    target = repo.path / "verdict.json"
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(repo.path / "assay.toml"),
         "--verdict-json", str(target)],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.ERROR.exit_code
    document = json.loads(target.read_text(encoding="utf-8"))
    r2 = next(claim for claim in document["claims"] if claim["rigor"] == "R2")
    assert (r2["status"], r2["reason_code"]) == ("ERROR", "MUTATION_DISCOVERY_FAILED")
    assert "mutation" not in r2, "a failed discovery boundary produced no candidate set"
    assert document["claims"][0]["status"] == "PASS", "R0 really did pass"
    assert verify.verify_document(document) == [], (
        "assay run emitted an artifact its own assay verify rejects"
    )


# ---------------------------------------------------------------------------
# P33 — the five cross-object invariants, at the RAW layer, ONE CLAUSE EACH
# ---------------------------------------------------------------------------
#
# Work item 3 is explicit that each invariant gets its own controlled break
# "per clause, not per function". These call the raw checks DIRECTLY rather
# than through `verify_document`, for this module's founding reason (A-182):
# `verify_document` merges the raw layer with the model's reconstruction, so
# a passing end-to-end negative cannot distinguish "the raw layer caught it"
# from "reconstruction caught it and the raw branch is dead code". Every test
# below is differential -- the clean document produces NO failure from the
# same function in the same breath -- so none can pass because a check
# rejects everything.


def _sql_r2_document(*, language: str = "sql", **overrides) -> dict:
    """A minimal, complete R0,R2 SQL document. SQL is used deliberately: it is
    the shape v4 could not express at all, so every clause below is exercised
    against the artifact this migration exists to make possible."""
    mutant = {
        "path": "schema/001.sql",
        "lineno": 3,
        "start_byte": 10,
        "end_byte": 20,
        "replacement_sha256": "c" * 64,
        "operator": "sql:drop-check",
        "description": "drop the CHECK constraint",
    }
    document = {
        "schema_version": 5,
        "assay_version": "0.1.0",
        "lane": "package",
        "commit": "4" * 40,
        "outcome": "PASS",
        "exit_code": 0,
        "started": "2026-08-09T11:00:00+00:00",
        "ended": "2026-08-09T11:00:01+00:00",
        "declared_rigor": ["R0", "R2"],
        "declared_evidence": [],
        "argv_declared": ["make", "test"],
        "argv_appended": [],
        "argv_effective": ["make", "test"],
        "argv_modified": False,
        "env_declared": {},
        "env_effective": {},
        "scope": "S1",
        "enforcement": "gate",
        "judgment": {
            "resolved": {
                "language": language,
                "source_roots": ["schema"],
                "base": "a" * 40,
            },
            "r2": {
                "jobs": 1,
                "max_mutants": 50,
                "operators": ["sql:drop-check"],
                "kill_attribution": "unattributed",
            },
        },
        "claims": [
            {"rigor": "R0", "source": "computed", "status": "PASS",
             "verified_by_assay": True},
            {
                "rigor": "R2", "source": "computed", "status": "PASS",
                "verified_by_assay": True,
                "mutation": {
                    "candidate_count": 1, "total": 1, "killed": [mutant],
                    "survived": [], "crashed": [], "budget_exceeded": [],
                    "equivalent": [],
                },
            },
        ],
        "evidence": [],
    }
    document.update(overrides)
    return document


def _raw(fn, document: dict) -> list[str]:
    failures: list[str] = []
    fn(document, failures)
    return failures


def test_raw_layer_clause_operator_prefix_must_equal_the_resolved_language():
    """Invariant 1. The schema closes each language's vocabulary but has no
    `$data`, so it cannot see that these `sql:` operators sit beside a
    `python` resolution."""
    check = verify._check_resolved_language_owns_every_operator
    clean = _sql_r2_document()
    assert _raw_two_arg(check, clean) == []
    broken = _sql_r2_document(language="python")
    failures = _raw_two_arg(check, broken)
    assert failures and any("sql:drop-check" in f for f in failures), failures


def _raw_two_arg(fn, document: dict) -> list[str]:
    failures: list[str] = []
    fn(document, document["judgment"], failures)
    return failures


def _operator_document(*, language: str, declared: list[str], applied: str) -> dict:
    """An R0,R2 document with invariant 1's two inputs set INDEPENDENTLY.

    `_sql_r2_document(language=...)` moves both at once -- it flips the
    resolved language, which makes the declared list AND every recorded
    outcome foreign in the same breath. That is what the two tests below
    exist to stop being the only shape available.
    """
    document = _sql_r2_document()
    document["judgment"]["resolved"]["language"] = language
    document["judgment"]["r2"]["operators"] = list(declared)
    for claim in document["claims"]:
        if claim.get("rigor") == "R2":
            for name in ("killed", "survived", "crashed", "budget_exceeded",
                         "equivalent"):
                for entry in claim["mutation"][name]:
                    entry["operator"] = applied
    return document


def test_raw_layer_clause_operator_language_the_DECLARED_half_alone():
    """Invariant 1a, isolated (P33 work item 3: per CLAUSE, not per function).

    `test_raw_layer_clause_operator_prefix_must_equal_the_resolved_language`
    above flips `judgment.resolved.language` on the whole document, which
    trips this clause AND the per-outcome clause together -- so either one
    surviving keeps it green. Here only the DECLARED list is foreign; every
    recorded outcome matches the resolution. The negative this defends:
    deleting the `judgment.r2.operators` sweep leaves a lane free to declare
    another language's catalogue as long as it never used it.
    """
    check = verify._check_resolved_language_owns_every_operator
    clean = _operator_document(
        language="python", declared=["python:compare-swap"],
        applied="python:compare-swap")
    assert _raw_two_arg(check, clean) == []
    broken = _operator_document(
        language="python", declared=["python:compare-swap", "sql:drop-check"],
        applied="python:compare-swap")
    failures = _raw_two_arg(check, broken)
    assert len(failures) == 1, failures
    assert "judgment.r2.operators" in failures[0], failures
    assert "sql:drop-check" in failures[0], failures


def test_raw_layer_clause_operator_language_the_PER_OUTCOME_half_alone():
    """Invariant 1b, isolated. The mirror of the test above: the declared
    policy is entirely clean and a single recorded mutant carries a foreign
    operator.

    The negative this defends is the sharper of the two, because a payload
    is what a run actually DID: deleting this sweep lets an artifact record
    mutants produced by a catalogue the lane's own resolution says it does
    not have, with the policy object agreeing with the language throughout.
    """
    check = verify._check_resolved_language_owns_every_operator
    clean = _operator_document(
        language="python", declared=["python:compare-swap"],
        applied="python:compare-swap")
    assert _raw_two_arg(check, clean) == []
    broken = _operator_document(
        language="python", declared=["python:compare-swap"],
        applied="sql:drop-check")
    failures = _raw_two_arg(check, broken)
    assert len(failures) == 1, failures
    assert "mutation payload" in failures[0], failures
    assert "sql:drop-check" in failures[0], failures


def test_model_clause_operator_language_the_DECLARED_half_alone():
    """Invariant 1a at the MODEL layer, isolated the same way.

    Reached through `_reconstruct_verdict` rather than `verify_document`, so
    no raw failure can stand in for the model's own refusal -- the same
    independence this module's header describes for defect 2.
    """
    clean = _operator_document(
        language="python", declared=["python:compare-swap"],
        applied="python:compare-swap")
    verify._reconstruct_verdict(clean)
    broken = _operator_document(
        language="python", declared=["python:compare-swap", "sql:drop-check"],
        applied="python:compare-swap")
    with pytest.raises(ValueError, match="judgment.r2.operators names"):
        verify._reconstruct_verdict(broken)


def test_model_clause_operator_language_the_PER_OUTCOME_half_alone():
    """Invariant 1b at the MODEL layer, isolated.

    The assertion is on the MESSAGE, not merely on "something raised", and
    that is load-bearing: a foreign operator in the payload is necessarily
    also an undeclared one, so if this clause were deleted the model would
    still raise -- from the undeclared-operator sweep that runs directly
    after it. Both messages open with the identical
    `claim[R2].mutation records outcome(s) for ...` prefix, so the pattern
    below deliberately anchors on the LANGUAGE clause's own tail instead.
    Anchoring on the shared prefix was tried first and the controlled break
    proved it vacuous: the sweep satisfied it verbatim.
    """
    clean = _operator_document(
        language="python", declared=["python:compare-swap"],
        applied="python:compare-swap")
    verify._reconstruct_verdict(clean)
    broken = _operator_document(
        language="python", declared=["python:compare-swap"],
        applied="sql:drop-check")
    with pytest.raises(ValueError, match=r"judgment\.resolved\.language is"):
        verify._reconstruct_verdict(broken)


def test_raw_layer_clause_base_is_required_when_r1_or_r2_is_present():
    """Invariant, A-223a's `if` half."""
    check = verify._check_base_matches_the_tiers_present
    clean = _sql_r2_document()
    assert _raw(lambda d, f: check(d["judgment"], f), clean) == []
    broken = _sql_r2_document()
    del broken["judgment"]["resolved"]["base"]
    failures = _raw(lambda d, f: check(d["judgment"], f), broken)
    assert failures and any("omits base" in f for f in failures), failures


def test_raw_layer_clause_base_is_forbidden_when_neither_r1_nor_r2_is_present():
    """A-227's `only-if` half -- the other clause of the same rule, broken
    separately because a single collapsed check could satisfy one and not the
    other."""
    check = verify._check_base_matches_the_tiers_present
    r3_only = {
        "resolved": {"language": "python", "source_roots": ["pkg"]},
        "r3": {"mechanism": "uncovered-line", "target": "pkg/mod.py"},
    }
    assert _raw(lambda d, f: check(d, f), r3_only) == []
    broken = json.loads(json.dumps(r3_only))
    broken["resolved"]["base"] = "9" * 40
    failures = _raw(lambda d, f: check(d, f), broken)
    assert failures and any("neither r1 nor r2" in f for f in failures), failures


def test_raw_layer_clause_equivalent_entries_require_a_declared_artifact():
    """Invariant 2, and its control: the SAME payload with the declaration
    present raises nothing."""
    check = verify._check_equivalence_pairing
    document = _sql_r2_document()
    claim = document["claims"][1]
    mutation = claim["mutation"]
    mutation["equivalent"] = mutation.pop("killed")
    mutation["killed"] = []
    paired = dict(document["judgment"]["r2"],
                  equivalence_artifact=".assay/schema-dump.sql")
    assert _raw(lambda d, f: check(claim, paired, f), document) == []
    failures = _raw(lambda d, f: check(claim, document["judgment"]["r2"], f), document)
    assert failures and any("equivalence_artifact" in f for f in failures), failures


def test_raw_layer_clause_declared_attribution_requires_its_artifact():
    """Invariant 4, clause 1."""
    check = verify._check_kill_attribution
    document = _sql_r2_document()
    claim = document["claims"][1]
    claim["mutation"]["killed"][0]["kill_signal"] = "23514"
    declared = dict(
        document["judgment"]["r2"],
        kill_attribution="declared",
        kill_signal_artifact=".assay/kill-signal.txt",
    )
    assert _raw(lambda d, f: check(claim, declared, f), document) == []
    orphaned = {k: v for k, v in declared.items() if k != "kill_signal_artifact"}
    failures = _raw(lambda d, f: check(claim, orphaned, f), document)
    assert failures and any("names no" in f for f in failures), failures


def test_raw_layer_clause_declared_attribution_requires_every_kill_explained():
    """Invariant 4, clause 2 -- a separate break, because an implementation
    can satisfy clause 1 and still let a kill go unexplained."""
    check = verify._check_kill_attribution
    declared_policy = {
        "jobs": 1, "max_mutants": 50, "operators": ["sql:drop-check"],
        "kill_attribution": "declared",
        "kill_signal_artifact": ".assay/kill-signal.txt",
    }
    document = _sql_r2_document()
    claim = document["claims"][1]
    claim["mutation"]["killed"][0]["kill_signal"] = "23514"
    assert _raw(lambda d, f: check(claim, declared_policy, f), document) == []
    broken = _sql_r2_document()
    broken_claim = broken["claims"][1]
    failures = _raw(lambda d, f: check(broken_claim, declared_policy, f), broken)
    assert failures and any("no kill_signal" in f for f in failures), failures


def test_raw_layer_clause_unattributed_forbids_a_signal_anywhere():
    """Invariant 4, clause 3 -- the one the schema deliberately leaves open,
    because a `kill_signal` on a KILLED entry is locally legal and only a
    layer that can see `kill_attribution` can refuse it."""
    check = verify._check_kill_attribution
    document = _sql_r2_document()
    claim = document["claims"][1]
    policy = document["judgment"]["r2"]
    assert _raw(lambda d, f: check(claim, policy, f), document) == []
    claim["mutation"]["killed"][0]["kill_signal"] = "23514"
    failures = _raw(lambda d, f: check(claim, policy, f), document)
    assert failures and any("no mechanism to name" in f for f in failures), failures


def test_raw_layer_clause_a_helper_entry_needs_a_correspondingly_judged_claim():
    """Invariant 5, observable direction only (A-223c)."""
    check = verify._check_helpers_have_a_judged_claim
    document = _sql_r2_document()
    document["helpers"] = [{
        "role": "mutation-sites", "tool": "assay-sql-sites",
        "resolved_path": "/opt/assay-helpers/bin/assay-sql-sites",
        "identity": "assay-sql-sites 0.1.0",
    }]
    assert _raw(check, document) == []
    broken = _sql_r2_document()
    broken["helpers"] = [dict(document["helpers"][0], role="statement-positions")]
    failures = _raw(check, broken)
    assert failures and any("statement-positions" in f for f in failures), failures


def test_raw_layer_clause_an_empty_helpers_array_is_refused():
    """A-230a's omission rule, at the layer that reads foreign documents."""
    check = verify._check_helpers_have_a_judged_claim
    assert _raw(check, _sql_r2_document()) == []
    failures = _raw(check, _sql_r2_document(helpers=[]))
    assert failures and any("present but empty" in f for f in failures), failures
