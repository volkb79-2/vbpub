"""Tests for the P16 carver-automation output-contract consumption
(handoff/P16-carver-automation.md): the daemon's EmitAttemptExit(role=
CARVER) branch (daemon._consume_carve_exit) and the carve_authority config
endpoint.

Cross-package split: CarveDispatch's own EXECUTION (worktree/branch
selection per carve_authority, packet content) is covered in
test_daemon.py (mirrors test_dispatch_implementer/test_open_wave_and_
launch_review's pattern); the carve TRIGGER itself (module contract item 9
of reconcile.py) is covered in test_reconcile.py. This file is the
carver's REQUIRED OUTPUT CONTRACT parsing + persistence + notification-
injection-boundary concern (oracle 2), plus the carve_authority UI/config
surface (oracle 4's UI half; the config.html render side is in
test_render.py).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nyxloom import daemon, lint, paths, reconcile, storage
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, EventType, Receipt,
    ReceiptResult, Role, Route, TaskState, TaskStateFile, utc_now,
)


# --------------------------------------------------------------------------
# local helpers / fixtures (never added to conftest.py)

CARVE_SUMMARY = {
    "carved": [
        {"id": "demo-P30-new", "why": "close a review-flagged gap", "source_kind": "review"},
    ],
    "review_reflection": (
        "<script>alert('leak')</script> the last wave had one contract defect; "
        "the roadmap's R2 milestone still has ample uncarved surface"
    ),
    "headroom_estimate": 8,
    "headroom_rationale": "roadmap R2/R3 still have ample uncarved surface",
    "outcome": "CANDIDATES_READY",
}


def _scripted(monkeypatch, sequence):
    """monkeypatch reconcile.plan_project to pop one actions-list per call
    (extra calls get []); local twin of test_daemon.py's own helper (per
    STANDING.md, local fixtures never move to conftest.py or get imported
    across test files)."""
    seq = list(sequence)

    def fake(inp):
        if seq:
            return seq.pop(0)
        return []

    monkeypatch.setattr(reconcile, "plan_project", fake)


def _seed_carve_task(project: str, seq: int, worktree: Path) -> tuple[str, str]:
    """A synthetic ACTIVE carve task hosting one RUNNING CARVER attempt --
    mirrors daemon._execute_carve_dispatch's own shape, so EmitAttemptExit's
    role==CARVER branch (_consume_carve_exit) has something real to
    consume. Returns (task_id, attempt_id)."""
    task_id = f"carve-{project}-{seq}"
    attempt_id = f"att-carve-{seq}"
    route = Route(route_id="fake-cli", cli="fake", model="fake-model", routes_rev="test-rev")
    attempt = Attempt(attempt_id=attempt_id, role=Role.CARVER, state=AttemptState.RUNNING,
                       route=route, started=utc_now(), worktree=str(worktree))
    tsf = TaskStateFile(
        schema_version=storage.SCHEMA_VERSION, task_id=task_id, project=project,
        state=TaskState.ACTIVE, since=utc_now(), handoff_path=None, attempts=[attempt],
    )
    storage.append_and_apply(
        project, {}, actor=Actor(ActorKind.OPERATOR, "test"),
        type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return task_id, attempt_id


def _write_carve_report(root: Path, reports_dir: str, seq: int, summary: dict | None) -> None:
    """Write the carver's REQUIRED OUTPUT CONTRACT file. `summary=None`
    writes malformed JSON (parse-failure oracle); omit entirely (don't call
    this) for the missing-file oracle."""
    d = root / reports_dir
    d.mkdir(parents=True, exist_ok=True)
    text = "not valid json {{{" if summary is None else json.dumps(summary)
    (d / f"CARVE-{seq}.md").write_text(text, encoding="utf-8")


def _write_receipt(project: str, attempt_id: str, result: ReceiptResult = ReceiptResult.DONE) -> None:
    d = paths.attempt_dir(project, attempt_id)
    d.mkdir(parents=True, exist_ok=True)
    receipt = Receipt(result=result, exit_code=0)
    (d / "receipt.json").write_text(json.dumps(receipt.to_dict()), encoding="utf-8")


# ==========================================================================
# Oracle 2: summary parse -- fake carver receipt (CarveSummary JSON) ->
# CARVE_OUTCOME with typed fields; reflection persisted to carves/; headroom
# < warn -> SPEC_ATTENTION headroom-low; reflection never in NOTIFICATION_*.
# ==========================================================================

def test_emit_attempt_exit_carver_emits_typed_outcome_and_persists_summary(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, CARVE_SUMMARY)
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))

    outcome_ev = next(e for e in events if e.type is EventType.CARVE_OUTCOME)
    assert outcome_ev.payload == {
        "seq": 1, "carved_ids": ["demo-P30-new"], "outcome": "CANDIDATES_READY",
        "headroom_estimate": 8,
    }
    # The reflection/why/rationale prose never lands in the typed event.
    dumped = json.dumps(outcome_ev.payload)
    assert "review_reflection" not in dumped
    assert "script" not in dumped
    assert "close a review-flagged gap" not in dumped

    persisted = json.loads((paths.project_dir("demo") / "carves" / "1.json").read_text())
    assert persisted["seq"] == 1
    assert "timestamp" in persisted
    assert persisted["review_reflection"] == CARVE_SUMMARY["review_reflection"]
    assert persisted["carved"] == CARVE_SUMMARY["carved"]

    tsf = storage.load_state("demo", task_id)
    assert tsf.state is TaskState.SUPERSEDED

    # headroom_estimate=8 >= default headroom_warn=5, outcome != ROADMAP_EXHAUSTED
    assert not any(e.type is EventType.SPEC_ATTENTION for e in events)

    # carve_authority defaults to 'branch' -> a typed NEEDS_OPERATOR fires.
    needs_op = next(e for e in events if e.type is EventType.NEEDS_OPERATOR)
    assert needs_op.payload == {"reason": "carve-ready", "carved_count": 1, "headroom_estimate": 8}

    # Injection boundary: the reflection text never appears in ANY
    # NOTIFICATION_* event payload (even forcing CARVE_OUTCOME into
    # push_classes below still can't produce one -- notify.py's frozen
    # notification_for() has no CARVE_OUTCOME case at all).
    notif_events = [e for e in events if e.type.value.startswith("NOTIFICATION_")]
    for ev in notif_events:
        payload_text = json.dumps(ev.payload)
        assert "script" not in payload_text
        assert CARVE_SUMMARY["review_reflection"] not in payload_text


def test_real_plan_project_actually_plans_emit_attempt_exit_for_exited_carver(
        tmp_state, sample_project, monkeypatch):
    """P50 2026-07-19 (closes a live incident, distinct from the oracle-2
    test above): every OTHER test in this file uses _scripted to hand-feed
    reconcile.EmitAttemptExit(role=CARVER) directly, proving only that
    _consume_carve_exit is CORRECT once invoked -- never that the real
    reconcile.plan_project, fed by the real daemon._attempt_scan, ever
    ACTUALLY PLANS that action for a real exited carver attempt. It never
    did: _attempt_scan's receipt-inclusion filter checked only (task ACTIVE
    + role IMPLEMENTER) or (task AWAITING_REVIEW + role FRONTIER_REVIEW),
    never (task ACTIVE + role CARVER) -- so a carver's receipt.json was
    silently excluded from ReconcileInput.receipts, has_receipt was always
    False for it, and reconcile.py's own already-written CARVER branch
    (present since P32 2026-07-16) could never fire. Two real synthetic
    carve tasks sat ACTIVE forever in production before this was caught.
    Uses REAL plan_project (no _scripted monkeypatch) with an attempt
    already in EXITED state (as the wrapper itself leaves it on exit,
    before the daemon has consumed it) -- the exact shape that was stuck."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    task_id, attempt_id = _seed_carve_task("demo", 2, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 2, CARVE_SUMMARY)
    _write_receipt("demo", attempt_id)

    states = storage.list_states("demo")
    tsf = states[task_id]
    tsf.attempts[0].state = AttemptState.EXITED
    storage.save_state(tsf)

    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    final = storage.load_state("demo", task_id)
    assert final.state is TaskState.SUPERSEDED, (
        "the real reconcile.plan_project must plan EmitAttemptExit for an "
        "exited carver attempt on its own -- not just when hand-fed"
    )
    assert any(e.type is EventType.CARVE_OUTCOME for e in storage.iter_events("demo"))


