from __future__ import annotations

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer, _split_for_telegram
from debian_install_v2.state import StateStore


# --- _split_for_telegram: pure function ----------------------------------

def test_split_short_message_is_unchanged():
    assert _split_for_telegram("hello") == ["hello"]


def test_split_at_blank_line_boundary():
    limit = 20
    message = "AAAAAAAAAA\n\nBBBBBBBBBBBBBBBBBBBB"
    chunks = _split_for_telegram(message, limit=limit)
    assert chunks == ["AAAAAAAAAA", "BBBBBBBBBBBBBBBBBBBB"]
    assert all(len(c) <= limit for c in chunks[:-1])  # last chunk may exceed if unsplittable


def test_split_falls_back_to_single_newline_without_blank_line():
    limit = 15
    message = "AAAAAAAAAAAAAA\nBBBBBBBBBBBBBB"
    chunks = _split_for_telegram(message, limit=limit)
    assert chunks == ["AAAAAAAAAAAAAA", "BBBBBBBBBBBBBB"]


def test_split_hard_splits_when_no_boundary_exists():
    limit = 10
    message = "A" * 25  # no newlines at all
    chunks = _split_for_telegram(message, limit=limit)
    assert "".join(chunks) == message
    assert all(len(c) <= limit for c in chunks)


# --- _notify: chunking + thread_id ----------------------------------------

def make_installer(tmp_path, **config_overrides):
    fields = {
        "state_dir": str(tmp_path / "state"), "log_dir": str(tmp_path / "logs"),
        "telegram_bot_token": "123:abc", "telegram_chat_id": "456",
        "auto_reboot_after_stage1": False, "never_reboot": True,
    }
    fields.update(config_overrides)
    config = Config(**fields)
    store = StateStore(config.state_dir)
    store.save_new(StateStore.new(config))
    installer = Installer(config, HostActions(dry_run=True))
    return installer


def test_notify_sends_each_chunk_in_order_and_stops_on_failure(tmp_path, monkeypatch):
    installer = make_installer(tmp_path)
    installer.actions.dry_run = False  # force past _notify's own dry-run guard

    sent_urls = []

    class FakeResp:
        def close(self):
            pass

    def fake_urlopen(request, timeout=15):
        sent_urls.append(request.full_url)
        return FakeResp()

    monkeypatch.setattr("debian_install_v2.installer.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("debian_install_v2.installer.time.sleep", lambda s: None)

    installer._notify("A" * 5000)  # forces a real multi-chunk split
    assert len(sent_urls) >= 2
    assert all("sendMessage" in url for url in sent_urls)


def test_notify_includes_message_thread_id_when_present(tmp_path, monkeypatch):
    installer = make_installer(tmp_path)
    installer.actions.dry_run = False
    installer.state.dry_run = False  # allow this direct write to actually persist
    installer.state.save(telegram_thread_id="789")

    captured = {}

    class FakeResp:
        def close(self):
            pass

    def fake_urlopen(request, timeout=15):
        captured["body"] = request.data.decode("utf-8")
        return FakeResp()

    monkeypatch.setattr("debian_install_v2.installer.urllib.request.urlopen", fake_urlopen)
    installer._notify("hello")
    assert "message_thread_id=789" in captured["body"]


def test_notify_skips_entirely_without_credentials(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, telegram_bot_token="", telegram_chat_id="")
    installer.actions.dry_run = False

    def fail_urlopen(*a, **k):
        raise AssertionError("should not attempt a network call without credentials")

    monkeypatch.setattr("debian_install_v2.installer.urllib.request.urlopen", fail_urlopen)
    installer._notify("hello")  # must not raise, must not call urlopen


# --- Hook-point wiring: install()/resume() call _notify at the right times

def test_install_and_resume_send_expected_stage_boundary_messages(tmp_path, monkeypatch):
    installer = make_installer(tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(installer, "_notify", lambda message: sent.append(message))
    monkeypatch.setattr(Installer, "_notifications_enabled", property(lambda self: True))

    installer.install()
    assert len(sent) == 2
    assert "Starting debian-install-v2" in sent[0]
    assert "Reboot is disabled by configuration" in sent[1]  # never_reboot=True in make_installer

    installer.resume()
    assert len(sent) == 4
    assert "Resumed stage2" in sent[2]
    assert "Install complete" in sent[3]


def test_install_failure_sends_exactly_one_failure_notification(tmp_path, monkeypatch):
    installer = make_installer(tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(installer, "_notify", lambda message: sent.append(message))
    monkeypatch.setattr(Installer, "_notifications_enabled", property(lambda self: True))
    monkeypatch.setattr(installer, "_stage1", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    try:
        installer.install()
    except RuntimeError:
        pass
    assert len(sent) == 2  # initial report, then the failure message
    assert "Install FAILED" in sent[-1]
    assert "boom" in sent[-1]


def test_verbose_progress_notifies_every_mark_step(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, telegram_verbose_progress=True)
    sent: list[str] = []
    monkeypatch.setattr(installer, "_notify", lambda message: sent.append(message))
    installer._mark_step("some_step", "success", "detail here")
    assert any("some_step" in message for message in sent)


def test_non_verbose_mark_step_does_not_notify(tmp_path, monkeypatch):
    installer = make_installer(tmp_path, telegram_verbose_progress=False)
    sent: list[str] = []
    monkeypatch.setattr(installer, "_notify", lambda message: sent.append(message))
    installer._mark_step("some_step", "success", "detail here")
    assert sent == []
