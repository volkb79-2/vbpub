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


CAUSE = "ModuleNotFoundError: No module named 'cli_extended'"


def fake_journalctl(tmp_path: Path, hook_url: str = "") -> Path:
    """The REAL journal shape (LT-01 host-logs/logs.txt): systemd lines only.

    The unit redirects stdout/stderr to the output file, so the traceback is
    never in the journal. One line carries a planted secret to test redaction.
    """
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "journalctl"
    planted = f"echo 'sd: posting to {hook_url} with {TG_TOKEN}'\n" if hook_url else ""
    script.write_text(
        "#!/bin/sh\n"
        "echo 'systemd[1]: Starting vbpub-bootstrap-stage2.service - vbpub debian install stage2...'\n"
        "echo \"systemd[1]: vbpub-bootstrap-stage2.service: Main process exited, code=exited, status=1/FAILURE\"\n"
        "echo \"systemd[1]: vbpub-bootstrap-stage2.service: Failed with result 'exit-code'.\"\n"
        + planted,
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def write_output(tmp_path: Path, hook_url: str = "") -> Path:
    """The stage output file as on the live host (logs.txt lines 37-44), plus planted secrets."""
    path = tmp_path / "custom_script.output2"
    planted = f"[INFO] using {hook_url} and {TG_TOKEN}\n" if hook_url else ""
    path.write_text(
        "[INFO] Confirmation accepted via --yes.\n"
        + planted +
        "Traceback (most recent call last):\n"
        "  File \"<frozen runpy>\", line 198, in _run_module_as_main\n"
        "  File \"/opt/vbpub-debian-install-v2/debian_install_v2/bootstrap.py\", line 9, in <module>\n"
        "    from cli_extended import (\n"
        f"{CAUSE}\n",
        encoding="utf-8",
    )
    return path


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
    output_file = write_output(tmp_path, hook_server)
    # What the real unit gets from bootstrap.env: the stage output path.
    env_file = next(v for k, v in render_units(tmp_path, staged, state_dir=str(state_dir))[1].items()
                    if k == "/etc/vbpub/bootstrap.env")
    assert "VBPUB_STAGE2_OUTPUT=" in env_file
    env = bare_env(
        tmp_path, PATH=str(fake_journalctl(tmp_path, hook_server)),
        VBPUB_STATE_DIR=str(state_dir), CREDENTIALS_DIRECTORY=str(creds),
        VBPUB_STAGE2_OUTPUT=str(output_file),
    )
    # `-E -S`: no PYTHONPATH, no site-packages -- cli_extended is not importable.
    result = run_bare([argv[0], argv[1], STAGE2], str(staged), env)
    assert result.returncode == 0, result.stderr

    assert len(Hook.bodies) == 1
    text = json.loads(Hook.bodies[0])["text"]
    assert text.startswith("❌ **lt\\-host**") or text.startswith("❌ **lt-host**")
    assert "run `deadbeef`" in text
    assert "stage2" in text
    assert f"{STAGE2} FAILED" in text
    # The cause comes from the OUTPUT FILE; the journal has only systemd lines.
    assert CAUSE in text
    assert "Main process exited" in text  # journal's systemd lines are included too
    assert "\n```\n" in text and text.endswith("\n```")
    assert len(text) < 3500

    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state["failed_unit"] == STAGE2
    assert state["phase"] == "stage1"  # untouched
    assert CAUSE in state["failed_output_tail"]
    assert "Main process exited" in state["failed_journal_tail"]
    assert CAUSE not in state["failed_journal_tail"]
    assert state["last_error"] == f"{CAUSE} (see {output_file})"
    assert oct(state_path.stat().st_mode & 0o777) == "0o600"

    everything = text + state_path.read_text() + result.stdout + result.stderr
    assert SECRET_ID not in everything
    assert TG_TOKEN not in everything
    assert "***REDACTED***" in text
    assert "***REDACTED***" in state["failed_output_tail"]
    assert "***REDACTED***" in state["failed_journal_tail"]


def test_no_exception_in_output_names_the_output_file_in_last_error(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_path = seed_state(state_dir)
    out = tmp_path / "out.log"
    out.write_text("just some lines\nno exception here\n")
    monkeypatch.setattr(failure_notify, "read_journal", lambda unit, secrets: ["systemd: failed"])
    env = {"VBPUB_STATE_DIR": str(state_dir), "VBPUB_STAGE2_OUTPUT": str(out)}
    assert failure_notify.main([STAGE2], env) == 0
    last_error = json.loads(state_path.read_text())["last_error"]
    assert str(out) in last_error and "journalctl" in last_error


def test_output_path_defaults_to_the_custom_script_output():
    assert failure_notify.output_path({}) == "/root/custom_script.output2"
    assert failure_notify.output_path({"VBPUB_STAGE2_OUTPUT": "relative"}) == "/root/custom_script.output2"
    assert failure_notify.output_path({"VBPUB_STAGE2_OUTPUT": "/x/y"}) == "/x/y"


def post_with_state(tmp_path, hook_server, **state_extra):
    state_dir = tmp_path / "state"
    state_path = seed_state(state_dir)
    state = json.loads(state_path.read_text())
    state.update(state_extra)
    state_path.write_text(json.dumps(state))
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "mattermost_webhook_url").write_text(hook_server + "\n")
    output_file = write_output(tmp_path)
    env = {
        "VBPUB_STATE_DIR": str(state_dir), "CREDENTIALS_DIRECTORY": str(creds),
        "VBPUB_STAGE2_OUTPUT": str(output_file),
    }
    return failure_notify.main([STAGE2], env), state_path


def test_duplicate_guard_skips_the_post_when_the_installer_just_announced(tmp_path, hook_server, monkeypatch):
    # Decision: SKIP the post (the installer's own message already carries the
    # exception text) but still enrich state.json with the evidence tails and
    # leave the installer's status/last_error untouched.
    monkeypatch.setattr(failure_notify, "read_journal", lambda unit, secrets: ["systemd: failed"])
    now = failure_notify.datetime.now(failure_notify.timezone.utc).isoformat()
    rc, state_path = post_with_state(
        tmp_path, hook_server, status="failed", phase="stage2",
        last_error="boom from installer", failure_notified_at=now,
    )
    assert rc == 0
    assert Hook.bodies == []
    state = json.loads(state_path.read_text())
    assert state["last_error"] == "boom from installer"
    assert state["status"] == "failed" and state["phase"] == "stage2"
    assert CAUSE in state["failed_output_tail"]
    assert state["failed_unit"] == STAGE2


def test_stale_installer_notice_does_not_suppress_a_new_crash(tmp_path, hook_server, monkeypatch):
    monkeypatch.setattr(failure_notify, "read_journal", lambda unit, secrets: ["systemd: failed"])
    rc, _ = post_with_state(
        tmp_path, hook_server, status="running", failure_notified_at="2020-01-01T00:00:00+00:00",
    )
    assert rc == 0
    assert len(Hook.bodies) == 1


def failing_installer_run(tmp_path, monkeypatch, post):
    """Installer.resume with a stage2 that raises; ``post`` stands in for post_webhook.

    Returns (state dict after the failure, state_dir). The first call is the
    'Resumed stage2' milestone, so only the FAILURE post is steered by ``post``.
    """
    from debian_install_v2.actions import HostActions
    from debian_install_v2.config import Config
    from debian_install_v2.installer import Installer
    from debian_install_v2.state import StateStore

    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        mattermost_webhook_url="https://mm.example.test/hooks/prodhookid",
        never_reboot=True, auto_reboot_after_stage1=False, credential_mode="systemd",
    )
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer = Installer(config, HostActions(dry_run=True))
    monkeypatch.setattr(installer.actions, "dry_run", False)
    monkeypatch.setattr(installer.state, "dry_run", False)
    monkeypatch.setattr("debian_install_v2.installer.post_webhook", post)

    def boom():
        raise RuntimeError("stage2 exploded")

    installer._stage2 = boom
    with pytest.raises(RuntimeError, match="stage2 exploded"):
        installer.resume()
    state_dir = Path(config.state_dir)
    return json.loads((state_dir / "state.json").read_text()), state_dir


