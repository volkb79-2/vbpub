"""Tests for the P30 intake UI: the Intake dashboard tab (render.py) and its
one sanctioned write path, POST /api/intake (daemon.py). intake_chat.py
itself (P29) is exercised by test_intake_chat.py; here advance_intake is
stubbed so these tests are scoped to the render/HTTP contract only.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from nyxloom import daemon, intake_chat, lint, paths, reconcile, render


# --------------------------------------------------------------------------
# O1: render.py renders an Intake tab -- open conversations + a start form

def test_intake_html_has_start_form_and_open_conversation(sample_project):
    registry = {"demo": sample_project.root}

    chat = intake_chat.IntakeChat(intake_id="intake-abc123", project="demo")
    chat.transcript.append(
        intake_chat.IntakeChatMessage(role="user", text="<b>add dark mode</b>",
                                       ts="2026-07-16T00:00:00+00:00")
    )
    intake_chat.save_chat(chat)

    render.render_all(registry)
    content = (paths.www_dir() / "intake.html").read_text(encoding="utf-8")

    # nav link + tab surface
    assert 'href="intake.html"' in content
    assert 'id="intake"' in content

    # a form to start a request from a rough feature request
    assert "<textarea" in content
    assert "startIntake(" in content

    # the open conversation is listed, html-escaped
    assert "intake-abc123" in content
    assert "&lt;b&gt;add dark mode&lt;/b&gt;" in content
    assert "<b>add dark mode</b>" not in content

    # the reply path
    assert "/api/intake" in content
    assert "sendIntakeReply(" in content


def test_intake_html_omits_finalized_conversation(sample_project):
    """A chat with a brief_id (already finalized) is not an OPEN
    conversation and must not clutter the tab."""
    registry = {"demo": sample_project.root}

    chat = intake_chat.IntakeChat(intake_id="intake-done1", project="demo",
                                   brief_id="BL-001")
    intake_chat.save_chat(chat)

    render.render_all(registry)
    content = (paths.www_dir() / "intake.html").read_text(encoding="utf-8")

    assert "intake-done1" not in content
    assert "No open intake conversations." in content


# --------------------------------------------------------------------------
# O2: POST /api/intake advances a turn via intake_chat.advance_intake

def _set_ephemeral_http_port(cfg):
    ptoml = cfg.root / ".nyxloom" / "project.toml"
    text = ptoml.read_text(encoding="utf-8")
    if "http_port" not in text:
        text = text.replace("[policy]\n", "[policy]\nhttp_port = 0\n", 1)
        ptoml.write_text(text, encoding="utf-8")


@pytest.fixture()
def http_daemon(tmp_state, sample_project, monkeypatch):
    monkeypatch.setattr(lint, "lint_project", lambda cfg: {})
    monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])
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


def test_post_intake_advances_turn_and_echoes_reply(http_daemon, monkeypatch):
    calls = []

    def fake_advance_intake(cfg, project, intake_id, user_text):
        calls.append((project, intake_id, user_text))
        return "understood -- tell me more"

    monkeypatch.setattr(intake_chat, "advance_intake", fake_advance_intake)

    base = f"http://127.0.0.1:{http_daemon.http_port}"

    body = json.dumps({"project": "demo", "text": "add a dark mode toggle"}).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/intake", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=5)
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data["ok"] is True
    assert data["reply"] == "understood -- tell me more"
    assert isinstance(data["intake_id"], str) and data["intake_id"]

    assert len(calls) == 1
    project, intake_id, user_text = calls[0]
    assert project == "demo"
    assert intake_id == data["intake_id"]
    assert user_text == "add a dark mode toggle"

    # a second turn, continuing the SAME conversation
    body2 = json.dumps({"project": "demo", "intake_id": intake_id,
                         "text": "priority: high"}).encode("utf-8")
    req2 = urllib.request.Request(f"{base}/api/intake", data=body2,
                                   headers={"Content-Type": "application/json"}, method="POST")
    resp2 = urllib.request.urlopen(req2, timeout=5)
    assert resp2.status == 200
    assert calls[1] == ("demo", intake_id, "priority: high")


def test_post_intake_unknown_project_404(http_daemon, monkeypatch):
    monkeypatch.setattr(intake_chat, "advance_intake", lambda *a, **k: "x")

    base = f"http://127.0.0.1:{http_daemon.http_port}"
    body = json.dumps({"project": "no-such-project", "text": "hi"}).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/intake", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 404


def test_post_intake_missing_text_400(http_daemon, monkeypatch):
    monkeypatch.setattr(intake_chat, "advance_intake", lambda *a, **k: "x")

    base = f"http://127.0.0.1:{http_daemon.http_port}"
    body = json.dumps({"project": "demo"}).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/intake", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400


def test_post_intake_get_not_allowed(http_daemon):
    base = f"http://127.0.0.1:{http_daemon.http_port}"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/intake", timeout=5)
    assert exc.value.code == 405
