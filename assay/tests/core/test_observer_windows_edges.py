"""B206 edges: transport failures, reply validation, wire decoding, profile
validation and the process helpers of the cgprofile observer client.

Each test pins a behaviour whose break changes an observable result (a reason
code, a refusal to build a profile, a spawn keyword), not just a line.
"""

from __future__ import annotations

import copy
import errno
import os
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest

from assay import observer_windows as ow
from observer_support import FakeObserver, error_reply, golden

WINDOW = "w-" + "0" * 32


@pytest.fixture(autouse=True)
def _no_ambient_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASSAY_RESOURCE_OBSERVER_SOCKET", raising=False)


def _close_reply() -> dict:
    return copy.deepcopy(golden("close-cgroup")["response"])


# --------------------------------------------------------------------------
# _exchange: the transport.
# --------------------------------------------------------------------------


class _FakeClient:
    """A stand-in socket whose connect/sendall behaviour is scripted."""

    def __init__(self, connect_error=None, send_error=None) -> None:
        self._connect_error = connect_error
        self._send_error = send_error
        self.received = False

    def settimeout(self, _value) -> None:
        pass

    def connect(self, _path) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    def sendall(self, _line) -> None:
        if self._send_error is not None:
            raise self._send_error

    def recv(self, _size) -> bytes:
        self.received = True
        return b""

    def close(self) -> None:
        pass


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(
        ow,
        "socket",
        types.SimpleNamespace(
            socket=lambda *_a, **_k: client,
            AF_UNIX=socket.AF_UNIX,
            SOCK_STREAM=socket.SOCK_STREAM,
            timeout=socket.timeout,
        ),
    )


def test_an_oversized_request_is_refused_before_any_connection():
    # A path that does not exist: had the client connected, the code would be
    # OBSERVER_UNAVAILABLE rather than OBSERVER_ERROR.
    with pytest.raises(ow.ObserverFailure) as caught:
        ow._exchange("/nonexistent/o.sock", {"pad": "x" * 5000}, timeout=1.0)
    assert caught.value.code == ow.OBSERVER_ERROR
    assert "exceeds 4096 bytes" in caught.value.detail


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (FileNotFoundError("gone"), ow.OBSERVER_UNAVAILABLE),
        (ConnectionRefusedError("refused"), ow.OBSERVER_UNAVAILABLE),
        (NotADirectoryError("not a dir"), ow.OBSERVER_UNAVAILABLE),
        (PermissionError("denied"), ow.OBSERVER_REFUSED),
        (OSError(errno.ECONNABORTED, "aborted"), ow.OBSERVER_ERROR),
    ],
)
def test_every_connect_failure_maps_to_its_reason_code(monkeypatch, error, code: str):
    _patch_client(monkeypatch, _FakeClient(connect_error=error))
    with pytest.raises(ow.ObserverFailure) as caught:
        ow._exchange("/x.sock", {"verb": "v"}, timeout=1.0)
    assert caught.value.code == code
    assert str(error) in caught.value.detail


def test_a_send_failure_is_an_observer_error_not_a_crash(monkeypatch):
    _patch_client(monkeypatch, _FakeClient(send_error=BrokenPipeError("pipe")))
    with pytest.raises(ow.ObserverFailure) as caught:
        ow._exchange("/x.sock", {"verb": "v"}, timeout=1.0)
    assert caught.value.code == ow.OBSERVER_ERROR
    assert "observer request failed" in caught.value.detail


def test_an_elapsed_deadline_stops_the_read_loop_without_reading(monkeypatch):
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(
        ow, "time", types.SimpleNamespace(monotonic=lambda: next(ticks))
    )
    with pytest.raises(ow.ObserverFailure) as caught:
        ow._exchange("/x.sock", {"verb": "v"}, timeout=1.0)
    assert caught.value.code == ow.OBSERVER_ERROR
    assert "timed out" in caught.value.detail
    assert client.received is False


@pytest.mark.parametrize(
    "reply",
    [
        b"",  # the peer closes without a byte
        b'{"ok":true',  # a reply with no terminating newline
    ],
)
def test_a_reply_that_never_completes_a_line_is_an_error(tmp_path: Path, reply: bytes):
    with FakeObserver(tmp_path / "o.sock", lambda request: reply) as observer:
        with pytest.raises(ow.ObserverFailure) as caught:
            ow._exchange(str(observer.path), {"verb": "v"}, timeout=2.0)
    assert caught.value.code == ow.OBSERVER_ERROR
    assert "not exactly one JSON line" in caught.value.detail


