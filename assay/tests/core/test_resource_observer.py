from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

import assay.resource_limits as resource_limits
from assay.resource_limits import (
    ResourceLimitAncestorsHiddenError,
    ResourceLimitCounters,
    ResourceLimitEvidence,
    ResourceLimitObservationError,
)


_INSPECT_CURRENT_CGROUP_OBSERVATION = (
    resource_limits.inspect_current_cgroup_observation
)


GATES = "/dev.slice/dev-gates.slice"
LEAF = f"{GATES}/run-gate-r2-test.scope"
MEMORY = {"max": 0, "oom": 0, "oom_kill": 0, "oom_group_kill": 0}


def _between(before, after):
    return ResourceLimitEvidence.between(before, after)[0]


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
            "pids_events_local": {"max": 0},
            "memory_events_local": dict(MEMORY),
        })
    return {
        "ok": True,
        "contract": 2,
        "cgroup_version": 2,
        "thread_id": thread_id,
        "gates_slice": GATES,
        "pids_events_scope": "hierarchical",
        "memory_events_scope": "hierarchical",
        "cgroups": cgroups,
    }


def _path_counters(leaf, *ancestors, local=None):
    paths = (LEAF, GATES, "/dev.slice", "/")
    values = (leaf, *ancestors)
    assert len(values) == len(paths)
    local_values = values if local is None else local
    assert len(local_values) == len(paths)
    return ResourceLimitCounters(
        pids_max=sum(value[0] or 0 for value in values),
        memory_max=sum(value[1] or 0 for value in values),
        memory_oom=sum(value[2] or 0 for value in values),
        memory_oom_kill=sum(value[3] or 0 for value in values),
        memory_oom_group_kill=sum(value[4] or 0 for value in values),
        limit_signature=tuple(
            (path, 31, True, True, True, True, True, True, False, False)
            for path in paths
        ),
        limit_values=((None, None),) * len(paths),
        path_event_counters=tuple(
            (path, *value, *local)
            for path, value, local in zip(paths, values, local_values, strict=True)
        ),
    )