def test_carve_outcome_never_produces_a_notification_even_if_forced_into_push_classes(
        tmp_state, sample_project, monkeypatch):
    """Strongest form of the injection-boundary oracle: even a future
    misconfiguration that adds CARVE_OUTCOME to push_classes cannot leak the
    reflection, because notify.py's frozen notification_for() has no
    CARVE_OUTCOME branch (returns None -> notify_event never appends a
    NOTIFICATION_REQUESTED for it at all)."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    cfg.notify.push_classes.append("CARVE_OUTCOME")
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, CARVE_SUMMARY)
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert any(e.type is EventType.CARVE_OUTCOME for e in events)

    # Exactly one NOTIFICATION_REQUESTED -- from the NEEDS_OPERATOR
    # 'carve-ready' event (default carve_authority='branch'), same count as
    # if CARVE_OUTCOME had never been added to push_classes at all: forcing
    # it in changed nothing, because notification_for() has no branch for
    # it (returns None -> notify_event never appends a request for it).
    notif_events = [e for e in events if e.type is EventType.NOTIFICATION_REQUESTED]
    assert len(notif_events) == 1
    assert json.dumps(notif_events[0].payload) == "{}"  # always-empty request payload


def test_emit_attempt_exit_carver_headroom_low_pushes_spec_attention(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    summary = dict(CARVE_SUMMARY, headroom_estimate=2)  # < default headroom_warn=5
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, summary)
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    spec_attns = [e for e in storage.iter_events("demo") if e.type is EventType.SPEC_ATTENTION]
    assert len(spec_attns) == 1
    assert spec_attns[0].payload == {"reason": "headroom-low", "detail": "2 packages left"}


def test_emit_attempt_exit_carver_roadmap_exhausted_pushes_both_spec_attentions(
        tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    summary = dict(CARVE_SUMMARY, outcome="ROADMAP_EXHAUSTED", headroom_estimate=0)
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, summary)
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    reasons = {e.payload["reason"] for e in storage.iter_events("demo")
               if e.type is EventType.SPEC_ATTENTION}
    # 0 < headroom_warn(5) triggers headroom-low TOO -- both fire together.
    assert reasons == {"headroom-low", "roadmap-exhausted"}


def test_emit_attempt_exit_carver_missing_report_pushes_needs_operator_parse_failed(
        tmp_state, sample_project, monkeypatch):
    """No CARVE-<seq>.md at all: not fatal, but no CARVE_OUTCOME either --
    a typed NEEDS_OPERATOR surfaces the failure instead of silently
    vanishing, and the carve slot still frees (SUPERSEDED)."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_receipt("demo", attempt_id)
    # (deliberately no _write_carve_report call)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.CARVE_OUTCOME for e in events)
    needs_op = next(e for e in events if e.type is EventType.NEEDS_OPERATOR)
    assert needs_op.payload == {"reason": "carve-parse-failed", "seq": 1}
    assert storage.load_state("demo", task_id).state is TaskState.SUPERSEDED