def test_a_reply_beyond_one_mebibyte_is_refused(tmp_path: Path):
    flood = b"x" * (ow.REPLY_LIMIT_BYTES + 4096)
    with FakeObserver(tmp_path / "o.sock", lambda request: flood) as observer:
        with pytest.raises(ow.ObserverFailure) as caught:
            ow._exchange(str(observer.path), {"verb": "v"}, timeout=5.0)
    assert caught.value.code == ow.OBSERVER_ERROR
    assert "exceeds 1 MiB" in caught.value.detail


# --------------------------------------------------------------------------
# open_window / close_window argument and reply checks.
# --------------------------------------------------------------------------


def test_open_window_rejects_an_unknown_mode_before_connecting():
    with pytest.raises(ValueError, match="mode must be one of"):
        ow.open_window("/nonexistent/o.sock", root_pid=1, mode="bogus")


def test_close_window_rejects_a_reply_naming_another_window(tmp_path: Path):
    reply = _close_reply()
    other = "w-" + "1" * 32
    assert reply["window"] != other
    with FakeObserver(tmp_path / "o.sock", lambda request: reply) as observer:
        with pytest.raises(ow.ObserverFailure) as caught:
            ow.close_window(str(observer.path), window=other, thread_id=1)
    assert caught.value.code == ow.OBSERVER_ERROR
    assert "another window" in caught.value.detail


@pytest.mark.parametrize("field", ["metrics", "missing", "sources", "context", "attribution"])
def test_a_close_reply_missing_a_section_or_not_an_object_is_malformed(field: str):
    for broken in (None, [], "x"):
        reply = _close_reply()
        reply[field] = broken
        with pytest.raises(ow.ObserverFailure, match="malformed"):
            ow._validate_close_reply(reply)
    reply = _close_reply()
    del reply[field]
    with pytest.raises(ow.ObserverFailure, match="malformed"):
        ow._validate_close_reply(reply)


def test_a_close_reply_with_a_missing_or_extra_metric_key_is_malformed():
    short = _close_reply()
    del short["metrics"]["cpu_seconds"]
    with pytest.raises(ow.ObserverFailure, match="malformed"):
        ow._validate_close_reply(short)
    extra = _close_reply()
    extra["metrics"]["bogus"] = 1
    with pytest.raises(ow.ObserverFailure, match="malformed"):
        ow._validate_close_reply(extra)


@pytest.mark.parametrize("bad", [True, "1", -1, float("inf"), float("nan")])
def test_a_close_reply_metric_that_is_not_a_finite_non_negative_number_is_refused(bad):
    reply = _close_reply()
    reply["metrics"]["cpu_seconds"] = bad
    with pytest.raises(ow.ObserverFailure, match="not a number"):
        ow._validate_close_reply(reply)


@pytest.mark.parametrize("section", ["missing", "sources"])
def test_a_close_reply_naming_an_unknown_metric_is_refused(section: str):
    reply = _close_reply()
    reply[section]["not_a_metric"] = "x"
    with pytest.raises(ow.ObserverFailure, match="unknown metric"):
        ow._validate_close_reply(reply)


def test_a_close_reply_with_an_invalid_attribution_mode_is_refused():
    reply = _close_reply()
    reply["attribution"] = {**reply["attribution"], "mode": "bogus"}
    with pytest.raises(ow.ObserverFailure, match="attribution mode"):
        ow._validate_close_reply(reply)
    reply["attribution"] = {k: v for k, v in reply["attribution"].items() if k != "mode"}
    with pytest.raises(ow.ObserverFailure, match="attribution mode"):
        ow._validate_close_reply(reply)


def test_the_golden_close_replies_still_validate():
    for name in ("close-cgroup", "close-process-tree", "close-shared"):
        ow._validate_close_reply(golden(name)["response"])


# --------------------------------------------------------------------------
# Wire decoding of WindowConfig / WindowResult.
# --------------------------------------------------------------------------


def _config_wire(**overrides) -> dict:
    wire = {"socket_path": "/run/o.sock", "mode": "cgroup", "interval_ms": 250,
            "skip_reason": None}
    wire.update(overrides)
    return wire


@pytest.mark.parametrize(
    "overrides",
    [
        {"socket_path": 5},
        {"mode": "bogus"},
        {"interval_ms": 99},
        {"interval_ms": 5001},
        {"interval_ms": 250.0},
        {"interval_ms": True},
        {"skip_reason": "NOT_A_REASON"},
        {"skip_reason": 3},
    ],
)
def test_window_config_from_wire_rejects_every_invalid_field(overrides):
    with pytest.raises(ValueError):
        ow.WindowConfig.from_wire(_config_wire(**overrides))


def test_window_config_from_wire_rejects_missing_or_extra_keys():
    wire = _config_wire()
    del wire["mode"]
    with pytest.raises(ValueError, match="unexpected shape"):
        ow.WindowConfig.from_wire(wire)
    with pytest.raises(ValueError, match="unexpected shape"):
        ow.WindowConfig.from_wire({**_config_wire(), "extra": 1})
    with pytest.raises(ValueError, match="unexpected shape"):
        ow.WindowConfig.from_wire([])


