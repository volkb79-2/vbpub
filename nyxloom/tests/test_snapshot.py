"""Unit tests for the CR-02a snapshot-input vocabulary (`nyxloom.snapshot`).

Scope: the descriptor/audit types themselves -- construction invariants, the
require()/degraded_or() split, redaction and bounding, deterministic ordering
and digesting, and payload round-trip (replay). The BEHAVIOURAL matrix that
proves faults produce zero effects and one actionable event lives in
`test_snapshot_faults.py`.

Everything here is pure: no clock, no filesystem, no network, no global state.
"""

from __future__ import annotations

import pytest

from nyxloom import snapshot
from nyxloom.snapshot import (
    InputClass, InputStatus, Provenance, Reason, SnapshotAudit, SnapshotBuilder,
    SnapshotInput, SnapshotUnavailable,
)


PROV = Provenance("event-log", "demo")


def _ok(name, cls=InputClass.AUTHORITATIVE, value=1):
    return SnapshotInput.ok_value(name, cls, value, provenance=PROV)


def _bad(name, cls=InputClass.AUTHORITATIVE, reason=Reason.SOURCE_ERROR,
         status=InputStatus.UNAVAILABLE, detail=""):
    return SnapshotInput.failed(name, cls, reason, provenance=PROV,
                                 status=status, detail=detail)


# --------------------------------------------------------------------------
# descriptor invariants


def test_ok_descriptor_carries_value_and_no_reason():
    d = _ok("lint", value={"a.md": []})
    assert d.ok and d.authoritative
    assert d.require() == {"a.md": []}
    assert d.reason is None


def test_failed_descriptor_never_yields_a_value_through_require():
    d = _bad("lint", detail="OSError: boom")
    assert not d.ok
    assert d.value is None
    with pytest.raises(SnapshotUnavailable) as exc:
        d.require()
    # The exception carries the descriptor, so a caller that DOES catch it can
    # still report the source and reason rather than a bare traceback.
    assert exc.value.descriptor is d
    assert "lint" in str(exc.value) and "source-error" in str(exc.value)


def test_an_ok_status_with_a_reason_code_is_rejected_at_construction():
    """A reason on an OK reading is a contradiction, and a silent one would
    let a caller branch on `.reason` and mis-handle a healthy input."""
    with pytest.raises(ValueError, match="cannot carry a reason"):
        SnapshotInput("lint", InputClass.AUTHORITATIVE, InputStatus.OK, PROV,
                       value=1, reason=Reason.SOURCE_ERROR)


def test_a_fault_status_without_a_reason_code_is_rejected_at_construction():
    """This is the invariant that keeps 'failures are data' honest: there is
    no way to record a fault whose machine-readable reason is missing."""
    with pytest.raises(ValueError, match="must carry a reason"):
        SnapshotInput("lint", InputClass.AUTHORITATIVE, InputStatus.UNAVAILABLE, PROV)


def test_failed_rejects_ok_as_a_fault_status():
    with pytest.raises(ValueError, match="not a fault status"):
        SnapshotInput.failed("lint", InputClass.AUTHORITATIVE, Reason.SOURCE_ERROR,
                              provenance=PROV, status=InputStatus.OK)


def test_degraded_or_is_advisory_only():
    """The central anti-sentinel rule, enforced in code rather than by review:
    authority-bearing code cannot reach a default-valued escape hatch."""
    advisory = _bad("provider_probe", cls=InputClass.ADVISORY)
    assert advisory.degraded_or("fallback") == "fallback"

    authoritative = _bad("lint")
    with pytest.raises(ValueError, match="advisory-only"):
        authoritative.degraded_or("fallback")


def test_degraded_or_returns_the_real_value_when_ok():
    assert _ok("provider_probe", cls=InputClass.ADVISORY, value=7).degraded_or(0) == 7


# --------------------------------------------------------------------------
# redaction + bounding


@pytest.mark.parametrize("raw, must_not_contain", [
    ("ntfy token=hunter2 rejected", "hunter2"),
    ("Authorization: Bearer sk-abc123xyz", "sk-abc123xyz"),
    ("connect failed api_key=deadbeefcafe", "deadbeefcafe"),
    ("PASSWORD = swordfish", "swordfish"),
])
def test_credential_shaped_values_are_redacted(raw, must_not_contain):
    out = snapshot.bounded_detail(raw)
    assert must_not_contain not in out
    assert "<redacted>" in out


def test_redaction_keeps_the_surrounding_diagnostic_readable():
    out = snapshot.bounded_detail("probe failed: token=abc for route flash-high")
    assert "probe failed" in out and "flash-high" in out


