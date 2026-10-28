from __future__ import annotations

import threading
import time

import pytest

from tnt.errors import ProfileError
from tnt.profile import Profile, Secret, SecretEntry
from tnt.session_controller import SessionController, WorkerLease


class FakeRpc:
    def __init__(self, read_only=False):
        self.read_only = read_only
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, host, *, read_only=False, events=None):
        self.profile = host
        self.rpc = FakeRpc(read_only)
        self.backend = object()
        self.insecure_tls = False
        self.missing_methods = {}
        self.credential = None
        self.events = events if events is not None else []
        self.closed = False

    def close(self):
        self.closed = True
        self.rpc.close()
        self.events.append(("close", self.profile.name))

    def banner(self):
        return f"{self.profile.name} connected"


def hosts():
    return {
        name: Profile(name, f"wss://{name}.example.invalid/api/current", "operator", auth="api_key")
        for name in ("lab", "backup")
    }


def controller(opener=None, *, initial=None, initial_write=False):
    calls = []

    def default_open(host, *, secret, insecure_ack=False, allow_write=False):
        calls.append((host.name, secret.reveal(), insecure_ack, allow_write))
        return FakeSession(host, read_only=not allow_write)

    value = SessionController(hosts(), "lab", {
        "lab": SecretEntry(api_key=Secret("1-lab-secret")),
        "backup": SecretEntry(api_key=Secret("1-backup-secret")),
    }, opener or default_open, initial_session=initial, initial_write=initial_write)
    return value, calls


def test_generation_changes_before_old_session_closes_and_worker_is_drained():
    events = []
    old = FakeSession(hosts()["lab"], events=events)
    controller_, calls = controller(initial=old)
    generation, session = controller_.capture()
    lease = controller_.register_worker(generation, session)
    assert lease is not None
    transition = controller_.begin_switch("backup")
    assert not controller_.is_current(generation, session)
    assert lease.cancelled.is_set()

    completed = threading.Event()

    def do_switch():
        switched = controller_.finish_switch(transition)
        assert switched.profile.name == "backup"
        completed.set()

    thread = threading.Thread(target=do_switch)
    thread.start()
    assert not completed.wait(0.03)
    assert not old.closed
    controller_.finish_worker(lease)
    thread.join(1)
    assert completed.is_set() and old.closed
    assert events[0] == ("close", "lab")
    assert calls == [("backup", "1-backup-secret", False, False)]
    assert controller_.generation > generation


def test_late_callback_is_rejected_after_switch_and_session_is_host_bound():
    controller_, _ = controller(initial=FakeSession(hosts()["lab"]))
    generation, old_session = controller_.capture()
    transition = controller_.begin_switch("backup")
    controller_.finish_switch(transition)
    assert not controller_.is_current(generation, old_session)
    assert controller_.active_host == "backup"
    assert controller_.capture()[1].profile.name == "backup"


def test_write_mode_is_saved_per_host_and_reopening_invalidates_generation():
    controller_, calls = controller(initial=FakeSession(hosts()["lab"], read_only=True))
    first_generation = controller_.generation
    switched = controller_.set_write_mode(True)
    assert switched.rpc.read_only is False
    assert controller_.generation > first_generation
    backup = controller_.switch("backup")
    assert backup.rpc.read_only is True
    lab = controller_.switch("lab")
    assert lab.rpc.read_only is False
    assert calls == [
        ("lab", "1-lab-secret", False, True),
        ("backup", "1-backup-secret", False, False),
        ("lab", "1-lab-secret", False, True),
    ]


def test_initial_write_mode_applies_to_first_picker_choice_only():
    controller_, calls = controller(initial_write=True)
    first = controller_.switch("backup")
    second = controller_.switch("lab")
    assert first.rpc.read_only is False
    assert second.rpc.read_only is True
    assert calls[0][3] is True and calls[1][3] is False


def test_new_session_job_calls_use_the_controller_generation():
    from tnt.session import Session
    from tnt.transport import GuardedRpc

    def open_host(host, *, secret, insecure_ack=False, allow_write=False):
        return Session(host, GuardedRpc(FakeRpc(read_only=not allow_write), read_only=not allow_write), object())

    controller_, _ = controller(open_host)
    transition = controller_.begin_switch("backup")
    session = controller_.finish_switch(transition)

    assert session.rpc._job_generation == transition.generation


def test_connection_test_uses_read_only_temporary_session_and_preserves_selection():
    created = []

    def open_host(host, *, secret, insecure_ack=False, allow_write=False):
        session = FakeSession(host, read_only=not allow_write)
        created.append((session, allow_write, secret.reveal()))
        return session

    controller_, _ = controller(open_host, initial=FakeSession(hosts()["lab"], read_only=False))
    before = controller_.capture()
    result = controller_.test_host("backup")
    assert result == "backup connected"
    assert controller_.capture() == before
    temporary, allow_write, key = created[0]
    assert allow_write is False and temporary.closed and key == "1-backup-secret"
    assert controller_.last_status["backup"] == "connected"