def test_window_config_from_wire_accepts_null_socket_bounds_and_a_known_skip_reason():
    config = ow.WindowConfig.from_wire(
        _config_wire(socket_path=None, interval_ms=100, skip_reason=ow.OBSERVER_ERROR)
    )
    assert config == ow.WindowConfig(None, "cgroup", 100, ow.OBSERVER_ERROR)
    top = ow.WindowConfig.from_wire(_config_wire(interval_ms=5000))
    assert top.interval_ms == 5000


def _result_wire(**overrides) -> dict:
    wire = {"reason": ow.OBSERVER_ERROR, "detail": "d", "reply": None, "mode": "cgroup",
            "window": WINDOW, "interval_ms": 250, "asked": True}
    wire.update(overrides)
    return wire


def test_window_result_from_wire_rejects_the_wrong_shape():
    with pytest.raises(ValueError, match="unexpected shape"):
        ow.WindowResult.from_wire("nope")
    wire = _result_wire()
    del wire["asked"]
    with pytest.raises(ValueError, match="unexpected shape"):
        ow.WindowResult.from_wire(wire)


def test_window_result_from_wire_needs_exactly_one_of_reason_and_reply():
    with pytest.raises(ValueError, match="exactly one of reason and reply"):
        ow.WindowResult.from_wire(_result_wire(reason=None, reply=None))
    with pytest.raises(ValueError, match="exactly one of reason and reply"):
        ow.WindowResult.from_wire(_result_wire(reason=ow.OBSERVER_ERROR, reply=_close_reply()))


def test_window_result_from_wire_rejects_a_reply_that_is_not_a_valid_close_reply():
    with pytest.raises(ValueError, match="must be an object"):
        ow.WindowResult.from_wire(_result_wire(reason=None, reply=[1]))
    broken = _close_reply()
    broken["metrics"]["cpu_seconds"] = -1
    with pytest.raises(ValueError, match="not a number"):
        ow.WindowResult.from_wire(_result_wire(reason=None, reply=broken))


def test_window_result_from_wire_drops_ill_typed_optional_fields():
    result = ow.WindowResult.from_wire(
        _result_wire(detail=7, mode="bogus", window=9, interval_ms="x", asked=1)
    )
    assert result.detail is None
    assert result.mode is None
    assert result.window is None
    assert result.interval_ms == ow.DEFAULT_INTERVAL_MS
    assert result.asked is False  # only a literal True counts as asked


# --------------------------------------------------------------------------
# validate_profile.
# --------------------------------------------------------------------------


def _good_profile() -> dict:
    response = _close_reply()
    return ow.build_profile(
        ow.WindowResult(reply=response, mode="cgroup", window=response["window"], asked=True),
        subject={"tool": "assay", "id": "c"}, wall_seconds=1.0,
    )


def test_a_good_profile_validates():
    ow.validate_profile(_good_profile())


def test_validate_profile_rejects_a_non_object_and_an_unknown_status():
    with pytest.raises(ValueError, match="resource-profile/1"):
        ow.validate_profile([])
    with pytest.raises(ValueError, match="status must be one of"):
        ow.validate_profile({**_good_profile(), "status": "BOGUS"})


def test_validate_profile_rejects_an_unknown_reason_code():
    unmeasured = ow.build_profile(None, subject={}, wall_seconds=1.0, not_started=True)
    ow.validate_profile(unmeasured)
    with pytest.raises(ValueError, match="not a known reason code"):
        ow.validate_profile({**unmeasured, "reason": "MADE_UP"})


@pytest.mark.parametrize("wall", [-1.0, "1", True, float("nan")])
def test_validate_profile_rejects_a_bad_wall_seconds(wall):
    with pytest.raises(ValueError, match="wall_seconds"):
        ow.validate_profile({**_good_profile(), "wall_seconds": wall})
    ow.validate_profile({**_good_profile(), "wall_seconds": None})


def test_validate_profile_rejects_malformed_metrics_and_missing():
    good = _good_profile()
    with pytest.raises(ValueError, match="every metric key"):
        ow.validate_profile({**good, "metrics": None})
    short = dict(good["metrics"])
    del short["cpu_seconds"]
    with pytest.raises(ValueError, match="every metric key"):
        ow.validate_profile({**good, "metrics": short})
    with pytest.raises(ValueError, match="missing must be an object"):
        ow.validate_profile({**good, "missing": []})


def test_validate_profile_rejects_a_null_metric_without_a_reason_and_a_non_number():
    good = _good_profile()
    nulled = {**good["metrics"], "cpu_seconds": None}
    with pytest.raises(ValueError, match="null without a reason"):
        ow.validate_profile({**good, "metrics": nulled, "missing": {}})
    ow.validate_profile({**good, "metrics": nulled, "missing": {"cpu_seconds": "X"}})
    with pytest.raises(ValueError, match="is not a number"):
        ow.validate_profile({**good, "metrics": {**good["metrics"], "cpu_seconds": "1"}})


