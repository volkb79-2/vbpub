"""Mattermost webhook notifications (LT-PREP): selection, payload, tolerance,
redaction and customScript quoting. No real network: a loopback HTTP server
or a monkeypatched opener stands in for Mattermost."""
from __future__ import annotations

import http.server
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import urllib.error
from dataclasses import asdict

import pytest

from debian_install_v2 import notify
from debian_install_v2.actions import HostActions
from debian_install_v2.bootstrap import main
from debian_install_v2.config import (
    Config, ConfigError, load_config, require_notify_credentials, resolve_notify_backend,
    validate_config,
)
from debian_install_v2.customscript import build_customscript_bundle
from debian_install_v2.installer import Installer
from debian_install_v2.state import StateStore
from debian_install_v2.templates import NOTIFY_SCRIPT

HOOK = "https://mattermost.example.test/hooks/abcDEF123secretid"
HOSTILE = "https://mm.example.test/hooks/a'b$(touch${IFS}x);`id`\"z&x=1;y"


class _Recorder(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.server.bodies.append((self.path, self.headers.get("Content-Type"), self.rfile.read(length)))
        self.send_response(self.server.status)
        self.end_headers()

    def log_message(self, *args):  # silence
        pass


@pytest.fixture()
def server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    httpd.bodies = []
    httpd.status = 200
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}/hooks/loopbacksecret"
    yield httpd
    httpd.shutdown()
    httpd.server_close()


# --- backend selection -------------------------------------------------------

def test_backend_inferred_from_the_credential_present():
    assert resolve_notify_backend(Config(mattermost_webhook_url=HOOK)) == "mattermost"
    assert resolve_notify_backend(Config(telegram_bot_token="1:t", telegram_chat_id="2")) == "telegram"
    assert resolve_notify_backend(Config()) == "none"


def test_both_credentials_without_explicit_choice_is_a_config_error():
    config = Config(mattermost_webhook_url=HOOK, telegram_bot_token="1:t", telegram_chat_id="2")
    with pytest.raises(ConfigError, match="both Telegram and Mattermost"):
        validate_config(config)


@pytest.mark.parametrize("choice", ["mattermost", "telegram", "none"])
def test_explicit_choice_resolves_ambiguity(choice):
    config = Config(
        notify_backend=choice, mattermost_webhook_url=HOOK,
        telegram_bot_token="1:t", telegram_chat_id="2",
    )
    validate_config(config)
    assert resolve_notify_backend(config) == choice


def test_unknown_backend_rejected():
    with pytest.raises(ConfigError, match="notify_backend must be one of"):
        validate_config(Config(notify_backend="slack"))


def test_explicit_backend_requires_its_credentials_on_initial_install():
    with pytest.raises(ConfigError, match="requires mattermost_webhook_url"):
        require_notify_credentials(Config(notify_backend="mattermost"))
    with pytest.raises(ConfigError, match="requires telegram_bot_token"):
        require_notify_credentials(Config(notify_backend="telegram"))
    require_notify_credentials(Config(notify_backend="none"))
    # ...but stage two re-validates credential-stripped configs: validate_config alone passes.
    validate_config(Config(notify_backend="mattermost"))


@pytest.mark.parametrize("bad", ["not a url", "ftp://h/hooks/x", "https:///hooks/x", "https://h/hooks/a\nb"])
def test_bad_webhook_urls_rejected_without_echoing_them(bad):
    with pytest.raises(ConfigError) as info:
        validate_config(Config(mattermost_webhook_url=bad))
    assert bad not in str(info.value)


def test_webhook_url_never_in_repr():
    assert "secretid" not in repr(Config(mattermost_webhook_url=HOOK))


def test_telegram_still_works_unchanged_when_inferred(tmp_path):
    config = Config(
        state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l"),
        telegram_bot_token="123:abc", telegram_chat_id="456",
    )
    installer = Installer(config, HostActions(dry_run=True))
    assert installer._notify_backend() == "telegram"


# --- payload, tolerance, retry bound ----------------------------------------

def test_post_sends_text_only_json(server):
    assert notify.post_webhook(server.url, "hello **md**") is True
    path, ctype, body = server.bodies[0]
    assert path == "/hooks/loopbacksecret"
    assert ctype == "application/json"
    assert json.loads(body) == {"text": "hello **md**"}  # no channel override, nothing else