def test_observer_response_becomes_strict_ancestry_bound_counters():
    counters = resource_limits._resource_counters_from_observer(_document(77), 77)

    assert counters.pids_max == 0
    assert counters.memory_max == 0
    assert counters.limit_signature[0] == (
        LEAF, 31, True, True, True, True, True, True, False, False
    )
    assert counters.limit_values[0] == (None, None)
    assert counters.path_event_counters[0] == (LEAF, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    assert counters.path_event_counters[-1] == ("/", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def test_observer_parser_preserves_distinct_hierarchical_and_local_counters():
    document = _document(77)
    leaf, parent = document["cgroups"][:2]
    leaf["pids_events"] = {"max": 3}
    leaf["pids_events_local"] = {"max": 1}
    parent["pids_events"] = {"max": 8}
    parent["pids_events_local"] = {"max": 2}
    leaf["memory_events"] = {"max": 5, "oom": 4, "oom_kill": 3, "oom_group_kill": 2}
    leaf["memory_events_local"] = {"max": 1, "oom": 1, "oom_kill": 0, "oom_group_kill": 0}
    parent["memory_events"] = {"max": 9, "oom": 8, "oom_kill": 7, "oom_group_kill": 6}
    parent["memory_events_local"] = {"max": 2, "oom": 2, "oom_kill": 1, "oom_group_kill": 0}

    counters = resource_limits._resource_counters_from_observer(document, 77)

    assert counters.path_event_counters[0] == (LEAF, 3, 5, 4, 3, 2, 1, 1, 1, 0, 0)
    assert counters.path_event_counters[1] == (
        GATES, 8, 9, 8, 7, 6, 2, 2, 2, 1, 0
    )


def test_observer_contract_2_without_hierarchical_local_counters_is_refused():
    document = _document(77)
    document["cgroups"][0]["pids_events_local"] = None

    with pytest.raises(ResourceLimitObservationError, match="pids.events.local is unavailable"):
        resource_limits._resource_counters_from_observer(document, 77)


def test_observer_contract_1_success_is_refused_without_local_counters():
    legacy = _document(77)
    legacy["contract"] = 1
    legacy.pop("pids_events_scope")
    legacy.pop("memory_events_scope")
    for cgroup in legacy["cgroups"]:
        cgroup.pop("pids_events_local")
        cgroup.pop("memory_events_local")

    with pytest.raises(ResourceLimitObservationError, match="contract version 2 is required"):
        resource_limits._resource_counters_from_observer(legacy, 77)


def test_observer_localevents_regular_file_supplies_local_counters():
    document = _document(77)
    document["pids_events_scope"] = "local"
    for cgroup in document["cgroups"]:
        cgroup["pids_events_local"] = None

    counters = resource_limits._resource_counters_from_observer(document, 77)

    assert counters.limit_signature[0][8] is True
    assert counters.limit_signature[0][9] is False
    assert counters.path_event_counters[0][1] == counters.path_event_counters[0][6]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.update(ok=False, error={"message": "peer refused"}), "peer refused"),
        (lambda raw: raw.update(extra=True), "unknown or missing fields"),
        (lambda raw: raw.update(contract=True), "contract version"),
        (lambda raw: raw.update(cgroup_version=1), "cgroup v2"),
        (lambda raw: raw.update(thread_id=78), "thread identity"),
        (lambda raw: raw.update(gates_slice="relative"), "not absolute"),
        (lambda raw: raw.update(pids_events_scope=[]), "pids event scope is malformed"),
        (lambda raw: raw.update(memory_events_scope="unknown"), "memory event scope is malformed"),
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
        (lambda raw: raw["cgroups"][0].update(pids_events_local={"other": 0}), "pids.events.local fields are malformed"),
        (lambda raw: raw["cgroups"][0].update(pids_events={"max": True}), "pids.events counters are malformed"),
        (lambda raw: raw["cgroups"][0].update(memory_events=None), "memory.max lacks memory.events"),
        (lambda raw: raw["cgroups"][0].update(pids_events=None), "pids.max lacks pids.events"),
        (lambda raw: raw["cgroups"].pop(), "does not reach the hierarchy root"),
        (lambda raw: [entry.update(path=path) for entry, path in zip(
            raw["cgroups"],
            ("/dev.slice/dev-background.slice/x.scope", "/dev.slice/dev-background.slice", "/dev.slice", "/"),
            strict=True,
        )], "outside its gates slice"),
        (lambda raw: [entry.update(pids_events=None, pids_events_local=None, pids_max_present=False, pids_max=None) for entry in raw["cgroups"]], "no pids.events counters"),
        (lambda raw: [entry.update(memory_events=None, memory_events_local=None, memory_max_present=False, memory_max=None) for entry in raw["cgroups"]], "no memory.events counters"),
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


def test_ancestor_local_and_candidate_deltas_count_as_limit_hits():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero)
    ancestor_only = _path_counters(zero, (1, 0, 0, 0, 0), zero, zero)
    mixed = _path_counters((1, 0, 0, 0, 0), (2, 0, 0, 0, 0), zero, zero)

    ancestor_evidence = _between(before, ancestor_only)
    assert ancestor_evidence.limit_hit
    assert ancestor_evidence.pids_events_max.delta == 1
    mixed_evidence = _between(before, mixed)
    assert mixed_evidence.limit_hit


def test_sibling_hierarchical_delta_does_not_change_ancestor_local_sample():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero, local=(zero,) * 4)
    sibling_only = _path_counters(
        zero,
        (1, 0, 0, 0, 0),
        zero,
        zero,
        local=(zero,) * 4,
    )

    evidence = _between(before, sibling_only)
    assert not evidence.limit_hit
    assert evidence.pids_events_max.delta == 0


def test_candidate_local_delta_is_detected_if_regular_read_misses_it():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero, local=(zero,) * 4)
    candidate_local = (1, 0, 0, 0, 0)
    after = _path_counters(
        zero, zero, zero, zero,
        local=(candidate_local, zero, zero, zero),
    )

    evidence = _between(before, after)

    assert evidence.pids_events_max.delta == 1
    assert evidence.limit_hit