def notifier_after(state_dir, tmp_path, hook_server, monkeypatch):
    monkeypatch.setattr(failure_notify, "read_journal", lambda unit, secrets: ["systemd: failed"])
    creds = tmp_path / "creds"
    creds.mkdir(exist_ok=True)
    (creds / "mattermost_webhook_url").write_text(hook_server + "\n")
    env = {
        "VBPUB_STATE_DIR": str(state_dir), "CREDENTIALS_DIRECTORY": str(creds),
        "VBPUB_STAGE2_OUTPUT": str(write_output(tmp_path)),
    }
    Hook.bodies.clear()
    assert failure_notify.main([STAGE2], env) == 0
    return list(Hook.bodies)


def post_ok(url, text, **kw):
    return True


def post_false(url, text, **kw):
    # Only the failure post fails; the "Resumed stage2" milestone before it succeeds.
    return "FAILED" not in text


def post_raises(url, text, **kw):
    if "FAILED" in text:
        raise OSError("network down")
    return True


def test_installer_post_succeeded_records_the_timestamp_and_the_notifier_skips(
    tmp_path, monkeypatch, hook_server
):
    state, state_dir = failing_installer_run(tmp_path, monkeypatch, post_ok)
    assert state["status"] == "failed" and state["last_error"] == "stage2 exploded"
    assert failure_notify.recently_notified(state)
    assert notifier_after(state_dir, tmp_path, hook_server, monkeypatch) == []


