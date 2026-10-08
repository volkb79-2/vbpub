"""B114 model boundaries missed by the archived R1 preflight snapshot.

Each negative changes one fact of an otherwise valid record and asserts the
specific refusal.  The positive controls make these tests sensitive to a
validator that simply rejects every cold-witness or ledger-shaped record.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from conftest import PROJECT_ROOT

from assay.errors import Outcome
from assay.r2_command import R2_APPENDED, R2_TRANSFORM_ID, transform_argv
from assay.resource_limits import CounterDelta
from assay.verdict import (
    CampaignBinding,
    EquivalenceLedger,
    JudgmentR2,
    MutantEvidence,
    Mutation,
    MutationExecution,
    MutationWitnessReceipt,
    R2BaselineFacts,
    R2Command,
)
from assay.verify import _reconstruct_verdict, verify_document


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
ARGV = ("pytest", "--cov=pkg", "--cov-report=json:coverage.json", "tests")


def _baseline(*, cold: bool, **changes: object) -> R2BaselineFacts:
    fields = dict(
        collection_count=1,
        collection_sha256=SHA_A,
        duplicates=0,
        hook_fingerprint_sha256=SHA_B,
        hook_count=1,
        runtime_fingerprint_sha256=SHA_C,
        wall_s=0.25 if cold else None,
    )
    fields.update(changes)
    return R2BaselineFacts(**fields)


def _command(**changes: object) -> R2Command:
    fields = dict(
        transform=R2_TRANSFORM_ID,
        argv_declared=ARGV,
        argv_transformed=transform_argv(ARGV),
        appended=R2_APPENDED,
        cwd=".",
        config_sha256=None,
        coverage_baseline=_baseline(cold=False),
        r2_baseline=_baseline(cold=True),
    )
    fields.update(changes)
    return R2Command(**fields)


def _evidence(**changes: object) -> MutantEvidence:
    fields = dict(
        command="r2",
        collection_count=1,
        collection_sha256=SHA_A,
        hook_fingerprint_sha256=SHA_B,
        started_count=1,
        failed_call_index=0,
    )
    fields.update(changes)
    return MutantEvidence(**fields)


def _witness() -> MutationWitnessReceipt:
    return MutationWitnessReceipt(
        node_id="tests/test_pkg.py::test_case",
        when="call",
        outcome="failed",
        session_exit_status=1,
        process_exit_status=1,
    )


def _ledger_verdict():
    path = PROJECT_ROOT / "tests/fixtures/verdicts/r2_pass_equivalence_ledger.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert verify_document(document) == []
    return _reconstruct_verdict(document)


def _plain_verdict():
    path = PROJECT_ROOT / "tests/fixtures/verdicts/r2_pass.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert verify_document(document) == []
    return _reconstruct_verdict(document)


def _survivor_verdict():
    path = PROJECT_ROOT / "tests/fixtures/verdicts/r2_fail_mutants_survived.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert verify_document(document) == []
    return _reconstruct_verdict(document)


def _with_r2(verdict, *, policy=None, mutation=None):
    claims = tuple(
        replace(claim, mutation=mutation) if claim.rigor == "R2" and mutation is not None
        else claim
        for claim in verdict.claims
    )
    judgment = (
        replace(verdict.judgment, r2=policy)
        if policy is not None else verdict.judgment
    )
    return replace(verdict, claims=claims, judgment=judgment)


def _cold_verdict():
    plain = _plain_verdict()
    command = _command(
        argv_declared=plain.argv_declared,
        argv_transformed=transform_argv(plain.argv_declared),
    )
    policy = replace(
        plain.judgment.r2, cold_witness_kills=True, r2_command=command
    )
    mutation = plain.claims[-1].mutation
    kills = tuple(
        replace(item, execution=MutationExecution(mode="witness-cold", witness=_witness()),
                evidence=_evidence())
        for item in mutation.killed
    )
    return _with_r2(plain, policy=policy, mutation=replace(mutation, killed=kills))


def _cold_survivor_verdict():
    plain = _survivor_verdict()
    command = _command(
        argv_declared=plain.argv_declared,
        argv_transformed=transform_argv(plain.argv_declared),
    )
    policy = replace(plain.judgment.r2, cold_witness_kills=True, r2_command=command)
    mutation = plain.claims[-1].mutation
    kills = tuple(replace(item, execution=MutationExecution(mode="full", witness=_witness()),
                          evidence=_evidence(command="declared", started_count=None,
                                             failed_call_index=None))
                  for item in mutation.killed)
    survivors = tuple(replace(item, evidence=_evidence(command="declared",
                                                      started_count=None,
                                                      failed_call_index=None))
                      for item in mutation.survived)
    return _with_r2(plain, policy=policy, mutation=replace(
        mutation, killed=kills, survived=survivors))


def test_valid_baselines_and_command_preserve_both_distinct_runs():
    command = _command()
    assert command.argv_transformed == ("pytest", "tests")
    assert command.appended == ("-p", "no:pytest_cov")
    assert command.coverage_baseline.wall_s is None
    assert command.r2_baseline.wall_s == 0.25
    assert command.to_dict()["r2_baseline"]["collection_count"] == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"collection_count": True}, "collection_count must be an integer"),
        ({"hook_count": -1}, "collection_count and hook_count must be >= 0"),
        ({"duplicates": 1}, "duplicates must equal 0"),
        ({"collection_sha256": "not-a-digest"}, "collection_sha256 must be a SHA-256"),
        ({"runtime_fingerprint_sha256": "bad"}, "runtime_fingerprint_sha256 must be"),
        ({"wall_s": float("inf")}, "wall_s must be finite"),
    ],
)
def test_baseline_refuses_invented_collection_or_runtime_facts(changes, message):
    with pytest.raises(ValueError, match=message):
        _baseline(cold=True, **changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"transform": "other"}, "transform must be"),
        ({"argv_declared": ["pytest"]}, "argv_declared must be a tuple"),
        ({"argv_declared": ("pytest", "--cov")}, "argv_declared cannot be transformed"),
        ({"argv_transformed": ("pytest",)}, "argv_transformed differs"),
        ({"appended": ()}, "appended must equal"),
        ({"cwd": "src/../tests"}, "cwd must be a normalized"),
        ({"config_sha256": "bad"}, "config_sha256 must be a SHA-256"),
        ({"coverage_baseline": object()}, "coverage_baseline has the wrong type"),
        ({"r2_baseline": object()}, "r2_baseline has the wrong type"),
        ({"coverage_baseline": _baseline(cold=False, runtime_fingerprint_sha256=None)},
         "coverage_baseline requires a runtime fingerprint"),
        ({"r2_baseline": _baseline(cold=True, runtime_fingerprint_sha256=None)},
         "r2_baseline requires a runtime fingerprint"),
        ({"coverage_baseline": _baseline(cold=True)}, "coverage_baseline must omit wall_s"),
        ({"r2_baseline": _baseline(cold=False)}, "r2_baseline requires wall_s"),
        ({"r2_baseline": _baseline(cold=True, collection_count=2)},
         "different collection_count"),
        ({"r2_baseline": _baseline(cold=True, collection_sha256=SHA_C)},
         "different collection_sha256"),
    ],
)
def test_command_refuses_unbound_transform_or_baselines(changes, message):
    with pytest.raises(ValueError, match=message):
        _command(**changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"audit_sha256": "bad"}, "audit_sha256 must be a SHA-256"),
        ({"entry_count": 0}, "entry_count must be an integer >= 1"),
        ({"entry_count": True}, "entry_count must be an integer >= 1"),
    ],
)
def test_reserved_ledger_envelope_refuses_unbound_audit(changes, message):
    fields = dict(path="ledger.toml", sha256=SHA_A, entry_count=1, audit_sha256=SHA_B)
    fields.update(changes)
    with pytest.raises(ValueError, match=message):
        EquivalenceLedger(**fields)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"command": "other"}, "command must be 'r2' or 'declared'"),
        ({"collection_count": -1}, "collection_count must be an integer >= 0"),
        ({"collection_sha256": "bad"}, "collection_sha256 must be a SHA-256"),
        ({"started_count": None}, "appear together"),
        ({"started_count": 0}, "started_count must be an integer >= 1"),
        ({"failed_call_index": True}, "failed_call_index must be an integer"),
        ({"command": "declared"}, "only R2 evidence may record a failed call prefix"),
        ({"failed_call_index": 1}, "must end the started prefix"),
        ({"started_count": 2, "failed_call_index": 1}, "exceeds collection_count"),
    ],
)
def test_attempt_evidence_refuses_false_collection_prefixes(changes, message):
    with pytest.raises(ValueError, match=message):
        _evidence(**changes)


def test_valid_cold_and_ledger_execution_modes_are_distinct():
    cold = MutationExecution(mode="witness-cold", witness=_witness())
    ledger = MutationExecution(mode="ledger", anchor="audit:decision-1")
    assert cold.to_dict() == {"mode": "witness-cold", "witness": _witness().to_dict()}
    assert ledger.to_dict() == {"mode": "ledger", "anchor": "audit:decision-1"}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"mode": "witness-cold"}, "requires a witness receipt"),
        ({"mode": "witness-cold", "witness": _witness(), "anchor": "audit"},
         "cannot carry prefix or ledger fields"),
        ({"mode": "ledger"}, "requires a non-empty anchor"),
        ({"mode": "ledger", "anchor": "\ud800"}, "anchor must be valid UTF-8"),
        ({"mode": "ledger", "anchor": "a" * 4097}, "anchor exceeds 4096"),
        ({"mode": "ledger", "anchor": "audit", "witness": _witness()},
         "cannot carry witness or prefix fields"),
        ({"mode": "witness-prefix", "anchor": "audit"},
         "cannot carry a ledger anchor"),
    ],
)
def test_execution_modes_refuse_mixed_proof_sources(fields, message):
    with pytest.raises(ValueError, match=message):
        MutationExecution(**fields)


def test_native_ledger_fixture_is_valid_but_cannot_forge_execution_evidence():
    verdict = _ledger_verdict()
    claim = verdict.claims[-1]
    killed = claim.mutation.killed[0]
    equivalent = claim.mutation.equivalent[0]
    assert equivalent.execution.mode == "ledger"
    assert equivalent.evidence is None
    assert verdict.judgment.r2.equivalence_ledger.entry_count == 1
    with pytest.raises(ValueError, match="ledger execution cannot carry per-execution resource evidence"):
        replace(equivalent, resource_limit_evidence=killed.resource_limit_evidence)
    with pytest.raises(ValueError, match="ingested MutantOutcome cannot carry execution evidence"):
        replace(killed, candidate_id=None, source_sha256=None,
                mutated_file_sha256=None, execution=None,
                resource_limit_evidence=None, evidence=_evidence())
    with pytest.raises(ValueError, match="mutation.killed cannot carry a ledger execution"):
        Mutation(candidate_count=1, total=1, killed=(equivalent,),
                 candidate_ids=(equivalent.candidate_id,))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"resource_limit_evidence": object()}, "resource_limit_evidence has the wrong type"),
        ({"evidence": object()}, "evidence has the wrong type"),
    ],
)
def test_native_outcome_requires_typed_resource_and_attempt_evidence(changes, message):
    killed = _plain_verdict().claims[-1].mutation.killed[0]
    with pytest.raises(ValueError, match=message):
        replace(killed, **changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"cold_witness_kills": 1}, "cold_witness_kills must be a bool"),
        ({"r2_command": object()}, "r2_command has the wrong type"),
        ({"equivalence_ledger": object()}, "equivalence_ledger has the wrong type"),
    ],
)
def test_native_policy_requires_typed_cold_and_ledger_fields(changes, message):
    fields = dict(jobs=1, max_mutants=50,
                  operators=("python:compare-swap",),
                  kill_attribution="unattributed", cold_witness_kills=False)
    fields.update(changes)
    with pytest.raises(ValueError, match=message):
        JudgmentR2(**fields)


@pytest.mark.parametrize(
    ("liveness", "message"),
    [
        ({"active": 1, "reason": "enabled", "plugin": "p.py",
          "cpu_window_s": 1.0, "idle_floor_s": 1.0}, "liveness.active must be a bool"),
        ({"active": True, "reason": "enabled", "plugin": None,
          "cpu_window_s": 1.0, "idle_floor_s": 1.0}, "plugin must be present when active"),
        ({"active": False, "reason": "disabled", "plugin": "p.py",
          "cpu_window_s": None, "idle_floor_s": None}, "plugin must be None when active is false"),
        ({"active": True, "reason": "enabled", "plugin": "p.py",
          "cpu_window_s": 0.0, "idle_floor_s": 1.0}, "cpu_window_s must be finite and > 0"),
        ({"active": False, "reason": "disabled", "plugin": None,
          "cpu_window_s": None, "idle_floor_s": 1.0}, "idle_floor_s must be None when inactive"),
    ],
)
def test_native_liveness_policy_refuses_false_plugin_or_window_claims(liveness, message):
    policy = _plain_verdict().judgment.r2
    with pytest.raises(ValueError, match=message):
        replace(policy, liveness=liveness)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": "bad/name"}, "campaign.name does not match"),
        ({"deadline_sha256": "bad"}, "campaign.deadline_sha256 must be"),
        ({"created_at_utc": "2026-10-07T00:00:00+00:00"}, "must use YYYY"),
        ({"created_at_utc": "2026-13-07T00:00:00Z"}, "not a valid UTC timestamp"),
        ({"expires_at_utc": "2026-10-07T00:00:00Z"}, "must be later"),
    ],
)
def test_campaign_binding_refuses_ambiguous_or_invalid_deadline(changes, message):
    fields = dict(name="b110", deadline_sha256=SHA_A,
                  created_at_utc="2026-10-07T00:00:00Z",
                  expires_at_utc="2026-10-07T01:00:00Z")
    fields.update(changes)
    with pytest.raises(ValueError, match=message):
        CampaignBinding(**fields)


def test_valid_campaign_binding_round_trips_as_exact_utc_clock():
    binding = CampaignBinding(name="b110", deadline_sha256=SHA_A,
                              created_at_utc="2026-10-07T00:00:00Z",
                              expires_at_utc="2026-10-07T01:00:00Z")
    assert binding.to_dict()["expires_at_utc"] == "2026-10-07T01:00:00Z"


def test_cold_witness_verdict_binds_each_kill_to_same_collected_suite():
    verdict = _cold_verdict()
    assert verdict.judgment.r2.cold_witness_kills is True
    assert all(item.evidence.collection_sha256 == SHA_A
               for item in verdict.claims[-1].mutation.killed)
    assert verdict.to_dict()["judgment"]["r2"]["r2_command"]["transform"] == R2_TRANSFORM_ID
    assert verify_document(verdict.to_dict()) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"campaign": object()}, "campaign must be a CampaignBinding"),
        ({"env_declared": []}, "env_declared must be a mapping"),
        ({"env_effective": {"": "value"}}, "env_effective contains an empty"),
        ({"env_declared": {"KEY": 3}}, r"env_declared\['KEY'\] must be a string"),
        ({"env_passthrough": ["KEY"]}, "env_passthrough must be a tuple"),
        ({"env_passthrough": ("KEY", "KEY")}, "env_passthrough must be unique"),
        ({"env_declared": {"KEY": "fixed"}, "env_passthrough": ("KEY",)},
         "collide with fixed env_declared"),
        ({"env_effective_passthrough_sha256": {"KEY": SHA_A}},
         "names must match the present"),
        ({"env_effective_passthrough_sha256": []},
         "must be a mapping when present"),
        ({"env_effective": {"KEY": "raw"}, "env_passthrough": ("KEY",),
          "env_effective_passthrough_sha256": {"KEY": SHA_A}},
         "must be '<passthrough>'"),
        ({"env_effective": {"KEY": "<passthrough>"}, "env_passthrough": ("KEY",),
          "env_effective_passthrough_sha256": {"KEY": "bad"}},
         "must be 64 lowercase hexadecimal"),
    ],
)
def test_verdict_refuses_invented_campaign_or_environment_facts(changes, message):
    base = _plain_verdict()
    with pytest.raises(ValueError, match=message):
        replace(base, **changes)


def test_digest_without_a_resolved_lane_is_not_a_known_empty_environment():
    from assay.verdict import Verdict

    with pytest.raises(ValueError, match="present but no lane resolved"):
        Verdict(lane="package", commit="a" * 40, outcome=Outcome.PASS,
                started="2026-10-07T00:00:00Z", ended="2026-10-07T00:00:01Z",
                assay_version="9.0.0", env_effective_passthrough_sha256={})


def test_equivalence_cannot_claim_two_proof_sources():
    base = _ledger_verdict()
    policy = replace(base.judgment.r2, equivalence_artifact="equivalent.bin")
    with pytest.raises(ValueError, match="both equivalence_artifact and equivalence_ledger"):
        _with_r2(base, policy=policy)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("wrong-count", "entry_count differs from mutation.equivalent"),
        ("wrong-mode", "equivalence-ledger entries must be unexecuted"),
    ],
)
def test_equivalence_ledger_counts_and_names_only_unexecuted_entries(mode, message):
    base = _ledger_verdict()
    policy = base.judgment.r2
    mutation = base.claims[-1].mutation
    if mode == "wrong-count":
        policy = replace(policy, equivalence_ledger=replace(policy.equivalence_ledger,
                                                             entry_count=2))
    else:
        item = mutation.equivalent[0]
        mutation = replace(mutation, equivalent=(replace(item,
                            execution=MutationExecution(mode="full"),
                            resource_limit_evidence=mutation.killed[0].resource_limit_evidence),))
    with pytest.raises(ValueError, match=message):
        _with_r2(base, policy=policy, mutation=mutation)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("missing-command", "cold-witness R2 payload requires"),
        ("unexpected-command", "r2_command requires cold_witness_kills=true"),
        ("different-argv", "argv_declared differs from top-level"),
        ("missing-evidence", "witness-cold kill requires started-prefix evidence"),
        ("wrong-evidence-command", "mutant evidence command differs from its baseline"),
        ("wrong-evidence-collection", "mutant evidence collection_sha256 differs"),
    ],
)
def test_cold_witness_verdict_refuses_unbound_execution(mode, message):
    base = _cold_verdict()
    policy = base.judgment.r2
    mutation = base.claims[-1].mutation
    first = mutation.killed[0]
    if mode == "missing-command":
        policy = replace(policy, r2_command=None)
    elif mode == "unexpected-command":
        policy = replace(policy, cold_witness_kills=False)
    elif mode == "different-argv":
        policy = replace(policy, r2_command=_command())
    elif mode == "missing-evidence":
        mutation = replace(mutation, killed=(replace(first, evidence=None), *mutation.killed[1:]))
    elif mode == "wrong-evidence-command":
        mutation = replace(mutation, killed=(replace(first,
            execution=MutationExecution(mode="full", witness=_witness()),
            evidence=_evidence(started_count=None, failed_call_index=None)),
            *mutation.killed[1:]))
    else:
        mutation = replace(mutation, killed=(replace(first, evidence=_evidence(
            collection_sha256=SHA_B)), *mutation.killed[1:]))
    with pytest.raises(ValueError, match=message):
        _with_r2(base, policy=policy, mutation=mutation)


def test_cold_policy_accepts_full_and_prefix_kills_with_their_own_baselines():
    base = _cold_verdict()
    mutation = base.claims[-1].mutation
    full = replace(mutation.killed[0],
                   execution=MutationExecution(mode="full", witness=_witness()),
                   evidence=_evidence(command="declared", started_count=None,
                                      failed_call_index=None))
    prefix = replace(mutation.killed[1],
                     execution=MutationExecution(
                         mode="witness-prefix", witness=_witness(),
                         prior_verdict_sha256=SHA_A,
                         prior_node_id=_witness().node_id,
                         current_node_id=_witness().node_id),
                     evidence=_evidence(started_count=None, failed_call_index=None))
    verdict = _with_r2(base, mutation=replace(mutation, killed=(full, prefix)))
    assert [item.execution.mode for item in verdict.claims[-1].mutation.killed] == [
        "full", "witness-prefix"
    ]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("full-no-witness", "cold-policy full kill requires a failed-call witness"),
        ("full-has-prefix", "full kill evidence cannot carry started-prefix facts"),
        ("prefix-has-prefix", "witness-prefix kill evidence cannot carry started-prefix facts"),
    ],
)
def test_cold_kill_requires_mode_specific_witness_and_prefix(mode, message):
    base = _cold_verdict()
    mutation = base.claims[-1].mutation
    first = mutation.killed[0]
    if mode == "full-no-witness":
        first = replace(first, execution=MutationExecution(mode="full"))
    elif mode == "full-has-prefix":
        first = replace(first, execution=MutationExecution(mode="full", witness=_witness()))
    else:
        first = replace(first, execution=MutationExecution(
            mode="witness-prefix", witness=_witness(),
            prior_verdict_sha256=SHA_A, prior_node_id=_witness().node_id,
            current_node_id=_witness().node_id))
    with pytest.raises(ValueError, match=message):
        _with_r2(base, mutation=replace(mutation, killed=(first, *mutation.killed[1:])))


def test_cold_disabled_policy_refuses_attempt_evidence():
    base = _cold_verdict()
    policy = replace(base.judgment.r2, cold_witness_kills=False, r2_command=None)
    with pytest.raises(ValueError, match="cold-disabled native outcomes cannot carry evidence"):
        _with_r2(base, policy=policy)


def test_cold_policy_refuses_ledger_execution_without_a_ledger():
    base = _ledger_verdict()
    policy = replace(base.judgment.r2,
                     equivalence_ledger=None, equivalence_artifact="equivalent.bin")
    with pytest.raises(ValueError, match="ledger execution requires an equivalent entry"):
        _with_r2(base, policy=policy)


def test_cold_survivor_has_a_declared_collection_and_cannot_omit_it():
    base = _cold_survivor_verdict()
    survivor = base.claims[-1].mutation.survived[0]
    assert survivor.evidence.command == "declared"
    mutation = base.claims[-1].mutation
    with pytest.raises(ValueError, match="cold-witness survivor requires collection evidence"):
        _with_r2(base, mutation=replace(mutation, survived=(replace(survivor, evidence=None),)))


def test_cold_survivor_may_bind_to_r2_collection_but_not_a_failed_prefix():
    base = _cold_survivor_verdict()
    mutation = base.claims[-1].mutation
    survivor = mutation.survived[0]
    r2_survivor = replace(survivor, evidence=_evidence(
        started_count=None, failed_call_index=None))
    assert _with_r2(base, mutation=replace(
        mutation, survived=(r2_survivor,))).claims[-1].mutation.survived[0].evidence.command == "r2"
    with pytest.raises(ValueError, match="started-prefix facts require witness-cold"):
        _with_r2(base, mutation=replace(mutation, survived=(replace(survivor,
            evidence=_evidence()),)))


def test_cold_kill_cannot_call_a_whole_collection_a_failed_prefix():
    base = _cold_verdict()
    mutation = base.claims[-1].mutation
    first = replace(mutation.killed[0], evidence=_evidence(
        started_count=None, failed_call_index=None))
    with pytest.raises(ValueError, match="witness-cold kill requires started-prefix evidence"):
        _with_r2(base, mutation=replace(mutation, killed=(first, *mutation.killed[1:])))


def test_raw_ledger_verdict_refuses_two_equivalence_proof_sources():
    path = PROJECT_ROOT / "tests/fixtures/verdicts/r2_pass_equivalence_ledger.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert verify_document(document) == []
    document["judgment"]["r2"]["equivalence_artifact"] = "equivalent.bin"
    assert verify_document(document)
    with pytest.raises(ValueError, match="both equivalence_artifact and equivalence_ledger"):
        _reconstruct_verdict(document)


def test_payload_free_r2_policy_refuses_command_or_ledger_proof():
    base = _plain_verdict()
    command = _command(argv_declared=base.argv_declared,
                       argv_transformed=transform_argv(base.argv_declared))
    with pytest.raises(ValueError, match="payload-free judgment.r2 cannot carry r2_command"):
        base._check_cold_witness_policy(None, replace(
            base.judgment.r2, cold_witness_kills=True, r2_command=command))
    ledger_policy = _ledger_verdict().judgment.r2
    with pytest.raises(ValueError, match="equivalence_ledger requires a mutation payload"):
        base._check_cold_witness_policy(None, ledger_policy)


def test_non_kill_attempt_evidence_does_not_claim_a_failed_call_prefix():
    base = _cold_verdict()
    mutation = base.claims[-1].mutation
    crashed = replace(mutation.killed[0], execution=MutationExecution(mode="full"),
                      evidence=_evidence(
        started_count=None, failed_call_index=None))
    moved = replace(mutation, killed=mutation.killed[1:], crashed=(crashed,),
                    candidate_ids=mutation.candidate_ids)
    claim = replace(base.claims[-1], mutation=moved)
    base._check_cold_witness_policy(claim, base.judgment.r2)


def _raw_cold_document():
    return deepcopy(_cold_verdict().to_dict())


def _raw_set(document, path, value):
    current = document
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("judgment", "r2", "r2_command"), [], "r2_command must be an object"),
        (("judgment", "r2", "r2_command", "transform"), "other/1",
         "r2_command transform must be"),
        (("judgment", "r2", "r2_command", "argv_declared"), ["pytest", "--cov"],
         "argv_declared cannot be transformed"),
        (("judgment", "r2", "r2_command", "argv_transformed"), ["pytest"],
         "argv_transformed differs"),
        (("judgment", "r2", "r2_command", "appended"), ["-p"],
         "appended argv is not the pinned"),
        (("judgment", "r2", "r2_command", "cwd"), "../tests",
         "cwd must be normalized"),
        (("judgment", "r2", "r2_command", "config_sha256"), "wrong",
         "config_sha256 must be a SHA-256"),
        (("judgment", "r2", "r2_command", "coverage_baseline"), [],
         "coverage_baseline must be an object"),
        (("judgment", "r2", "r2_command", "r2_baseline", "wall_s"), True,
         "r2_baseline.wall_s must be finite"),
        (("judgment", "r2", "r2_command", "coverage_baseline", "wall_s"), 0.0,
         "coverage_baseline must omit wall_s"),
        (("judgment", "r2", "r2_command", "r2_baseline", "collection_count"), 2,
         "baseline collection_count values differ"),
        (("judgment", "r2", "cold_witness_kills"), 1,
         "cold_witness_kills must be a bool"),
    ],
)
def test_raw_v15_command_refuses_unproven_transform_and_baseline(path, value, message):
    document = _raw_cold_document()
    _raw_set(document, path, value)
    failures = verify_document(document)
    assert any(message in failure for failure in failures), failures


@pytest.mark.parametrize(
    ("campaign", "message"),
    [
        ([], "campaign binding must be an object"),
        ({"name": "b110"}, "campaign binding has missing or unknown fields"),
        ({"name": "bad/name", "deadline_sha256": SHA_A,
          "created_at_utc": "2026-10-07T00:00:00Z",
          "expires_at_utc": "2026-10-07T01:00:00Z"},
         "campaign.name does not match"),
        ({"name": "b110", "deadline_sha256": "bad",
          "created_at_utc": "2026-10-07T00:00:00Z",
          "expires_at_utc": "2026-10-07T01:00:00Z"},
         "campaign.deadline_sha256 must be"),
        ({"name": "b110", "deadline_sha256": SHA_A,
          "created_at_utc": "2026-10-07T00:00:00+00:00",
          "expires_at_utc": "2026-10-07T01:00:00Z"},
         "campaign.created_at_utc must use YYYY-MM-DDTHH:MM:SSZ"),
        ({"name": "b110", "deadline_sha256": SHA_A,
          "created_at_utc": "2026-13-07T00:00:00Z",
          "expires_at_utc": "2026-10-07T01:00:00Z"},
         "campaign.created_at_utc is not a valid UTC timestamp"),
        ({"name": "b110", "deadline_sha256": SHA_A,
          "created_at_utc": "2026-10-07T01:00:00Z",
          "expires_at_utc": "2026-10-07T01:00:00Z"},
         "campaign.expires_at_utc must be later"),
    ],
)
def test_raw_campaign_binding_refuses_untrusted_deadline(campaign, message):
    document = _raw_cold_document()
    document["campaign"] = campaign
    failures = verify_document(document)
    assert any(message in failure for failure in failures), failures


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"node_id": ""}, "node_id must be a non-empty string"),
        ({"node_id": "\ud800"}, "node_id must be valid UTF-8"),
        ({"node_id": "x" * 4097}, "node_id exceeds 4096"),
        ({"when": "setup"}, "failed call-phase report"),
        ({"outcome": "passed"}, "failed call-phase report"),
        ({"session_exit_status": True}, "session_exit_status must equal 1"),
        ({"process_exit_status": 0}, "process_exit_status must equal 1"),
    ],
)
def test_witness_receipt_refuses_invalid_node_or_nonfailure_facts(changes, message):
    fields = dict(node_id="tests/test_pkg.py::test_case", when="call",
                  outcome="failed", session_exit_status=1,
                  process_exit_status=1)
    fields.update(changes)
    with pytest.raises(ValueError, match=message):
        MutationWitnessReceipt(**fields)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"mode": "full", "witness": object()}, "witness has the wrong type"),
        ({"mode": "full", "anchor": "audit"}, "full execution cannot carry"),
        ({"mode": "witness-cold"}, "requires a witness receipt"),
        ({"mode": "witness-cold", "witness": _witness(),
          "prior_node_id": "tests/test_pkg.py::test_case"},
         "cannot carry prefix or ledger fields"),
        ({"mode": "witness-prefix", "witness": None,
          "prior_verdict_sha256": SHA_A,
          "prior_node_id": "tests/test_pkg.py::test_case",
          "current_node_id": "tests/test_pkg.py::test_case"},
         "requires a witness receipt"),
        ({"mode": "witness-prefix", "witness": _witness(),
          "prior_verdict_sha256": "bad",
          "prior_node_id": "tests/test_pkg.py::test_case",
          "current_node_id": "tests/test_pkg.py::test_case"},
         "prior_verdict_sha256 must be a SHA-256"),
        ({"mode": "witness-prefix", "witness": _witness(),
          "prior_verdict_sha256": SHA_A, "prior_node_id": "",
          "current_node_id": "tests/test_pkg.py::test_case"},
         "prior_node_id must be a non-empty string"),
        ({"mode": "witness-prefix", "witness": _witness(),
          "prior_verdict_sha256": SHA_A, "prior_node_id": "\ud800",
          "current_node_id": "\ud800"},
         "prior_node_id must be valid UTF-8"),
        ({"mode": "witness-prefix", "witness": _witness(),
          "prior_verdict_sha256": SHA_A, "prior_node_id": "x" * 4097,
          "current_node_id": "x" * 4097},
         "prior_node_id exceeds 4096"),
        ({"mode": "witness-prefix", "witness": _witness(),
          "prior_verdict_sha256": SHA_A,
          "prior_node_id": "tests/test_pkg.py::test_case",
          "current_node_id": "tests/test_pkg.py::other"},
         "prior_node_id and current_node_id must match"),
        ({"mode": "witness-prefix", "witness": _witness(),
          "prior_verdict_sha256": SHA_A,
          "prior_node_id": "tests/test_pkg.py::other",
          "current_node_id": "tests/test_pkg.py::other"},
         "receipt node_id must match current_node_id"),
    ],
)
def test_execution_receipt_requires_one_coherent_proof_source(fields, message):
    if fields["mode"] == "witness-prefix":
        defaults = dict(prior_verdict_sha256=SHA_A,
                        prior_node_id=_witness().node_id,
                        current_node_id=_witness().node_id,
                        witness=_witness())
    else:
        defaults = {}
    defaults.update(fields)
    with pytest.raises(ValueError, match=message):
        MutationExecution(**defaults)


def test_witness_prefix_serializes_its_prior_and_current_node_binding():
    execution = MutationExecution(
        mode="witness-prefix", witness=_witness(),
        prior_verdict_sha256=SHA_A, prior_node_id=_witness().node_id,
        current_node_id=_witness().node_id,
    )
    assert execution.to_dict()["prior_verdict_sha256"] == SHA_A


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"discard_reason": "invented"}, "discard_reason must be one of"),
        ({"candidate_id": None}, "identity and execution fields must be present together"),
        ({"candidate_id": "bad"}, "candidate_id must be a lowercase SHA-256"),
        ({"candidate_id": "f" * 64}, "candidate_id does not match"),
        ({"execution": object()}, "execution has the wrong type"),
        ({"resource_limit_evidence": None}, "requires resource_limit_evidence"),
    ],
)
def test_native_outcome_requires_real_identity_and_bounded_execution(changes, message):
    killed = _plain_verdict().claims[-1].mutation.killed[0]
    with pytest.raises(ValueError, match=message):
        replace(killed, **changes)


def test_ingested_outcome_cannot_smuggle_native_evidence_or_resource_counters():
    killed = _plain_verdict().claims[-1].mutation.killed[0]
    ingested = dict(candidate_id=None, source_sha256=None,
                    mutated_file_sha256=None, execution=None,
                    resource_limit_evidence=None)
    with pytest.raises(ValueError, match="cannot carry execution evidence"):
        replace(killed, **ingested, evidence=_evidence())
    with pytest.raises(ValueError, match="cannot carry resource_limit_evidence"):
        replace(
            killed,
            **{
                **ingested,
                "resource_limit_evidence": killed.resource_limit_evidence,
            },
        )


def test_mutation_model_rejects_false_limit_hit_bucket_and_inventory():
    mutation = _plain_verdict().claims[-1].mutation
    killed = mutation.killed[0]
    counters = killed.resource_limit_evidence
    hit = replace(counters, pids_events_max=CounterDelta.between(1, 2))
    with pytest.raises(ValueError, match="resource-limit-affected.*crashed"):
        replace(mutation, killed=(replace(killed, resource_limit_evidence=hit),
                                  *mutation.killed[1:]))
    with pytest.raises(ValueError, match="candidate_ids entry must be a 64-character"):
        replace(mutation, candidate_ids=("bad",))
    with pytest.raises(ValueError, match="candidate_ids contains a duplicate"):
        replace(mutation, candidate_ids=(SHA_A, SHA_A))
    with pytest.raises(ValueError, match="budget_per_candidate_derived_s must be"):
        replace(mutation, budget_per_candidate_derived_s=0)


def test_duplicate_digest_across_distinct_sites_is_refused(monkeypatch):
    import assay.verdict as verdict_module

    plain = _plain_verdict()
    mutation = plain.claims[-1].mutation
    first = mutation.killed[0]
    second = mutation.killed[1]
    shared = "f" * 64
    monkeypatch.setattr(verdict_module, "candidate_id_from_fields", lambda **_kwargs: shared)
    first = replace(first, candidate_id=shared)
    second = replace(second, candidate_id=shared, path="assay/src/assay/other.py")
    with pytest.raises(ValueError, match="candidate_id.*appears in both"):
        replace(mutation, candidate_count=2, total=2, candidate_ids=(SHA_A, SHA_B),
                killed=(first,), survived=(second,))


@pytest.mark.parametrize(
    ("liveness", "message"),
    [
        ("invalid", "must be a mapping with exactly"),
        ({"active": True}, "must be a mapping with exactly"),
        ({"active": True, "reason": "", "plugin": "hook.py",
          "cpu_window_s": 1.0, "idle_floor_s": 0.5}, "reason must be a non-empty"),
        ({"active": True, "reason": "enabled", "plugin": 3,
          "cpu_window_s": 1.0, "idle_floor_s": 0.5}, "plugin must be a non-empty"),
        ({"active": True, "reason": "enabled", "plugin": "hook.py",
          "cpu_window_s": 1.0, "idle_floor_s": 0.0}, "idle_floor_s must be finite and > 0"),
        ({"active": False, "reason": "disabled", "plugin": None,
          "cpu_window_s": 1.0, "idle_floor_s": None}, "cpu_window_s must be None when inactive"),
    ],
)
def test_native_liveness_contract_rejects_missing_or_incoherent_facts(liveness, message):
    policy = _plain_verdict().judgment.r2
    with pytest.raises(ValueError, match=message):
        replace(policy, liveness=liveness)


def test_verdict_serializes_a_valid_campaign_binding():
    verdict = _plain_verdict()
    binding = CampaignBinding(
        name="b110-pilot", deadline_sha256=SHA_A,
        created_at_utc="2026-10-07T00:00:00Z",
        expires_at_utc="2026-10-07T02:00:00Z",
    )
    assert replace(verdict, campaign=binding).to_dict()["campaign"] == binding.to_dict()