def test_http_error_never_raises_and_retries_exactly_the_bound(server, caplog):
    server.status = 500
    sleeps = []
    with caplog.at_level(logging.WARNING):
        ok = notify.post_webhook(server.url, "x", sleep=sleeps.append)
    assert ok is False
    assert len(server.bodies) == notify.POST_ATTEMPTS == 2
    assert len(sleeps) == notify.POST_ATTEMPTS - 1
    assert "HTTP 500" in caplog.text
    assert "loopbacksecret" not in caplog.text and "/hooks/" not in caplog.text
    assert "127.0.0.1" in caplog.text  # host part only


def test_network_error_tolerated_and_bounded():
    calls = []

    def opener(request, timeout):
        calls.append(timeout)
        raise urllib.error.URLError(f"unreachable {HOOK}")

    assert notify.post_webhook(HOOK, "x", opener=opener, sleep=lambda s: None) is False
    assert calls == [notify.POST_TIMEOUT_S] * notify.POST_ATTEMPTS


def test_network_error_message_never_logs_the_url(caplog):
    def opener(request, timeout):
        raise OSError(f"boom {HOOK}")

    with caplog.at_level(logging.WARNING):
        notify.post_webhook(HOOK, "x", opener=opener, sleep=lambda s: None)
    assert "secretid" not in caplog.text
    assert "mattermost.example.test" in caplog.text


def test_second_attempt_can_succeed():
    outcomes = [OSError("down"), type("R", (), {"status": 200, "close": lambda self: None})()]

    def opener(request, timeout):
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    assert notify.post_webhook(HOOK, "x", opener=opener, sleep=lambda s: None) is True


def test_unusable_url_does_not_raise():
    assert notify.post_webhook("::not a url::", "x", sleep=lambda s: None) is False


# --- message format ----------------------------------------------------------

def test_format_is_prefixed_and_bounded_and_redacted():
    text = notify.format_mattermost_message(
        host_label="netcup-1", server_name="v1001", run_id="0123456789abcdef", stage="stage1",
        event="install FAILED", status="fail",
        excerpt=f"curl said {HOOK} ``` and " + "x" * 5000, secrets=(HOOK,),
    )
    first = text.splitlines()[0]
    assert first == "❌ **netcup-1** (`v1001`) | run `01234567` | stage1 | install FAILED"
    assert "secretid" not in text and "```" in text and text.count("```") == 2
    assert len(text) < 1200


def test_redact_text_masks_any_hook_url_even_unknown_ones():
    assert "zzz" not in notify.redact_text("see https://other.test/hooks/zzz now")
    assert "123456:" not in notify.redact_text("tok 123456789:" + "A" * 35)


# --- installer wiring: milestones, credentials, secrecy ---------------------

def _installer(tmp_path, **overrides):
    fields = {
        "state_dir": str(tmp_path / "state"), "log_dir": str(tmp_path / "logs"),
        "mattermost_webhook_url": HOOK, "notify_host_label": "netcup-1",
        "auto_reboot_after_stage1": False, "never_reboot": True,
        "credential_mode": "systemd",
    }
    fields.update(overrides)
    config = Config(**fields)
    store = StateStore(config.state_dir)
    store.save_new(StateStore.new(config))
    return Installer(config, HostActions(dry_run=True))


def _live(installer, monkeypatch):
    posts: list[str] = []
    monkeypatch.setattr(installer.actions, "dry_run", False)
    monkeypatch.setattr(installer.state, "dry_run", False)
    monkeypatch.setattr(
        "debian_install_v2.installer.post_webhook",
        lambda url, text, **kw: posts.append(text) or True,
    )
    return posts


