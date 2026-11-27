import json
import os
import socket
import struct

import pytest

from fleetd.admission import AdmissionError, ExitCode
from fleetd import agent_protocol
from fleetd.agent_policy import load_agent_policy
from fleetd.agent_protocol import (
    ARTIFACT_NAMES,
    MAX_FETCH_BYTES,
    MAX_FRAME_BYTES,
    FrameError,
    read_frame,
    require_end_of_input,
    validate_relative_path,
    validate_request,
    write_frame,
)


SHA = "a" * 40
SHA256 = "b" * 64


def _recv_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.recv(remaining)
        assert chunk, "framed response ended before its declared length"
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_fd_exact(fd, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = os.read(fd, remaining)
        assert chunk, "framed response ended before its declared length"
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _policy(tmp_path, identities="", extra_argv='[["--verbose"]]'):
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()
    path = tmp_path / "agent.toml"
    identity_table = identities or '''[[identities]]
transport = "peer"
principal = "controller-devcontainer"
group = "controller"
uid = 1000
gid = 100

[[identities]]
transport = "forced-command"
principal = "worker"
group = "worker"
'''
    path.write_text(f'''schema_version = 1
idle_connection_s = 20
query_timeout_s = 5
legacy_jobs_dir = "{tmp_path / 'legacy-jobs'}"
lease_reconcile_interval_s = 30
lease_bind_timeout_s = 10
max_connections = 4
runner_user = "vscode"
requested_by_clients = ["fleetd"]
extra_argv = {extra_argv}
dev_argv = [["status"]]

[repo_roots]
vbpub = "{checkouts}"

[env_values]
CI = ["1"]

{identity_table}''', encoding="utf-8")
    checkout = checkouts / SHA
    checkout.mkdir()
    return load_agent_policy(path), checkout


def _submit():
    return {
        "verb": "submit",
        "job_id": "controller-run-1",
        "repo": "vbpub",
        "sha": SHA,
        "base": None,
        "project": ".",
        "lane": "r0-r1",
        "shard": None,
        "resources": {
            "planned_cores": 1.5,
            "cpu_set": None,
            "footprint": "small",
            "declared_peak_bytes": None,
        },
        "priority": "gate",
        "requested_by": {"client": "fleetd", "version": "1.2.3", "run": "run-1"},
        "stores": {"collect": None, "seed": None},
        "extra_argv": [],
        "env": {},
        "dev": None,
        "kind": "lane",
    }


def test_read_and_write_use_capped_big_endian_json_frames():
    client, server = socket.socketpair()
    try:
        body = b'{"verb":"probe"}'
        client.sendall(struct.pack(">I", len(body)) + body)
        assert read_frame(server, 2) == {"verb": "probe"}

        write_frame(server, {"ok": True})
        header = _recv_exact(client, 4)
        length = struct.unpack(">I", header)[0]
        assert length <= MAX_FRAME_BYTES
        response_body = _recv_exact(client, length)
        assert json.loads(response_body) == {"ok": True}
        assert header + response_body == struct.pack(">I", length) + b'{"ok":true}'
    finally:
        client.close()
        server.close()


def test_read_frame_absolute_deadline_accepts_ready_frame_without_host_timing(monkeypatch):
    monkeypatch.setattr(agent_protocol.time, "monotonic", lambda: 1.0)
    client, server = socket.socketpair()
    try:
        expected = {"frame": "complete"}
        write_frame(client, expected)
        assert read_frame(server, None, deadline=2.0) == expected
    finally:
        client.close()
        server.close()


def test_read_frame_absolute_deadline_refuses_before_waiting_when_already_expired():
    client, server = socket.socketpair()
    waits = []

    def waiter(*args):
        waits.append(args)
        return [], [], []

    try:
        with pytest.raises(FrameError, match="frame deadline expired"):
            read_frame(server, None, deadline=10.0, clock=lambda: 10.0, waiter=waiter)
        assert waits == []
    finally:
        client.close()
        server.close()


def test_read_frame_absolute_deadline_checks_time_after_reading_a_chunk():
    client, server = socket.socketpair()
    clock_values = iter((0.0, 0.0, 10.0))
    waits = []

    def waiter(*args):
        waits.append(args)
        return [args[0][0]], [], []

    try:
        write_frame(client, {"frame": "too-late"})
        with pytest.raises(FrameError, match="frame deadline expired"):
            read_frame(server, None, deadline=10.0, clock=lambda: next(clock_values), waiter=waiter)
        assert len(waits) == 1
    finally:
        client.close()
        server.close()


def test_same_frame_helpers_work_on_stdin_stdout_style_pipes():
    read_fd, write_fd = os.pipe()
    try:
        body = b'{"verb":"facts"}'
        os.write(write_fd, struct.pack(">I", len(body)) + body)
        os.close(write_fd)
        write_fd = -1
        assert read_frame(read_fd, 2) == {"verb": "facts"}
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)

    read_fd, write_fd = os.pipe()
    try:
        write_frame(write_fd, {"ok": True})
        os.close(write_fd)
        write_fd = -1
        header = _read_fd_exact(read_fd, 4)
        length = struct.unpack(">I", header)[0]
        response_body = _read_fd_exact(read_fd, length)
        assert json.loads(response_body) == {"ok": True}
        assert header + response_body == struct.pack(">I", length) + b'{"ok":true}'
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


