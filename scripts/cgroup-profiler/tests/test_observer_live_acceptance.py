"""The Sol B5 live-acceptance probe's own logic, against fake observer
responses and a fake AF_UNIX server (no host, no cgroup writes)."""

from __future__ import annotations

import importlib.util
import json
import socket
import threading
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "observer_live_acceptance",
    Path(__file__).resolve().parents[1] / "tools" / "observer-live-acceptance.py",
)
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def _entry(path, *, hier=0, local=0, mount_id=42):
    return {
        "path": path, "mount_id": mount_id,
        "pids_events": {"max": hier}, "pids_events_local": {"max": local},
        "memory_events": {"max": hier}, "memory_events_local": {"max": local},
    }


def _response(leaf=(0, 0), shared=(0, 0), root=(0, 0)):
    return {
        "ok": True, "contract": 2, "pids_events_scope": "hierarchical",
        "memory_events_scope": "hierarchical",
        "cgroups": [
            _entry("/g/rg-a", hier=leaf[0], local=leaf[1]),
            _entry("/g", hier=shared[0], local=shared[1]),
            _entry("/", hier=root[0], local=root[1]),
        ],
    }


def test_sibling_only_event_moves_hierarchical_but_not_local_counters():
    before = _response()
    after = _response(shared=(3, 0))
    assert probe.evaluate(before, after, "sibling-only") == []
    # The same delta must NOT satisfy the enforced-at-ancestor expectation.
    assert probe.evaluate(before, after, "enforced") != []


def test_event_enforced_at_the_shared_ancestor_moves_the_local_counter():
    before = _response()
    after = _response(shared=(2, 2))
    assert probe.evaluate(before, after, "enforced") == []
    assert probe.evaluate(before, after, "sibling-only") != []


def test_no_change_satisfies_neither_expectation():
    quiet = _response()
    assert probe.evaluate(quiet, quiet, "sibling-only") != []
    assert probe.evaluate(quiet, quiet, "enforced") != []


def test_leaf_and_root_growth_do_not_count_as_shared_ancestor_evidence():
    after = _response(leaf=(5, 5), root=(5, 5))
    assert probe.evaluate(_response(), after, "enforced") != []


def test_shape_check_accepts_contract2_and_flags_missing_local_maps():
    assert probe.check_shape(_response()) == []
    bad = _response()
    bad["cgroups"][1]["pids_events_local"] = None
    assert any("no pids_events_local" in p for p in probe.check_shape(bad))
    assert probe.check_shape({"ok": False}) != []
    wrong_scope = _response()
    wrong_scope["pids_events_scope"] = "local"
    assert probe.check_shape(wrong_scope) != []


def test_contract1_refusal_must_be_bad_argument_naming_the_contract():
    good = {"ok": False, "contract": 2, "error": {
        "code": "bad-argument", "message": "observer contract 1 is unsupported"}}
    assert probe.contract1_refused(good)
    assert not probe.contract1_refused({"ok": True, "contract": 2})
    assert not probe.contract1_refused({"ok": False, "error": {
        "code": "daemon-error", "message": "contract"}})
    assert not probe.contract1_refused({"ok": False, "error": {
        "code": "bad-argument", "message": "malformed"}})


def _serve(path, reply_for):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def run():
        conn, _ = server.accept()
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                data += conn.recv(4096)
            conn.sendall((json.dumps(reply_for(json.loads(data))) + "\n").encode())
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_cli_contract1_and_read_round_trip_over_a_socket(tmp_path, capsys):
    sock = tmp_path / "o.sock"
    thread = _serve(sock, lambda req: (
        {"ok": False, "contract": 2, "error": {
            "code": "bad-argument", "message": "contract 1 unsupported"}}
        if req["contract"] == 1 else _response()))
    assert probe.main(["contract1", "--socket", str(sock)]) == 0
    thread.join(timeout=5)
    sock2 = tmp_path / "o2.sock"
    thread = _serve(sock2, lambda req: _response())
    out = tmp_path / "r.json"
    assert probe.main(["read", "--socket", str(sock2), "--out", str(out)]) == 0
    thread.join(timeout=5)
    assert json.loads(out.read_text())["contract"] == 2


def test_cli_compare_and_transport_error_exit_codes(tmp_path):
    before, after = tmp_path / "b.json", tmp_path / "a.json"
    before.write_text(json.dumps(_response()))
    after.write_text(json.dumps(_response(shared=(1, 0))))
    base = ["compare", "--before", str(before), "--after", str(after)]
    assert probe.main(base + ["--expect", "sibling-only"]) == 0
    assert probe.main(base + ["--expect", "enforced"]) == 1
    assert probe.main(["contract1", "--socket", str(tmp_path / "missing.sock")]) == 2