def test_validate_profile_rejects_a_missing_observer_object():
    with pytest.raises(ValueError, match="observer must be an object"):
        ow.validate_profile({**_good_profile(), "observer": None})


# --------------------------------------------------------------------------
# Subreaper keyword, close failures, open failures.
# --------------------------------------------------------------------------


def test_the_subreaper_preexec_calls_prctl_with_the_child_subreaper_flag(monkeypatch):
    calls: list[tuple] = []
    fake_libc = types.SimpleNamespace(prctl=lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(ow, "_libc", fake_libc)
    assert ow.subreaper_preexec(None) == {}
    slot = ow.WindowSlot(ow.WindowConfig("/x.sock", "cgroup"))
    kwargs = ow.subreaper_preexec(slot)
    assert set(kwargs) == {"preexec_fn"}
    assert calls == []  # nothing runs until the child executes it
    kwargs["preexec_fn"]()
    assert calls == [(36, 1, 0, 0, 0)]  # PR_SET_CHILD_SUBREAPER, enable


def test_a_failed_window_close_is_recorded_in_the_slot_and_close_is_idempotent(tmp_path: Path):
    open_reply = golden("open")["response"]

    def handler(request):
        return open_reply if request["verb"] == "window-open" else error_reply("window-lost")

    with FakeObserver(tmp_path / "o.sock", handler) as observer:
        with ow.activate(ow.WindowConfig(str(observer.path), "cgroup", 250)) as active:
            opened = ow.open_for_root(os.getpid())
            assert opened is not None
            opened.close()
            opened.close()  # second close sends nothing
            result = active.result
        assert [r["verb"] for r in observer.requests] == ["window-open", "window-close"]
    assert result is not None
    assert result.reason == ow.WINDOW_LOST
    assert result.asked is True
    assert result.window == open_reply["window"]
    assert result.reply is None


def test_an_open_with_an_invalid_mode_records_an_observer_error_not_a_crash():
    config = ow.WindowConfig("/nonexistent/o.sock", "bogus", 250)
    with ow.activate(config) as slot:
        assert slot is not None and slot.active
        assert ow.open_for_root(os.getpid()) is None
        result = slot.result
    assert result is not None
    assert result.reason == ow.OBSERVER_ERROR
    assert result.asked is True
    assert "window-open failed" in (result.detail or "")
    assert result.mode == "bogus"


def test_an_unrepresentable_open_argument_records_an_observer_error(monkeypatch):
    def boom(*_a, **_k):
        raise OverflowError("too big")

    monkeypatch.setattr(ow, "open_window", boom)
    with ow.activate(ow.WindowConfig("/x.sock", "cgroup", 250)) as slot:
        assert ow.open_for_root(1) is None
        assert slot.result.reason == ow.OBSERVER_ERROR
        assert "too big" in slot.result.detail


# --------------------------------------------------------------------------
# Waiting for a root to exit without reaping it.
# --------------------------------------------------------------------------


def test_waiting_without_a_pidfd_polls_until_the_child_exits_unreaped(monkeypatch):
    def no_pidfd(_pid):
        raise OSError(errno.ENOSYS, "no pidfd")

    monkeypatch.setattr(os, "pidfd_open", no_pidfd)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
    try:
        assert ow.exited_unreaped(proc.pid) is False
        assert ow.wait_exited_unreaped(proc.pid, None) is True  # polling, no deadline
        # still a zombie: waitid did not reap it
        assert Path(f"/proc/{proc.pid}/stat").read_text().split(") ")[1][0] == "Z"
    finally:
        proc.wait()


def test_waiting_without_a_pidfd_gives_up_at_the_deadline(monkeypatch):
    monkeypatch.setattr(os, "pidfd_open", lambda _pid: (_ for _ in ()).throw(OSError("x")))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert ow.wait_exited_unreaped(proc.pid, 0.1) is False
    finally:
        proc.kill()
        proc.wait()


def test_a_child_reaped_by_someone_else_counts_as_exited():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # reaped: waitid now raises ChildProcessError
    assert ow.exited_unreaped(proc.pid) is True
    assert ow.wait_exited_unreaped(proc.pid, 1.0) is True


def test_activating_no_config_yields_no_slot_and_leaves_the_current_slot_alone():
    with ow.activate(ow.WindowConfig("/x.sock", "cgroup", 250)) as outer:
        with ow.activate(None) as inner:
            assert inner is None
            assert ow.current_slot() is outer
        assert ow.current_slot() is outer
    assert ow.current_slot() is None
