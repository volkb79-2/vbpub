from __future__ import annotations

import json
import socket
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import assay.resource_limits as resource_limits
from assay.resource_limits import (
    ResourceLimitAncestorsHiddenError,
    ResourceLimitCounters,
    ResourceLimitEvidence,
    ResourceLimitObservationError,
)


GATES = "/dev.slice/dev-gates.slice"
LEAF = f"{GATES}/run-gate-r2-test.scope"
MEMORY = {"max": 0, "oom": 0, "oom_kill": 0, "oom_group_kill": 0}


def _document(thread_id: int) -> dict:
    cgroups = []
    for path in (LEAF, GATES, "/dev.slice", "/"):
        cgroups.append({
            "path": path,
            "mount_id": 31,
            "pids_max_present": True,
            "pids_max": None,
            "memory_max_present": True,
            "memory_max": None,
            "pids_events": {"max": 0},
            "memory_events": dict(MEMORY),
        })
    return {
        "ok": True,
        "contract": 1,
        "cgroup_version": 2,
        "thread_id": thread_id,
        "gates_slice": GATES,
        "cgroups": cgroups,
    }


def _path_counters(leaf, *ancestors):
    paths = (LEAF, GATES, "/dev.slice", "/")
    values = (leaf, *ancestors)
    assert len(values) == len(paths)
    return ResourceLimitCounters(
        pids_max=sum(value[0] or 0 for value in values),
        memory_max=sum(value[1] or 0 for value in values),
        memory_oom=sum(value[2] or 0 for value in values),
        memory_oom_kill=sum(value[3] or 0 for value in values),
        memory_oom_group_kill=sum(value[4] or 0 for value in values),
        limit_signature=tuple(
            (path, 31, True, None, True, None, True, True) for path in paths
        ),
        path_event_counters=tuple(
            (path, *value) for path, value in zip(paths, values, strict=True)
        ),
    )


def test_observer_response_becomes_strict_ancestry_bound_counters():
    counters = resource_limits._resource_counters_from_observer(_document(77), 77)

    assert counters.pids_max == 0
    assert counters.memory_max == 0
    assert counters.limit_signature[0] == (LEAF, 31, True, None, True, None, True, True)
    assert counters.path_event_counters[0] == (LEAF, 0, 0, 0, 0, 0)
    assert counters.path_event_counters[-1] == ("/", 0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.update(ok=False, error={"message": "peer refused"}), "peer refused"),
        (lambda raw: raw.update(extra=True), "unknown or missing fields"),
        (lambda raw: raw.update(contract=True), "contract version"),
        (lambda raw: raw.update(cgroup_version=1), "cgroup v2"),
        (lambda raw: raw.update(thread_id=78), "thread identity"),
        (lambda raw: raw.update(gates_slice="relative"), "not absolute"),
        (lambda raw: raw.update(cgroups=[]), "non-empty list"),
        (lambda raw: raw["cgroups"][0].update(extra=True), "entry is malformed"),
        (lambda raw: raw["cgroups"][0].update(path="/a/../b"), "not canonical"),
        (lambda raw: raw["cgroups"][1].update(path="/wrong"), "not ordered leaf-to-root"),
        (lambda raw: raw["cgroups"][0].update(mount_id=0), "mount ID is invalid"),
        (lambda raw: raw["cgroups"][1].update(mount_id=32), "span multiple mounts"),
        (lambda raw: raw["cgroups"][0].update(pids_max_present=1), "presence is malformed"),
        (lambda raw: raw["cgroups"][0].update(pids_max_present=False, pids_max=0), "absent pids.max has a value"),
        (lambda raw: raw["cgroups"][0].update(memory_max=-1), "memory.max value is malformed"),
        (lambda raw: raw["cgroups"][0].update(pids_events={"other": 0}), "pids.events fields are malformed"),
        (lambda raw: raw["cgroups"][0].update(pids_events={"max": True}), "pids.events counters are malformed"),
        (lambda raw: raw["cgroups"][0].update(memory_events=None), "memory.max lacks memory.events"),
        (lambda raw: raw["cgroups"][0].update(pids_events=None), "pids.max lacks pids.events"),
        (lambda raw: raw["cgroups"].pop(), "does not reach the hierarchy root"),
        (lambda raw: [entry.update(path=path) for entry, path in zip(
            raw["cgroups"],
            ("/dev.slice/dev-background.slice/x.scope", "/dev.slice/dev-background.slice", "/dev.slice", "/"),
            strict=True,
        )], "outside its gates slice"),
        (lambda raw: [entry.update(pids_events=None, pids_max_present=False, pids_max=None) for entry in raw["cgroups"]], "no pids.events counters"),
        (lambda raw: [entry.update(memory_events=None, memory_max_present=False, memory_max=None) for entry in raw["cgroups"]], "no memory.events counters"),
    ],
)
def test_observer_response_rejects_malformed_or_unattributable_state(mutation, message):
    raw = _document(77)
    mutation(raw)
    with pytest.raises(ResourceLimitObservationError, match=message):
        resource_limits._resource_counters_from_observer(raw, 77)


