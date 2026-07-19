"""Tests for PACKAGE P41 (re-carve of the rejected P31): seeding the
carver with a P29 intake brief so a carve of a briefed backlog item loses
none of the interview's pre-researched context.

O1: daemon._carve_source_note_lines(cfg, item_id=...) embeds a GENUINELY
briefed item's detail verbatim, and does NOT for the same item once its
brief is gone (header stripped).

O2: daemon.dispatch_targeted_carve(project, item_id) seeds a carver leg,
through reconcile's real carve-dispatch control flow, with ONLY that one
item's brief -- not the untargeted headroom-refill carve sources, and not
another briefed item's brief.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import SAMPLE_ROUTES_TOML

from nyxloom import (
    adapters, backlog_items, daemon, intake_chat, lint, notify, paths, reconcile,
    render, storage, wrapper,
)
from nyxloom.types import EventType, Role, TaskState


# --------------------------------------------------------------------------
# local helpers / fixtures (never added to conftest.py, per STANDING.md)

ALIGNED_PURPOSE = "ALIGNED-PURPOSE-frobnicate-the-widget-cache-4b7f"
ELICITED_DETAIL = "ELICITED-DETAIL-needs-oauth2-refresh-token-9c21"


@pytest.fixture()
def patch_siblings(monkeypatch):
    """Local twin of test_daemon.py's own fixture (STANDING.md: local
    fixtures never move to conftest.py or get imported across test files)."""
    calls = {"launch_detached": []}

    def fake_probe(route):
        return (True, "ok")

    def fake_build_dispatch(route, *, handoff_path, worktree, branch, task_id, gate_hint,
                             receipt_path, **_kw):
        # P44 2026-07-19: **_kw absorbs the new role=/carve_authority= kwargs
        # the daemon.py CARVER call site now passes explicitly (role-scoped
        # prompt text) -- this fake only records argv, not prompt text.
        return ["fake-cli", "--task", task_id], "prompt"

    def fake_launch_detached(spec):
        calls["launch_detached"].append(spec)
        Path(spec.attempt_dir).mkdir(parents=True, exist_ok=True)
        return 4242

    monkeypatch.setattr(adapters, "probe", fake_probe)
    monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)
    monkeypatch.setattr(wrapper, "launch_detached", fake_launch_detached)
    monkeypatch.setattr(render, "render_after_event", lambda registry: paths.www_dir())
    monkeypatch.setattr(notify, "notify_event", lambda cfg, states, ev: None)
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    return calls


def _backlog_path(cfg) -> Path:
    return backlog_items.resolve_path(cfg)


# ==========================================================================
# O1: _carve_source_note_lines(cfg, item_id=...) embeds a briefed item's
# detail; the SAME item without a brief does not.
# ==========================================================================

def test_targeted_source_notes_include_genuinely_briefed_items_detail(tmp_state, sample_project):
    cfg = sample_project
    path = _backlog_path(cfg)
    # NOTE: the D-NNN link and the priority are deliberately NOT written into
    # the detail prose -- that is not where P29 puts them (see
    # test_real_p29_brief_round_trips_...). They must reach the carver from
    # the header tokens or not at all.
    detail = f"{ALIGNED_PURPOSE}\n{ELICITED_DETAIL}"
    item_id = backlog_items.create(path, "widget cache frobnicator", detail,
                                    priority=2, decisions=["D-042"])

    d = daemon.Daemon({"demo": cfg.root})
    lines = d._carve_source_note_lines(cfg, item_id=item_id)
    joined = "\n".join(lines)

    assert ALIGNED_PURPOSE in joined
    assert ELICITED_DETAIL in joined
    assert "D-042" in joined
    assert "priority: 2" in joined


def test_real_p29_brief_round_trips_priority_and_decisions_to_the_carver(
        tmp_state, sample_project):
    """O1 end-to-end through the REAL P29 shape, not a hand-built one.

    intake_chat._parse_brief splits `Priority:`/`Decisions:` OUT of the free
    prose into their own fields, and backlog_items.create() persists them as
    header tokens -- so item.detail provably never carries them. A test that
    hand-writes "Linked D-042." into the detail string asserts only that
    detail round-trips, and would still pass if the carver never saw the
    priority or the linked decision at all. Drive the real parser instead."""
    reply = (
        "BRIEF: widget cache frobnicator\n"
        "Priority: 2\n"
        "Decisions: D-042, D-043\n"
        f"Detail: {ALIGNED_PURPOSE}\n"
        f"{ELICITED_DETAIL}\n"
    )
    parsed = intake_chat._parse_brief(reply)
    # Pin the premise: the parser really does keep these out of the prose.
    assert parsed.priority == 2
    assert parsed.decisions == ["D-042", "D-043"]
    assert "D-042" not in parsed.detail
    assert "Priority" not in parsed.detail

    cfg = sample_project
    item_id = backlog_items.create(_backlog_path(cfg), parsed.title, parsed.detail,
                                    priority=parsed.priority, decisions=parsed.decisions)

    d = daemon.Daemon({"demo": cfg.root})
    joined = "\n".join(d._carve_source_note_lines(cfg, item_id=item_id))

    assert ALIGNED_PURPOSE in joined
    assert ELICITED_DETAIL in joined
    # The interview asked the operator for these (steps 4 and 6); a direct
    # carve that drops them is exactly the context loss P41 closes.
    assert "priority: 2" in joined
    assert "D-042" in joined
    assert "D-043" in joined


def test_targeted_source_notes_omit_detail_once_brief_is_gone(tmp_state, sample_project):
    """The SAME item, header stripped (no longer is_briefed): its body
    prose must NOT surface as a brief -- proves this isn't a no-op that
    inlines raw detail regardless of is_briefed."""
    cfg = sample_project
    path = _backlog_path(cfg)
    detail = f"{ALIGNED_PURPOSE}\n{ELICITED_DETAIL}\nLinked D-042."
    item_id = backlog_items.create(path, "widget cache frobnicator", detail,
                                    priority=2, decisions=["D-042"])

    text = path.read_text(encoding="utf-8")
    stripped_lines = [ln for ln in text.splitlines() if "nyxloom:backlog" not in ln]
    path.write_text("\n".join(stripped_lines) + "\n", encoding="utf-8")

    items = backlog_items.parse(path)
    item = next(it for it in items if it.id == item_id)
    assert item.header_line is None
    assert not backlog_items.is_briefed(item)

    d = daemon.Daemon({"demo": cfg.root})
    lines = d._carve_source_note_lines(cfg, item_id=item_id)
    joined = "\n".join(lines)

    assert ALIGNED_PURPOSE not in joined
    assert ELICITED_DETAIL not in joined
    assert "no intake brief" in joined


def test_targeted_source_notes_unknown_item_yields_plain_reference(tmp_state, sample_project):
    cfg = sample_project
    d = daemon.Daemon({"demo": cfg.root})
    lines = d._carve_source_note_lines(cfg, item_id="B999")
    joined = "\n".join(lines)
    assert "B999" in joined
    assert "not found" in joined


def test_untargeted_source_notes_unchanged_no_item_id(tmp_state, sample_project):
    """Backward-compat: the untargeted call (item_id omitted) still just
    points at the conventional backlog/roadmap file paths -- extending the
    function for O1 must not disturb the existing headroom-refill path."""
    cfg = sample_project
    (cfg.root / "nyxloom-trove").mkdir(parents=True, exist_ok=True)
    (cfg.root / "nyxloom-trove" / "backlog.md").write_text("# backlog\n", encoding="utf-8")

    d = daemon.Daemon({"demo": cfg.root})
    lines = d._carve_source_note_lines(cfg)
    joined = "\n".join(lines)
    assert "nyxloom-trove/backlog.md" in joined


# ==========================================================================
# backlog_items.is_briefed / brief_detail -- the header-gate itself (the
# "inverted detail extraction" the first P31 attempt was rejected for).
# ==========================================================================

def test_brief_detail_returns_detail_only_for_a_briefed_item(tmp_state, sample_project):
    cfg = sample_project
    item_id = backlog_items.create(_backlog_path(cfg), "widget cache frobnicator",
                                    f"{ALIGNED_PURPOSE}\n{ELICITED_DETAIL}", priority=2)
    assert backlog_items.brief_detail(cfg, item_id) == f"{ALIGNED_PURPOSE}\n{ELICITED_DETAIL}"


def test_brief_detail_none_for_unknown_item(tmp_state, sample_project):
    assert backlog_items.brief_detail(sample_project, "B999") is None


def test_brief_detail_none_for_unheadered_bullet_with_body_prose(tmp_state, sample_project):
    """The rejection this re-carve exists to avoid: an un-headered legacy
    bullet's continuation prose is ordinary body text, NOT an intake brief,
    no matter how much of it there is -- is_briefed gates on the header."""
    cfg = sample_project
    path = _backlog_path(cfg)
    item_id = backlog_items.create(path, "legacy thing", ALIGNED_PURPOSE)
    text = path.read_text(encoding="utf-8")
    path.write_text("\n".join(ln for ln in text.splitlines()
                              if "nyxloom:backlog" not in ln) + "\n", encoding="utf-8")

    item = next(it for it in backlog_items.parse(path) if it.id == item_id)
    assert item.detail.strip()          # body prose IS present ...
    assert not backlog_items.is_briefed(item)   # ... but it is not a brief.
    assert backlog_items.brief_detail(cfg, item_id) is None


def test_brief_detail_none_for_headered_item_with_no_detail(tmp_state, sample_project):
    cfg = sample_project
    item_id = backlog_items.create(_backlog_path(cfg), "title only", "")
    item = next(it for it in backlog_items.parse(_backlog_path(cfg)) if it.id == item_id)
    assert item.header_line is not None
    assert not backlog_items.is_briefed(item)
    assert backlog_items.brief_detail(cfg, item_id) is None


# ==========================================================================
# O2: dispatch_targeted_carve(project, item_id) -- built through the real
# carve-dispatch control flow (reconcile.CarveDispatch + _execute_carve_
# dispatch), seeded with ONLY the chosen item's brief.
# ==========================================================================

def _configure_frontier_route() -> None:
    paths.routes_path().write_text(
        SAMPLE_ROUTES_TOML + "\n[tiers.frontier-review]\nroutes = [\"fake-cli\"]\n"
    )


def test_dispatch_targeted_carve_seeds_only_the_chosen_items_brief(
        tmp_state, sample_project, patch_siblings):
    cfg = sample_project
    _configure_frontier_route()
    path = _backlog_path(cfg)

    target_detail = f"{ALIGNED_PURPOSE}\n{ELICITED_DETAIL}"
    target_id = backlog_items.create(path, "widget cache frobnicator", target_detail,
                                      priority=2, decisions=["D-042"])

    distractor_marker = "DISTRACTOR-DETAIL-should-never-appear-6e21"
    backlog_items.create(path, "unrelated other feature", distractor_marker)

    d = daemon.Daemon({"demo": cfg.root})
    events = d.dispatch_targeted_carve("demo", target_id)

    created_ev = next(e for e in events if e.type is EventType.ATTEMPT_CREATED)
    assert created_ev.payload["attempt"]["role"] == "carver"
    task_id = created_ev.task_id
    attempt_id = created_ev.attempt_id

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.ACTIVE
    assert tsf.attempts[0].role is Role.CARVER
    assert f"item={target_id}" in (tsf.notes or "")

    packet_md = (paths.attempt_dir("demo", attempt_id) / "packet" / "packet.md").read_text(
        encoding="utf-8")

    assert ALIGNED_PURPOSE in packet_md
    assert ELICITED_DETAIL in packet_md
    assert target_id in packet_md
    # From the header tokens, not the prose -- the full interview context.
    assert "D-042" in packet_md
    assert "priority: 2" in packet_md

    # Only the targeted item's brief -- not the distractor's, and not the
    # untargeted headroom-refill carve's generic source list.
    assert distractor_marker not in packet_md
    assert "Carve sources (v2 SS8)" not in packet_md
    assert "Review-derived follow-ups" not in packet_md


def test_dispatch_targeted_carve_distinct_from_untargeted_carve_dispatch(
        tmp_state, sample_project, patch_siblings, monkeypatch):
    """The untargeted headroom-refill trigger (reconcile.py module contract
    item 9) still produces the generic packet when item_id is None --
    dispatch_targeted_carve is an ADDITIONAL path, not a replacement."""
    cfg = sample_project
    _configure_frontier_route()

    def fake_plan(inp):
        return [reconcile.CarveDispatch(project="demo")]

    monkeypatch.setattr(reconcile, "plan_project", fake_plan)

    d = daemon.Daemon({"demo": cfg.root})
    n = d.run_pass("demo")
    assert n == 1

    task_id = "carve-demo-1"
    tsf = storage.load_state("demo", task_id)
    attempt = tsf.attempts[0]
    packet_md = (paths.attempt_dir("demo", attempt.attempt_id) / "packet" / "packet.md").read_text(
        encoding="utf-8")
    assert "Carve sources (v2 SS8)" in packet_md
    assert "targeted intake brief" not in packet_md


def test_dispatch_targeted_carve_no_frontier_route_pushes_needs_operator(
        tmp_state, sample_project, patch_siblings):
    """Same defense-in-depth as the untargeted path: no frontier-review
    route configured -> typed NEEDS_OPERATOR, no orphaned synthetic task."""
    cfg = sample_project
    path = _backlog_path(cfg)
    item_id = backlog_items.create(path, "widget cache frobnicator", ALIGNED_PURPOSE)

    d = daemon.Daemon({"demo": cfg.root})
    events = d.dispatch_targeted_carve("demo", item_id)

    assert any(e.type is EventType.NEEDS_OPERATOR
               and e.payload.get("reason") == "carve-no-route" for e in events)
    assert not any(e.type is EventType.ATTEMPT_CREATED for e in events)