def test_unknown_host_and_missing_secret_fail_before_opener_is_called():
    called = []
    controller_, _ = controller(lambda *a, **kw: called.append((a, kw)))
    with pytest.raises(ProfileError, match="unknown host"):
        controller_.begin_switch("missing")
    controller_.secret_entries.pop("backup")
    with pytest.raises(ProfileError, match="no credentials for host"):
        controller_.switch("backup")
    assert called == []


def test_opening_error_scrubs_credential_from_status_and_exception():
    def failing_open(host, *, secret, **kwargs):
        raise RuntimeError(f"bad response echoed {secret.reveal()}")

    controller_, _ = controller(failing_open)
    transition = controller_.begin_switch("backup")
    with pytest.raises(ProfileError) as caught:
        controller_.finish_switch(transition)
    assert "1-backup-secret" not in str(caught.value)
    assert "1-backup-secret" not in controller_.last_status["backup"]
    assert "<redacted>" in controller_.last_status["backup"]


def test_reconnect_uses_the_active_hosts_secret_and_preserves_session_identity():
    old = FakeSession(hosts()["lab"], read_only=True)
    controller_, calls = controller(initial=old)
    generation = controller_.generation
    old_rpc = old.rpc
    reopened = controller_.reconnect_current(generation, old)
    assert reopened is old
    assert controller_.session is old
    assert old.rpc.read_only is True
    assert old_rpc.closed
    assert calls == [("lab", "1-lab-secret", False, False)]


def test_reconnect_rejects_a_stale_generation_and_closes_candidate():
    old = FakeSession(hosts()["lab"], read_only=True)
    controller_, _ = controller(initial=old)
    generation = controller_.generation
    controller_.begin_switch("backup")
    with pytest.raises(ProfileError, match="no longer active"):
        controller_.reconnect_current(generation, old)


def test_initial_session_credential_is_retained_for_direct_tui_reconnects():
    initial = FakeSession(hosts()["lab"], read_only=True)
    initial.credential = Secret("1-session-key")
    calls = []
    # The controller captures the initial session credential when no paired entry was passed.
    controller_ = SessionController({"lab": hosts()["lab"]}, "lab", {},
                                    lambda host, *, secret, **kw: calls.append((host.name, secret.reveal()))
                                    or FakeSession(host, read_only=not kw.get("allow_write", False)),
                                    initial_session=initial)
    assert controller_.write_mode_for("lab") is False
    controller_.set_write_mode(True)
    assert calls[-1] == ("lab", "1-session-key")


def test_switch_refuses_overlap_and_disconnect_closes_and_clears_active_session():
    old = FakeSession(hosts()["lab"])
    controller_, calls = controller(initial=old)
    transition = controller_.begin_switch("backup")
    with pytest.raises(ProfileError, match="already in progress"):
        controller_.begin_switch("lab")
    switched = controller_.finish_switch(transition)
    assert switched.profile.name == "backup" and old.closed
    disconnected = controller_.begin_disconnect()
    assert disconnected.target_host is None and disconnected.previous_session is switched
    assert controller_.finish_switch(disconnected) is None
    assert switched.closed and controller_.active_host is None
    with pytest.raises(ProfileError, match="choose a host"):
        controller_.set_write_mode(True)
    assert calls == [("backup", "1-backup-secret", False, False)]


def test_switch_opener_failure_closes_old_session_and_records_safe_status():
    old = FakeSession(hosts()["lab"])

    def fail(host, *, secret, **kwargs):
        raise RuntimeError(f"server echoed {secret.reveal()}")

    controller_, _ = controller(fail, initial=old)
    transition = controller_.begin_switch("backup")
    with pytest.raises(ProfileError) as caught:
        controller_.finish_switch(transition)
    assert old.closed and "1-backup-secret" not in str(caught.value)
    assert "1-backup-secret" not in controller_.last_status["backup"]
    assert controller_.session is None and not controller_._switching


def test_connection_test_failure_is_read_only_and_scrubs_status():
    created = []

    def failing(host, *, secret, allow_write, **kwargs):
        created.append((host.name, allow_write))
        raise RuntimeError(f"echoed {secret.reveal()}")

    controller_, _ = controller(failing, initial=FakeSession(hosts()["lab"]))
    with pytest.raises(ProfileError) as caught:
        controller_.test_host("backup")
    assert "1-backup-secret" not in str(caught.value)
    assert "1-backup-secret" not in controller_.last_status["backup"]
    assert "<redacted>" in controller_.last_status["backup"]
    assert created == [("backup", False)]
    assert not controller_._workers


