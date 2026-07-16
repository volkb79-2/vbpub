"""Tests for nyxloom.intake_chat (P29: feature-intake agent, backend).

Cross-package seams (adapters.build_dispatch/build_resume) are monkeypatched
using the SAME record-argv/emit shell-script convention test_decision_chat.py
(and test_adapters.py before it) establishes: a script that
`echo "$@" > "$RECORD_FILE"` then `cat "$EMIT_FILE"`, so intake_chat's real
subprocess-execution path runs for real against a canned CLI. No live model
is ever invoked.
"""

from __future__ import annotations

import textwrap

import pytest

from nyxloom import adapters, backlog_items, cli, decisions, intake_chat, paths


# --------------------------------------------------------------------------
# local fixtures / helpers (never added to conftest.py)

ROUTES_TOML_WITH_REVIEW = textwrap.dedent("""\
    revision = "test-rev"

    [tiers.flash-high]
    routes = ["fake-cli"]

    [tiers.frontier-review]
    routes = ["intake-agent-route"]

    [routes.fake-cli]
    cli = "fake"
    model = "fake-model"
    probe = ["true"]
    usage_source = "none"

    [routes.intake-agent-route]
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


def _record_and_emit_script(tmp_path):
    script = tmp_path / "record_and_emit.sh"
    script.write_text('#!/bin/sh\necho "$@" > "$RECORD_FILE"\ncat "$EMIT_FILE"\n')
    script.chmod(0o755)
    return script


def _use_review_routes() -> None:
    paths.routes_path().write_text(ROUTES_TOML_WITH_REVIEW, encoding="utf-8")


def _stub_turn(tmp_path, monkeypatch, reply_text: str, *, session_id: str = "sess-abc",
               tag: str = "turn"):
    """Wire EMIT_FILE/RECORD_FILE for one subprocess turn; returns the
    record_file path (assert on it for argv-shape checks)."""
    script = _record_and_emit_script(tmp_path)
    emit_file = tmp_path / f"emit-{tag}.txt"
    emit_file.write_text(f'{{"session_id": "{session_id}"}}\n{reply_text}\n')
    record_file = tmp_path / f"record-{tag}.txt"
    monkeypatch.setenv("EMIT_FILE", str(emit_file))
    monkeypatch.setenv("RECORD_FILE", str(record_file))
    monkeypatch.setattr(adapters, "build_dispatch",
                         lambda route, **kw: ([str(script)], "prompt"))
    monkeypatch.setattr(adapters, "build_resume",
                         lambda route, **kw: [str(script)])
    return record_file


# ==========================================================================
# Oracle 1: first turn launches a read-only, redacted, context-seeded session
# ==========================================================================

def test_first_turn_launches_readonly_redacted_session_with_context_refs(
        sample_project, tmp_path, monkeypatch):
    cfg = sample_project
    _use_review_routes()

    calls = {"build_dispatch": [], "build_resume": []}
    script = _record_and_emit_script(tmp_path)
    emit_file = tmp_path / "emit1.txt"
    emit_file.write_text('{"session_id": "sess-abc"}\nSure, tell me more about the request.\n')
    record_file = tmp_path / "record1.txt"
    monkeypatch.setenv("EMIT_FILE", str(emit_file))
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    def fake_build_dispatch(route, **kw):
        calls["build_dispatch"].append(kw)
        return [str(script)], "prompt"

    def fake_build_resume(route, **kw):
        calls["build_resume"].append(kw)
        return [str(script)]

    monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)
    monkeypatch.setattr(adapters, "build_resume", fake_build_resume)

    reply = intake_chat.advance_intake(cfg, "demo", "INTAKE-1", "I want a dark mode toggle")

    assert len(calls["build_dispatch"]) == 1
    assert len(calls["build_resume"]) == 0
    assert reply == "Sure, tell me more about the request."

    # Persistence round-trips via save_chat/load_chat under an intake dir.
    chat = intake_chat.load_chat("demo", "INTAKE-1")
    assert chat is not None
    assert chat.session_id == "sess-abc"
    assert [m.role for m in chat.transcript] == ["user", "agent"]

    # READ-ONLY + REDACTED posture: the FINAL argv actually invoked (recorded
    # by the script) carries the read-only policy, excludes Edit/Write/Bash.
    recorded = record_file.read_text(encoding="utf-8")
    assert "--allowedTools" in recorded
    assert "Read Grep Glob" in recorded
    assert "--disallowedTools" in recorded
    assert "Edit Write Bash" in recorded
    assert "--append-system-prompt" in recorded

    # The system prompt names the project context sources to read.
    assert "roadmap.md" in recorded
    assert "backlog.md" in recorded
    assert "nyxloom-trove/handoffs" in recorded
    assert "nyxloom.toml" in recorded

    # ...and it carries the operator's actual request. The first turn is the
    # ONLY place it ever appears (build_dispatch has no free-prose prompt
    # param, and unlike a D-entry there is no on-disk copy to read back), so
    # without this the agent is asked to confirm a request it never saw.
    assert "I want a dark mode toggle" in recorded


def test_first_turn_request_survives_shell_metacharacters(sample_project, tmp_path, monkeypatch):
    """The request is operator prose, not a typed field: it must reach the
    agent verbatim as a single argv element, quoting/newlines and all."""
    cfg = sample_project
    _use_review_routes()
    record_file = _stub_turn(tmp_path, monkeypatch, "Understood.", tag="meta")

    request = "add a \"dark mode\" toggle; it's $HOME-scoped\nand persists per-user"
    intake_chat.advance_intake(cfg, "demo", "INTAKE-7", request)

    prompt = intake_chat._first_turn_system_prompt(cfg, "demo", "INTAKE-7", request)
    assert request in prompt
    # The recorded argv is `echo "$@"`, which flattens newlines to spaces --
    # assert on the distinctive fragments rather than the raw string.
    recorded = record_file.read_text(encoding="utf-8")
    assert '"dark mode"' in recorded
    assert "$HOME-scoped" in recorded


def test_no_review_route_configured_degrades_to_typed_reply(sample_project, monkeypatch):
    """Negative: no 'frontier-review' route -> a fixed, typed reply, no
    adapters call attempted (mirrors decision_chat's own degrade path)."""
    cfg = sample_project
    paths.routes_path().write_text(ROUTES_TOML_NO_REVIEW, encoding="utf-8")

    def boom(*a, **kw):
        raise AssertionError("adapters.build_dispatch must not be called without a route")

    monkeypatch.setattr(adapters, "build_dispatch", boom)

    reply = intake_chat.advance_intake(cfg, "demo", "INTAKE-9", "hello")
    assert "frontier-review" in reply
    chat = intake_chat.load_chat("demo", "INTAKE-9")
    assert chat is not None
    assert chat.session_id is None


def test_reply_redacted_before_storing(sample_project, tmp_path, monkeypatch):
    cfg = sample_project
    _use_review_routes()
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
    _stub_turn(tmp_path, monkeypatch, f"here is a token: {secret} done", tag="secret")

    reply = intake_chat.advance_intake(cfg, "demo", "INTAKE-2", "any secrets in here?")

    assert secret not in reply
    assert "[REDACTED]" in reply
    chat = intake_chat.load_chat("demo", "INTAKE-2")
    assert secret not in chat.transcript[-1].text


# ==========================================================================
# Oracle 2: BRIEF: finalize -> structured backlog item
# ==========================================================================

def test_brief_persists_structured_backlog_item(sample_project, tmp_path, monkeypatch):
    cfg = sample_project
    _use_review_routes()

    brief_reply = (
        "Understood, this is ready to carve.\n\n"
        "BRIEF: Add dark mode toggle\n"
        "Priority: 2\n"
        "Detail:\n"
        "Purpose: reduce eye strain for night users.\n"
        "Elicited detail: a settings-page toggle, persisted per-user.\n"
        "Consequences: new settings key + a CSS theme swap.\n"
    )
    _stub_turn(tmp_path, monkeypatch, brief_reply, tag="brief")

    reply = intake_chat.advance_intake(cfg, "demo", "INTAKE-3", "I want a dark mode toggle")
    assert reply == brief_reply.strip("\n")

    chat = intake_chat.load_chat("demo", "INTAKE-3")
    assert chat is not None
    assert chat.brief_id is not None

    items = backlog_items.parse(backlog_items.resolve_path(cfg))
    item = next(it for it in items if it.id == chat.brief_id)
    assert item.status == "open"
    assert item.priority == 2

    backlog_text = backlog_items.resolve_path(cfg).read_text(encoding="utf-8")
    assert "reduce eye strain" in backlog_text
    assert "settings-page toggle" in backlog_text


def test_brief_past_the_reply_cap_still_finalizes(sample_project, tmp_path, monkeypatch):
    """A finalize turn recaps the interview and THEN emits BRIEF:, so the
    block naturally lands past MAX_REPLY_CHARS. Parsing must see the full
    redacted reply; the cap bounds only what is stored/echoed."""
    cfg = sample_project
    _use_review_routes()

    preamble = ("Thanks -- here is my full understanding of the request "
                "before I finalize it into a brief. ") * 15
    brief_reply = (
        preamble + "\n\n"
        "BRIEF: Add dark mode toggle\n"
        "Priority: 2\n"
        "Detail:\n"
        "Purpose: reduce eye strain for night users.\n"
    )
    assert len(brief_reply) > intake_chat.MAX_REPLY_CHARS  # guard the premise
    _stub_turn(tmp_path, monkeypatch, brief_reply, tag="longbrief")

    reply = intake_chat.advance_intake(cfg, "demo", "INTAKE-8", "I want dark mode")

    chat = intake_chat.load_chat("demo", "INTAKE-8")
    assert chat.brief_id is not None
    items = backlog_items.parse(backlog_items.resolve_path(cfg))
    item = next(it for it in items if it.id == chat.brief_id)
    assert item.status == "open"
    assert item.priority == 2
    assert "reduce eye strain" in backlog_items.resolve_path(cfg).read_text(encoding="utf-8")

    # The stored/echoed reply is still capped.
    assert len(reply) == intake_chat.MAX_REPLY_CHARS
    assert len(chat.transcript[-1].text) == intake_chat.MAX_REPLY_CHARS


def test_product_call_past_the_reply_cap_still_files_decision(sample_project, tmp_path, monkeypatch):
    """Same cap hazard on the PRODUCT_CALL: path -- a product call raised at
    the end of a long turn must not be silently swallowed."""
    cfg = sample_project
    _use_review_routes()

    reply_text = (
        ("Here is the context I gathered from the roadmap and backlog. " * 20) + "\n"
        "PRODUCT_CALL: Should SSO be mandatory at launch? | Discuss SSO enforcement.\n"
    )
    assert len(reply_text) > intake_chat.MAX_REPLY_CHARS
    _stub_turn(tmp_path, monkeypatch, reply_text, tag="longcall")

    intake_chat.advance_intake(cfg, "demo", "INTAKE-10", "we need SSO")

    chat = intake_chat.load_chat("demo", "INTAKE-10")
    assert chat.opened_decisions == ["D-001"]
    parsed = decisions.parse_inbox((cfg.root / cfg.decisions_inbox).read_text(encoding="utf-8"))
    assert any(d.id == "D-001" and d.status == "OPEN" for d in parsed)


# ==========================================================================
# Oracle 3: a genuine product call files a D-NNN and the brief links it
# ==========================================================================

def test_product_call_files_decision_and_brief_links_it(sample_project, tmp_path, monkeypatch):
    cfg = sample_project
    _use_review_routes()

    turn1_reply = (
        "Before I can scope this, one thing only you can decide.\n"
        "PRODUCT_CALL: Should SSO be mandatory or optional at launch? | "
        "Discuss SSO enforcement for the login rework intake.\n"
    )
    _stub_turn(tmp_path, monkeypatch, turn1_reply, session_id="sess-sso", tag="call")

    reply1 = intake_chat.advance_intake(cfg, "demo", "INTAKE-4", "we need SSO login")
    assert "PRODUCT_CALL" in reply1

    chat = intake_chat.load_chat("demo", "INTAKE-4")
    assert chat is not None
    assert chat.opened_decisions == ["D-001"]

    parsed = decisions.parse_inbox((cfg.root / cfg.decisions_inbox).read_text(encoding="utf-8"))
    d = next(x for x in parsed if x.id == "D-001")
    assert d.status == "OPEN"
    assert "SSO" in d.question

    # Second turn: RESUMES (not relaunched) and finalizes with a BRIEF that
    # links the decision opened above.
    turn2_reply = (
        "BRIEF: Add SSO login\n"
        "Decisions: D-001\n"
        "Detail:\n"
        "Purpose: let operators log in via SSO once D-001 is resolved.\n"
    )
    calls = {"build_dispatch": 0, "build_resume": 0}
    script = _record_and_emit_script(tmp_path)
    emit_file = tmp_path / "emit-call2.txt"
    emit_file.write_text('{"session_id": "sess-sso"}\n' + turn2_reply)
    record_file = tmp_path / "record-call2.txt"
    monkeypatch.setenv("EMIT_FILE", str(emit_file))
    monkeypatch.setenv("RECORD_FILE", str(record_file))

    def fake_build_dispatch(route, **kw):
        calls["build_dispatch"] += 1
        return [str(script)], "prompt"

    def fake_build_resume(route, **kw):
        calls["build_resume"] += 1
        return [str(script)]

    monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)
    monkeypatch.setattr(adapters, "build_resume", fake_build_resume)

    intake_chat.advance_intake(cfg, "demo", "INTAKE-4", "mandatory please")

    assert calls["build_dispatch"] == 0
    assert calls["build_resume"] == 1

    chat = intake_chat.load_chat("demo", "INTAKE-4")
    assert chat.brief_id is not None
    items = backlog_items.parse(backlog_items.resolve_path(cfg))
    item = next(it for it in items if it.id == chat.brief_id)
    assert item.decisions == ["D-001"]


def test_brief_falls_back_to_opened_decisions_when_unstated(sample_project, tmp_path, monkeypatch):
    """If the model's BRIEF: block forgets to restate Decisions:, the brief
    still links every D-NNN opened earlier in the SAME chat."""
    cfg = sample_project
    _use_review_routes()

    turn1_reply = "PRODUCT_CALL: Pick a rollout strategy? | Discuss rollout strategy.\n"
    _stub_turn(tmp_path, monkeypatch, turn1_reply, session_id="sess-roll", tag="roll1")
    intake_chat.advance_intake(cfg, "demo", "INTAKE-5", "roll this out")

    turn2_reply = "BRIEF: Staged rollout\nDetail:\nPurpose: ship safely.\n"
    _stub_turn(tmp_path, monkeypatch, turn2_reply, session_id="sess-roll", tag="roll2")
    intake_chat.advance_intake(cfg, "demo", "INTAKE-5", "go ahead")

    chat = intake_chat.load_chat("demo", "INTAKE-5")
    items = backlog_items.parse(backlog_items.resolve_path(cfg))
    item = next(it for it in items if it.id == chat.brief_id)
    assert item.decisions == chat.opened_decisions == ["D-001"]


# ==========================================================================
# Oracle 4: the `intake` CLI verb advances/resumes a turn
# ==========================================================================

def test_cli_intake_verb_starts_then_resumes(sample_project, tmp_state, tmp_path, capsys, monkeypatch):
    cfg = sample_project
    _use_review_routes()

    calls = {"build_dispatch": 0, "build_resume": 0}
    script = _record_and_emit_script(tmp_path)

    def _set_emit(reply_text, session_id, tag):
        emit_file = tmp_path / f"emit-cli-{tag}.txt"
        emit_file.write_text(f'{{"session_id": "{session_id}"}}\n{reply_text}\n')
        monkeypatch.setenv("EMIT_FILE", str(emit_file))
        monkeypatch.setenv("RECORD_FILE", str(tmp_path / f"record-cli-{tag}.txt"))

    def fake_build_dispatch(route, **kw):
        calls["build_dispatch"] += 1
        return [str(script)], "prompt"

    def fake_build_resume(route, **kw):
        calls["build_resume"] += 1
        return [str(script)]

    monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)
    monkeypatch.setattr(adapters, "build_resume", fake_build_resume)

    _set_emit("Hello, tell me more.", "sess-cli", "1")
    exit_code = cli.main(["intake", "demo", "INTAKE-6", "I need a new report page"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Hello, tell me more." in out

    chat = intake_chat.load_chat("demo", "INTAKE-6")
    assert chat is not None
    assert chat.session_id == "sess-cli"
    assert len(chat.transcript) == 2

    _set_emit("Got it, thanks.", "sess-cli", "2")
    exit_code2 = cli.main(["intake", "demo", "INTAKE-6", "more detail here"])
    assert exit_code2 == 0
    out2 = capsys.readouterr().out
    assert "Got it, thanks." in out2

    assert calls["build_dispatch"] == 1
    assert calls["build_resume"] == 1

    chat2 = intake_chat.load_chat("demo", "INTAKE-6")
    assert len(chat2.transcript) == 4
