"""Tests for nyxloom.decision_chat (P18: decision-chat bridge).

Cross-package seams (adapters.build_dispatch/build_resume) are monkeypatched
per the P18 handoff's own test strategy, using the SAME record-argv/emit
shell-script convention test_adapters.py already establishes (a script that
`echo "$@" > "$RECORD_FILE"` then `cat "$EMIT_FILE"`), so decision_chat's
real subprocess-execution path runs for real against a canned CLI.

DEVIATION NOTE (see decision_chat.py's own module docstring for the full
account): the whole bridge runs over cfg.notify.cmd_topic (P12's existing
feedback-channel field), NOT a new decision_topic -- the 2-channel design
in nyxloom-trove/nyxloom.toml unifies them. Tests below therefore configure
cfg.notify.cmd_topic (never a decision_topic field, which does not exist).
"""

from __future__ import annotations

import json
import logging
import textwrap

import pytest
import structlog.contextvars

from nyxloom import (
    adapters, commands, control_auth, decision_chat, decisions, log, notify, paths,
    storage,
)
from nyxloom.config import load_registry
from nyxloom.types import Actor, ActorKind, EventType

# CR-15: `advance_chat` now REQUIRES the authenticated identity that drove the
# turn -- there is no unattributed answer to a human decision. These direct
# unit calls stand in for the HTTP endpoint's authenticated operator.
TEST_OPERATOR = Actor(ActorKind.OPERATOR, "test-operator")
CHANNEL_OPERATOR = "phone-operator"