@pytest.mark.parametrize(
    ("header", "body", "message"),
    [
        (0, b"", "outside 1"),
        (MAX_FRAME_BYTES + 1, b"", "outside 1"),
        (3, b"{}", "truncated frame"),
        (1, b"\xff", "not UTF-8"),
        (2, b"[]", "JSON object"),
        (23, b'{"verb":"x","verb":"y"}', "duplicate JSON object key"),
        (4, b"NaN ", "invalid JSON constant"),
    ],
)
def test_read_frame_rejects_invalid_wire_shapes(header, body, message):
    client, server = socket.socketpair()
    try:
        client.sendall(struct.pack(">I", header) + body)
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(FrameError, match=message):
            read_frame(server, 2)
    finally:
        client.close()
        server.close()


def test_read_frame_maps_json_parser_depth_to_frame_error(monkeypatch):
    body = b'{"x":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"

    def depth_error(*_args, **_kwargs):
        raise RecursionError("maximum parser depth exceeded")

    monkeypatch.setattr("fleetd.agent_protocol.json.loads", depth_error)
    client, server = socket.socketpair()
    try:
        client.sendall(struct.pack(">I", len(body)) + body)
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(FrameError, match="not valid JSON"):
            read_frame(server, 2)
    finally:
        client.close()
        server.close()


def test_read_frame_maps_integer_digit_limit_to_frame_error():
    body = b"9" * 5000
    client, server = socket.socketpair()
    try:
        client.sendall(struct.pack(">I", len(body)) + body)
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(FrameError, match="not valid JSON"):
            read_frame(server, 2)
    finally:
        client.close()
        server.close()


def test_read_frame_drops_an_idle_stream_without_waiting(monkeypatch):
    client, server = socket.socketpair()
    monkeypatch.setattr("fleetd.agent_protocol.select.select", lambda *_args: ([], [], []))
    try:
        with pytest.raises(FrameError, match="idle connection deadline"):
            read_frame(server, 2)
    finally:
        client.close()
        server.close()


def test_read_frame_maps_descriptor_select_and_read_failures(monkeypatch):
    class MissingDescriptor:
        def fileno(self):
            raise OSError("closed stream")

    class InvalidDescriptor:
        def fileno(self):
            return -1

    with pytest.raises(FrameError, match="must expose a file descriptor"):
        read_frame(MissingDescriptor(), None)
    with pytest.raises(FrameError, match="invalid file descriptor"):
        read_frame(InvalidDescriptor(), None)

    monkeypatch.setattr(
        "fleetd.agent_protocol.select.select",
        lambda *_args: (_ for _ in ()).throw(ValueError("bad select fd")),
    )
    with pytest.raises(FrameError, match="cannot wait for framed input"):
        read_frame(123, None)

    monkeypatch.setattr("fleetd.agent_protocol.select.select", lambda readers, *_args: (readers, [], []))
    monkeypatch.setattr(
        "fleetd.agent_protocol.os.read",
        lambda *_args: (_ for _ in ()).throw(OSError("read denied")),
    )
    with pytest.raises(FrameError, match="cannot read framed input"):
        read_frame(123, None)