@pytest.mark.parametrize("post", [post_false, post_raises], ids=["post-returns-false", "post-raises"])
def test_installer_post_failed_leaves_the_timestamp_unset_and_the_notifier_posts(
    tmp_path, monkeypatch, hook_server, post
):
    state, state_dir = failing_installer_run(tmp_path, monkeypatch, post)
    assert state["status"] == "failed" and state["last_error"] == "stage2 exploded"
    assert "failure_notified_at" not in state
    bodies = notifier_after(state_dir, tmp_path, hook_server, monkeypatch)
    assert len(bodies) == 1
    assert CAUSE in json.loads(bodies[0])["text"]


def test_notify_reports_delivery_truthfully(tmp_path, monkeypatch):
    from debian_install_v2.actions import HostActions
    from debian_install_v2.config import Config
    from debian_install_v2.installer import Installer

    # No backend: nothing was delivered, so nothing may be recorded as announced.
    bare = Installer(Config(state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l")),
                     HostActions(dry_run=False), inspect_host=False)
    assert bare._notify("x", event="x") is False
    configured = Installer(
        Config(state_dir=str(tmp_path / "s2"), log_dir=str(tmp_path / "l2"),
               mattermost_webhook_url="https://mm.example.test/hooks/h"),
        HostActions(dry_run=False), inspect_host=False,
    )
    monkeypatch.setattr("debian_install_v2.installer.post_webhook", lambda url, text, **kw: False)
    assert configured._notify("x", event="x") is False
    monkeypatch.setattr("debian_install_v2.installer.post_webhook", lambda url, text, **kw: True)
    assert configured._notify("x", event="x") is True


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
    output_file = write_output(tmp_path, "https://mm.example.test/hooks/zzz")
    env = {"VBPUB_STATE_DIR": str(state_dir), "VBPUB_STAGE2_OUTPUT": str(output_file)}
    assert failure_notify.main([STAGE2], env) == 0
    assert sent["token"] == TG_TOKEN and sent["chat"] == "42" and sent["thread"] == "77"
    assert f"{STAGE2} FAILED: {CAUSE}" in sent["text"]
    assert CAUSE in sent["text"].split("\n", 1)[1]
    assert TG_TOKEN not in sent["text"]
    assert "hooks/zzz" not in sent["text"]


def test_send_telegram_url_keeps_the_token_colon_unescaped():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["data"] = request.data

        class Response:
            def close(self):
                pass

        return Response()

    assert failure_notify.send_telegram(TG_TOKEN, "42", "hi", "", opener=opener)
    assert seen["url"] == f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    assert "%3A" not in seen["url"]
    assert b"chat_id=42" in seen["data"]


def test_journal_lines_are_redacted_with_known_secrets(tmp_path, monkeypatch):
    bin_dir = fake_journalctl(tmp_path, "https://mm.example.test/hooks/zzz999")
    monkeypatch.setenv("PATH", str(bin_dir))
    lines = failure_notify.read_journal(STAGE2, ("zzz999-custom-secret",))
    joined = "\n".join(lines)
    assert "zzz999" not in joined
    assert TG_TOKEN not in joined
    assert "Main process exited" in joined


def test_output_tail_is_bounded_redacted_and_cause_found(tmp_path):
    path = tmp_path / "o.log"
    path.write_text("".join(f"line {i}\n" for i in range(100)) + f"secret https://h.test/hooks/zz9 x\n{CAUSE}\n")
    lines = failure_notify.read_output_tail(str(path), ())
    assert len(lines) == failure_notify.OUTPUT_LINES
    assert lines[-1] == CAUSE and failure_notify.find_cause(lines) == CAUSE
    assert "zz9" not in "\n".join(lines)
    assert failure_notify.read_output_tail(str(tmp_path / "missing"), ()) == []


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
