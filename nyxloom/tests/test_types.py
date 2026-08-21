"""Guard: no Role defined-but-never-dispatched (P43).

Statically scans daemon.py + reconcile.py source text for dispatch sites
(`role=Role.<NAME>`) and asserts every Role member is either dispatched or
explicitly reserved+tracked in RESERVED_ROLES — no silent stubs. A reserved
role must also cite a backlog item that really exists, so reserving is TRACKED
deferral rather than the same silent stub under a new name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nyxloom.types import (
    Actor, ActorKind, Event, EventType, Receipt, ReceiptResult,
    RESERVED_ROLES, Role, TaskState, TaskStateFile, utc_now,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_SRC = (REPO_ROOT / "src" / "nyxloom" / "daemon.py").read_text()
RECONCILE_SRC = (REPO_ROOT / "src" / "nyxloom" / "reconcile.py").read_text()
TYPES_SRC = (REPO_ROOT / "src" / "nyxloom" / "types.py").read_text()
# PACKAGE F1 (2026-07-17): nyxloom's own backlog.md was git-mv'd to the
# numeric-prefixed spine inbox (4-backlog.md then; 4-backlog-inbox.md since
# 2026-08-21, docs/backlog-entries-spec.md), AND the inbox is now authored by
# spine_writer as schema-validated FRONTMATTER items (`- id: <B-id>`), not
# body bullets (`- **B-id**`). The guard reads the machine-trusted
# frontmatter surface -- the same surface `nyxloom lint` validates -- so a
# regenerated inbox can't silently drop a reserved role's tracking item. The
# literal 'nyxloom-trove/backlog.md' string is a frozen comment TAG inside
# types.py's RESERVED_ROLES block, not a resolved path.
BACKLOG_SRC = (REPO_ROOT / "nyxloom-trove" / "4-backlog-inbox.md").read_text()
# A backlog item id `X` is present iff the frontmatter has a `- id: X` entry.
_BACKLOG_ID_RE = r"^\s*-\s+id:\s*{}\b"

# CR-05b: the control plane's DISPATCH SURFACE is no longer one file. Effect
# execution now lives in `effects*.py` modules, and more move there with each
# CR-05 sub-package -- so the scan globs them rather than listing them, and a
# family that moves does not silently drop out of this census.
CONTROL_PLANE_SRC = "\n".join(
    p.read_text(encoding="utf-8")
    for p in sorted((REPO_ROOT / "src" / "nyxloom").glob("effects*.py"))
)

_DISPATCH_RE = re.compile(r"role=Role\.(\w+)")
_RESERVED_BLOCK_RE = re.compile(r"^RESERVED_ROLES.*?^\}\)", re.M | re.S)
_RESERVED_REF_RE = re.compile(r"Role\.(\w+),\s*#\s*nyxloom-trove/backlog\.md:\s*(\S+)")


def _dispatched_roles() -> set[Role]:
    names = (set(_DISPATCH_RE.findall(DAEMON_SRC))
             | set(_DISPATCH_RE.findall(RECONCILE_SRC))
             | set(_DISPATCH_RE.findall(CONTROL_PLANE_SRC)))
    return {Role[name] for name in names}


def _reserved_backlog_refs() -> dict[Role, str]:
    """Map each RESERVED_ROLES member to the backlog id its comment cites."""
    if not RESERVED_ROLES:
        return {}  # B5: RESERVED_ROLES is empty (every role dispatched) -> nothing to scan
    block = _RESERVED_BLOCK_RE.search(TYPES_SRC)
    assert block is not None, "could not locate the RESERVED_ROLES block in types.py"
    return {Role[name]: ref for name, ref in _RESERVED_REF_RE.findall(block.group(0))}


def test_every_role_is_dispatched_or_reserved():
    dispatched = _dispatched_roles()
    for role in Role:
        assert role in dispatched or role in RESERVED_ROLES, (
            f"{role} is neither dispatched (role=Role.{role.name} in "
            f"daemon.py/reconcile.py/effects*.py) nor in RESERVED_ROLES — silent stub"
        )


def test_reserved_and_dispatched_roles_are_disjoint():
    assert _dispatched_roles().isdisjoint(RESERVED_ROLES)


def test_self_review_is_dispatched_and_not_reserved():
    """B5 2026-07-20: the self_review leg wired Role.SELF_REVIEW into daemon.py's
    LaunchSelfReview executor (a real `role=Role.SELF_REVIEW` dispatch site), so
    it is now DISPATCHED and no longer reserved. This deliberately INVERTS the
    pre-B5 anchor (was: reserved + not dispatched) -- a legitimate flip because
    the role went from defined-but-dead to actually-wired, exactly the transition
    RESERVED_ROLES exists to track. Still non-hollow: it proves the source scan
    finds the real dispatch site, not just that the set membership flipped."""
    assert Role.SELF_REVIEW in _dispatched_roles()
    assert Role.SELF_REVIEW not in RESERVED_ROLES


def test_implementer_is_dispatched_and_not_reserved():
    """Non-hollow anchor: proves the scan finds a real dispatch site, not
    just that RESERVED_ROLES is disjoint from Role by construction."""
    assert Role.IMPLEMENTER in _dispatched_roles()
    assert Role.IMPLEMENTER not in RESERVED_ROLES


def test_every_reserved_role_cites_a_live_backlog_item():
    """Reserving is only legitimate if it is tracked: without this, a role can
    be parked in RESERVED_ROLES with no backlog ref and stay green forever —
    the original silent stub, relabelled."""
    refs = _reserved_backlog_refs()
    assert set(refs) == set(RESERVED_ROLES), (
        "every RESERVED_ROLES member needs a trailing "
        "`# nyxloom-trove/backlog.md: <id>` comment; missing: "
        f"{{{', '.join(sorted(r.name for r in set(RESERVED_ROLES) - set(refs)))}}}"
    )
    for role, backlog_id in refs.items():
        assert re.search(_BACKLOG_ID_RE.format(re.escape(backlog_id)), BACKLOG_SRC, re.M), (
            f"{role} cites backlog item {backlog_id!r}, but 4-backlog.md has no "
            f"`- id: {backlog_id}` frontmatter item — reserved-but-untracked is "
            f"still a silent stub"
        )


def test_all_roles_dispatched_none_reserved_after_b5():
    """B5 2026-07-20: SELF_REVIEW was the LAST reserved role; wiring the
    self_review leg dispatched it, so RESERVED_ROLES is now empty and EVERY Role
    is dispatched. Replaces the old SELF_REVIEW-cites-backlog anchor, which
    became moot once the role stopped being reserved. A future defined-but-dead
    role must go back into RESERVED_ROLES WITH a citation comment; the (now
    dormant) test_every_reserved_role_cites_a_live_backlog_item re-arms
    automatically when a real reserved role reappears in the multi-line block."""
    assert RESERVED_ROLES == frozenset()
    dispatched = _dispatched_roles()
    for role in Role:
        assert role in dispatched, f"{role} is neither dispatched nor reserved"


# ============================================================================
# B21 2026-07-23 (D-R16 §3, scope-amendment escalation). Oracle O7: the new
# EventType members, the new ReceiptResult member, and the new Receipt field
# are ADDITIVE ONLY (D-B21-3) and round-trip through their EXISTING generic
# serde (_Serde.to_dict/from_dict, Event's own _FIELD_TYPES) -- no bespoke
# converter needed for any of the three, proving the addition is truly
# additive rather than a change requiring new serde machinery.
# ============================================================================

def test_receipt_result_pre_existing_members_unchanged():
    """D-B21-3 non-hollow anchor: the four pre-B21 members keep their exact
    .value -- a real pin on the VALUES, not just 'the names still exist'."""
    assert ReceiptResult.DONE.value == "done"
    assert ReceiptResult.BLOCKED.value == "blocked"
    assert ReceiptResult.LIMIT.value == "limit"
    assert ReceiptResult.ERROR.value == "error"


def test_receipt_result_scope_amendment_member_added():
    """O7: the new member exists with its own value. NEGATIVE: it is a
    genuinely distinct member from BLOCKED -- a scope-amendment request is a
    different, never-a-dead-end outcome, not BLOCKED relabelled."""
    assert ReceiptResult.SCOPE_AMENDMENT.value == "scope_amendment"
    assert ReceiptResult.SCOPE_AMENDMENT is not ReceiptResult.BLOCKED


def test_receipt_amendment_request_field_round_trips():
    """O7: Receipt.amendment_request is a plain additive dict field -- no
    _FIELD_TYPES converter needed (the generic _Serde encode/decode passes a
    plain dict through transparently). Round-trips with a populated value.
    NEGATIVE: a pre-B21 receipt dict with NO amendment_request key at all
    still from_dict's cleanly, defaulting to None -- proves old receipts are
    unaffected, not silently broken by the new field."""
    r = Receipt(
        result=ReceiptResult.SCOPE_AMENDMENT, exit_code=0,
        amendment_request={"file": "src/demo/x.py", "reason": "needs a shared helper"},
    )
    d = r.to_dict()
    assert d["amendment_request"] == {"file": "src/demo/x.py", "reason": "needs a shared helper"}
    back = Receipt.from_dict(d)
    assert back.amendment_request == {"file": "src/demo/x.py", "reason": "needs a shared helper"}
    assert back.result is ReceiptResult.SCOPE_AMENDMENT

    legacy = {"result": "done", "exit_code": 0}
    legacy_receipt = Receipt.from_dict(legacy)
    assert legacy_receipt.amendment_request is None


def test_event_type_scope_amendment_members_added_and_round_trip():
    """O7: both new EventType members exist, are distinct from every
    pre-existing member and each other, and round-trip through Event's
    existing generic serde (Event._FIELD_TYPES maps 'type': EventType with no
    special-casing per member -- a new member needs zero new serde code)."""
    assert EventType.SCOPE_AMENDMENT_REQUESTED.value == "SCOPE_AMENDMENT_REQUESTED"
    assert EventType.SCOPE_AMENDMENT_APPROVED.value == "SCOPE_AMENDMENT_APPROVED"
    assert EventType.SCOPE_AMENDMENT_REQUESTED is not EventType.TASK_BLOCKED
    assert EventType.SCOPE_AMENDMENT_APPROVED is not EventType.SCOPE_AMENDMENT_REQUESTED

    ev = Event(
        schema_version=1, sequence=1, timestamp=utc_now(), project="demo",
        actor=Actor(ActorKind.TICK, "test"), type=EventType.SCOPE_AMENDMENT_APPROVED,
        payload={"file": "src/demo/x.py", "reason": "needs a shared helper"},
        task_id="demo-P01-sample",
    )
    d = ev.to_dict()
    assert d["type"] == "SCOPE_AMENDMENT_APPROVED"
    back = Event.from_dict(d)
    assert back.type is EventType.SCOPE_AMENDMENT_APPROVED
    assert back.payload == {"file": "src/demo/x.py", "reason": "needs a shared helper"}


# ============================================================================
# CR-07d 2026-08-04: DRAFT removed as a constructible TaskState. The mirror of
# TestLegacyRoleValue / TestAttemptFromDict (tests/test_review_independent_
# rename.py) for TaskState._missing_ -- same enum machinery, same read-compat
# contract, different concrete deserialization path (TaskStateFile.from_dict
# rather than Attempt.from_dict).
# ============================================================================

class TestLegacyDraftValue:
    """The _missing_ classmethod on TaskState maps the old serialized value."""

    def test_legacy_value_maps_to_needs_decision(self):
        assert TaskState("DRAFT") is TaskState.NEEDS_DECISION

    def test_current_value_maps_to_itself(self):
        assert TaskState("NEEDS_DECISION") is TaskState.NEEDS_DECISION

    def test_nonexistent_value_raises(self):
        with pytest.raises(ValueError):
            TaskState("not-a-real-state")


class TestTaskStateFileFromDict:
    """O7-shaped end-to-end proof: a pre-CR-07d statefile with a literal
    "DRAFT" state loads without raising, and lands on NEEDS_DECISION rather
    than a value nobody chose."""

    def test_a_legacy_draft_statefile_loads_as_needs_decision(self):
        d = {
            "schema_version": 1, "task_id": "t-1", "project": "demo",
            "state": "DRAFT", "since": utc_now().isoformat(),
        }
        tsf = TaskStateFile.from_dict(d)
        assert tsf.state is TaskState.NEEDS_DECISION

    def test_a_current_statefile_round_trips_unchanged(self):
        d = {
            "schema_version": 1, "task_id": "t-1", "project": "demo",
            "state": "QUEUED", "since": utc_now().isoformat(),
        }
        tsf = TaskStateFile.from_dict(d)
        assert tsf.state is TaskState.QUEUED
        assert tsf.to_dict()["state"] == "QUEUED"