def test_observer_error_document_must_be_structured():
    with pytest.raises(ResourceLimitObservationError, match="malformed error"):
        resource_limits._resource_counters_from_observer(
            {"ok": False, "contract": 1, "error": "no"}, 77
        )

    with pytest.raises(ResourceLimitObservationError, match="not an object"):
        resource_limits._resource_counters_from_observer(["not", "an", "object"], 77)


def test_ancestor_only_and_mixed_sibling_deltas_abort_without_evidence():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero)
    ancestor_only = _path_counters(zero, (1, 0, 0, 0, 0), zero, zero)
    mixed = _path_counters((1, 0, 0, 0, 0), (2, 0, 0, 0, 0), zero, zero)

    for after in (ancestor_only, mixed):
        with pytest.raises(ResourceLimitObservationError, match="shared ancestor resource event"):
            ResourceLimitEvidence.between(before, after)


@pytest.mark.parametrize(
    "path_event_counters",
    [
        [("/leaf", 0, 0, 0, 0, 0)],
        (["/leaf", 0, 0, 0, 0, 0],),
        (("/leaf", 0, 0, 0, 0),),
        ((42, 0, 0, 0, 0, 0),),
        (("relative", 0, 0, 0, 0, 0),),
        (("/leaf/./child", 0, 0, 0, 0, 0),),
        (("/leaf", 0, 0, 0, 0, 0), ("/leaf", 0, 0, 0, 0, 0)),
        (("/leaf", True, 0, 0, 0, 0),),
        (("/leaf", -1, 0, 0, 0, 0),),
    ],
)
def test_resource_counter_rejects_malformed_path_event_snapshots(path_event_counters):
    with pytest.raises(ValueError, match="path_event_counters"):
        ResourceLimitCounters(
            pids_max=0,
            memory_oom=0,
            memory_max=0,
            memory_oom_kill=0,
            memory_oom_group_kill=0,
            path_event_counters=path_event_counters,
        )


def test_candidate_delta_refuses_counter_availability_drift_and_handles_absent_leaf():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters((None, 0, 0, 0, 0), zero, zero, zero)
    after = _path_counters((0, 0, 0, 0, 0), zero, zero, zero)
    with pytest.raises(ResourceLimitObservationError, match="availability changed"):
        ResourceLimitEvidence.between(before, after)

    absent = (None, None, None, None, None)
    no_counters = _path_counters(absent, absent, absent, absent)
    evidence = ResourceLimitEvidence.between(no_counters, no_counters)
    assert not evidence.limit_hit
    assert evidence.pids_events_max == resource_limits.CounterDelta(
        before=0, after=0, delta=0
    )


@pytest.mark.parametrize("parent_delta", [0, 1])
def test_leaf_resource_delta_is_attributed_when_ancestor_is_quiet_or_matches(parent_delta):
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero)
    after = _path_counters((1, 0, 0, 0, 0), (parent_delta, 0, 0, 0, 0), zero, zero)

    evidence = ResourceLimitEvidence.between(before, after)
    assert evidence.limit_hit
    assert evidence.pids_events_max.delta == 1


def test_missing_leaf_counter_cannot_be_inferred_from_ancestor():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters((None, 0, 0, 0, 0), zero, zero, zero)
    after = _path_counters((None, 0, 0, 0, 0), (1, 0, 0, 0, 0), zero, zero)
    with pytest.raises(ResourceLimitObservationError, match="shared ancestor resource event"):
        ResourceLimitEvidence.between(before, after)


def test_cgroup_ancestry_change_and_decreasing_leaf_counter_refuse():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero)
    changed = replace(
        before,
        path_event_counters=(("/different", *zero), *before.path_event_counters[1:]),
    )
    with pytest.raises(ResourceLimitObservationError, match="ancestry samples changed"):
        ResourceLimitEvidence.between(before, changed)

    with pytest.raises(ValueError, match="non-negative integer"):
        _path_counters((-1, 0, 0, 0, 0), zero, zero, zero)


def test_hidden_ancestor_fallback_is_explicit_and_narrow(monkeypatch):
    remote = _path_counters((0, 0, 0, 0, 0), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0))

    def hidden():
        raise ResourceLimitAncestorsHiddenError("local namespace hides parent")

    monkeypatch.setattr(resource_limits, "read_current_cgroup_counters", hidden)
    monkeypatch.setattr(resource_limits, "read_resource_observer_counters", lambda path: remote)
    assert resource_limits.read_candidate_resource_counters(
        observer_socket_path="/run/cgprofile/resource-observer.sock"
    ) is remote
    with pytest.raises(ResourceLimitAncestorsHiddenError):
        resource_limits.read_candidate_resource_counters()

    def ordinary_failure():
        raise ResourceLimitObservationError("bad local mount")

    monkeypatch.setattr(resource_limits, "read_current_cgroup_counters", ordinary_failure)
    with pytest.raises(ResourceLimitObservationError, match="bad local mount"):
        resource_limits.read_candidate_resource_counters(
            observer_socket_path="/run/cgprofile/resource-observer.sock"
        )