def test_test_host_rejects_switching_and_unknown_names():
    controller_, _ = controller()
    transition = controller_.begin_switch("backup")
    with pytest.raises(ProfileError, match="transition is in progress"):
        controller_.test_host("lab")
    controller_.finish_switch(transition)
    with pytest.raises(ProfileError, match="unknown host"):
        controller_.test_host("missing")


def test_worker_cancellation_drains_connection_test_before_switch_completes():
    entered = threading.Event()
    release = threading.Event()
    temporary_sessions = []

    def open_host(host, *, secret, allow_write=False, **kwargs):
        if host.name == "backup":
            entered.set()
            release.wait(2)
        session = FakeSession(host, read_only=not allow_write)
        temporary_sessions.append(session)
        return session

    controller_, _ = controller(open_host, initial=FakeSession(hosts()["lab"]))
    result = []
    worker = threading.Thread(target=lambda: result.append(controller_.test_host("backup")))
    worker.start()
    assert entered.wait(1)
    transition = controller_.begin_switch("backup")
    done = threading.Event()

    def switch():
        controller_.finish_switch(transition)
        done.set()

    switcher = threading.Thread(target=switch)
    switcher.start()
    assert not done.wait(0.03)
    release.set()
    worker.join(1)
    switcher.join(1)
    assert result == ["backup connected"] and done.is_set()
    assert temporary_sessions[0].closed and controller_.session.profile.name == "backup"


def test_switch_candidate_is_closed_if_controller_closes_while_connecting():
    entered = threading.Event()
    release = threading.Event()
    candidates = []

    def open_host(host, *, secret, **kwargs):
        entered.set()
        release.wait(2)
        candidate = FakeSession(host)
        candidates.append(candidate)
        return candidate

    controller_, _ = controller(open_host)
    transition = controller_.begin_switch("backup")
    result = []
    worker = threading.Thread(target=lambda: result.append(controller_.finish_switch(transition)))
    worker.start()
    assert entered.wait(1)
    controller_.close()
    release.set()
    worker.join(1)
    assert result == [None] and candidates[0].closed and controller_.session is None


def test_controller_close_cancels_and_drains_registered_workers_before_closing():
    old = FakeSession(hosts()["lab"])
    controller_, _ = controller(initial=old)
    generation, session = controller_.capture()
    lease = controller_.register_worker(generation, session)
    assert lease is not None
    finished = threading.Event()

    def close():
        controller_.close()
        finished.set()

    worker = threading.Thread(target=close)
    worker.start()
    assert lease.cancelled.wait(1)
    assert not finished.wait(0.03) and not old.closed
    controller_.finish_worker(lease)
    worker.join(1)
    assert finished.is_set() and old.closed and controller_.active_host is None


def test_update_hosts_prunes_status_and_reconnect_refusal_is_reported():
    initial = FakeSession(hosts()["lab"])

    def fail(host, *, secret, **kwargs):
        raise RuntimeError(f"failed for {secret.reveal()}")

    controller_, _ = controller(fail, initial=initial)
    controller_.last_status["backup"] = "previous result"
    controller_.update_hosts({"lab": hosts()["lab"]}, "lab", {})
    assert "backup" not in controller_.last_status
    with pytest.raises(ProfileError, match="default host"):
        controller_.update_hosts({"lab": hosts()["lab"]}, "backup", {})
    with pytest.raises(ProfileError, match="no credentials for host"):
        controller_.reconnect_current(controller_.generation, initial)
    assert controller_.last_status["lab"] == "connected"


def test_reconnect_failure_redacts_status_and_preserves_transport_kind():
    from tnt.errors import TransportError

    initial = FakeSession(hosts()["lab"])

    def fail(host, *, secret, **kwargs):
        raise TransportError("connect", f"echoed {secret.reveal()}")

    controller_, _ = controller(fail, initial=initial)
    with pytest.raises(TransportError) as caught:
        controller_.reconnect_current(controller_.generation, initial)
    assert caught.value.kind == "connect"
    assert "1-lab-secret" not in str(caught.value)
    assert "<redacted>" in controller_.last_status["lab"]


def test_registration_rejects_stale_generation_or_foreign_session_and_finish_is_idempotent():
    old = FakeSession(hosts()["lab"])
    controller_, _ = controller(initial=old)
    generation, session = controller_.capture()
    assert controller_.register_worker(generation - 1, session) is None
    assert controller_.register_worker(generation, FakeSession(hosts()["lab"])) is None
    lease = controller_.register_worker(generation, session)
    assert lease is not None
    controller_.finish_worker(lease)
    assert lease.finished.is_set() and not controller_._workers
    controller_.finish_worker(lease)
    assert lease.finished.is_set()