def test_emit_attempt_exit_carver_malformed_json_report_pushes_needs_operator_parse_failed(
        tmp_state, sample_project, monkeypatch):
    """A CARVE-<seq>.md that exists but is not valid JSON: same parse-
    failure path as a missing file (never raises)."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, None)  # malformed
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert not any(e.type is EventType.CARVE_OUTCOME for e in events)
    needs_op = next(e for e in events if e.type is EventType.NEEDS_OPERATOR)
    assert needs_op.payload == {"reason": "carve-parse-failed", "seq": 1}


def test_emit_attempt_exit_carver_main_authority_no_needs_operator(
        tmp_state, sample_project, monkeypatch):
    """carve_authority == 'main' (or 'files'): no NEEDS_OPERATOR — the tick
    self-materializes next pass, no human merge required."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    cfg = sample_project
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8").replace(
        "[policy]\n", '[policy]\ncarve_authority = "main"\n', 1)
    ptoml.write_text(text, encoding="utf-8")
    task_id, attempt_id = _seed_carve_task("demo", 1, cfg.root)
    _write_carve_report(cfg.root, cfg.reports_dir, 1, CARVE_SUMMARY)
    _write_receipt("demo", attempt_id)

    _scripted(monkeypatch, [[reconcile.EmitAttemptExit(task_id=task_id, attempt_id=attempt_id)]])
    d = daemon.Daemon({"demo": cfg.root})
    d.run_pass("demo")

    events = list(storage.iter_events("demo"))
    assert any(e.type is EventType.CARVE_OUTCOME for e in events)
    assert not any(e.type is EventType.NEEDS_OPERATOR for e in events)