def test_hidden_ancestor_fallback_rejects_bad_socket_path(monkeypatch):
    monkeypatch.setattr(
        resource_limits,
        "read_current_cgroup_counters",
        lambda: (_ for _ in ()).throw(ResourceLimitAncestorsHiddenError("hidden")),
    )
    for path in ("", "relative.sock"):
        with pytest.raises(ResourceLimitObservationError, match="socket path"):
            resource_limits.read_candidate_resource_counters(observer_socket_path=path)


def test_socket_client_sends_one_request_and_parses_one_response(tmp_path):
    socket_path = tmp_path / "observer.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    received = {}

    def serve_one():
        conn, _ = listener.accept()
        with conn:
            payload = bytearray()
            while b"\n" not in payload:
                payload.extend(conn.recv(4096))
            received.update(json.loads(bytes(payload).split(b"\n", 1)[0]))
            response = _document(received["args"]["thread_id"])
            conn.sendall(json.dumps(response).encode("utf-8") + b"\n")

    server = threading.Thread(target=serve_one, daemon=True)
    server.start()
    counters = resource_limits.read_resource_observer_counters(str(socket_path))
    server.join(timeout=2)
    listener.close()
    assert not server.is_alive()
    assert received["verb"] == "resource-limits"
    assert received["args"] == {"thread_id": threading.get_native_id()}
    assert received["contract"] == 1
    assert counters.path_event_counters[0][0] == LEAF


@pytest.mark.parametrize("contents", ["not-json\n", "{}\n\n"])
def test_socket_client_rejects_malformed_response_line(tmp_path, contents):
    socket_path = tmp_path / "observer.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)

    def serve_one():
        conn, _ = listener.accept()
        with conn:
            while b"\n" not in conn.recv(4096):
                pass
            conn.sendall(contents.encode("ascii"))

    server = threading.Thread(target=serve_one, daemon=True)
    server.start()
    with pytest.raises(ResourceLimitObservationError):
        resource_limits.read_resource_observer_counters(str(socket_path))
    server.join(timeout=2)
    listener.close()


def test_socket_client_rejects_deep_json_without_parser_traceback(tmp_path):
    socket_path = tmp_path / "observer.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    # Python 3.14's JSON decoder accepts more than 2,000 nested arrays; this
    # bounded 200-KiB response reaches its recursion guard while staying below
    # the observer client's 1-MiB response ceiling.
    depth = 100_000
    deeply_nested = b"[" * depth + b"0" + b"]" * depth + b"\n"

    def serve_one():
        conn, _ = listener.accept()
        with conn:
            payload = bytearray()
            while b"\n" not in payload:
                payload.extend(conn.recv(4096))
            conn.sendall(deeply_nested)

    server = threading.Thread(target=serve_one, daemon=True)
    server.start()
    with pytest.raises(ResourceLimitObservationError, match="cannot be parsed as JSON"):
        resource_limits.read_resource_observer_counters(str(socket_path))
    server.join(timeout=2)
    listener.close()
    assert not server.is_alive()


@pytest.mark.parametrize("path", ["", "relative.sock"])
def test_socket_client_requires_absolute_nonempty_path(path):
    with pytest.raises(ResourceLimitObservationError, match="socket path"):
        resource_limits.read_resource_observer_counters(path)


def test_socket_client_refuses_regular_file_and_missing_path(tmp_path):
    regular = tmp_path / "not-socket"
    regular.write_text("x", encoding="ascii")
    with pytest.raises(ResourceLimitObservationError, match="not a Unix socket"):
        resource_limits.read_resource_observer_counters(str(regular))
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect"):
        resource_limits.read_resource_observer_counters(str(tmp_path / "missing.sock"))


@pytest.mark.parametrize("failure", ["oversize", "connect"])
def test_socket_client_bounds_response_and_wraps_transport_errors(
    tmp_path, monkeypatch, failure
):
    socket_path = tmp_path / "observer.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.close()

    class FakeClient:
        closed = False

        def settimeout(self, _value):
            pass

        def connect(self, _path):
            if failure == "connect":
                raise OSError("connection refused")

        def sendall(self, _payload):
            pass

        def recv(self, _size):
            return b"x" * (1024 * 1024 + 1)

        def close(self):
            self.closed = True

    client = FakeClient()
    monkeypatch.setattr(
        resource_limits.socket,
        "socket",
        lambda *_args, **_kwargs: client,
    )
    expected = "response exceeds 1 MiB" if failure == "oversize" else "socket request failed"
    with pytest.raises(ResourceLimitObservationError, match=expected):
        resource_limits.read_resource_observer_counters(str(socket_path))
    assert client.closed