def test_every_milestone_is_posted_with_the_common_prefix(tmp_path, monkeypatch):
    installer = _installer(tmp_path)
    posts = _live(installer, monkeypatch)
    monkeypatch.setattr(installer, "_initial_event_text", lambda: "starting: 4 cores, 8000 MiB, uefi, release trixie, root /dev/sda1, 2 swap partition(s)")
    monkeypatch.setattr(installer, "_stage1", lambda: installer._reboot())
    monkeypatch.setattr(installer, "_stage2", lambda: None)
    monkeypatch.setattr(installer, "_remove_controller_ssh_key", lambda: None)
    monkeypatch.setattr("debian_install_v2.installer.collect_host_facts", lambda i: {})
    monkeypatch.setattr("debian_install_v2.installer.format_facts_html", lambda f: "")

    installer.install()
    installer.resume()
    # failure in each stage
    monkeypatch.setattr(installer, "_stage1", lambda: (_ for _ in ()).throw(RuntimeError(f"boom via {HOOK}")))
    installer._notify_stage = "stage1"
    with pytest.raises(RuntimeError):
        installer.install()
    monkeypatch.setattr(installer, "_stage2", lambda: (_ for _ in ()).throw(RuntimeError("stage2 broke")))
    with pytest.raises(RuntimeError):
        installer.resume()
    assert len(posts) == 8, posts
    for text in posts:  # (a second install() starts a new run id, so only the shape is pinned)
        assert re.match(r"^\S+ \*\*netcup-1\*\* \(`[^`]+`\) \| run `[0-9a-f]{8}` \| stage[12] \| ", text)
        assert "secretid" not in text
    events = [re.sub(r"^.*? \| run `[0-9a-f]{8}` \| ", "", p.splitlines()[0]) for p in posts]
    assert events[1:4] == [
        "stage1 | complete; reboot disabled, stage2 needs a manual resume",
        "stage2 | resumed after reboot",
        events[3],
    ]
    assert events[3].startswith("stage2 | install complete (duration ")
    assert events[5] == "stage1 | install FAILED" and events[7] == "stage2 | install FAILED"
    assert [p[0] for p in (posts[0], posts[1])] == ["⏳", "⚠"]
    assert posts[3].startswith("✅") and posts[5].startswith("❌")
    assert "boom via ***REDACTED***" in posts[5] and "```" in posts[5]


def test_verbose_step_marks(tmp_path, monkeypatch):
    installer = _installer(tmp_path, telegram_verbose_progress=True)
    posts = _live(installer, monkeypatch)
    installer._mark_step("docker_install", "success", "docker 27")
    installer._mark_step("swap", "failed", "no space")
    assert posts[0].startswith("✅") and "docker_install: success - docker 27" in posts[0]
    assert posts[1].startswith("❌")


def test_notifications_off_for_backend_none_and_dry_run(tmp_path, monkeypatch):
    installer = _installer(tmp_path, notify_backend="none")
    posts = _live(installer, monkeypatch)
    installer._notify("x", event="x")
    assert posts == []
    installer = _installer(tmp_path / "b")
    posts = []
    monkeypatch.setattr("debian_install_v2.installer.post_webhook", lambda *a, **k: posts.append(1))
    installer._notify("x", event="x")  # dry-run actions
    assert posts == []


def test_webhook_url_not_persisted_in_state_and_backend_pinned(tmp_path):
    installer = _installer(tmp_path)
    raw = (tmp_path / "state" / "state.json").read_text()
    assert "secretid" not in raw and "mattermost_webhook_url" not in raw
    assert json.loads(raw)["config"]["notify_backend"] == "mattermost"


@pytest.mark.parametrize("mode", ["root-storage", "systemd"])
def test_webhook_credential_files_written(tmp_path, mode):
    config = Config(
        state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
        mattermost_webhook_url=HOOK, never_reboot=True, auto_reboot_after_stage1=False,
        credential_mode=mode,
    )
    actions = HostActions(dry_run=True)
    Installer(config, actions).install()
    writes = actions.dry_run_writes
    assert writes["/etc/vbpub/credentials/mattermost_webhook_url"] == HOOK + "\n"
    assert not any("telegram" in path for path in writes)
    if mode == "root-storage":
        assert writes[f"{tmp_path / 'state'}/credentials/mattermost_webhook_url"] == HOOK + "\n"
    else:
        unit = next(v for k, v in writes.items() if k.endswith("vbpub-bootstrap-stage2.service"))
        assert "mattermost_webhook_url:/etc/vbpub/credentials/mattermost_webhook_url" in unit