def test_controller_requires_known_default_and_connection_test_can_be_cancelled():
    with pytest.raises(ProfileError, match="default host"):
        SessionController(hosts(), "missing", {}, lambda *a, **k: None)

    entered = threading.Event()
    release = threading.Event()
    controller_, _ = controller(lambda host, **kw: (entered.set(), release.wait(2), FakeSession(host))[2],
                                initial=FakeSession(hosts()["lab"]))
    results = []
    worker = threading.Thread(target=lambda: results.append(controller_.test_host("backup")))
    worker.start()
    assert entered.wait(1)
    transition = controller_.begin_switch("backup")
    release.set()
    worker.join(1)
    controller_.finish_switch(transition)
    assert results == ["backup connected"]
    assert controller_.last_status["backup"] == "connected"
    assert controller_._workers == {}


def test_reconnect_candidate_is_closed_when_host_changes_during_open():
    entered = threading.Event()
    release = threading.Event()
    created = []

    def open_host(host, *, secret, **kwargs):
        if host.name == "lab":
            entered.set()
            release.wait(2)
        session = FakeSession(host, read_only=not kwargs.get("allow_write", False))
        created.append(session)
        return session

    old = FakeSession(hosts()["lab"])
    controller_, _ = controller(open_host, initial=old)
    errors = []
    thread = threading.Thread(target=lambda: _capture_error(
        errors, lambda: controller_.reconnect_current(controller_.generation, old)))
    thread.start()
    assert entered.wait(1)
    transition = controller_.begin_switch("backup")
    switched = controller_.finish_switch(transition)
    release.set()
    thread.join(1)
    assert switched.profile.name == "backup"
    assert len(errors) == 1 and isinstance(errors[0], ProfileError)
    assert "no longer active" in str(errors[0])
    assert created[-1].closed


def _capture_error(errors, call):
    try:
        call()
    except Exception as exc:  # capture a worker-thread failure for the test assertion
        errors.append(exc)


def test_reconnect_preserves_profile_errors_from_opener():
    initial = FakeSession(hosts()["lab"])

    def fail(*args, **kwargs):
        raise ProfileError("synthetic credential refusal")

    controller_, _ = controller(fail, initial=initial)
    with pytest.raises(ProfileError, match="synthetic credential refusal"):
        controller_.reconnect_current(controller_.generation, initial)


def test_initial_password_session_credential_is_retained_and_foreign_finish_is_ignored():
    password_profile = Profile("password-host", "wss://password-host.example.invalid/api/current", "operator",
                               auth="password")
    initial = FakeSession(password_profile)
    initial.credential = Secret("placeholder-password")
    controller_ = SessionController({}, None, {}, lambda *a, **k: None, initial_session=initial)
    assert controller_.secret_entries["password-host"].password.reveal() == "placeholder-password"
    foreign = type("Transition", (), {
        "workers": (), "previous_session": None, "target_host": None, "generation": controller_.generation - 1,
    })()
    controller_._switching = True
    assert controller_.finish_switch(foreign) is None
    assert controller_._switching is True

    controller_.finish_worker(WorkerLease(-1, None))
    assert not controller_._workers
    controller_.close()


def test_cancelled_test_error_does_not_overwrite_status_and_generic_reconnect_failure_is_scrubbed():
    entered = threading.Event()
    release = threading.Event()
    start_status = "operator's previous result"

    def fail_after_switch(host, *, secret, **kwargs):
        if host.name == "backup" and not entered.is_set():
            entered.set()
            release.wait(2)
            raise RuntimeError(f"failed and echoed {secret.reveal()}")
        return FakeSession(host)

    controller_, _ = controller(fail_after_switch, initial=FakeSession(hosts()["lab"]))
    controller_.last_status["backup"] = start_status
    errors = []
    worker = threading.Thread(target=lambda: _capture_error(errors, lambda: controller_.test_host("backup")))
    worker.start()
    assert entered.wait(1)
    transition = controller_.begin_switch("backup")
    release.set()
    worker.join(1)
    assert len(errors) == 1 and "1-backup-secret" not in str(errors[0])
    assert controller_.last_status["backup"] == start_status
    controller_.finish_switch(transition)

    initial = FakeSession(hosts()["lab"])

    def generic_failure(host, *, secret, **kwargs):
        raise RuntimeError(f"reconnect leaked {secret.reveal()}")

    reconnect_controller, _ = controller(generic_failure, initial=initial)
    with pytest.raises(ProfileError, match="cannot reconnect.*redacted"):
        reconnect_controller.reconnect_current(reconnect_controller.generation, initial)
    assert "1-lab-secret" not in reconnect_controller.last_status["lab"]