@pytest.mark.parametrize(
    "path_event_counters",
        [
        [("/leaf", *([0] * 10))],
        (["/leaf", *([0] * 10)],),
        (("/leaf", *([0] * 9)),),
        ((42, *([0] * 10)),),
        (("relative", *([0] * 10)),),
        (("/leaf/./child", *([0] * 10)),),
        (("/leaf", *([0] * 10)), ("/leaf", *([0] * 10))),
        (("/leaf", True, *([0] * 9)),),
        (("/leaf", -1, *([0] * 9)),),
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
        _between(before, after)

    absent = (None, None, None, None, None)
    no_counters = _path_counters(absent, absent, absent, absent)
    evidence = _between(no_counters, no_counters)
    assert not evidence.limit_hit
    assert evidence.pids_events_max == resource_limits.CounterDelta(
        before=0, after=0, delta=0
    )


@pytest.mark.parametrize("parent_delta", [0, 1])
def test_leaf_resource_delta_is_attributed_when_ancestor_is_quiet_or_matches(parent_delta):
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero)
    after = _path_counters((1, 0, 0, 0, 0), (parent_delta, 0, 0, 0, 0), zero, zero)

    evidence = _between(before, after)
    assert evidence.limit_hit
    assert evidence.pids_events_max.delta == 1


def test_missing_leaf_counter_cannot_be_inferred_from_ancestor():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters((None, 0, 0, 0, 0), zero, zero, zero)
    after = _path_counters((None, 0, 0, 0, 0), (1, 0, 0, 0, 0), zero, zero)
    with pytest.raises(ResourceLimitObservationError, match="shared ancestor resource event"):
        _between(before, after)


def test_cgroup_ancestry_change_and_decreasing_leaf_counter_refuse():
    zero = (0, 0, 0, 0, 0)
    before = _path_counters(zero, zero, zero, zero)
    changed = replace(
        before,
        path_event_counters=(
            ("/different", *before.path_event_counters[0][1:]),
            *before.path_event_counters[1:],
        ),
    )
    with pytest.raises(ResourceLimitObservationError, match="ancestry samples changed"):
        _between(before, changed)

    with pytest.raises(ValueError, match="non-negative integer"):
        _path_counters((-1, 0, 0, 0, 0), zero, zero, zero)


def test_hidden_ancestor_fallback_is_explicit_and_narrow(monkeypatch):
    remote = _path_counters((0, 0, 0, 0, 0), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0))

    def hidden():
        raise ResourceLimitAncestorsHiddenError("local namespace hides parent")

    monkeypatch.setattr(resource_limits, "read_current_cgroup_counters", hidden)
    monkeypatch.setattr(resource_limits, "read_resource_observer_counters", lambda path: remote)
    assert resource_limits.read_candidate_resource_counters(
        observer_socket_path="/run/cgprofile-observer/resource-observer.sock"
    ) is remote
    with pytest.raises(ResourceLimitAncestorsHiddenError):
        resource_limits.read_candidate_resource_counters()

    def ordinary_failure():
        raise ResourceLimitObservationError("bad local mount")

    monkeypatch.setattr(resource_limits, "read_current_cgroup_counters", ordinary_failure)
    with pytest.raises(ResourceLimitObservationError, match="bad local mount"):
        resource_limits.read_candidate_resource_counters(
            observer_socket_path="/run/cgprofile-observer/resource-observer.sock"
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
    assert received["contract"] == 2
    assert counters.path_event_counters[0][0] == LEAF


def test_socket_client_deadline_bounds_a_slowly_streaming_peer(tmp_path, monkeypatch):
    socket_path = tmp_path / "observer.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    monkeypatch.setattr(
        resource_limits, "_RESOURCE_OBSERVER_REQUEST_TIMEOUT_SECONDS", 0.05
    )

    def serve_slowly():
        conn, _ = listener.accept()
        with conn:
            request_bytes = bytearray()
            while b"\n" not in request_bytes:
                request_bytes.extend(conn.recv(4096))
            request = json.loads(bytes(request_bytes).split(b"\n", 1)[0])
            response = json.dumps(
                _document(request["args"]["thread_id"])
            ).encode("utf-8") + b"\n"
            for offset in range(0, len(response), 16):
                try:
                    conn.sendall(response[offset : offset + 16])
                except OSError:
                    return
                time.sleep(0.01)

    server = threading.Thread(target=serve_slowly, daemon=True)
    server.start()
    try:
        with pytest.raises(ResourceLimitObservationError, match="timed out"):
            resource_limits.read_resource_observer_counters(str(socket_path))
    finally:
        server.join(timeout=2)
        listener.close()
    assert not server.is_alive()