def test_detail_is_bounded_and_single_line():
    d = _bad("lint", detail="x" * 5000 + "\nsecond line\nthird")
    assert len(d.detail) <= snapshot.DETAIL_MAX
    assert "\n" not in d.detail
    assert d.detail.endswith("...")


def test_provenance_fields_are_bounded_too():
    p = Provenance("git", "y" * 1000)
    assert len(p.ref) <= snapshot.DETAIL_MAX


# --------------------------------------------------------------------------
# acquire()


def test_acquire_translates_an_exception_into_a_typed_failure():
    def boom():
        raise OSError("lint directory unreadable")

    d = snapshot.acquire("lint", InputClass.AUTHORITATIVE, boom, provenance=PROV)
    assert not d.ok
    assert d.reason is Reason.SOURCE_ERROR
    assert d.status is InputStatus.UNAVAILABLE
    assert "OSError" in d.detail and "unreadable" in d.detail


def test_acquire_passes_a_successful_value_through_unchanged():
    sentinel = object()
    d = snapshot.acquire("lint", InputClass.AUTHORITATIVE, lambda: sentinel,
                          provenance=PROV)
    assert d.ok and d.require() is sentinel


def test_acquire_does_not_swallow_base_exceptions():
    """KeyboardInterrupt/SystemExit are shutdown signals, not source faults.
    Turning one into "the lint result is unavailable" would be a brand-new
    fail-open AND would make the daemon unkillable mid-pass."""
    def interrupted():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        snapshot.acquire("lint", InputClass.AUTHORITATIVE, interrupted, provenance=PROV)


def test_acquire_honours_a_caller_supplied_reason_and_status():
    def boom():
        raise ValueError("half a receipt")

    d = snapshot.acquire("receipts", InputClass.AUTHORITATIVE, boom, provenance=PROV,
                          reason=Reason.MALFORMED_VALUE, status=InputStatus.MALFORMED)
    assert d.reason is Reason.MALFORMED_VALUE
    assert d.status is InputStatus.MALFORMED


# --------------------------------------------------------------------------
# aggregation


def test_permits_effects_is_false_iff_an_authoritative_input_failed():
    assert SnapshotAudit((_ok("a"), _ok("b"))).permits_effects
    # An advisory fault alone does NOT close the gate: allowed progress
    # continues, which is the whole point of the advisory class.
    assert SnapshotAudit((_ok("a"), _bad("b", cls=InputClass.ADVISORY))).permits_effects
    assert not SnapshotAudit((_ok("a"), _bad("b"))).permits_effects


def test_failures_and_degradations_partition_by_class():
    audit = SnapshotAudit((
        _ok("clean"),
        _bad("lint"),
        _bad("probe", cls=InputClass.ADVISORY),
    ))
    assert [i.name for i in audit.failures()] == ["lint"]
    assert [i.name for i in audit.degradations()] == ["probe"]


def test_multiple_simultaneous_failures_are_ordered_deterministically():
    """Ordering must not depend on acquisition order, or the same outage
    produces a different event payload (and a different digest) each pass."""
    forward = SnapshotAudit((_bad("zeta"), _bad("alpha"), _bad("mid")))
    reverse = SnapshotAudit((_bad("mid"), _bad("alpha"), _bad("zeta")))
    assert [i.name for i in forward.failures()] == ["alpha", "mid", "zeta"]
    assert forward.summary() == reverse.summary()
    assert forward.digest() == reverse.digest()
    assert forward.event_payload() == reverse.event_payload()


def test_digest_ignores_run_specific_detail_but_tracks_the_reason():
    """Two passes with the same fault must dedup; a fault that CHANGES kind
    must not."""
    a = SnapshotAudit((_bad("lint", detail="OSError: errno 5"),))
    b = SnapshotAudit((_bad("lint", detail="OSError: errno 13"),))
    c = SnapshotAudit((_bad("lint", reason=Reason.MALFORMED_VALUE,
                             status=InputStatus.MALFORMED),))
    assert a.digest() == b.digest()
    assert a.digest() != c.digest()
    assert SnapshotAudit(()).digest() != a.digest()


def test_duplicate_input_names_are_rejected():
    """One name, one reading. Two descriptors for "lint" would make
    `permits_effects` depend on which one a reader happened to find."""
    with pytest.raises(ValueError, match="duplicate snapshot input"):
        SnapshotAudit((_ok("lint"), _bad("lint")))