def test_resume_restores_webhook_from_credential_file(tmp_path, monkeypatch):
    installer = _installer(tmp_path)
    cred = tmp_path / "creddir"
    cred.mkdir()
    (cred / "mattermost_webhook_url").write_text(HOOK + "\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred))
    monkeypatch.setattr(installer, "_stage2", lambda: None)
    monkeypatch.setattr(installer, "_remove_controller_ssh_key", lambda: None)
    installer.config = Config(**{**asdict(installer.config), "mattermost_webhook_url": ""})
    _live(installer, monkeypatch)
    installer.resume()
    assert installer.config.mattermost_webhook_url == HOOK
    assert installer.config.notify_backend == "mattermost"


# --- customScript / build-customscript quoting ------------------------------

def _env_of(command):
    tokens = shlex.split(command)
    return dict(token.split("=", 1) for token in tokens[: tokens.index("python3")])


@pytest.mark.parametrize("url", [HOOK, HOSTILE])
def test_customscript_carries_webhook_shell_quoted(url):
    bundle = build_customscript_bundle(Config(mattermost_webhook_url=url))
    env = _env_of(bundle["customScript"])  # shlex round-trip: no injection, no truncation
    assert set(env) == {"REPO_URL", "REPO_BRANCH", "VBPUB_CONFIG_EXTRA_JSON"}
    assert json.loads(env["VBPUB_CONFIG_EXTRA_JSON"])["mattermost_webhook_url"] == url
    assert shlex.quote(json.dumps(json.loads(env["VBPUB_CONFIG_EXTRA_JSON"]), sort_keys=True, separators=(",", ":"))) in bundle["customScript"]


def test_hostile_url_with_whitespace_is_refused():
    with pytest.raises(ConfigError):
        validate_config(Config(mattermost_webhook_url="https://h/hooks/a b;rm -rf /"))


def _write_config(tmp_path, **extra):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"mattermost_webhook_url": HOOK, **extra}))
    return str(path)


def test_build_customscript_refuses_redaction_without_debug_raw_and_never_prints_url(tmp_path, capsys):
    cfg = _write_config(tmp_path)
    code = main(["build-customscript", "--config", cfg])
    captured = capsys.readouterr()
    assert code != 0
    assert "secretid" not in captured.out + captured.err
    assert "Mattermost webhook" in captured.out + captured.err


def test_plan_output_never_contains_the_url(tmp_path, capsys):
    cfg = _write_config(tmp_path, state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "log"))
    main(["--debug", "build-customscript", "--config", cfg])
    captured = capsys.readouterr()
    assert "secretid" not in captured.out + captured.err


def test_invalid_explicit_backend_in_cli_config_fails_cleanly(tmp_path, capsys):
    cfg = _write_config(tmp_path, notify_backend="telegram")
    assert main(["build-customscript", "--config", cfg, "--debug-raw"]) != 0
    assert "secretid" not in capsys.readouterr().err


def test_both_credentials_cli_error_is_clear(tmp_path, capsys):
    cfg = _write_config(tmp_path, telegram_bot_token="1:tok", telegram_chat_id="2")
    assert main(["build-customscript", "--config", cfg, "--debug-raw"]) != 0
    out = capsys.readouterr()
    assert "set notify_backend explicitly" in out.out + out.err
    assert "secretid" not in out.out + out.err


def test_wizard_secret_set_includes_webhook():
    from debian_install_v2 import wizard

    assert "mattermost_webhook_url" in wizard._SUMMARY_HIDDEN_FIELDS
    assert {"notify_backend", "mattermost_webhook_url", "notify_host_label"} <= wizard.wizard_field_names()


# --- the vbpub-notify shell helper (units on the target host) ---------------

def test_notify_script_posts_json_to_the_webhook(tmp_path, server):
    script = tmp_path / "vbpub-notify"
    script.write_text(NOTIFY_SCRIPT)
    script.chmod(0o755)
    cred = tmp_path / "cred"
    cred.mkdir()
    (cred / "mattermost_webhook_url").write_text(server.url + "\n")
    env = {**os.environ, "CREDENTIALS_DIRECTORY": str(cred)}
    result = subprocess.run([str(script), 'back up "quoted" $x'], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    body = json.loads(server.bodies[0][2])
    assert list(body) == ["text"] and 'back up "quoted" $x' in body["text"]
    assert "loopbacksecret" not in result.stdout + result.stderr


def test_notify_script_failure_never_fails_the_caller_nor_leaks_url(tmp_path, server):
    server.status = 500
    script = tmp_path / "vbpub-notify"
    script.write_text(NOTIFY_SCRIPT.replace("time.sleep(1.5)", "pass"))
    script.chmod(0o755)
    cred = tmp_path / "cred"
    cred.mkdir()
    (cred / "mattermost_webhook_url").write_text(server.url + "\n")
    env = {**os.environ, "CREDENTIALS_DIRECTORY": str(cred)}
    result = subprocess.run([str(script), "m"], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "send failed" in result.stderr
    assert "loopbacksecret" not in result.stdout + result.stderr
    assert len(server.bodies) == 2
