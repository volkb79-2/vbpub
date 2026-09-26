"""Direct raw-verifier checks that are hidden by the model reconstruction.

These functions deliberately run below JSON Schema/model validation.  Each
case asserts the raw layer's own diagnosis so its independent comparisons stay
reachable and reviewable.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from assay import verify as raw_verify
from assay.errors import Outcome, ReasonCode
from assay.verdict import CanaryAttempt


def _check(function, *args):
    failures = []
    function(*args, failures)
    return failures


def test_raw_position_order_checker_handles_malformed_and_valid_shapes():
    check = lambda values: _check(
        raw_verify._positions_are_ascending, values, "positions"
    )

    assert check("not-an-array") == []
    assert check([]) == []
    assert any("strictly ascending" in item for item in check([{"path": "a", "lineno": 2}, {"path": "a", "lineno": 1}]))
    assert check(["not-an-object"]) == []
    assert check([{"path": 4, "lineno": 1}]) == []
    assert check([{"path": "a", "lineno": True}]) == []
    assert check([{"path": "a", "lineno": "1"}]) == []


def test_raw_mutant_identity_refuses_each_incomplete_identity_field():
    complete = {
        "path": "src/a.py",
        "start_byte": 0,
        "end_byte": 1,
        "replacement_sha256": "a" * 64,
        "operator": "python:compare-swap",
    }

    assert raw_verify._raw_mutant_identity("not-an-object") is None
    for name, value in (
        ("path", 3),
        ("replacement_sha256", None),
        ("operator", 3),
        ("start_byte", True),
        ("end_byte", "1"),
    ):
        malformed = {**complete, name: value}
        assert raw_verify._raw_mutant_identity(malformed) is None
    assert raw_verify._raw_mutant_identity(complete) == (
        "src/a.py", 0, 1, "a" * 64, "python:compare-swap"
    )


def test_raw_b106_identity_reconstruction_refuses_malformed_identity_inputs():
    entry = {
        "path": None,
        "start_byte": 0,
        "end_byte": 1,
        "operator": "python:compare-swap",
        "candidate_id": "a" * 64,
        "source_sha256": "b" * 64,
        "mutated_file_sha256": "c" * 64,
        "execution": {"mode": "full"},
    }
    document = {
        "claims": [{
            "rigor": "R2",
            "reason_code": None,
            "mutation": {
                "candidate_ids": ["a" * 64],
                "candidate_count": 1,
                "total": 1,
                "killed": [entry],
            },
        }],
        "judgment": {"r2": {"producer": "native", "max_mutants": 10}},
    }

    failures = _check(raw_verify._check_b106_mutation_provenance, document)

    assert any("does not match its recorded identity" in item for item in failures)


def test_raw_mutant_identity_order_checker_handles_shape_and_duplicate_cases():
    first = {
        "path": "src/a.py",
        "start_byte": 0,
        "end_byte": 1,
        "replacement_sha256": "a" * 64,
        "operator": "python:compare-swap",
    }
    later = {**first, "start_byte": 2, "end_byte": 3}

    assert _check(raw_verify._mutant_identities_are_ascending, "bad", "mutants") == []
    assert _check(raw_verify._mutant_identities_are_ascending, [], "mutants") == []
    assert _check(
        raw_verify._mutant_identities_are_ascending,
        [{**first, "start_byte": True}],
        "mutants",
    ) == []
    assert any(
        "strictly ascending" in item
        for item in _check(
            raw_verify._mutant_identities_are_ascending,
            [later, first],
            "mutants",
        )
    )
    assert any(
        "strictly ascending" in item
        for item in _check(
            raw_verify._mutant_identities_are_ascending,
            [first, first],
            "mutants",
        )
    )


def test_raw_worktree_integrity_checks_both_lists_and_their_overlap():
    assert _check(raw_verify._check_worktree_integrity, {"worktree_integrity": None}) == []
    assert _check(raw_verify._check_worktree_integrity, {"worktree_integrity": []})

    for key in ("ignored_dirty_paths", "overridden_dirty_paths"):
        assert any(key in item for item in _check(
            raw_verify._check_worktree_integrity,
            {"worktree_integrity": {key: "not-an-array"}},
        ))
        assert any("entries must be strings" in item for item in _check(
            raw_verify._check_worktree_integrity,
            {"worktree_integrity": {key: [1]}},
        ))
        assert any("sorted by UTF-8 bytes" in item for item in _check(
            raw_verify._check_worktree_integrity,
            {"worktree_integrity": {key: ["z", "a"]}},
        ))
        assert any("duplicate path" in item for item in _check(
            raw_verify._check_worktree_integrity,
            {"worktree_integrity": {key: ["a", "a"]}},
        ))

    assert _check(raw_verify._check_worktree_integrity, {
        "worktree_integrity": {
            "ignored_dirty_paths": ["a"],
            "overridden_dirty_paths": ["b"],
        }
    }) == []
    assert any("both dirty lists" in item for item in _check(
        raw_verify._check_worktree_integrity,
        {
            "worktree_integrity": {
                "ignored_dirty_paths": ["a"],
                "overridden_dirty_paths": ["a"],
            }
        },
    ))


def _attempt(
    target,
    *,
    control=Outcome.PASS,
    transformed=Outcome.FAIL,
    reason=ReasonCode.UNCOVERED_LINES,
):
    return CanaryAttempt(
        target=target,
        description="probe",
        control_outcome=control,
        transformed_outcome=transformed,
        expected_reason_code=ReasonCode.UNCOVERED_LINES,
        observed_reason_code=reason if transformed is Outcome.FAIL else None,
    )


def _not_attempted(target, why):
    return CanaryAttempt(
        target=target,
        description="probe",
        disposition="not_attempted",
        not_attempted_reason=why,
    )


def _r3_check(attempts, aggregation, status, reason):
    claim = SimpleNamespace(
        rigor="R3",
        canary=SimpleNamespace(attempts=tuple(attempts)),
        status=status,
        reason_code=reason,
    )
    judgment = SimpleNamespace(r3=SimpleNamespace(aggregation=aggregation))
    verdict = SimpleNamespace(claims=[claim], judgment=judgment)
    failures = []
    raw_verify._check_r3_rederivation(verdict, failures)
    return failures


def test_raw_r3_rederivation_accepts_any_short_circuit_after_first_pass():
    failures = _r3_check(
        [
            _attempt("src/first.py"),
            _not_attempted("src/second.py", "short_circuited"),
            _not_attempted("src/third.py", "short_circuited"),
        ],
        "any",
        Outcome.PASS,
        None,
    )

    assert failures == []


def test_raw_r3_rederivation_rejects_invalid_short_circuit_and_terminal_order():
    before_pass = _r3_check(
        [_not_attempted("src/first.py", "short_circuited"), _attempt("src/second.py")],
        "any",
        Outcome.PASS,
        None,
    )
    assert any("no earlier attempt PASSed" in item for item in before_pass)

    under_all = _r3_check(
        [_attempt("src/first.py", transformed=Outcome.PASS), _not_attempted("src/second.py", "short_circuited")],
        "all",
        Outcome.PASS,
        None,
    )
    assert any("only 'any' short-circuits" in item for item in under_all)

    no_terminal = _r3_check(
        [_not_attempted("src/first.py", "earlier_target_terminal"), _attempt("src/second.py")],
        "any",
        Outcome.PASS,
        None,
    )
    assert any("no earlier attempt was terminal" in item for item in no_terminal)


def test_raw_r3_rederivation_keeps_failures_nonterminal_under_all():
    failures = _r3_check(
        [
            _attempt("src/first.py", transformed=Outcome.PASS),
            _attempt("src/second.py", transformed=Outcome.FAIL),
        ],
        "all",
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )

    assert failures == []

    two_passes = _r3_check(
        [_attempt("src/first.py"), _attempt("src/second.py")],
        "all",
        Outcome.PASS,
        None,
    )
    assert two_passes == []


def test_raw_r3_rederivation_derives_failure_when_any_has_no_pass():
    failures = _r3_check(
        [_attempt("src/only.py", transformed=Outcome.PASS)],
        "any",
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )

    assert failures == []


def test_raw_r3_rederivation_checks_terminal_and_budget_bookkeeping():
    after_terminal = _r3_check(
        [
            _attempt("src/first.py", control=Outcome.FAIL),
            _attempt("src/second.py", transformed=Outcome.FAIL),
        ],
        "all",
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert any("after attempt 0 ended the claim" in item for item in after_terminal)

    after_failure_all = _r3_check(
        [
            _attempt("src/first.py", transformed=Outcome.PASS),
            _attempt("src/second.py", control=Outcome.FAIL),
        ],
        "all",
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )
    assert after_failure_all == []

    earlier_marker_after_terminal = _r3_check(
        [
            _attempt("src/first.py", control=Outcome.FAIL),
            _not_attempted("src/second.py", "earlier_target_terminal"),
        ],
        "all",
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert earlier_marker_after_terminal == []

    marker_before_terminal = _r3_check(
        [
            _not_attempted("src/first.py", "earlier_target_terminal"),
            _attempt("src/second.py", control=Outcome.FAIL),
        ],
        "all",
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )
    assert any("no earlier attempt was terminal" in item for item in marker_before_terminal)

    valid_budget_stop = _r3_check(
        [
            _attempt("src/first.py", transformed=Outcome.FAIL),
            _not_attempted("src/second.py", "budget_exhausted"),
        ],
        "all",
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )
    assert valid_budget_stop == []

    budget_on_judged_claim = _r3_check(
        [_not_attempted("src/first.py", "budget_exhausted")],
        "all",
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )
    assert any("only the mechanism's own" in item for item in budget_on_judged_claim)

    attempted_after_budget = _r3_check(
        [
            _not_attempted("src/first.py", "budget_exhausted"),
            _attempt("src/second.py", transformed=Outcome.PASS),
        ],
        "all",
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    )
    assert any("at or after attempt 0" in item for item in attempted_after_budget)

    invalid_nonjudged = _r3_check(
        [_attempt("src/only.py")],
        "any",
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    )
    assert any("only non-judged status that may" in item for item in invalid_nonjudged)

    # This aggregation is schema-invalid, but it reaches the raw checker in
    # isolation and exercises its already-decided branch when a later PASS is
    # encountered. Public verdicts can reach this state only as `any` after a
    # prior PASS, where terminal bookkeeping stops the loop first.
    already_decided = _r3_check(
        [
            _attempt("src/first.py", transformed=Outcome.PASS),
            _attempt("src/second.py", transformed=Outcome.FAIL),
        ],
        "unknown-aggregation",
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )
    assert already_decided == []

    unrecognized_marker = SimpleNamespace(
        target="src/marker.py",
        disposition="not_attempted",
        not_attempted_reason="unknown-marker",
    )
    assert _r3_check(
        [unrecognized_marker],
        "any",
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    ) == []


def test_raw_snapshot_link_paths_refuse_empty_and_non_string_entries():
    failures = _check(
        raw_verify._check_snapshot_policy,
        {
            "declared_rigor": ["R0", "R1"],
            "snapshot_policy": {
                "selection": "repository",
                "link_paths": ["valid/path", 3],
            },
        },
    )

    assert any("link_paths[1] must be a non-empty string" in item for item in failures)


def test_raw_resolved_operator_check_handles_absent_and_malformed_claim_shapes():
    assert _check(
        raw_verify._check_resolved_language_owns_every_operator,
        {},
        {"resolved": {"language": 3}},
    ) == []
    assert _check(
        raw_verify._check_resolved_language_owns_every_operator,
        {"claims": "not-an-array"},
        {"resolved": {"language": "python"}},
    ) == []


def test_raw_kill_attribution_requires_a_source_and_uniform_signals():
    claim = {"mutation": {"killed": [{"path": "src/a.py"}]}}
    missing_source = _check(
        raw_verify._check_kill_attribution,
        claim,
        {"kill_attribution": "declared"},
    )
    assert any("names no kill_signal_artifact" in item for item in missing_source)
    assert any("leaves killed mutant(s)" in item for item in missing_source)

    attributed = {
        "mutation": {"killed": [{"path": "src/a.py", "kill_signal": "failed"}]}
    }
    assert _check(
        raw_verify._check_kill_attribution,
        attributed,
        {"kill_attribution": "declared", "kill_signal_artifact": "signals.json"},
    ) == []

    unattributed = {
        "mutation": {"killed": [{"path": "src/a.py", "kill_signal": "failed"}]}
    }
    failures = _check(
        raw_verify._check_kill_attribution,
        unattributed,
        {"kill_attribution": "unattributed", "kill_signal_artifact": "signals.json"},
    )
    assert any("reporting kills as unattributed" in item for item in failures)
    assert any("records a kill_signal" in item for item in failures)


def test_raw_helper_claim_check_skips_malformed_entries_and_refuses_empty_arrays():
    assert _check(
        raw_verify._check_helpers_have_a_judged_claim,
        {"helpers": [None], "claims": []},
    ) == []
    failures = _check(
        raw_verify._check_helpers_have_a_judged_claim,
        {"helpers": [], "claims": []},
    )
    assert any("helpers is present but empty" in item for item in failures)


def test_raw_interval_check_requires_timezone_offsets_and_orders_instants():
    assert _check(
        raw_verify._check_interval_is_ordered,
        {"started": None, "ended": "2026-09-01T00:00:00+00:00"},
    ) == []

    naive = _check(
        raw_verify._check_interval_is_ordered,
        {"started": "2026-09-01T00:00:00", "ended": "2026-09-01T00:00:01+00:00"},
    )
    assert any("has no UTC offset" in item for item in naive)

    backward = _check(
        raw_verify._check_interval_is_ordered,
        {"started": "2026-09-01T00:00:02+00:00", "ended": "2026-09-01T00:00:01+00:00"},
    )
    assert any("runs backwards" in item for item in backward)


def _claim_like(**overrides):
    values = {
        "rigor": "R1",
        "status": Outcome.FAIL,
        "detail": None,
        "detail_dropped_bytes": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_raw_claim_detail_checks_presence_status_and_utf8_size():
    failures = []
    raw_verify._check_claim_detail(
        SimpleNamespace(claims=[_claim_like(detail_dropped_bytes=1)]), failures
    )
    assert any("with no detail" in item for item in failures)

    failures = []
    raw_verify._check_claim_detail(
        SimpleNamespace(
            claims=[
                _claim_like(
                    status=Outcome.PASS,
                    detail="refusal",
                    detail_dropped_bytes=0,
                )
            ]
        ),
        failures,
    )
    assert any("is a PASS and carries a detail" in item for item in failures)

    failures = []
    raw_verify._check_claim_detail(
        SimpleNamespace(
            claims=[
                _claim_like(
                    detail="x" * (raw_verify.CLAIM_DETAIL_BYTES + 1),
                    detail_dropped_bytes=1,
                )
            ]
        ),
        failures,
    )
    assert any("over the" in item for item in failures)

    failures = []
    raw_verify._check_claim_detail(
        SimpleNamespace(claims=[_claim_like(detail="bounded")]), failures
    )
    assert any("with no detail_dropped_bytes" in item for item in failures)


def _empty_raw_mutation(**overrides):
    payload = {name: [] for name in raw_verify.MUTATION_BUCKETS}
    payload.update({"candidate_count": 0, "total": 0})
    payload.update(overrides)
    return payload


def test_raw_mutation_payload_shape_checks_terminals_sentinel_and_completed_run():
    assert _check(raw_verify._check_mutation_payload_shapes, {"claims": "bad"}) == []
    assert _check(raw_verify._check_mutation_payload_shapes, {"claims": []}) == []

    unsupported = {
        "claims": [{"rigor": "R2", "reason_code": "MUTATION_UNSUPPORTED", "mutation": _empty_raw_mutation()}]
    }
    assert any("yet still attaches a mutation payload" in item for item in _check(
        raw_verify._check_mutation_payload_shapes, unsupported
    ))
    no_mutants_without_payload = {
        "claims": [{"rigor": "R2", "reason_code": "NO_MUTANTS"}]
    }
    assert any("NO_MUTANTS with no mutation payload" in item for item in _check(
        raw_verify._check_mutation_payload_shapes, no_mutants_without_payload
    ))

    sentinel = _empty_raw_mutation(candidate_count=6)
    exact = {
        "claims": [{"rigor": "R2", "reason_code": "MUTANT_LIMIT_EXCEEDED", "mutation": sentinel}],
        "judgment": {"r2": {"max_mutants": 5}},
    }
    assert _check(raw_verify._check_mutation_payload_shapes, exact) == []

    bad_sentinel = _empty_raw_mutation(candidate_count=7)
    mismatched = {
        "claims": [{"rigor": "R2", "reason_code": "NO_MUTANTS", "mutation": bad_sentinel}],
        "judgment": {"r2": {"max_mutants": 5}},
    }
    failures = _check(raw_verify._check_mutation_payload_shapes, mismatched)
    assert any("not one more than" in item for item in failures)
    assert any("rather than MUTANT_LIMIT_EXCEEDED" in item for item in failures)

    completed_payload = _empty_raw_mutation(candidate_count=1, total=2)
    completed = {
        "claims": [{
            "rigor": "R2",
            "reason_code": None,
            "mutation": completed_payload,
        }],
        "judgment": {"r2": {"max_mutants": 1}},
    }
    failures = _check(raw_verify._check_mutation_payload_shapes, completed)
    assert any("attempted 2 mutant(s)" in item for item in failures)
    assert any("attempts every candidate" in item for item in failures)

    misplaced_discard_reason = _empty_raw_mutation(
        candidate_count=1,
        total=1,
        killed=[{"path": "src/a.py", "discard_reason": "compile_error"}],
    )
    failures = _check(
        raw_verify._check_mutation_payload_shapes,
        {
            "claims": [{"rigor": "R2", "mutation": misplaced_discard_reason}],
            "judgment": {"r2": {"max_mutants": 1}},
        },
    )
    assert any("carries discard_reason" in item for item in failures)

    boolean_count = _empty_raw_mutation(candidate_count=True, total=0)
    assert _check(
        raw_verify._check_mutation_payload_shapes,
        {
            "claims": [{"rigor": "R2", "mutation": boolean_count}],
            "judgment": {"r2": {"max_mutants": 1}},
        },
    ) == []

    non_integer_total = _empty_raw_mutation(candidate_count=1, total=True)
    assert _check(
        raw_verify._check_mutation_payload_shapes,
        {
            "claims": [{"rigor": "R2", "mutation": non_integer_total}],
            "judgment": {"r2": {"max_mutants": 1}},
        },
    ) == []


def test_raw_r2_rederivation_rejects_wrong_terminal_outcomes():
    unsupported = SimpleNamespace(
        rigor="R2", reason_code=ReasonCode.MUTATION_UNSUPPORTED,
        status=Outcome.FAIL, mutation=None,
    )
    failures = _check(
        raw_verify._check_r2_rederivation,
        SimpleNamespace(claims=[unsupported], judgment=None),
    )
    assert any("no mutation implementation" in item for item in failures)

    discovery = SimpleNamespace(
        rigor="R2", reason_code=ReasonCode.MUTATION_DISCOVERY_FAILED,
        status=Outcome.PASS, mutation=None,
    )
    failures = _check(
        raw_verify._check_r2_rederivation,
        SimpleNamespace(claims=[discovery], judgment=None),
    )
    assert any("closed vocabulary does not bind" in item for item in failures)


def test_raw_judgment_operator_scan_skips_non_string_operator_data():
    document = {
        "claims": [
            {
                "rigor": "R2",
                "mutation": {"killed": [{"operator": None}]},
            }
        ],
        "judgment": {
            "r2": {"operators": ["python:compare-swap"]},
        },
    }

    assert _check(raw_verify._check_judgment_matches_claims, document) == []


def test_raw_reconstructor_reaches_optional_judge_and_helper_fields():
    fixture = Path(__file__).parent / "fixtures" / "verdicts" / "r2_pass.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    document["judge_provenance"] = {
        "name": "assay",
        "version": "7.1.0",
        "artifact": "wheel",
        "digest_algorithm": "sha256",
        "digest": "a" * 64,
    }
    document["helpers"] = [
        {
            "role": "mutation-sites",
            "tool": "assay",
            "resolved_path": "/opt/assay",
            "identity": "assay 7.1.0",
        }
    ]

    assert raw_verify.verify_document(document) == []


def test_raw_outcome_owner_reports_an_unassigned_reason_if_the_catalog_drifts(
    monkeypatch,
):
    monkeypatch.setattr(raw_verify, "REASON_CODES", {Outcome.PASS: frozenset()})

    assert raw_verify._outcome_owning(ReasonCode.COMMAND_FAILED) is None


def test_raw_node_ids_and_mutation_intervals_cover_clean_and_refused_paths():
    assert not raw_verify._is_bounded_node_id(None)
    assert not raw_verify._is_bounded_node_id("")

    complete = {
        "path": "src/a.py",
        "start_byte": 0,
        "end_byte": 1,
        "replacement_sha256": "a" * 64,
        "operator": "python:compare-swap",
    }
    assert _check(
        raw_verify._check_identities_are_unique,
        {"killed": [complete]},
        ("killed",),
    ) == []

    zero_width = {**complete, "start_byte": 3, "end_byte": 3}
    failures = _check(
        raw_verify._check_identities_are_unique,
        {"killed": [zero_width]},
        ("killed",),
    )
    assert any("spans no bytes" in item for item in failures)