# ==========================================================================
# Oracle 4 (UI half): carve_authority via POST /api/config/policy (reuses
# P15's config endpoint pattern, per the handoff's own instruction).
# ==========================================================================

def _seed_carve_authority_line(root: Path) -> None:
    """update_project_policy is a surgical single-line editor: it can only
    rewrite a key that already has an explicit anchor line in [policy]
    (same documented constraint P15's own report flagged). Seed one so the
    success-path test below has something to rewrite."""
    ptoml = root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "carve_authority" not in text:
        text = text.replace("[policy]\n", '[policy]\ncarve_authority = "branch"\n', 1)
        ptoml.write_text(text, encoding="utf-8")


def _set_ephemeral_http_port(cfg) -> None:
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "http_port" not in text:
        text = text.replace("[policy]\n", "[policy]\nhttp_port = 0\n", 1)
        ptoml.write_text(text, encoding="utf-8")


@pytest.fixture()
def cfg_daemon(tmp_state, sample_project, monkeypatch):
    """A live Daemon HTTP server over the 'demo' project (mirrors
    test_config_ui.py's own fixture, duplicated locally per STANDING.md's
    'never share test fixtures across files' rule)."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    _seed_carve_authority_line(sample_project.root)
    _set_ephemeral_http_port(sample_project)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert d.http_port != 0
    try:
        yield d
    finally:
        d.stop()
        t.join(timeout=5)


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_post_carve_authority_updates_project_toml_and_emits_config_changed(
        cfg_daemon, sample_project):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"

    status, _resp = _post(base, "/api/config/policy",
                           {"project": "demo", "key": "carve_authority", "value": "main"})
    assert status == 200

    text = (sample_project.root / ".nyxloom" / "project.toml").read_text(encoding="utf-8")
    assert 'carve_authority = "main"' in text

    changed = [e for e in storage.iter_events("demo") if e.type is EventType.CONFIG_CHANGED]
    assert len(changed) == 1
    assert changed[0].payload == {
        "scope": "policy", "key": "carve_authority", "old": "branch", "new": "main"}
    assert changed[0].actor.kind is ActorKind.OPERATOR
    assert changed[0].actor.id == "ui"


def test_post_carve_authority_rejects_unknown_value_no_write_no_event(
        cfg_daemon, sample_project):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    original = (sample_project.root / ".nyxloom" / "project.toml").read_text(encoding="utf-8")

    status, resp = _post(base, "/api/config/policy",
                          {"project": "demo", "key": "carve_authority", "value": "bogus"})
    assert status == 400
    assert "error" in resp
    assert (sample_project.root / ".nyxloom" / "project.toml").read_text(encoding="utf-8") == original
    assert not any(e.type is EventType.CONFIG_CHANGED for e in storage.iter_events("demo"))


def test_post_carve_authority_rejects_non_string_value(cfg_daemon):
    d = cfg_daemon
    base = f"http://127.0.0.1:{d.http_port}"
    status, resp = _post(base, "/api/config/policy",
                          {"project": "demo", "key": "carve_authority", "value": 3})
    assert status == 400
    assert "error" in resp


def test_post_carve_ahead_target_int_key_via_existing_bounds_path(
        tmp_state, sample_project, monkeypatch):
    """carve_ahead_target/headroom_warn are plain int Policy keys, wired
    through the EXISTING numeric _POLICY_BOUNDS path (no special-casing
    needed) -- a quick end-to-end check that they were actually added."""
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
    ptoml = sample_project.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8").replace(
        "[policy]\n", "[policy]\ncarve_ahead_target = 5\nheadroom_warn = 5\n", 1)
    ptoml.write_text(text, encoding="utf-8")
    _set_ephemeral_http_port(sample_project)

    d = daemon.Daemon({"demo": sample_project.root})
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while d.http_port == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        base = f"http://127.0.0.1:{d.http_port}"
        status, _resp = _post(base, "/api/config/policy",
                               {"project": "demo", "key": "carve_ahead_target", "value": 8})
        assert status == 200
        assert "carve_ahead_target = 8" in ptoml.read_text(encoding="utf-8")

        status2, resp2 = _post(base, "/api/config/policy",
                                {"project": "demo", "key": "headroom_warn", "value": 999})
        assert status2 == 400
        assert "error" in resp2
    finally:
        d.stop()
        t.join(timeout=5)
