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

_BUCKETS = ("killed", "survived", "crashed", "budget_exceeded")


def _mutant(operator: str = "compare-swap", start: int = 25) -> dict:
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
        "schema_version": 4,
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
        "judgment": {"r2": {"jobs": 1, "max_mutants": 50, "operators": ["compare-swap"]}},
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
    document = _r2_document(bucket=bucket, operator="boolop-swap")
    failures = _raw_failures(document)
    assert failures, (
        f"the raw layer accepted an undeclared operator in {bucket!r}; before "
        f"this repair `killed` was omitted from the sweep and only the model "
        f"objected"
    )
    assert any("boolop-swap" in item and "never declared" in item for item in failures)


@pytest.mark.parametrize("bucket", _BUCKETS)
def test_the_raw_layer_accepts_the_same_bucket_when_the_policy_declares_it(bucket):
    # The negative's control: identical artifact, operator inside the policy.
    # Without this, a raw check that rejected everything would pass above.
    document = _r2_document(bucket=bucket, operator="compare-swap")
    assert _raw_failures(document) == []


def test_verify_document_also_rejects_the_undeclared_killed_operator_end_to_end():
    document = _r2_document(bucket="killed", operator="boolop-swap")
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
    document = _r2_document(bucket="survived", operator="compare-swap")
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
        'jobs = 1\nmax_mutants = 5\noperators = ["compare-swap"]\n',
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