def test_observation_preflight_bounds_a_stuck_probe_thread(monkeypatch):
    workers = []

    class StuckWorker:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
            self.join_timeout = None
            self.alive_checks = 0
            workers.append(self)

        def start(self):
            self.started = True

        def join(self, timeout=None):
            self.join_timeout = timeout

        def is_alive(self):
            self.alive_checks += 1
            return self.started

    monkeypatch.setattr(
        resource_limits, "_RESOURCE_OBSERVER_REQUEST_TIMEOUT_SECONDS", 0.05
    )
    monkeypatch.setattr(
        resource_limits, "_RESOURCE_OBSERVER_PREFLIGHT_JOIN_GRACE_SECONDS", 0.05
    )
    # The autouse conftest fixture makes ordinary tests independent of the
    # host cgroup view by replacing this module attribute with an available
    # result. Restore the production function for this capability test, and
    # replace only its local Thread dependency so no process-wide thread class
    # is changed while the suite runs.
    monkeypatch.setattr(
        resource_limits,
        "inspect_current_cgroup_observation",
        _INSPECT_CURRENT_CGROUP_OBSERVATION,
    )
    monkeypatch.setattr(
        resource_limits, "threading", SimpleNamespace(Thread=StuckWorker)
    )
    capability = resource_limits.inspect_current_cgroup_observation()

    assert len(workers) == 1
    assert workers[0].started
    assert workers[0].name == "assay-cgroup-observation-preflight"
    assert workers[0].daemon is True
    assert callable(workers[0].target)
    assert workers[0].join_timeout == pytest.approx(0.1)
    assert workers[0].alive_checks == 1
    assert not capability.available
    assert "did not complete before its deadline" in capability.reason


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


def test_socket_client_refuses_regular_file_and_missing_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        resource_limits, "_RESOURCE_OBSERVER_RESTART_RETRY_SECONDS", 0.0
    )
    regular = tmp_path / "not-socket"
    regular.write_text("x", encoding="ascii")
    with pytest.raises(ResourceLimitObservationError, match="not a Unix socket"):
        resource_limits.read_resource_observer_counters(str(regular))
    with pytest.raises(ResourceLimitObservationError, match="cannot inspect"):
        resource_limits.read_resource_observer_counters(str(tmp_path / "missing.sock"))


def test_socket_client_retries_when_daemon_replaces_observer_socket(tmp_path, monkeypatch):
    socket_path = tmp_path / "observer.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()
    monkeypatch.setattr(
        resource_limits, "_RESOURCE_OBSERVER_RESTART_RETRY_SECONDS", 1.0
    )
    monkeypatch.setattr(
        resource_limits, "_RESOURCE_OBSERVER_RETRY_INTERVAL_SECONDS", 0.01
    )

    def replace_socket_and_serve():
        time.sleep(0.05)
        socket_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        listener.listen(1)
        try:
            conn, _ = listener.accept()
            with conn:
                payload = bytearray()
                while b"\n" not in payload:
                    payload.extend(conn.recv(4096))
                request = json.loads(bytes(payload).split(b"\n", 1)[0])
                response = _document(request["args"]["thread_id"])
                conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
        finally:
            listener.close()

    server = threading.Thread(target=replace_socket_and_serve, daemon=True)
    server.start()
    counters = resource_limits.read_resource_observer_counters(str(socket_path))
    server.join(timeout=2)
    assert not server.is_alive()
    assert counters.path_event_counters[0][0] == LEAF


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
