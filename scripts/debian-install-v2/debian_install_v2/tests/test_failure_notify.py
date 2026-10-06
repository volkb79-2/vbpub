"""LT-S2 F2: the OnFailure notifier is stdlib-only, records state, never leaks secrets."""

from __future__ import annotations

import ast
import json
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from debian_install_v2 import failure_notify
from debian_install_v2.tests.test_bare_host_launch import (
    STAGE2, bare_env, exec_argv, render_units, run_bare, staged,  # noqa: F401  (fixture)
)

PACKAGE = Path(failure_notify.__file__).resolve().parent
SECRET_ID = "abc123secretwebhookid"
TG_TOKEN = "123456789:" + "A" * 35


def module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: relative import"
            names.add(node.module or "")
    return names


@pytest.mark.parametrize("module", ["failure_notify.py", "notify.py"])
def test_notifier_and_notify_are_stdlib_only(module):
    allowed_extra = {"debian_install_v2.notify"} if module == "failure_notify.py" else set()
    for name in module_imports(PACKAGE / module):
        top = name.split(".")[0]
        assert top in sys.stdlib_module_names or name in allowed_extra, f"{module} imports {name}"


class Hook(BaseHTTPRequestHandler):
    bodies: list[bytes] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        Hook.bodies.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def hook_server():
    Hook.bodies = []
    server = HTTPServer(("127.0.0.1", 0), Hook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/hooks/{SECRET_ID}"
    server.shutdown()
    thread.join(timeout=5)


def fake_journalctl(tmp_path: Path, hook_url: str) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "journalctl"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'Traceback (most recent call last):'\n"
        "echo \"ModuleNotFoundError: No module named 'cli_extended'\"\n"
        f"echo 'posting to {hook_url} with {TG_TOKEN}'\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def seed_state(state_dir: Path, **config) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "state.json"
    path.write_text(json.dumps({
        "schema_version": 1, "run_id": "deadbeefcafef00d", "phase": "stage1", "status": "running",
        "config": {"notify_host_label": "lt-host", **config}, "steps": {},
    }), encoding="utf-8")
    return path


def test_bare_host_failure_notification_end_to_end(staged, tmp_path, hook_server):  # noqa: F811
    state_dir = tmp_path / "state"
    state_path = seed_state(state_dir)
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "mattermost_webhook_url").write_text(hook_server + "\n")
    units, _ = render_units(tmp_path, staged, state_dir=str(state_dir))
    argv = exec_argv(units["vbpub-bootstrap-failed@.service"])
    env = bare_env(
        tmp_path, PATH=str(fake_journalctl(tmp_path, hook_server)),
        VBPUB_STATE_DIR=str(state_dir), CREDENTIALS_DIRECTORY=str(creds),
    )
    # `-E -S`: no PYTHONPATH, no site-packages -- cli_extended is not importable.
    result = run_bare([argv[0], argv[1], STAGE2], str(staged), env)
    assert result.returncode == 0, result.stderr

    assert len(Hook.bodies) == 1
    text = json.loads(Hook.bodies[0])["text"]
    assert text.startswith("❌ **lt\\-host**") or text.startswith("❌ **lt-host**")
    assert "run `deadbeef`" in text
    assert "stage2" in text
    assert f"{STAGE2} FAILED - see journalctl -u {STAGE2}" in text
    assert "ModuleNotFoundError: No module named 'cli_extended'" in text
    assert "\n```\n" in text and text.endswith("\n```")

    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state["failed_unit"] == STAGE2
    assert state["phase"] == "stage1"  # untouched
    assert "ModuleNotFoundError" in state["failed_journal_tail"]
    assert STAGE2 in state["last_error"]
    assert oct(state_path.stat().st_mode & 0o777) == "0o600"

    everything = text + state_path.read_text() + result.stdout + result.stderr
    assert SECRET_ID not in everything
    assert TG_TOKEN not in everything
    assert "***REDACTED***" in text
    assert "***REDACTED***" in state["failed_journal_tail"]


def test_telegram_backend_uses_credentials_and_thread(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    seed_state(state_dir)
    state = json.loads((state_dir / "state.json").read_text())
    state["telegram_thread_id"] = "77"
    (state_dir / "state.json").write_text(json.dumps(state))
    creds = state_dir / "credentials"
    creds.mkdir()
    (creds / "telegram_bot_token").write_text(TG_TOKEN + "\n")
    (creds / "telegram_chat_id").write_text("42\n")
    sent = {}
    monkeypatch.setattr(failure_notify, "send_telegram", lambda token, chat, text, thread: sent.update(
        token=token, chat=chat, text=text, thread=thread) or True)
    monkeypatch.setattr(failure_notify, "read_journal", lambda unit, secrets: ["boom"])
    assert failure_notify.main([STAGE2], {"VBPUB_STATE_DIR": str(state_dir)}) == 0
    assert sent["token"] == TG_TOKEN and sent["chat"] == "42" and sent["thread"] == "77"
    assert f"{STAGE2} FAILED" in sent["text"]
    assert TG_TOKEN not in sent["text"]


def test_journal_lines_are_redacted_with_known_secrets(tmp_path, monkeypatch):
    bin_dir = fake_journalctl(tmp_path, "https://mm.example.test/hooks/zzz999")
    monkeypatch.setenv("PATH", str(bin_dir))
    lines = failure_notify.read_journal(STAGE2, ("zzz999-custom-secret",))
    joined = "\n".join(lines)
    assert "zzz999" not in joined
    assert TG_TOKEN not in joined
    assert "ModuleNotFoundError" in joined


def test_missing_journalctl_and_state_and_credentials_never_raise(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert failure_notify.main([STAGE2], {"VBPUB_STATE_DIR": str(tmp_path / "nostate")}) == 0
    out = capsys.readouterr().out
    assert "state.json not updated" in out
    assert "no notification credentials" in out


def test_unit_name_is_sanitised(monkeypatch):
    assert failure_notify.safe_unit_name("a b;rm -rf /\n") == "a?b?rm?-rf???"
    assert failure_notify.safe_unit_name("") == "unknown-unit"


def test_failed_post_is_reported_without_the_url(tmp_path, capsys, monkeypatch):
    state_dir = tmp_path / "state"
    seed_state(state_dir)
    creds = tmp_path / "creds"
    creds.mkdir()
    url = f"http://127.0.0.1:1/hooks/{SECRET_ID}"
    (creds / "mattermost_webhook_url").write_text(url)
    monkeypatch.setattr(failure_notify, "read_journal", lambda unit, secrets: ["x"])
    monkeypatch.setattr(failure_notify, "post_webhook", lambda u, t: False)
    rc = failure_notify.main([STAGE2], {"VBPUB_STATE_DIR": str(state_dir), "CREDENTIALS_DIRECTORY": str(creds)})
    captured = capsys.readouterr()
    assert rc == 0
    assert "mattermost notification FAILED" in captured.out
    assert SECRET_ID not in captured.out + captured.err
    assert json.loads((state_dir / "state.json").read_text())["status"] == "failed"
