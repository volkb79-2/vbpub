import json
import os
import socket
import struct

import pytest

from fleetd.admission import AdmissionError, ExitCode
from fleetd.agent_policy import load_agent_policy
from fleetd.agent_protocol import (
    ARTIFACT_NAMES,
    MAX_FETCH_BYTES,
    MAX_FRAME_BYTES,
    FrameError,
    read_frame,
    validate_relative_path,
    validate_request,
    write_frame,
)


SHA = "a" * 40
SHA256 = "b" * 64


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
        header = client.recv(4)
        length = struct.unpack(">I", header)[0]
        assert length <= MAX_FRAME_BYTES
        assert json.loads(client.recv(length)) == {"ok": True}
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
        header = os.read(read_fd, 4)
        length = struct.unpack(">I", header)[0]
        assert json.loads(os.read(read_fd, length)) == {"ok": True}
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


def test_read_frame_drops_an_idle_stream_without_waiting(monkeypatch):
    client, server = socket.socketpair()
    monkeypatch.setattr("fleetd.agent_protocol.select.select", lambda *_args: ([], [], []))
    try:
        with pytest.raises(FrameError, match="idle connection deadline"):
            read_frame(server, 2)
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf")])
def test_read_frame_rejects_invalid_idle_deadlines(timeout):
    with pytest.raises(FrameError, match="idle timeout"):
        read_frame(0, timeout)


def test_write_frame_refuses_oversized_or_invalid_responses():
    client, server = socket.socketpair()
    try:
        with pytest.raises(FrameError, match="exceeds"):
            write_frame(server, {"payload": "x" * MAX_FRAME_BYTES})
        with pytest.raises(FrameError, match="cannot encode"):
            write_frame(server, {"payload": float("nan")})
        with pytest.raises(FrameError, match="cannot encode"):
            write_frame(object(), {})
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
