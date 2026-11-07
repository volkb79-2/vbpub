"""cgprofiled control contract 2 in the daemon: negotiation, ownership, the config
verbs, gc arguments, status states and the live-reload plumbing
(PROTOCOL 10.1-10.7, CGPCTL-SPEC 3).

Everything drives `SessionServer._dispatch` with an explicit peer
`(pid, uid, gid)` so the SO_PEERCRED-derived context is exercised without a
socket; the goldens freeze the contract-2 documents P-cgprofilectl codes against.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from cgprofile import daemon_config, serve
from tests.conftest import cgroup_files, write_cgroup

SOCKET_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rg55" / "socket"
REGEN_ENV = "CGPROFILE_REGEN_SOCKET_GOLDENS"
CONTAINER_ID = "a" * 64
SESSION_ID = "s-20260912T101500Z-9f01"
EPOCH = datetime(2026, 9, 12, 10, 15, 0, tzinfo=timezone.utc).timestamp()
ROOT = (1, 0, 0)
ALICE = (4242, 1000, 1000)
BOB = (4343, 2000, 2000)
NOBODY = None  # unreadable SO_PEERCRED


def _req(verb: str, contract: int = 2, **args: Any) -> Dict[str, Any]:
    return {"verb": verb, "args": args, "contract": contract}


def _start(**over: Any) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "target": f"containerid:{CONTAINER_ID}", "scope": "container-shared",
        "token": None, "damon": "off", "interval": 1.0,
        "meta": {"lane": "x", "project": "p", "worktree": "w", "commit": None,
                 "run_gate_revision": 1, "kind": "command", "expected": None},
    }
    args.update(over)
    return _req("start", **args)


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files())
    write_cgroup(root, "dev.slice", cgroup_files())
    write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
    write_cgroup(
        root, f"dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope",
        cgroup_files(memory_current=500 * 1024 * 1024),
    )
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("1.0 1.0 1.0 1/100 999\n")
    (proc / "meminfo").write_text("MemTotal:  1000 kB\nMemAvailable: 500 kB\n")
    pressure = proc / "pressure"
    pressure.mkdir()
    for name in ("cpu", "memory", "io"):
        (pressure / name).write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                                     "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    srv = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), cgroup_root=str(root), proc_root=str(proc),
        clock=lambda: EPOCH, session_id_fn=lambda: SESSION_ID, keep_sessions=3,
    )
    yield srv
    srv._stop_all_sessions(aborted_reason="test-teardown")


def _stored(server, name, *, status="finished", owner=1000, ended="2026-09-12T10:15:00Z",
            extra: Optional[Dict[str, Any]] = None) -> None:
    folder = Path(server.sessions_dir) / name
    folder.mkdir(parents=True)
    manifest: Dict[str, Any] = {
        "status": status, "started_at": "2026-09-12T10:14:00Z", "ended_at": ended,
        "scope": "container-shared", "container_id": CONTAINER_ID, "token": "t",
    }
    if owner is not False:
        manifest["owner_uid"] = owner
    manifest.update(extra or {})
    (folder / "manifest.json").write_text(json.dumps(manifest))


def _golden(name: str, doc: Any, tmp_path: Path) -> None:
    text = json.dumps(doc, indent=2).replace(str(tmp_path), "/var/lib/cgprofile") + "\n"
    path = SOCKET_FIXTURES / name
    if os.environ.get(REGEN_ENV) == "1":  # pragma: no cover - maintenance path
        path.write_text(text)
        return
    assert path.is_file(), f"missing golden {path} (regenerate with {REGEN_ENV}=1)"
    assert path.read_text() == text, f"{path} differs from the live document"


# ── negotiation ──────────────────────────────────────────────────────────

class TestNegotiation:
    @pytest.mark.parametrize("contract", [1, 2])
    def test_supported_contracts_are_echoed(self, server, contract):
        resp = server._dispatch(_req("host", contract))
        assert resp["ok"] is True and resp["contract"] == contract

    def test_omitted_contract_is_one(self, server):
        resp = server._dispatch({"verb": "host", "args": {}})
        assert resp["ok"] is True and resp["contract"] == 1

    @pytest.mark.parametrize("bad", [3, 0, True, "2", 2.0, None])
    def test_unsupported_contract_is_a_contract_1_error(self, server, bad):
        resp = server._dispatch({"verb": "host", "args": {}, "contract": bad})
        assert resp["ok"] is False and resp["contract"] == 1
        assert resp["error"]["code"] == "bad-argument"
        assert "speaks contracts [1, 2]" in resp["error"]["message"]

    def test_request_contract_ignores_a_non_object(self):
        assert serve.SessionServer._request_contract([1]) == 1

    @pytest.mark.parametrize("verb", ["config", "config-set", "config-reset"])
    def test_contract_2_verbs_are_refused_at_contract_1(self, server, verb):
        resp = server._dispatch(_req(verb, 1))
        assert resp["ok"] is False and resp["contract"] == 1
        assert resp["error"]["code"] == "bad-argument"
        assert "exists only at contract 2" in resp["error"]["message"]
        assert "contract 1" in resp["error"]["message"]

    def test_unknown_verb_is_stamped_with_the_request_contract(self, server):
        resp = server._dispatch(_req("nope", 2))
        assert resp["contract"] == 2 and resp["error"]["code"] == "bad-argument"

    def test_streaming_verb_in_dispatch_is_not_streaming_at_contract_2(self, server):
        resp = server._dispatch(_req("watch", 2, session=SESSION_ID))
        assert resp["error"]["code"] == "not-streaming" and resp["contract"] == 2

    def test_in_process_dispatch_without_a_peer_is_trusted(self, server):
        resp = server._dispatch(_req("config-set", 2, key="interval", value=2))
        assert resp["ok"] is True


class TestVersion:
    def test_contract_1_keeps_the_exec_key_false(self, server):
        doc = server._dispatch(_req("version", 1))
        assert doc["transports"]["exec"] is False
        assert doc["contracts"] == [1, 2]
        assert "config" not in doc

    def test_contract_2_drops_exec_and_reports_the_config_block(self, server):
        doc = server._dispatch(_req("version", 2))
        assert "exec" not in doc["transports"]
        assert doc["config"] == {
            "digest": server.settings.digest, "last_reload_error": None, "overrides": 0,
        }
        server._dispatch(_req("config-set", 2, key="interval", value=2))
        assert server._dispatch(_req("version", 2))["config"]["overrides"] == 1


# ── ownership ────────────────────────────────────────────────────────────

class TestOwnership:
    def _started(self, server, peer=ALICE):
        resp = server._dispatch(_start(), peer=peer)
        assert resp["ok"] is True, resp
        return resp["session"]

    def test_start_records_the_peer_uid_in_the_manifest(self, server):
        sid = self._started(server)
        assert server._sessions[sid].owner_uid == 1000
        server._sessions[sid].stop_event.set()
        server._sessions[sid].thread.join(timeout=5.0)
        server._dispatch(_req("stop", session=sid), peer=ROOT)
        assert server._stored_owner(sid) == 1000

    @pytest.mark.parametrize("peer, ok", [(ALICE, True), (ROOT, True), (BOB, False), (NOBODY, False)])
    def test_stop_of_a_live_session(self, server, peer, ok):
        sid = self._started(server)
        resp = server._dispatch(_req("stop", session=sid), peer=peer)
        assert resp["ok"] is ok
        if not ok:
            assert resp["error"]["code"] == "peer-refused"
            assert "owner uid 1000" in resp["error"]["message"]

    def test_unreadable_peer_message_names_unavailable(self, server):
        sid = self._started(server)
        resp = server._dispatch(_req("stop", session=sid), peer=NOBODY)
        assert "peer uid unavailable" in resp["error"]["message"]

    def test_stop_of_a_finished_session_is_checked_too(self, server):
        sid = self._started(server)
        server._dispatch(_req("stop", session=sid), peer=ALICE)
        assert server._dispatch(_req("stop", session=sid), peer=BOB)["error"]["code"] == "peer-refused"
        assert server._dispatch(_req("stop", session=sid), peer=ALICE)["ok"] is True

    def test_stop_of_a_stored_session_is_checked(self, server):
        sid = self._started(server)
        server._dispatch(_req("stop", session=sid), peer=ALICE)
        del server._sessions[sid]
        assert server._dispatch(_req("stop", session=sid), peer=BOB)["error"]["code"] == "peer-refused"
        assert server._dispatch(_req("stop", session=sid), peer=ALICE)["ok"] is True

    def test_a_store_without_an_owner_is_uid_0_only(self, server):
        sid = self._started(server)
        server._dispatch(_req("stop", session=sid), peer=ALICE)
        del server._sessions[sid]
        manifest_path = Path(server.sessions_dir) / sid / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        del manifest["owner_uid"]
        manifest_path.write_text(json.dumps(manifest))
        refused = server._dispatch(_req("stop", session=sid), peer=ALICE)
        assert refused["error"]["code"] == "peer-refused"
        assert "no owner recorded" in refused["error"]["message"]
        assert server._dispatch(_req("stop", session=sid), peer=ROOT)["ok"] is True

    def test_stored_owner_of_a_missing_manifest_is_none(self, server):
        assert server._stored_owner("s-20260912T101500Z-0001") is None

    def test_read_manifest_refuses_a_non_object(self, server):
        _stored(server, "s-20260912T101500Z-0001")
        (Path(server.sessions_dir) / "s-20260912T101500Z-0001" / "manifest.json").write_text("[1]")
        assert server._read_manifest("s-20260912T101500Z-0001") is None


# ── gc ───────────────────────────────────────────────────────────────────

NAMES = [f"s-20260912T101500Z-000{i}" for i in range(1, 6)]


class TestGc:
    def _five(self, server, owners=(1000, 1000, 2000, 1000, 2000)):
        for name, owner in zip(NAMES, owners):
            _stored(server, name, owner=owner)

    def test_root_gc_removes_all_beyond_keep(self, server):
        self._five(server)
        resp = server._dispatch(_req("gc", keep=2), peer=ROOT)
        assert resp == {"ok": True, "contract": 2, "removed": NAMES[:3], "kept": 2, "dry_run": False}
        assert sorted(os.listdir(server.sessions_dir)) == NAMES[3:]

    def test_dry_run_removes_nothing(self, server):
        self._five(server)
        resp = server._dispatch(_req("gc", keep=2, dry_run=True), peer=ROOT)
        assert resp["removed"] == NAMES[:3] and resp["dry_run"] is True
        assert sorted(os.listdir(server.sessions_dir)) == NAMES

    def test_a_non_root_peer_removes_only_its_own(self, server):
        self._five(server)
        resp = server._dispatch(_req("gc", keep=1), peer=ALICE)
        assert resp["removed"] == [NAMES[0], NAMES[1], NAMES[3]]
        assert resp["kept"] == 2
        assert NAMES[2] in os.listdir(server.sessions_dir)

    def test_unreadable_peer_is_refused(self, server):
        resp = server._dispatch(_req("gc"), peer=NOBODY)
        assert resp["error"]["code"] == "peer-refused"

    def test_exporting_sessions_are_skipped(self, server):
        self._five(server)
        server._exporting[NAMES[0]] = 1
        resp = server._dispatch(_req("gc", keep=2), peer=ROOT)
        assert NAMES[0] not in resp["removed"] and resp["kept"] == 3

    def test_keep_days_expires_old_sessions(self, server):
        _stored(server, NAMES[0], ended="2026-09-01T00:00:00Z")
        _stored(server, NAMES[1])
        resp = server._dispatch(_req("gc", keep_days=3), peer=ROOT)
        assert resp["removed"] == [NAMES[0]] and resp["kept"] == 1

    @pytest.mark.parametrize("args, fragment", [
        ({"bogus": 1}, "unexpected gc argument"),
        ({"dry_run": "yes"}, "'dry_run' must be a boolean"),
        ({"keep": 0}, "'keep' must be an integer >= 1"),
        ({"keep": True}, "'keep' must be an integer >= 1"),
        ({"keep": "3"}, "'keep' must be an integer >= 1"),
        ({"keep": 4}, "above the configured maximum 3"),
        ({"keep_days": 15}, "above the configured maximum 14"),
    ])
    def test_bad_arguments(self, server, args, fragment):
        resp = server._dispatch(_req("gc", **args), peer=ROOT)
        assert resp["error"]["code"] == "bad-argument"
        assert fragment in resp["error"]["message"]

    def test_gc_at_contract_1_keeps_the_old_shape_and_uses_the_default_keep(self, server):
        self._five(server)
        resp = server._dispatch(_req("gc", 1))
        assert resp["removed"] == NAMES[:2] and resp["dry_run"] is False

    def test_retention_of_an_unreadable_sessions_dir(self, server, tmp_path):
        os.rmdir(server.sessions_dir)
        assert server._run_retention() == ([], 0)


# ── status ───────────────────────────────────────────────────────────────

class TestStatusStates:
    def test_default_state_lists_live_with_owner_at_contract_2(self, server):
        sid = server._dispatch(_start(), peer=ALICE)["session"]
        doc = server._dispatch(_req("status"))
        assert [e["session"] for e in doc["sessions"]] == [sid]
        assert doc["sessions"][0]["state"] == "live" and doc["sessions"][0]["owner_uid"] == 1000
        one = server._dispatch(_req("status", session=sid))
        assert one["session"]["state"] == "live" and one["session"]["owner_uid"] == 1000

    def test_contract_1_status_has_no_state_or_owner(self, server):
        server._dispatch(_start(), peer=ALICE)
        entry = server._dispatch(_req("status", 1))["sessions"][0]
        assert "state" not in entry and "owner_uid" not in entry

    def test_state_at_contract_1_is_refused(self, server):
        resp = server._dispatch(_req("status", 1, state="done"))
        assert resp["error"]["code"] == "bad-argument"
        assert "exists only at contract 2" in resp["error"]["message"]

    def test_unknown_state_is_refused(self, server):
        resp = server._dispatch(_req("status", state="running"))
        assert "must be live, done or all" in resp["error"]["message"]

    def test_done_lists_finished_and_aborted_newest_first(self, server):
        _stored(server, NAMES[0])
        _stored(server, NAMES[1], status="aborted")
        _stored(server, NAMES[2], status="live")                      # skipped: not terminal
        _stored(server, NAMES[3], owner="x")                          # owner not an int -> None
        (Path(server.sessions_dir) / "not-a-session").mkdir()         # skipped: not an id
        (Path(server.sessions_dir) / NAMES[4]).mkdir()                # skipped: no manifest
        doc = server._dispatch(_req("status", state="done"))
        assert [(e["session"], e["state"], e["owner_uid"]) for e in doc["sessions"]] == [
            (NAMES[3], "done", None), (NAMES[1], "aborted", 1000), (NAMES[0], "done", 1000),
        ]
        assert doc["sessions"][2]["scope"] == "container-shared"

    def test_all_merges_live_and_stored_and_is_capped(self, server):
        sid = server._dispatch(_start(), peer=ALICE)["session"]
        for name in NAMES[:4]:
            _stored(server, name)
        doc = server._dispatch(_req("status", state="all"))
        assert len(doc["sessions"]) == 3  # keep_sessions
        assert doc["sessions"][0]["session"] == max([sid] + NAMES[:4])

    def test_live_state_is_just_live(self, server):
        _stored(server, NAMES[0])
        assert server._dispatch(_req("status", state="live"))["sessions"] == []

    def test_stored_entries_survive_an_unreadable_directory(self, server):
        os.rmdir(server.sessions_dir)
        assert server._dispatch(_req("status", state="done"))["sessions"] == []


# ── config verbs ─────────────────────────────────────────────────────────

class TestConfigVerbs:
    def test_config_describes_the_settings(self, server):
        doc = server._dispatch(_req("config"), peer=ALICE)  # reads are for any allowed peer
        assert doc["ok"] and doc["config"]["keys"]["keep_sessions"]["value"] == 3

    def test_config_takes_no_arguments(self, server):
        assert server._dispatch(_req("config", x=1))["error"]["code"] == "bad-argument"

    def test_set_applies_and_audits(self, server, capsys):
        resp = server._dispatch(_req("config-set", key="keep_days", value=5), peer=ROOT)
        assert resp["ok"] and resp["config-set"]["new"] == {"value": 5, "source": "override"}
        assert resp["config-set"]["audit_id"] == "1"
        assert server.keep_days == 5
        err = capsys.readouterr().err
        assert "config-audit id=1 op=set uid=0 pid=1 key=keep_days old=14 new=5 persist=false" in err

    def test_dry_run_does_not_apply(self, server):
        resp = server._dispatch(_req("config-set", key="keep_days", value=5, dry_run=True), peer=ROOT)
        assert resp["config-set"]["applied"] is False and server.keep_days == 14

    def test_persist_writes_the_overrides_file(self, server):
        resp = server._dispatch(_req("config-set", key="keep_days", value=5, persist=True), peer=ROOT)
        assert resp["config-set"]["persisted"] is True
        assert os.path.isfile(server.settings.overrides_path)

    def test_persist_failure_is_reported_and_audited(self, server, tmp_path, capsys):
        blocker = tmp_path / "blocker"
        blocker.write_text("")
        server.settings.overrides_path = str(blocker / "overrides.toml")
        resp = server._dispatch(_req("config-set", key="keep_days", value=5, persist=True), peer=ROOT)
        assert resp["error"]["code"] == "persist-failed"
        assert server.keep_days == 14
        assert "op=refused" in capsys.readouterr().err

    @pytest.mark.parametrize("peer, who", [(ALICE, "1000"), (NOBODY, "unavailable")])
    def test_non_root_writes_are_refused_and_audited(self, server, capsys, peer, who):
        for verb, args in (("config-set", {"key": "interval", "value": 5}),
                           ("config-reset", {"all": True})):
            resp = server._dispatch(_req(verb, **args), peer=peer)
            assert resp["error"]["code"] == "peer-refused"
            assert f"uid 0 only (peer uid {who})" in resp["error"]["message"]
        assert capsys.readouterr().err.count("op=refused") == 2
        assert server.default_interval == 1.0

    @pytest.mark.parametrize("args, fragment", [
        ({"key": "interval", "value": 1, "bogus": 1}, "unexpected config-set argument"),
        ({"key": "interval"}, "requires 'key' and 'value'"),
        ({"key": "interval", "value": 1, "persist": 1}, "'persist' must be a boolean"),
        ({"key": "interval", "value": 1, "dry_run": "x"}, "'dry_run' must be a boolean"),
        ({"key": "sessions_dir", "value": "/x"}, "not a runtime-tunable key"),
        ({"key": "max_sessions", "value": 9999}, "max_sessions"),
    ])
    def test_set_bad_arguments(self, server, args, fragment):
        resp = server._dispatch(_req("config-set", **args), peer=ROOT)
        assert resp["error"]["code"] == "bad-argument"
        assert fragment in resp["error"]["message"]

    def test_reset_one_and_all(self, server):
        server._dispatch(_req("config-set", key="keep_days", value=5), peer=ROOT)
        server._dispatch(_req("config-set", key="max_sessions", value=2), peer=ROOT)
        one = server._dispatch(_req("config-reset", key="keep_days"), peer=ROOT)
        assert one["config-reset"]["reset"] == ["keep_days"] and server.keep_days == 14
        every = server._dispatch(_req("config-reset", all=True), peer=ROOT)
        assert every["config-reset"]["reset"] == ["max_sessions"] and server.max_sessions == 16

    @pytest.mark.parametrize("args, fragment", [
        ({}, "exactly one of 'key' or 'all'"),
        ({"key": "interval", "all": True}, "exactly one of 'key' or 'all'"),
        ({"all": "yes"}, "'all' must be a boolean"),
        ({"key": "sessions_dir"}, "not a runtime-tunable key"),
        ({"key": "interval", "x": 1}, "unexpected config-reset argument"),
    ])
    def test_reset_bad_arguments(self, server, args, fragment):
        resp = server._dispatch(_req("config-reset", **args), peer=ROOT)
        assert resp["error"]["code"] == "bad-argument"
        assert fragment in resp["error"]["message"]

    def test_reset_persist_failure(self, server, tmp_path):
        server._dispatch(_req("config-set", key="keep_days", value=5, persist=True), peer=ROOT)
        blocker = tmp_path / "blocker"
        blocker.write_text("")
        server.settings.overrides_path = str(blocker / "overrides.toml")
        resp = server._dispatch(_req("config-reset", all=True), peer=ROOT)
        assert resp["error"]["code"] == "persist-failed"


# ── applying settings to the running server ──────────────────────────────

class TestApplySettings:
    @pytest.mark.parametrize("key, value, attr, expected", [
        ("interval", 2.5, "default_interval", 2.5),
        ("damon_default", "off", "damon_default", "off"),
        ("keep_sessions", 9, "keep_sessions", 9),
        ("keep_days", 9, "keep_days", 9),
        ("max_sessions", 7, "max_sessions", 7),
        ("export_max_bytes", 1234, "export_max_bytes", 1234),
        ("allow_uids", [5, 6], "allow_uids", [5, 6]),
        ("allow_uids", [], "allow_uids", None),
    ])
    def test_tunable_keys_reach_their_attribute(self, server, key, value, attr, expected):
        if key == "allow_uids" and not value:
            server._dispatch(_req("config-set", key=key, value=[5]), peer=ROOT)
        server._dispatch(_req("config-set", key=key, value=value), peer=ROOT)
        assert getattr(server, attr) == expected

    def test_slot_caps_are_resized(self, server):
        server._dispatch(_req("config-set", key="max_watchers", value=1), peer=ROOT)
        server._dispatch(_req("config-set", key="max_exports", value=1), peer=ROOT)
        assert server._watch_slots.acquire() and not server._watch_slots.acquire()
        assert server._export_slots.acquire() and not server._export_slots.acquire()

    def test_file_only_keys_apply_on_a_direct_refresh(self, server):
        server.settings._file["observe_slices"] = ["a.slice"]
        server.settings._file["gates_slice"] = "other-gates.slice"
        server._apply_settings()
        assert server.observe_slices == ["a.slice"]
        assert server.gates_slice_name == "other-gates.slice"
        assert server.windows.gates_slice_name == "other-gates.slice"

    def test_unchanged_values_are_left_alone(self, server):
        server.keep_days = 99  # a hand edit that settings do not know about
        server._apply_settings()
        assert server.keep_days == 99

    def test_reload_settings_rereads_the_file(self, server):
        Path(server.settings.config_path).write_text("interval = 3\nkeep_days = 4\n")
        assert server.reload_settings() is True
        assert server.default_interval == 3 and server.keep_days == 4

    def test_reload_settings_with_a_broken_file_keeps_the_old_values(self, server):
        Path(server.settings.config_path).write_text("interval = \n")
        assert server.reload_settings() is False
        assert server.default_interval == 1.0
        assert server.settings.last_reload_error


class TestResizableSlots:
    def test_acquire_release_resize(self):
        slots = serve._ResizableSlots(1)
        assert slots.acquire() is True
        assert slots.acquire() is False
        slots.resize(2)
        assert slots.acquire() is True
        slots.release()
        slots.release()
        with pytest.raises(ValueError):
            slots.release()

    def test_lowering_the_cap_never_evicts(self):
        slots = serve._ResizableSlots(2)
        assert slots.acquire() and slots.acquire()
        slots.resize(1)
        slots.release()
        assert slots.acquire() is False
        slots.release()
        assert slots.acquire() is True


class TestSighup:
    def test_sighup_reloads_on_a_worker_thread(self, server, monkeypatch):
        handlers = {}
        monkeypatch.setattr(serve.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
        server._install_signals()
        assert set(handlers) == {serve.signal.SIGTERM, serve.signal.SIGINT, serve.signal.SIGHUP}
        Path(server.settings.config_path).write_text("keep_days = 6\n")
        seen = {}
        original = server.reload_settings

        def spy():
            seen["thread"] = threading.current_thread().name
            return original()

        monkeypatch.setattr(server, "reload_settings", spy)
        handlers[serve.signal.SIGHUP](serve.signal.SIGHUP, None)
        deadline = time.time() + 5
        while "thread" not in seen and time.time() < deadline:
            time.sleep(0.01)
        for thread in threading.enumerate():
            if thread.name == "cgprofiled-reload":
                thread.join(timeout=5)
        assert seen["thread"] == "cgprofiled-reload" and server.keep_days == 6
        handlers[serve.signal.SIGTERM](serve.signal.SIGTERM, None)
        assert server._stopping is True


# ── goldens (PROTOCOL 10.7) ──────────────────────────────────────────────

class TestContract2Goldens:
    def test_the_contract_2_documents_are_frozen(self, server, tmp_path):
        server.settings.loaded_at = EPOCH
        server.started_at = "2026-09-12T10:15:00Z"
        pairs = {}

        def run(name, request, peer=ROOT):
            response = server._dispatch(request, peer=peer)
            pairs[name] = (request, response)
            return response

        run("config", _req("config"))
        run("config-set", _req("config-set", key="keep_days", value=5, persist=True))
        run("config-set-dry-run", _req("config-set", key="keep_days", value=6, dry_run=True))
        run("config-set-refused-nonroot", _req("config-set", key="keep_days", value=7), peer=ALICE)
        blocker = Path(server.settings.overrides_path).parent
        saved = server.settings.overrides_path
        blocker.mkdir(parents=True, exist_ok=True)
        server.settings.overrides_path = str(blocker / "blocker" / "x.toml")
        (blocker / "blocker").write_text("")
        run("config-set-persist-failed", _req("config-set", key="keep_days", value=8, persist=True))
        server.settings.overrides_path = saved
        run("config-reset", _req("config-reset", key="keep_days"))
        run("version-c2", _req("version"))
        run("status-c2", _req("status", state="all"))
        for name, (request, response) in pairs.items():
            if "version" in name:
                response["daemon"]["started_at"] = "2026-09-12T10:15:00Z"
                response["cgprofile"] = "1.0.0"
                response["daemon"]["version"] = "1.0.0"
            home = Path(server.sessions_dir).parent
            _golden(f"{name}-request.json", request, home)
            _golden(f"{name}-response.json", response, home)

    def test_connection_falls_back_to_the_bare_uid_when_full_credentials_are_unavailable(self, server):
        import socket as _socket

        seen: Dict[str, Any] = {}
        server._peer_credentials = lambda conn: None
        server._peer_uid = lambda conn: 1000
        original = server._dispatch

        def spy(req, peer=None):
            seen["peer"] = peer
            return original(req, peer=peer)

        server._dispatch = spy
        client, daemon = _socket.socketpair()
        client.settimeout(5.0)
        try:
            client.sendall((json.dumps(_req("version")) + "\n").encode())
            server._handle_connection(daemon)
            data = client.recv(65536)
        finally:
            client.close()
        assert json.loads(data.decode().splitlines()[0])["ok"] is True
        assert seen["peer"] == (0, 1000, 0)

    def test_every_golden_error_carries_contract_2(self, server):
        resp = server._dispatch(_req("config-set", key="keep_days", value=7), peer=ALICE)
        assert resp["contract"] == 2