def test_untimed_frame_writes_map_os_errors_and_zero_progress(monkeypatch):
    monkeypatch.setattr(
        "fleetd.agent_protocol.os.write",
        lambda *_args: (_ for _ in ()).throw(OSError("write denied")),
    )
    with pytest.raises(FrameError, match="cannot write framed response"):
        agent_protocol._write_all(123, b"response", None)

    monkeypatch.setattr("fleetd.agent_protocol.os.write", lambda *_args: 0)
    with pytest.raises(FrameError, match="write made no progress"):
        agent_protocol._write_all(123, b"response", None)


@pytest.mark.parametrize("timeout", [True, "1", 0, -1, float("inf"), 10 ** 1000])
def test_timed_frame_writes_reject_invalid_deadlines(timeout):
    with pytest.raises(FrameError, match="response idle timeout"):
        agent_protocol._write_all(123, b"response", timeout)


def test_timed_frame_writes_restore_blocking_mode_after_partial_progress(monkeypatch):
    written = bytearray()
    attempts = [0]
    blocking_changes = []

    def write(_fd, content):
        attempts[0] += 1
        if attempts[0] == 1:
            raise BlockingIOError("try again")
        count = min(2, len(content))
        written.extend(content[:count])
        return count

    monkeypatch.setattr("fleetd.agent_protocol.os.get_blocking", lambda _fd: True)
    monkeypatch.setattr("fleetd.agent_protocol.os.set_blocking", lambda _fd, value: blocking_changes.append(value))
    monkeypatch.setattr("fleetd.agent_protocol.select.select", lambda _r, writers, _e, _t: ([], writers, []))
    monkeypatch.setattr("fleetd.agent_protocol.os.write", write)
    monkeypatch.setattr("fleetd.agent_protocol.time.monotonic", lambda: 1.0)

    agent_protocol._write_all(123, b"response", 5)

    assert bytes(written) == b"response"
    assert attempts[0] == 5
    assert blocking_changes == [False, True]


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("get-blocking", "cannot configure"),
        ("set-nonblocking", "cannot configure"),
        ("select", "cannot wait for framed response"),
        ("not-writable", "write idle deadline"),
        ("write", "cannot write framed response"),
        ("no-progress", "write made no progress"),
        ("deadline", "write idle deadline"),
    ],
)
def test_timed_frame_write_failures_are_reported_and_restore_mode(monkeypatch, failure, message):
    blocking_changes = []

    def get_blocking(_fd):
        if failure == "get-blocking":
            raise OSError("cannot inspect mode")
        return True

    def set_blocking(_fd, value):
        blocking_changes.append(value)
        if failure == "set-nonblocking" and value is False:
            raise OSError("cannot change mode")

    def select_waiter(_readers, writers, _errors, _timeout):
        if failure == "select":
            raise ValueError("select rejected descriptor")
        return ([], [], []) if failure == "not-writable" else ([], writers, [])

    def write(_fd, _content):
        if failure == "write":
            raise OSError("write failed")
        if failure == "no-progress":
            return 0
        return 1

    clock_values = iter([1.0, 2.0])
    monkeypatch.setattr("fleetd.agent_protocol.os.get_blocking", get_blocking)
    monkeypatch.setattr("fleetd.agent_protocol.os.set_blocking", set_blocking)
    monkeypatch.setattr("fleetd.agent_protocol.select.select", select_waiter)
    monkeypatch.setattr("fleetd.agent_protocol.os.write", write)
    monkeypatch.setattr("fleetd.agent_protocol.time.monotonic", lambda: next(clock_values))

    with pytest.raises(FrameError, match=message):
        agent_protocol._write_all(123, b"response", 1 if failure == "deadline" else 5)
    expected_changes = {
        "get-blocking": [], "set-nonblocking": [False],
    }.get(failure, [False, True])
    assert blocking_changes == expected_changes


def test_timed_frame_write_reports_failure_to_restore_blocking_mode(monkeypatch):
    calls = []

    def set_blocking(_fd, value):
        calls.append(value)
        if value is True:
            raise OSError("restore denied")

    monkeypatch.setattr("fleetd.agent_protocol.os.get_blocking", lambda _fd: True)
    monkeypatch.setattr("fleetd.agent_protocol.os.set_blocking", set_blocking)
    monkeypatch.setattr("fleetd.agent_protocol.select.select", lambda _r, writers, _e, _t: ([], writers, []))
    monkeypatch.setattr("fleetd.agent_protocol.os.write", lambda _fd, content: len(content))
    monkeypatch.setattr("fleetd.agent_protocol.time.monotonic", lambda: 1.0)

    with pytest.raises(FrameError, match="cannot restore framed response stream"):
        agent_protocol._write_all(123, b"response", 5)
    assert calls == [False, True]


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf")])
def test_read_frame_rejects_invalid_idle_deadlines(timeout):
    with pytest.raises(FrameError, match="idle timeout"):
        read_frame(0, timeout)