@pytest.fixture()
def channel_operator(monkeypatch) -> str:
    """Name the operator the ntfy feedback topic is bound to (CR-15).

    Without it the channel's mutating routes are CLOSED. monkeypatch owns the
    variable so the process env is restored at teardown."""
    monkeypatch.setenv(control_auth.CHANNEL_OPERATOR_ENV, CHANNEL_OPERATOR)
    return CHANNEL_OPERATOR


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """PACKAGE P05c safety net -- see test_backlog_items.py's copy of this
    fixture for the full rationale (byte-unchanged CLI oracle,
    docs/plan-logging.md P05c)."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py)

ROUTES_TOML_WITH_REVIEW = textwrap.dedent("""\
    revision = "test-rev"

    [tiers.flash-high]
    routes = ["fake-cli"]

    [tiers.frontier-review]
    routes = ["decision-agent-route"]

    [routes.fake-cli]
    cli = "fake"
    model = "fake-model"
    probe = ["true"]
    usage_source = "none"

    [routes.decision-agent-route]
    cli = "claude"
    model = "claude-test-model"
    """)

ROUTES_TOML_NO_REVIEW = textwrap.dedent("""\
    revision = "test-rev"

    [tiers.flash-high]
    routes = ["fake-cli"]

    [routes.fake-cli]
    cli = "fake"
    model = "fake-model"
    probe = ["true"]
    usage_source = "none"
    """)


def _write_inbox(root, decision_id: str, status: str, question: str = "Ratify the launch bar?") -> None:
    (root / "docs" / "DECISIONS-INBOX.md").write_text(
        "# Decisions inbox\n\n"
        "Preamble prose that parsers must ignore.\n\n"
        "---\n\n"
        f"## {decision_id} · 2026-07-16 · test session · {status}\n\n"
        f"**Question:** {question}\n\n"
        f'**Resume prompt:** "Discuss {decision_id} in docs/DECISIONS-INBOX.md."\n\n'
        "---\n",
        encoding="utf-8",
    )


def _record_and_emit_script(tmp_path):
    script = tmp_path / "record_and_emit.sh"
    script.write_text('#!/bin/sh\necho "$@" > "$RECORD_FILE"\ncat "$EMIT_FILE"\n')
    script.chmod(0o755)
    return script


def _use_review_routes() -> None:
    paths.routes_path().write_text(ROUTES_TOML_WITH_REVIEW, encoding="utf-8")


# ==========================================================================
# Oracle 1 + Oracle 2: first reply launches, second reply resumes + finalizes
# ==========================================================================

def test_first_reply_launches_agent_and_captures_session(sample_project, tmp_path, monkeypatch):
    """Oracle 1 (first half): DECISION_OPENED already recorded (an OPEN
    entry is enough); the first user reply builds argv via
    adapters.build_dispatch, runs it, and captures the session id from the
    stream-json-shaped first log line."""
    cfg = sample_project
    _use_review_routes()
    _write_inbox(cfg.root, "D-001", "OPEN")

    script = _record_and_emit_script(tmp_path)
    emit_file = tmp_path / "emit1.txt"
    emit_file.write_text('{"session_id": "sess-abc"}\nSure, let me look at the pointers.\n')
    record_file = tmp_path / "record1.txt"
    monkeypatch.setenv("EMIT_FILE", str(emit_file))
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    calls = {"build_dispatch": [], "build_resume": []}

    def fake_build_dispatch(route, *, handoff_path, worktree, branch, task_id, gate_hint, receipt_path):
        calls["build_dispatch"].append({"route": route.route_id, "task_id": task_id})
        return [str(script)], "prompt"

    def fake_build_resume(route, *, session, worktree, prompt):
        calls["build_resume"].append({"route": route.route_id, "session": session, "prompt": prompt})
        return [str(script)]

    monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)
    monkeypatch.setattr(adapters, "build_resume", fake_build_resume)

    reply = decision_chat.advance_chat(cfg, "demo", "D-001", "please discuss",
                                      actor=TEST_OPERATOR)

    assert len(calls["build_dispatch"]) == 1
    assert calls["build_dispatch"][0]["route"] == "decision-agent-route"
    assert len(calls["build_resume"]) == 0
    assert reply == "Sure, let me look at the pointers."

    chat = decision_chat.load_chat("demo", "D-001")
    assert chat is not None
    assert chat.session_id == "sess-abc"
    assert [m.role for m in chat.transcript] == ["user", "agent"]
    assert chat.transcript[0].text == "please discuss"
    assert chat.transcript[1].text == "Sure, let me look at the pointers."

    # Oracle 3 (tool allowlist): the FINAL argv actually invoked (recorded
    # by the script) carries the read-only policy, excludes Edit/Write/Bash.
    recorded = record_file.read_text(encoding="utf-8")
    assert "--allowedTools" in recorded
    assert "Read Grep Glob" in recorded
    assert "--disallowedTools" in recorded
    assert "Edit Write Bash" in recorded
    assert "--append-system-prompt" in recorded


def test_second_reply_resumes_session_and_finalizes_decision(sample_project, tmp_path, monkeypatch):
    """Oracle 1 (second half) + Oracle 2: a second reply RESUMES the
    existing session (adapters.build_resume, not a relaunch), and when the
    agent's reply carries a DECISION: line, decisions.decide() is called,
    a DECISION_RESOLVED event is appended, and the inbox flips to DECIDED
    (releasing any depends_on: [D-001] holds on the next reconcile pass)."""
    cfg = sample_project
    _use_review_routes()
    _write_inbox(cfg.root, "D-001", "DISCUSSING")

    # Pre-seed an established chat (as if a first turn already happened).
    chat = decision_chat.DecisionChat(decision_id="D-001", project="demo",
                                       session_id="sess-abc", route_id="decision-agent-route")
    chat.transcript.append(decision_chat.DecisionChatMessage(
        role="user", text="please discuss", ts="2026-07-16T00:00:00+00:00"))
    chat.transcript.append(decision_chat.DecisionChatMessage(
        role="agent", text="Sure, let me look.", ts="2026-07-16T00:00:01+00:00"))
    decision_chat.save_chat(chat)

    script = _record_and_emit_script(tmp_path)
    emit_file = tmp_path / "emit2.txt"
    emit_file.write_text('{"session_id": "sess-abc"}\nDECISION: option-b — go with the tightened bar\n')
    record_file = tmp_path / "record2.txt"
    monkeypatch.setenv("EMIT_FILE", str(emit_file))
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    calls = {"build_dispatch": [], "build_resume": []}

    def fake_build_dispatch(route, **kw):
        calls["build_dispatch"].append(kw)
        return [str(script)], "prompt"

    def fake_build_resume(route, *, session, worktree, prompt):
        calls["build_resume"].append({"session": session, "prompt": prompt})
        return [str(script)]

    monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)
    monkeypatch.setattr(adapters, "build_resume", fake_build_resume)

    reply = decision_chat.advance_chat(cfg, "demo", "D-001", "let's go with option b",
                                      actor=TEST_OPERATOR)

    # RESUMED, not relaunched.
    assert len(calls["build_dispatch"]) == 0
    assert len(calls["build_resume"]) == 1
    assert calls["build_resume"][0]["session"] == "sess-abc"
    assert calls["build_resume"][0]["prompt"] == "let's go with option b"
    assert reply == "DECISION: option-b — go with the tightened bar"

    # Finalization.
    parsed = decisions.parse_inbox((cfg.root / cfg.decisions_inbox).read_text(encoding="utf-8"))
    d = next(x for x in parsed if x.id == "D-001")
    assert d.status == "DECIDED"
    assert "option-b" in d.decided_note

    events = list(storage.iter_events("demo"))
    resolved = [e for e in events if e.type is EventType.DECISION_RESOLVED and e.decision_id == "D-001"]
    assert len(resolved) == 1


def test_no_review_route_configured_degrades_to_typed_reply(sample_project, monkeypatch):
    """Negative case: no 'frontier-review' route -> a fixed, typed reply
    (never a crash), no adapters call attempted."""
    cfg = sample_project
    paths.routes_path().write_text(ROUTES_TOML_NO_REVIEW, encoding="utf-8")
    _write_inbox(cfg.root, "D-005", "OPEN")

    def boom(*a, **kw):
        raise AssertionError("adapters.build_dispatch must not be called without a route")

    monkeypatch.setattr(adapters, "build_dispatch", boom)

    reply = decision_chat.advance_chat(cfg, "demo", "D-005", "hello",
                                      actor=TEST_OPERATOR)
    assert "frontier-review" in reply
    chat = decision_chat.load_chat("demo", "D-005")
    assert chat is not None
    assert chat.session_id is None


# ==========================================================================
# Oracle 3: redaction (the sanctioned free-text exception is still redacted)
# ==========================================================================

def test_reply_redacted_before_posting_and_storing(sample_project, tmp_path, monkeypatch):
    cfg = sample_project
    _use_review_routes()
    _write_inbox(cfg.root, "D-070", "OPEN")

    script = _record_and_emit_script(tmp_path)
    emit_file = tmp_path / "emit-secret.txt"
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
    emit_file.write_text(f'{{"session_id": "sess-secret"}}\nhere is a token: {secret} done\n')
    record_file = tmp_path / "record-secret.txt"
    monkeypatch.setenv("EMIT_FILE", str(emit_file))
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    monkeypatch.setattr(adapters, "build_dispatch", lambda route, **kw: ([str(script)], "prompt"))

    reply = decision_chat.advance_chat(cfg, "demo", "D-070", "any secrets?",
                                      actor=TEST_OPERATOR)

    assert secret not in reply
    assert "[REDACTED]" in reply

    chat = decision_chat.load_chat("demo", "D-070")
    assert secret not in chat.transcript[-1].text


# ==========================================================================
# Router: loop guard + D-id prefix + decide-command + bare-text ambiguity
# ==========================================================================

def test_loop_guard_ignores_own_tag_and_reply_tag(sample_project, monkeypatch):
    calls = []
    monkeypatch.setattr(decision_chat, "advance_chat",
                        lambda *a, **kw: calls.append(a))

    registry = load_registry()
    assert decision_chat.handle_feedback_message(registry, "D-001: hi", [decision_chat.DECISION_AGENT_TAG]) is None
    assert decision_chat.handle_feedback_message(registry, "D-001: hi", ["nyxloomd-reply"]) is None
    assert calls == []


def test_wrap_command_handler_routes_decision_prefix_and_falls_through(
        sample_project, monkeypatch, channel_operator):
    cfg = sample_project
    _write_inbox(cfg.root, "D-010", "OPEN")
    registry = load_registry()

    calls = []
    monkeypatch.setattr(decision_chat, "advance_chat",
                         lambda cfg_, project, decision_id, text, *, actor:
                         calls.append((project, decision_id, text, actor)))

    base_calls = []

    def base_handler(text, tags):
        base_calls.append(text)
        return "base-reply"

    wrapped = decision_chat.wrap_command_handler(registry, base_handler)

    # Decision-shaped -> handled here, base handler never runs.
    result = wrapped("D-010: let's talk", [])
    assert result is None
    # CR-15: the named channel operator, not the transport name "feedback-chat".
    assert calls == [("demo", "D-010", "let's talk",
                      Actor(ActorKind.OPERATOR, channel_operator))]
    assert base_calls == []

    # A verb command -> falls through untouched.
    calls.clear()
    result2 = wrapped("status demo", [])
    assert result2 == "base-reply"
    assert base_calls == ["status demo"]
    assert calls == []

    # Unknown D-id (not OPEN/DISCUSSING anywhere) -> also falls through.
    result3 = wrapped("D-999: nobody home", [])
    assert result3 == "base-reply"
    assert calls == []


def test_decide_command_finalizes_via_feedback_channel(sample_project, monkeypatch,
                                                        channel_operator):
    cfg = sample_project
    _write_inbox(cfg.root, "D-020", "OPEN")
    registry = load_registry()

    monkeypatch.setattr(notify, "send", lambda nc, note: (True, "ok"))

    result = decision_chat.handle_feedback_message(registry, "decide D-020 option-a", [])
    assert result is None

    parsed = decisions.parse_inbox((cfg.root / cfg.decisions_inbox).read_text(encoding="utf-8"))
    d = next(x for x in parsed if x.id == "D-020")
    assert d.status == "DECIDED"

    events = list(storage.iter_events("demo"))
    resolved = [e for e in events
                if e.type is EventType.DECISION_RESOLVED and e.decision_id == "D-020"]
    assert len(resolved) == 1
    # CR-15: "who answered this decision" resolves to the named operator.
    assert resolved[0].actor == Actor(ActorKind.OPERATOR, channel_operator)
    # ...and the human-readable inbox records the same authority, so the two
    # durable answers to "who decided this" cannot drift apart.
    inbox = (cfg.root / cfg.decisions_inbox).read_text(encoding="utf-8")
    assert f"**Decision ({channel_operator}," in inbox


def test_bare_text_routes_only_when_exactly_one_chat_active(sample_project, monkeypatch,
                                                             channel_operator):
    cfg = sample_project
    _write_inbox(cfg.root, "D-030", "OPEN")
    registry = load_registry()

    calls = []
    monkeypatch.setattr(decision_chat, "advance_chat",
                         lambda cfg_, project, decision_id, text, *, actor:
                         calls.append((project, decision_id, text, actor)))

    def base_handler(text, tags):
        return "base-reply"

    wrapped = decision_chat.wrap_command_handler(registry, base_handler)

    # No active chat yet (no session_id captured) -> falls through.
    assert wrapped("just some text", []) == "base-reply"
    assert calls == []

    # Seed an active chat (has a session already).
    chat = decision_chat.DecisionChat(decision_id="D-030", project="demo", session_id="sess-x")
    decision_chat.save_chat(chat)

    result = wrapped("go ahead with it", [])
    assert result is None
    assert calls == [("demo", "D-030", "go ahead with it",
                      Actor(ActorKind.OPERATOR, channel_operator))]


# ==========================================================================
# CR-15 (RISK-005): the feedback channel answers HUMAN DECISIONS, and it
# carries no verified sender identity. Closed by default.
# ==========================================================================

def _control_refusals() -> list:
    return [e for e in storage.iter_events(control_auth.CONTROL_LEDGER_PROJECT)
            if e.type is EventType.CONTROL_MUTATION_REFUSED]


@pytest.mark.parametrize("message,ingress", [
    ("decide D-040 option-a", "ntfy:decide"),
    ("D-040: let's talk", "ntfy:decision-chat"),
])
def test_channel_decision_routes_are_closed_and_audited_by_default(
        tmp_state, sample_project, monkeypatch, message, ingress):
    """Anything that can publish to this topic could otherwise supply the
    human's answer -- the exact exposure RISK-005 names. Without a named
    channel operator: the decision stays OPEN, no event is appended, no agent
    turn is launched, the reply is the fixed refusal, and one refusal is
    audited."""
    cfg = sample_project
    _write_inbox(cfg.root, "D-040", "OPEN")
    registry = load_registry()
    monkeypatch.setattr(decision_chat, "advance_chat",
                        lambda *a, **kw: pytest.fail("a closed channel ran a turn"))

    result = decision_chat.handle_feedback_message(registry, message, [])

    assert result == commands.CHANNEL_CLOSED_REPLY
    parsed = decisions.parse_inbox(
        (cfg.root / cfg.decisions_inbox).read_text(encoding="utf-8"))
    assert next(x for x in parsed if x.id == "D-040").status == "OPEN"
    assert list(storage.iter_events("demo")) == []

    refusals = _control_refusals()
    assert len(refusals) == 1
    assert refusals[0].payload == {"path": ingress,
                                   "reason": "no-named-channel-operator"}
    assert refusals[0].actor.id == control_auth.UNAUTHENTICATED_ACTOR_ID


def test_refused_channel_decision_cannot_distinguish_a_real_id_from_a_fake(
        tmp_state, sample_project):
    """The refusal precedes `find_project_for_decision`, so the channel is not
    an oracle for which decisions are open -- the same property the HTTP 401
    has, proved the same way: identical replies AND identical audit records."""
    cfg = sample_project
    _write_inbox(cfg.root, "D-050", "OPEN")
    registry = load_registry()

    real = decision_chat.handle_feedback_message(registry, "decide D-050 yes", [])
    fake = decision_chat.handle_feedback_message(registry, "decide D-999 yes", [])

    assert real == fake == commands.CHANNEL_CLOSED_REPLY
    payloads = [e.payload for e in _control_refusals()]
    assert payloads == [{"path": "ntfy:decide",
                         "reason": "no-named-channel-operator"}] * 2
    ledger_text = json.dumps(
        [e.to_dict() for e in
         storage.iter_events(control_auth.CONTROL_LEDGER_PROJECT)])
    assert "D-050" not in ledger_text
    assert "D-999" not in ledger_text


def test_closed_channel_bare_text_falls_through_without_a_state_lookup(
        tmp_state, sample_project, monkeypatch):
    """Bare text can only mutate via the sole-active-chat lookup, so a closed
    channel must not perform that lookup at all: it falls through to the verb
    dispatch, and audits nothing (an ordinary chat line is not evidence that
    anyone attempted a mutation)."""
    _write_inbox(sample_project.root, "D-060", "OPEN")
    registry = load_registry()
    decision_chat.save_chat(decision_chat.DecisionChat(
        decision_id="D-060", project="demo", session_id="sess-x"))
    monkeypatch.setattr(decision_chat, "_find_sole_active_chat",
                        lambda reg: pytest.fail("closed channel looked up state"))
    monkeypatch.setattr(decision_chat, "advance_chat",
                        lambda *a, **kw: pytest.fail("a closed channel ran a turn"))

    result = decision_chat.handle_feedback_message(registry, "go ahead with it", [])

    assert result is decision_chat._NOT_HANDLED
    assert not _control_refusals()


def test_an_unusable_channel_identity_keeps_the_decision_route_closed(
        tmp_state, sample_project, monkeypatch):
    """An unusable value must not degrade to a default identity: that would
    reopen the ingress and attribute a human decision to a name nobody chose."""
    monkeypatch.setenv(control_auth.CHANNEL_OPERATOR_ENV, "not a valid id")
    _write_inbox(sample_project.root, "D-070", "OPEN")
    registry = load_registry()

    assert decision_chat.handle_feedback_message(
        registry, "decide D-070 option-a", []) == commands.CHANNEL_CLOSED_REPLY
    parsed = decisions.parse_inbox(
        (sample_project.root / sample_project.decisions_inbox).read_text(encoding="utf-8"))
    assert next(x for x in parsed if x.id == "D-070").status == "OPEN"


# ==========================================================================
# Outbound pushes: typed-fields-only vs. the sanctioned free-text exception
# ==========================================================================

def test_notify_decision_opened_uses_typed_fields_only(sample_project, monkeypatch):
    cfg = sample_project
    cfg.notify.ntfy_url = "http://fake-ntfy.example"
    cfg.notify.cmd_topic = "feedback"

    sent = []
    monkeypatch.setattr(notify, "send", lambda nc, note: sent.append(note) or (True, "ok"))

    decision_chat.notify_decision_opened(cfg, "D-002")

    assert len(sent) == 1
    note = sent[0]
    assert "D-002" in note["title"]
    assert "D-002" in note["body"]
    assert note["tags"] == ["decision"]


def test_post_feedback_carries_free_text_with_loop_guard_tag(sample_project, monkeypatch):
    cfg = sample_project
    cfg.notify.ntfy_url = "http://fake-ntfy.example"
    cfg.notify.cmd_topic = "feedback"

    sent = []
    monkeypatch.setattr(notify, "send", lambda nc, note: sent.append((nc, note)) or (True, "ok"))

    decision_chat._post_feedback(cfg, "D-001", "free text reply body")

    assert len(sent) == 1
    nc, note = sent[0]
    assert nc.ntfy_topic == "feedback"
    assert note["body"] == "free text reply body"
    assert note["tags"] == [decision_chat.DECISION_AGENT_TAG]


def test_find_project_for_decision_unknown_returns_none(sample_project):
    registry = load_registry()
    assert decision_chat.find_project_for_decision(registry, "D-does-not-exist") is None


# ==========================================================================
# PACKAGE P05c (docs/plan-logging.md, logging sweep): a failed notify send
# is silently swallowed (`except Exception: pass`, unchanged) -- this
# rubric-matches §5's WARNING tier ("degraded-but-continuing... a fallback
# taken") and must never propagate. Direct coverage of that branch (the
# existing tests above only exercise the success path).
# ==========================================================================

def test_notify_decision_opened_swallows_send_failure(sample_project, monkeypatch):
    cfg = sample_project
    cfg.notify.ntfy_url = "http://fake-ntfy.example"
    cfg.notify.cmd_topic = "feedback"

    def _raise(nc, note):
        raise RuntimeError("ntfy unreachable")

    monkeypatch.setattr(notify, "send", _raise)

    # Must not raise -- a failed notify push degrades silently (WARNING-
    # logged, per §5), never sinks the caller.
    decision_chat.notify_decision_opened(cfg, "D-002")


def test_post_feedback_swallows_send_failure(sample_project, monkeypatch):
    cfg = sample_project
    cfg.notify.ntfy_url = "http://fake-ntfy.example"
    cfg.notify.cmd_topic = "feedback"

    def _raise(nc, note):
        raise RuntimeError("ntfy unreachable")

    monkeypatch.setattr(notify, "send", _raise)

    decision_chat._post_feedback(cfg, "D-001", "free text reply body")