def test_summary_names_source_status_and_reason():
    audit = SnapshotAudit((
        _bad("lint"),
        _bad("receipts", reason=Reason.MALFORMED_VALUE, status=InputStatus.MALFORMED),
    ))
    assert audit.summary() == (
        "lint:unavailable:source-error; receipts:malformed:malformed-value")


def test_get_finds_a_named_input_and_returns_none_for_an_absent_one():
    audit = SnapshotAudit((_ok("lint"),))
    assert audit.get("lint").ok
    assert audit.get("nope") is None


# --------------------------------------------------------------------------
# serde / replay


def test_event_payload_round_trips_through_from_payload():
    """Replay equivalence: the audit rebuilt from a durable event reports the
    same failures, degradations, summary and digest as the pass that emitted
    it. Without this, a fail-closed pass could not be explained after the
    fact -- which is the only reason the event exists."""
    original = SnapshotAudit((
        _bad("lint", detail="OSError: boom"),
        _bad("receipts", reason=Reason.MALFORMED_VALUE, status=InputStatus.MALFORMED,
             detail="att-1:JSONDecodeError"),
        _bad("provider_probe", cls=InputClass.ADVISORY, reason=Reason.PROBE_FAILED),
        _ok("state"),
    ))
    payload = original.event_payload()
    replayed = SnapshotAudit.from_payload(payload)

    assert [i.name for i in replayed.failures()] == [i.name for i in original.failures()]
    assert [i.name for i in replayed.degradations()] == [
        i.name for i in original.degradations()]
    assert replayed.summary() == original.summary()
    assert replayed.digest() == original.digest()
    assert replayed.permits_effects == original.permits_effects
    # Replaying twice is stable.
    assert SnapshotAudit.from_payload(replayed.event_payload()).digest() == original.digest()


def test_event_payload_carries_reason_provenance_and_detail_per_source():
    audit = SnapshotAudit((_bad("lint", detail="OSError: boom"),))
    row = audit.event_payload()["unavailable"][0]
    assert row["name"] == "lint"
    assert row["class"] == "authoritative"
    assert row["status"] == "unavailable"
    assert row["reason"] == "source-error"
    assert row["provenance"] == {"kind": "event-log", "ref": "demo"}
    assert "OSError" in row["detail"]


def test_event_payload_of_a_clean_audit_is_empty_but_well_formed():
    payload = SnapshotAudit((_ok("lint"),)).event_payload()
    assert payload["unavailable"] == [] and payload["degraded"] == []
    assert payload["summary"] == ""
    assert SnapshotAudit.from_payload(payload).inputs == ()


def test_descriptor_to_dict_never_carries_the_value():
    """Values are live domain objects (a states dict, an event tuple). Putting
    one in an event payload would be unbounded and often unserialisable."""
    d = _ok("state", value={"t1": object()})
    assert "value" not in d.to_dict()


# --------------------------------------------------------------------------
# builder


def test_builder_collects_both_classes_into_one_audit():
    b = SnapshotBuilder()
    b.authoritative("state", lambda: {"t": 1}, provenance=PROV)
    b.advisory("probe", lambda: (_ for _ in ()).throw(OSError("down")),
                provenance=PROV, reason=Reason.PROBE_FAILED)
    audit = b.audit()
    assert audit.permits_effects            # only the advisory one failed
    assert [i.name for i in audit.degradations()] == ["probe"]


def test_builder_authoritative_failure_closes_the_audit():
    b = SnapshotBuilder()
    b.authoritative("lint", lambda: (_ for _ in ()).throw(OSError("down")),
                     provenance=PROV)
    assert not b.audit().permits_effects


def test_audits_assembled_from_several_groups_still_reject_collisions():
    """SnapshotAudit is the composition point, so composing groups is just
    constructing one from their concatenated inputs -- and the duplicate-name
    guard has to hold there too, because two acquisition groups that both
    claim the name "lint" would otherwise silently drop one reading.

    This replaces a `merge_audits()` helper that had no production caller (it
    was written for CR-05/CR-06, three packages away). STANDING.md forbids
    scaffolding; the behaviour it was there to guarantee is asserted here,
    and CR-05 can add the helper in ten lines when it has a caller to test
    it against."""
    groups = [SnapshotAudit((_ok("state"),)), SnapshotAudit((_bad("lint"),))]

    merged = SnapshotAudit(tuple(i for g in groups for i in g.inputs))

    assert [i.name for i in merged.inputs] == ["lint", "state"]
    assert not merged.permits_effects
    with pytest.raises(ValueError, match="duplicate"):
        SnapshotAudit(tuple(i for g in [groups[0], groups[0]] for i in g.inputs))