@pytest.mark.parametrize(
    "timeout", [0, -1, True, float("inf"), 10 ** 10000],
    ids=["zero", "negative", "bool", "infinite", "float-overflow"],
)
def test_require_end_of_input_rejects_invalid_deadlines(timeout):
    with pytest.raises(FrameError, match="idle timeout"):
        require_end_of_input(object(), timeout)


def test_require_end_of_input_rejects_invalid_descriptors_and_wait_failures(monkeypatch):
    class InvalidDescriptor:
        def fileno(self):
            return -1

    with pytest.raises(FrameError, match="file descriptor"):
        require_end_of_input(InvalidDescriptor(), 1)
    with pytest.raises(FrameError, match="expose a file descriptor"):
        require_end_of_input(object(), 1)

    client, server = socket.socketpair()
    try:
        monkeypatch.setattr("fleetd.agent_protocol.select.select", lambda *_args: ([], [], []))
        with pytest.raises(FrameError, match="before request end"):
            require_end_of_input(server, 1)

        def failed_select(*_args):
            raise OSError("select failed")

        monkeypatch.setattr("fleetd.agent_protocol.select.select", failed_select)
        with pytest.raises(FrameError, match="cannot wait for request end"):
            require_end_of_input(server, 1)
    finally:
        client.close()
        server.close()


def test_require_end_of_input_maps_read_errors(monkeypatch):
    client, server = socket.socketpair()
    try:
        monkeypatch.setattr(
            "fleetd.agent_protocol.select.select",
            lambda readers, _writers, _errors, _timeout: (readers, [], []),
        )

        def failed_read(*_args):
            raise OSError("read failed")

        monkeypatch.setattr("fleetd.agent_protocol.os.read", failed_read)
        with pytest.raises(FrameError, match="cannot read request end"):
            require_end_of_input(server, 1)
    finally:
        client.close()
        server.close()


def test_write_frame_refuses_oversized_or_invalid_responses():
    client, server = socket.socketpair()
    try:
        with pytest.raises(FrameError, match="exceeds"):
            write_frame(server, {"payload": "x" * MAX_FRAME_BYTES})
        with pytest.raises(FrameError, match="cannot encode"):
            write_frame(server, {"payload": float("nan")})
        with pytest.raises(FrameError, match="cannot encode"):
            write_frame(object(), {})
        class InvalidDescriptor:
            def fileno(self):
                return -1

        with pytest.raises(FrameError, match="invalid file descriptor"):
            write_frame(InvalidDescriptor(), {"ok": True})
    finally:
        client.close()
        server.close()


def test_require_end_of_input_rejects_any_bytes_after_the_single_frame():
    client, server = socket.socketpair()
    try:
        client.sendall(b"extra")
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(FrameError, match="unexpected bytes after request frame"):
            require_end_of_input(server, 1)
    finally:
        client.close()
        server.close()


def test_require_end_of_input_accepts_eof_after_the_one_request_frame():
    client, server = socket.socketpair()
    try:
        client.shutdown(socket.SHUT_WR)
        assert require_end_of_input(server, 1) is None
    finally:
        client.close()
        server.close()


def test_validate_relative_path_rejects_absolute_parent_and_nonportable_paths():
    assert validate_relative_path(".", "project", allow_dot=True) == "."
    with pytest.raises(AdmissionError, match="safe relative path"):
        validate_relative_path("/etc/passwd", "path")
    with pytest.raises(AdmissionError, match="parent path"):
        validate_relative_path("../outside", "path")
    with pytest.raises(AdmissionError, match="safe relative path"):
        validate_relative_path("dir\\file", "path")
    with pytest.raises(AdmissionError, match="empty, dot, or parent"):
        validate_relative_path("dir//file", "path")


def test_validate_request_accepts_closed_submit_and_protocol_verbs(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)

    request = _submit()
    assert validate_request(request, identity, policy) is request
    assert validate_request({"verb": "probe"}, identity, policy)["verb"] == "probe"
    assert validate_request({"verb": "facts"}, identity, policy)["verb"] == "facts"
    assert validate_request({"verb": "have", "repo": "vbpub"}, identity, policy)["repo"] == "vbpub"


@pytest.mark.parametrize(
    ("edit", "fragment"),
    [
        (lambda request: request["resources"].update(footprint=[]), "resources.footprint is invalid"),
        (lambda request: request.update(priority={}), "priority is invalid"),
        (lambda request: request.update(kind=[]), "kind is invalid"),
    ],
)
def test_validate_request_type_checks_closed_submit_enums(tmp_path, edit, fragment):
    policy, _checkout = _policy(tmp_path)
    request = _submit()
    edit(request)

    with pytest.raises(AdmissionError, match=fragment) as raised:
        validate_request(request, policy.resolve_peer(1000, 100), policy)
    assert raised.value.exit_code == ExitCode.REFUSAL


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("footprint", [], "lease.footprint is invalid"),
        ("priority", {}, "lease.priority is invalid"),
    ],
)
def test_validate_request_type_checks_closed_lease_enums(tmp_path, field, value, fragment):
    policy, _checkout = _policy(tmp_path)
    request = {
        "verb": "lease", "lease_id": "controller-lease-1", "container_name": "rg-test",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    request[field] = value

    with pytest.raises(AdmissionError, match=fragment) as raised:
        validate_request(request, policy.resolve_peer(1000, 100), policy)
    assert raised.value.exit_code == ExitCode.REFUSAL


@pytest.mark.parametrize(
    ("edit", "fragment"),
    [
        (lambda data: data.update(group="controller"), "unknown field.*group"),
        (lambda data: data.update(job_id="worker-run-1"), "not prefixed by the authenticated group"),
        (lambda data: data.update(repo="bad/repo"), "valid repository name"),
        (lambda data: data.update(repo="a" * 40), "valid repository name"),
        (lambda data: data.update(repo="not-allowed"), "not on the host allow-list"),
        (lambda data: data.update(sha="short"), "full hexadecimal commit id"),
        (lambda data: data.update(project="/etc"), "safe relative path"),
        (lambda data: data.update(project="../outside"), "parent path"),
        (lambda data: data["resources"].update(planned_cores=True), "finite number"),
        (lambda data: data["resources"].update(cpu_set=[0]), "reserved"),
        (lambda data: data.update(shard={"index": 0, "total": 1}), "reserved"),
        (lambda data: data.update(extra_argv=["--unsafe"]), "not on the host allow-list"),
        (lambda data: data.update(env={"PATH": "/tmp"}), "not on the host allow-list"),
        (lambda data: data.update(requested_by={"client": "unknown", "version": "1.2.3", "run": "r"}), "not on the host allow-list"),
        (lambda data: data.update(extra=True), "unknown field.*extra"),
    ],
)
def test_validate_request_rejects_untrusted_or_malformed_submit_fields(tmp_path, edit, fragment):
    policy, _checkout = _policy(tmp_path)
    request = _submit()
    edit(request)

    with pytest.raises(AdmissionError, match=fragment):
        validate_request(request, policy.resolve_peer(1000, 100), policy)


def test_validate_request_checks_symlink_escapes_and_store_paths(tmp_path):
    policy, checkout = _policy(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (checkout / "escape").symlink_to(outside, target_is_directory=True)
    request = _submit()
    request["project"] = "escape"
    with pytest.raises(AdmissionError, match="project escapes"):
        validate_request(request, policy.resolve_peer(1000, 100), policy)

    request = _submit()
    request["stores"] = {"collect": "escape/file", "seed": None}
    with pytest.raises(AdmissionError, match="stores.collect escapes"):
        validate_request(request, policy.resolve_peer(1000, 100), policy)


def test_validate_request_checks_put_and_fetch_closed_names_and_ranges(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)

    assert validate_request(
        {"verb": "put", "name": "candidates", "sha256": SHA256, "bytes": 12}, identity, policy,
    )["name"] == "candidates"
    assert validate_request(
        {"verb": "put", "name": "assay-seed-17.tgz", "sha256": SHA256, "bytes": 12}, identity, policy,
    )["name"] == "assay-seed-17.tgz"
    assert validate_request(
        {"verb": "put", "name": "assay-seed-17.tgz.sha256", "sha256": SHA256, "bytes": 12}, identity, policy,
    )["name"] == "assay-seed-17.tgz.sha256"
    with pytest.raises(AdmissionError, match="closed artifact set"):
        validate_request(
            {"verb": "put", "name": "../../outside", "sha256": SHA256, "bytes": 12}, identity, policy,
        )

    assert ARTIFACT_NAMES
    assert validate_request(
        {"verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log", "offset": 0, "length": 1},
        identity, policy,
    )["artifact"] == "job.log"
    with pytest.raises(AdmissionError, match="closed fetch set"):
        validate_request(
            {"verb": "fetch", "job_id": "controller-run-1", "artifact": "secret", "offset": 0, "length": 1},
            identity, policy,
        )
    with pytest.raises(AdmissionError, match="length"):
        validate_request(
            {"verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log", "offset": 0,
             "length": MAX_FETCH_BYTES + 1},
            identity, policy,
        )


def test_validate_request_uses_transport_identity_and_restricts_lease(tmp_path):
    policy, _checkout = _policy(tmp_path)
    forced = policy.resolve_forced_group("worker")
    with pytest.raises(AdmissionError) as error:
        policy.resolve_peer(101, 100)
    assert error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    with pytest.raises(AdmissionError, match="controller socket"):
        validate_request(
            {"verb": "lease", "lease_id": "worker-lease", "container_name": "rg-1",
             "footprint": "small", "planned_cores": 1, "priority": "gate"},
            forced, policy,
        )


def test_lease_reconnect_request_names_only_the_existing_lease_and_immutable_container_id(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = {"verb": "lease", "lease_id": "controller-run-1", "container_id": "c" * 64}

    assert validate_request(request, identity, policy) is request
    request["container_name"] = "untrusted-extra"
    with pytest.raises(AdmissionError, match="unknown field.*container_name"):
        validate_request(request, identity, policy)

    request = {"verb": "lease", "lease_id": "controller-run-1", "container_id": "short"}
    with pytest.raises(AdmissionError, match="full lowercase Docker"):
        validate_request(request, identity, policy)


def test_validate_request_checks_dev_payload_and_allowlist(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    request["kind"] = "dev"
    request["dev"] = {
        "tracked": {"sha256": SHA256, "bytes": 100},
        "untracked": {"sha256": SHA256, "bytes": 120},
        "verb_argv": ["status"], "tty": False, "worktree_key": "main",
    }
    assert validate_request(request, identity, policy)["kind"] == "dev"
    request["dev"]["verb_argv"] = ["shell"]
    with pytest.raises(AdmissionError, match="not on the host allow-list"):
        validate_request(request, identity, policy)
    request["dev"]["verb_argv"] = []
    with pytest.raises(AdmissionError, match="must not be empty"):
        validate_request(request, identity, policy)


def test_only_candidates_requires_an_allowlisted_host_created_file(tmp_path):
    policy, _checkout = _policy(tmp_path, extra_argv='[["--only-candidates", "<host-candidates>"]]')
    identity = policy.resolve_peer(1000, 100)
    candidate_path = "/var/lib/fleetd-agent/uploads/controller/candidates/" + SHA256
    request = _submit()
    request["extra_argv"] = ["--only-candidates", candidate_path]

    with pytest.raises(AdmissionError, match="host-created"):
        validate_request(request, identity, policy)
    assert validate_request(
        request, identity, policy, created_candidate_paths=frozenset({candidate_path}),
    )["extra_argv"] == ["--only-candidates", candidate_path]


@pytest.mark.parametrize(
    ("edit", "fragment"),
    [
        (lambda request: request.update(base="not-hex"), "base must be a hexadecimal commit id"),
        (lambda request: request.update(lane="../bad"), "safe Run-Gate lane name"),
        (lambda request: request["resources"].update(cpu_set="0"), "array of non-negative CPU ids"),
        (lambda request: request["resources"].update(cpu_set=[1, 1]), "duplicate CPU ids"),
        (lambda request: request["resources"].update(declared_peak_bytes=True), "declared_peak_bytes must be an integer"),
        (lambda request: request["resources"].update(planned_cores=10 ** 400), "finite number"),
        (lambda request: request["resources"].update(planned_cores=0), "finite positive number"),
        (lambda request: request["requested_by"].update(version="v1"), "requested_by.version must be SemVer"),
        (lambda request: request.update(stores={"collect": None, "seed": "bad"}), "stores.seed must be an object"),
        (lambda request: request.update(stores={"collect": None, "seed": {}}), "stores.seed is missing field"),
        (
            lambda request: request.update(stores={
                "collect": None, "seed": {"path": "seed.tgz", "sha256": SHA256, "bytes": 1, "extra": 1},
            }),
            "stores.seed has unknown field",
        ),
        (
            lambda request: request.update(stores={
                "collect": None, "seed": {"path": "../outside", "sha256": SHA256, "bytes": 1},
            }),
            "parent path",
        ),
        (
            lambda request: request.update(stores={
                "collect": None, "seed": {"path": "seed.tgz", "sha256": "bad", "bytes": 1},
            }),
            "stores.seed.sha256 must be a lowercase SHA-256",
        ),
        (
            lambda request: request.update(stores={
                "collect": None, "seed": {"path": "seed.tgz", "sha256": SHA256, "bytes": 0},
            }),
            "stores.seed.bytes must be an integer",
        ),
        (lambda request: request.update(extra_argv="--verbose"), "extra_argv must be an argv array"),
        (lambda request: request.update(env="CI=1"), "env must be an object"),
        (lambda request: request.update(env={"CI": "2"}), "env value for 'CI' is not on the host allow-list"),
        (lambda request: request.update(kind="lane", dev={}), "dev payload is only allowed"),
    ],
)
def test_validate_submit_rejects_remaining_closed_field_errors(tmp_path, edit, fragment):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    edit(request)

    with pytest.raises(AdmissionError, match=fragment) as raised:
        validate_request(request, identity, policy)
    assert raised.value.exit_code == ExitCode.REFUSAL


def test_validate_request_covers_request_shape_argv_candidate_and_dev_errors(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    for request, fragment in (
        ([], "request must be a JSON object"),
        ({"verb": 1}, "request.verb must be a string"),
        ({"verb": "nope"}, "unsupported agent verb"),
    ):
        with pytest.raises(AdmissionError, match=fragment) as raised:
            validate_request(request, identity, policy)
        assert raised.value.exit_code == ExitCode.REFUSAL
    with pytest.raises(AdmissionError, match="lowercase SHA-256 digest") as bad_put_digest:
        validate_request(
            {"verb": "put", "name": "candidates", "sha256": "bad", "bytes": 1}, identity, policy,
        )
    assert bad_put_digest.value.exit_code == ExitCode.REFUSAL

    with pytest.raises(AdmissionError, match="container_name is invalid") as bad_container_name:
        validate_request({
            "verb": "lease", "lease_id": "controller-lease-1", "container_name": "../runner",
            "footprint": "tiny", "planned_cores": 1, "priority": "gate",
        }, identity, policy)
    assert bad_container_name.value.exit_code == ExitCode.REFUSAL


    malformed_dev = _submit()
    malformed_dev["kind"] = "dev"
    malformed_dev["dev"] = {
        "tracked": [], "untracked": {"sha256": SHA256, "bytes": 1},
        "verb_argv": ["status"], "tty": False, "worktree_key": "main",
    }
    with pytest.raises(AdmissionError, match="dev.tracked must be an object"):
        validate_request(malformed_dev, identity, policy)

    malformed_argv = _submit()
    malformed_argv["kind"] = "dev"
    malformed_argv["dev"] = {
        "tracked": {"sha256": SHA256, "bytes": 1}, "untracked": {"sha256": SHA256, "bytes": 1},
        "verb_argv": "status", "tty": False, "worktree_key": "main",
    }
    with pytest.raises(AdmissionError, match="dev.verb_argv must be an argv array"):
        validate_request(malformed_argv, identity, policy)

    bad_tty = _submit()
    bad_tty["kind"] = "dev"
    bad_tty["dev"] = {
        "tracked": {"sha256": SHA256, "bytes": 1}, "untracked": {"sha256": SHA256, "bytes": 1},
        "verb_argv": ["status"], "tty": 1, "worktree_key": "main",
    }
    with pytest.raises(AdmissionError, match="dev.tty must be a boolean"):
        validate_request(bad_tty, identity, policy)

    bad_worktree = _submit()
    bad_worktree["kind"] = "dev"
    bad_worktree["dev"] = {
        "tracked": {"sha256": SHA256, "bytes": 1}, "untracked": {"sha256": SHA256, "bytes": 1},
        "verb_argv": ["status"], "tty": False, "worktree_key": "../main",
    }
    with pytest.raises(AdmissionError, match="dev.worktree_key is invalid"):
        validate_request(bad_worktree, identity, policy)

    candidates = _submit()
    candidates["extra_argv"] = ["--only-candidates"]
    with pytest.raises(AdmissionError, match="requires exactly one host-created file"):
        validate_request(candidates, identity, policy)


@pytest.mark.parametrize("length", [12, 63])
def test_validate_request_refuses_ambiguous_hex_lease_names(tmp_path, length):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = {
        "verb": "lease", "lease_id": "controller-lease-hex",
        "container_name": "a" * length,
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    with pytest.raises(
        AdmissionError,
        match=agent_protocol.AMBIGUOUS_CONTAINER_NAME_REASON,
    ) as refused:
        validate_request(request, identity, policy)
    assert refused.value.exit_code == ExitCode.REFUSAL


def test_validate_request_accepts_status_progress_cancel_and_lease_and_checks_required_strings(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    ordinary_requests = (
        {"verb": "status", "job_id": "controller-run-1"},
        {"verb": "progress", "job_id": "controller-run-1", "offset": 12},
        {"verb": "cancel", "job_id": "controller-run-1", "reason": "operator request"},
        {"verb": "fetch", "job_id": "controller-run-1", "artifact": "job.log", "offset": 1, "length": 2},
    )
    for request in ordinary_requests:
        assert validate_request(request, identity, policy) is request

    lease = {
        "verb": "lease", "lease_id": "controller-lease-1", "container_name": "runner-1",
        "footprint": "tiny", "planned_cores": 1, "priority": "gate",
    }
    assert validate_request(lease, identity, policy) is lease

    with pytest.raises(AdmissionError, match="progress.offset must be an integer") as progress:
        validate_request({"verb": "progress", "job_id": "controller-run-1", "offset": -1}, identity, policy)
    assert progress.value.exit_code == ExitCode.REFUSAL
    with pytest.raises(AdmissionError, match="cancel.reason must be a non-empty string") as cancel:
        validate_request({"verb": "cancel", "job_id": "controller-run-1", "reason": ""}, identity, policy)
    assert cancel.value.exit_code == ExitCode.REFUSAL
    with pytest.raises(AdmissionError, match="fetch.artifact must be a non-empty string") as fetch:
        validate_request({
            "verb": "fetch", "job_id": "controller-run-1", "artifact": "", "offset": 0, "length": 1,
        }, identity, policy)
    assert fetch.value.exit_code == ExitCode.REFUSAL


def test_validate_request_checks_successful_seed_environment_and_candidate_allowlist(tmp_path):
    policy, _checkout = _policy(
        tmp_path, extra_argv='[["--only-candidates", "<host-candidates>"]]',
    )
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    request["stores"] = {
        "collect": None, "seed": {"path": "seed.tgz", "sha256": SHA256, "bytes": 2},
    }
    request["env"] = {"CI": "1"}
    candidate_path = "/var/lib/fleetd-agent/uploads/controller/candidates/" + SHA256
    request["extra_argv"] = ["--only-candidates", candidate_path]

    assert validate_request(
        request, identity, policy, created_candidate_paths=frozenset({candidate_path}),
    ) is request

    other_root = tmp_path / "other"
    other_root.mkdir()
    policy, _checkout = _policy(other_root, extra_argv='[["--verbose"]]')
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    request["extra_argv"] = ["--only-candidates", candidate_path]
    with pytest.raises(AdmissionError, match="extra_argv is not on the host allow-list"):
        validate_request(request, identity, policy, created_candidate_paths=frozenset({candidate_path}))


def test_validate_request_refuses_overlapping_store_paths(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    request["stores"] = {"collect": ["scripts", "scripts/netcup"], "seed": None}

    with pytest.raises(AdmissionError, match="stores.collect paths may not overlap"):
        validate_request(request, identity, policy)


def test_validate_request_refuses_duplicate_store_paths(tmp_path):
    policy, _checkout = _policy(tmp_path)
    identity = policy.resolve_peer(1000, 100)
    request = _submit()
    request["stores"] = {"collect": ["scripts", "scripts"], "seed": None}

    with pytest.raises(AdmissionError, match="stores.collect must contain unique relative paths"):
        validate_request(request, identity, policy)
