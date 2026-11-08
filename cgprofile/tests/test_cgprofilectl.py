"""cgprofilectl: every verb against a fake contract-1/contract-2 daemon (CGPROFILECTL-SPEC.md)."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import types

import pytest

import _fakectl
from cgprofile import ctlwire
from cgprofile.cli import cgprofilectl
from cgprofile.ctlwire import WireFailure

SESSION_LIVE = "s-20261104T100000Z-aaaa"
SESSION_DONE = "s-20261103T100000Z-bbbb"
SUMMARY = {"verdict": "ok", "peak": 5}
CID = "c" * 64


def live_entry(uid=None):
    return {"session": SESSION_LIVE, "state": "live", "owner_uid": os.getuid() if uid is None else uid,
            "target": {"container_id": CID}, "scope": "container", "started_at": "2026-11-04T10:00:00Z",
            "elapsed_seconds": 12.5, "meta": {"lane": "r0"}}


def done_entry(uid=4242):
    return {"session": SESSION_DONE, "state": "done", "owner_uid": uid, "target": {"cgroup": "/dev.slice/x"},
            "scope": "observe", "started_at": "2026-11-03T10:00:00Z", "ended_at": "2026-11-03T10:05:00Z"}


def handlers(**over):
    def status(args, contract):
        if args.get("session"):
            if args["session"] == SESSION_LIVE:
                return {"ok": True, "contract": 2, "session": live_entry(), "host": {}}
            return {"ok": False, "error": {"code": "unknown-session", "message": "no such session"}}
        sessions = [live_entry()]
        if args.get("state") in ("done", "all"):
            sessions.append(done_entry())
        if args.get("state") == "done":
            sessions = sessions[1:]
        return {"ok": True, "contract": 2, "at": "2026-11-04T10:01:00Z", "sessions": sessions}

    table = {
        "version": lambda a, c: dict(_fakectl.V2_VERSION),
        "config": lambda a, c: dict(_fakectl.CONFIG_DOC),
        "host": lambda a, c: {"ok": True, "contract": 1, "load": [0.5, 0.4, 0.3], "damon": "available"},
        "status": status,
        "start": lambda a, c: {"ok": True, "contract": 2, "session": SESSION_LIVE, "started_at": "t",
                               "target": a["target"], "placement": None},
        "stop": lambda a, c: {"ok": True, "contract": 1, "session": a["session"], "already_stopped": False,
                              "summary": SUMMARY},
        "gc": lambda a, c: {"ok": True, "contract": 2, "removed": [] if a.get("keep") == 99 else [SESSION_DONE],
                            "kept": 1},
        # the real daemon sends no ack line: reading / verdict / one end carrying `reason` (serve.py _stream_watch)
        "watch": lambda a, c: [{"event": "reading", "at": "t1", "live": {
            "memory_current_bytes": 7, "cpu_cores_recent": 0.5, "samples": 3}},
            {"event": "verdict", "at": "t2", "verdict": "stalled"}, {"event": "end", "at": "t3", "reason": "stopped"}],
        "export": lambda a, c: _fakectl.export_reply(
            {"summary.json": json.dumps(SUMMARY).encode(), "host.jsonl": b"{}\n"}),
        "config-set": lambda a, c: {"ok": True, "contract": 2, "config-set": {
            "key": a["key"], "old": 1.0, "new": a["value"], "persisted": a["persist"], "audit_id": "a1"}},
        "config-reset": lambda a, c: {"ok": True, "contract": 2, "config-reset": {
            "reset": ["interval"] if (a.get("key") or a.get("all")) else [], "values": {}}},
    }
    table.update(over)
    return table


@pytest.fixture()
def sockdir():
    with tempfile.TemporaryDirectory(prefix="cgc") as directory:
        yield directory


@pytest.fixture()
def daemon(sockdir):
    started = []

    def make(table=None, name="c.sock"):
        d = _fakectl.FakeDaemon(os.path.join(sockdir, name), table if table is not None else handlers())
        started.append(d)
        return d

    yield make
    for d in started:
        d.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CGPROFILE_CONTROL_SOCKET", raising=False)
    monkeypatch.delenv("CGPROFILE_OBSERVER_SOCKET", raising=False)
    monkeypatch.delenv("CGPROFILE_RUN_DIR", raising=False)


@pytest.fixture()
def cli(capsys):
    def run(*argv, socket_path=None):
        full = (["--socket", socket_path] if socket_path else []) + list(argv)
        code = cgprofilectl.build_cli().run(argv=full)
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return run


def jdoc(out):
    return json.loads(out.strip().splitlines()[-1])


# ── plumbing ─────────────────────────────────────────────────────────────────

def test_identity_and_main(capsys):
    assert cgprofilectl.identity().command == "cgprofilectl"
    assert cgprofilectl.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("cgprofilectl ")


def test_main_runs_the_given_argv(daemon, capsys):
    d = daemon()
    assert cgprofilectl.main(["--socket", d.path, "status"]) == 0


def test_seconds_since():
    assert cgprofilectl._seconds_since(None) is None
    assert cgprofilectl._seconds_since("garbage") is None
    assert cgprofilectl._seconds_since("2026-11-04T10:00:10Z", now=1793786410.0 + 5) is not None
    naive = cgprofilectl._seconds_since("2026-11-04T10:00:00", now=0.0)
    assert naive < 0
    assert cgprofilectl._seconds_since("2020-01-01T00:00:00Z") > 0


def test_iso():
    assert cgprofilectl._iso("x") == "x" and cgprofilectl._iso(3) is None


def test_socket_group_and_membership(tmp_path, monkeypatch):
    assert cgprofilectl.socket_group(str(tmp_path / "absent")) == {"group": None, "gid": None}
    present = cgprofilectl.socket_group(str(tmp_path))
    assert present["gid"] == os.stat(tmp_path).st_gid
    monkeypatch.setattr(cgprofilectl.grp, "getgrgid", lambda gid: (_ for _ in ()).throw(KeyError(gid)))
    assert cgprofilectl.socket_group(str(tmp_path))["group"] is None
    assert cgprofilectl.caller_in_group(None) is False
    assert cgprofilectl.caller_in_group(os.getegid()) is True
    monkeypatch.setattr(os, "getegid", lambda: 1)
    monkeypatch.setattr(os, "getgroups", lambda: [5, 6])
    assert cgprofilectl.caller_in_group(6) is True
    assert cgprofilectl.caller_in_group(7) is False


def test_skew():
    assert cgprofilectl._skew("2.0.0", None) == "equal"
    assert cgprofilectl._skew("2.0.0", "2.0.0") == "equal"
    assert cgprofilectl._skew("1.2.0", "2.0.0") == "client-older"
    assert cgprofilectl._skew("2.1.0", "2.0.0") == "client-newer"


# ── failure rendering ────────────────────────────────────────────────────────

def test_unreachable_daemon_text_and_json(cli, sockdir):
    absent = os.path.join(sockdir, "absent.sock")
    code, out, err = cli("status", socket_path=absent)
    assert code == 3
    assert "cause: socket-missing" in err and "systemctl" in err
    code, out, err = cli("status", "--json", socket_path=absent)
    doc = jdoc(out)
    assert code == 3 and doc["ok"] is False and doc["error"]["cause"] == "socket-missing"


def test_usage_failure_has_no_cause_or_hint(cli, daemon):
    d = daemon()
    code, out, err = cli("config", "reset", socket_path=d.path)
    assert code == 2 and "exactly one" in err and "cause" not in err


def test_empty_socket_env_is_a_usage_error(cli, monkeypatch):
    monkeypatch.setenv("CGPROFILE_CONTROL_SOCKET", "")
    code, out, err = cli("status")
    assert code == 2 and "CGPROFILE_CONTROL_SOCKET" in err


def test_socket_env_is_honoured(cli, daemon, monkeypatch):
    d = daemon()
    monkeypatch.setenv("CGPROFILE_CONTROL_SOCKET", d.path)
    code, out, err = cli("host")
    assert code == 0 and "damon" in out


def test_wrong_socket_env_path_fails_as_an_unreachable_endpoint(cli, monkeypatch, sockdir):
    path = os.path.join(sockdir, "not-the-daemon.sock")
    monkeypatch.setenv("CGPROFILE_CONTROL_SOCKET", path)

    code, out, err = cli("status")

    assert code == 3 and "socket-missing" in err and path in err


def test_unset_socket_env_is_a_usage_error(cli):
    code, out, err = cli("status")
    assert code == 2 and "CGPROFILE_CONTROL_SOCKET" in err


def test_ownership_refusal_is_exit_2_and_other_peer_refusal_is_exit_3(cli, daemon):
    refuse = {"ok": False, "error": {"code": "peer-refused", "message": "not yours"}}
    d = daemon(handlers(stop=lambda a, c: refuse, host=lambda a, c: refuse))
    assert cli("stop", "--yes", SESSION_LIVE, socket_path=d.path)[0] == 2
    code, out, err = cli("host", socket_path=d.path)
    assert code == 3 and "socket-permission" in err


# ── status / host ────────────────────────────────────────────────────────────

def test_status_against_a_contract_2_daemon(cli, daemon):
    d = daemon()
    code, out, err = cli("status", "--json", socket_path=d.path)
    doc = jdoc(out)
    assert code == 0 and doc["schema"] == 1 and doc["ok"] is True
    assert doc["daemon"]["contracts"] == [1, 2] and doc["daemon"]["version"] == "2.0.0"
    assert doc["socket"]["allow_uids"] == [0, 1000] and doc["config"]["digest"] == "sha256:abc"
    assert doc["skew"]["contract_ok"] is True
    code, out, err = cli("status", socket_path=d.path)
    assert "daemon 2.0.0" in out and "config sha256:abc" in out


def test_status_against_a_1x_daemon(cli, daemon):
    d = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    code, out, err = cli("status", "--json", socket_path=d.path)
    doc = jdoc(out)
    assert code == 0 and doc["config"] is None and doc["daemon"]["version"] == "1.2.0"
    assert doc["resource_observer"] is None
    code, out, err = cli("status", socket_path=d.path)
    assert "n/a (contract 1 daemon)" in out


def test_status_tolerates_oddly_shaped_daemon_documents(cli, daemon):
    odd = {"ok": True, "contracts": [1, 2], "daemon": "x", "resource_observer": 5, "config": 7}
    d = daemon(handlers(version=lambda a, c: odd, config=lambda a, c: {"ok": True, "config": None}))
    code, out, err = cli("status", "--json", socket_path=d.path)
    doc = jdoc(out)
    assert code == 0 and doc["socket"]["allow_uids"] == [] and doc["config"]["overrides"] is None


def test_host_strips_envelope_fields(cli, daemon):
    d = daemon()
    code, out, err = cli("host", "--json", socket_path=d.path)
    doc = jdoc(out)
    assert doc["load"] == [0.5, 0.4, 0.3] and "contract" not in doc


# ── sessions / show ──────────────────────────────────────────────────────────

def test_sessions_rows_and_filters(cli, daemon):
    d = daemon()
    code, out, err = cli("sessions", "--json", socket_path=d.path)
    rows = {r["session"]: r for r in jdoc(out)["sessions"]}
    assert rows[SESSION_LIVE]["target"] == f"containerid:{CID}" and rows[SESSION_LIVE]["mine"] is True
    assert rows[SESSION_DONE]["target"] == "cgroup:/dev.slice/x" and rows[SESSION_DONE]["mine"] is False
    assert rows[SESSION_LIVE]["label"] == "r0"
    code, out, err = cli("sessions", "--mine", "--json", socket_path=d.path)
    assert [r["session"] for r in jdoc(out)["sessions"]] == [SESSION_LIVE]
    code, out, err = cli("sessions", socket_path=d.path)
    assert SESSION_DONE in out and "uid=4242" in out
    assert d.requests[-1]["args"] == {"state": "all"}


def test_sessions_empty_and_odd_entries(cli, daemon):
    odd = {"ok": True, "sessions": ["junk", {"session": "s-x", "target": "no", "meta": 3}]}
    d = daemon(handlers(status=lambda a, c: odd))
    code, out, err = cli("sessions", "--json", socket_path=d.path)
    assert jdoc(out)["sessions"][0]["target"] is None
    d2 = daemon(handlers(status=lambda a, c: {"ok": True, "sessions": []}), name="b.sock")
    code, out, err = cli("sessions", socket_path=d2.path)
    assert out.strip() == "no sessions"


def test_sessions_needs_contract_2(cli, daemon):
    d = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    code, out, err = cli("sessions", "--json", socket_path=d.path)
    assert code == 3 and jdoc(out)["error"]["cause"] == "contract-unsupported"


def test_show_live_finished_and_unknown(cli, daemon):
    d = daemon()
    code, out, err = cli("show", SESSION_LIVE, "--json", socket_path=d.path)
    assert jdoc(out)["state"] == "live" and jdoc(out)["session"]["session"] == SESSION_LIVE
    code, out, err = cli("show", SESSION_DONE, "--json", socket_path=d.path)
    assert jdoc(out)["state"] == "done" and jdoc(out)["summary"] == SUMMARY
    code, out, err = cli("show", SESSION_DONE, socket_path=d.path)
    assert '"verdict": "ok"' in out
    code, out, err = cli("show", "s-nope", "--json", socket_path=d.path)
    assert code == 2 and jdoc(out)["error"]["code"] == "unknown-session"


def test_show_live_answer_without_a_session_object_falls_back_to_the_listing(cli, daemon):
    d = daemon(handlers(status=lambda a, c: (
        {"ok": True, "session": "x"} if a.get("session") else {"ok": True, "sessions": [done_entry()]})))
    code, out, err = cli("show", SESSION_DONE, "--json", socket_path=d.path)
    assert code == 0 and jdoc(out)["summary"] == SUMMARY


def test_show_propagates_other_daemon_errors(cli, daemon):
    boom = {"ok": False, "error": {"code": "daemon-error", "message": "boom"}}
    d = daemon(handlers(status=lambda a, c: boom))
    assert cli("show", SESSION_DONE, socket_path=d.path)[0] == 1


@pytest.mark.parametrize("parts", [{"host.jsonl": b"{}\n"}, {"summary.json": b"not json"}])
def test_show_without_a_readable_summary(cli, daemon, parts):
    d = daemon(handlers(export=lambda a, c: _fakectl.export_reply(parts)))
    code, out, err = cli("show", SESSION_DONE, socket_path=d.path)
    assert code == 2 and "summary.json" in err


# ── start ────────────────────────────────────────────────────────────────────

def test_start_container_id_sends_the_documented_request(cli, daemon):
    d = daemon()
    code, out, err = cli("start", "--target", f"containerid:{CID}", "--interval", "2", "--damon", "on",
                         "--meta", '{"lane": "x"}', "--json", socket_path=d.path)
    assert code == 0 and jdoc(out)["session"] == SESSION_LIVE
    sent = [r for r in d.requests if r["verb"] == "start"][0]
    assert sent["contract"] == 2
    args = sent["args"]
    assert args["target"] == f"containerid:{CID}" and args["scope"] == "container" and args["damon"] == "on"
    assert args["meta"]["lane"] == "x" and args["meta"]["kind"] == "adhoc" and args["interval"] == 2.0
    code, out, err = cli("start", "--target", f"containerid:{CID}", socket_path=d.path)
    assert out.strip() == SESSION_LIVE
    assert "damon" not in [r for r in d.requests if r["verb"] == "start"][-1]["args"]


def test_start_observe_targets_default_to_observe_scope(cli, daemon):
    d = daemon()
    code, out, err = cli("start", "--target", "slice:dev.slice", "--dry-run", "--json", socket_path=d.path)
    assert code == 0 and jdoc(out)["request"]["scope"] == "observe" and jdoc(out)["dry_run"] is True
    code, out, err = cli("start", "--target", f"containerid:{CID}", "--scope", "container-shared",
                         "--dry-run", socket_path=d.path)
    assert '"scope": "container-shared"' in out
    assert [r["verb"] for r in d.requests] == []


@pytest.mark.parametrize("meta,fragment", [("{nope", "valid JSON"), ("[1]", "JSON object")])
def test_start_rejects_bad_meta(cli, daemon, meta, fragment):
    d = daemon()
    code, out, err = cli("start", "--target", "cgroup:/x", "--meta", meta, socket_path=d.path)
    assert code == 2 and fragment in err


def test_start_against_a_1x_daemon(cli, daemon):
    d = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    code, out, err = cli("start", "--target", f"containerid:{CID}", socket_path=d.path)
    assert code == 3 and "contract-unsupported" in err


def test_resolve_container(monkeypatch):
    monkeypatch.setattr(cgprofilectl.shutil, "which", lambda name: None)
    with pytest.raises(WireFailure) as caught:
        cgprofilectl.resolve_container("web")
    assert "needs docker" in caught.value.message
    monkeypatch.setattr(cgprofilectl.shutil, "which", lambda name: "/usr/bin/docker")

    def fake(stdout, code):
        return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=stdout, stderr="")

    monkeypatch.setattr(cgprofilectl.subprocess, "run", fake("nothex\n", 0))
    with pytest.raises(WireFailure):
        cgprofilectl.resolve_container("web")
    monkeypatch.setattr(cgprofilectl.subprocess, "run", fake(CID + "\n", 1))
    with pytest.raises(WireFailure):
        cgprofilectl.resolve_container("web")
    monkeypatch.setattr(cgprofilectl.subprocess, "run", fake(CID + "\n", 0))
    assert cgprofilectl.resolve_container("web") == CID


def test_start_container_name_is_resolved_client_side(cli, daemon, monkeypatch):
    d = daemon()
    monkeypatch.setattr(cgprofilectl, "resolve_container", lambda name: CID)
    code, out, err = cli("start", "--target", "container:web", "--dry-run", "--json", socket_path=d.path)
    assert jdoc(out)["request"]["target"] == f"containerid:{CID}"


# ── stop ─────────────────────────────────────────────────────────────────────

def test_stop_prints_the_summary(cli, daemon):
    d = daemon()
    code, out, err = cli("stop", "--yes", SESSION_LIVE, "--json", socket_path=d.path)
    doc = jdoc(out)
    assert code == 0 and doc["summary"] == SUMMARY and doc["already_stopped"] is False
    code, out, err = cli("stop", "--yes", SESSION_LIVE, socket_path=d.path)
    assert f"stopped {SESSION_LIVE}" in out
    assert not [r for r in d.requests if r["verb"] == "status"]


def test_stop_by_root_confirms_for_a_foreign_owner(cli, daemon, monkeypatch):
    d = daemon()
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(cgprofilectl.CliRuntime, "confirm", lambda self, prompt: False)
    code, out, err = cli("stop", SESSION_DONE, socket_path=d.path)
    assert code == 2 and "declined" in err
    assert not [r for r in d.requests if r["verb"] == "stop"]
    monkeypatch.setattr(cgprofilectl.CliRuntime, "confirm", lambda self, prompt: True)
    assert cli("stop", SESSION_DONE, socket_path=d.path)[0] == 0


def test_stop_by_root_skips_the_prompt_for_root_unknown_or_non_integer_owners(cli, daemon, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr(cgprofilectl.CliRuntime, "confirm", lambda self, prompt: pytest.fail("must not prompt"))
    for entries in ([{"session": SESSION_DONE, "owner_uid": 0}], [{"session": SESSION_DONE, "owner_uid": None}],
                    [{"session": "s-other", "owner_uid": 5}, "junk"]):
        d = daemon(handlers(status=lambda a, c, entries=entries: {"ok": True, "sessions": entries}),
                   name=f"r{id(entries)}.sock")
        assert cli("stop", SESSION_DONE, socket_path=d.path)[0] == 0


def test_stop_by_root_against_a_1x_daemon_does_not_look_up_owners(cli, daemon, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0)
    d = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION),
                "stop": handlers()["stop"]})
    assert cli("stop", "--yes", SESSION_LIVE, socket_path=d.path)[0] == 0


# ── watch ────────────────────────────────────────────────────────────────────

def test_watch_text_and_json(cli, daemon):
    d = daemon()
    code, out, err = cli("watch", SESSION_LIVE, socket_path=d.path)
    assert code == 0
    assert "mem=7 cpu=0.5 samples=3" in out and "verdict stalled" in out and "end stopped" in out
    code, out, err = cli("watch", SESSION_LIVE, "--json", "--watch-interval", "5", socket_path=d.path)
    docs = [json.loads(line) for line in out.strip().splitlines()]
    assert docs[-1]["event"] == "end" and all(doc["schema"] == 1 for doc in docs)
    assert [r for r in d.requests if r["verb"] == "watch"][-1]["args"]["watch_interval"] == 5.0


def test_watch_line_without_a_live_object():
    assert cgprofilectl._watch_line({"event": "reading", "at": "t", "live": "x"}).startswith("t mem=None")
    assert cgprofilectl._watch_line({"event": "end"}) == "end"


# ── prune ────────────────────────────────────────────────────────────────────

def test_prune_dry_run_lists_candidates_and_removes_nothing(cli, daemon):
    d = daemon()
    code, out, err = cli("prune", "--dry-run", "--json", socket_path=d.path)
    doc = jdoc(out)
    assert code == 0 and doc["removed"] == [SESSION_DONE] and doc["dry_run"] is True
    assert [r["args"]["dry_run"] for r in d.requests if r["verb"] == "gc"] == [True]
    code, out, err = cli("prune", "--dry-run", socket_path=d.path)
    assert f"would remove {SESSION_DONE}" in out


def test_prune_with_nothing_to_remove(cli, daemon):
    d = daemon()
    code, out, err = cli("prune", "--keep", "99", "--keep-days", "3", socket_path=d.path)
    assert code == 0 and "nothing to remove" in out
    assert [r["args"] for r in d.requests if r["verb"] == "gc"] == [{"dry_run": True, "keep": 99, "keep_days": 3}]


def test_prune_confirmed_removes_after_the_preview(cli, daemon):
    d = daemon()
    code, out, err = cli("prune", "--yes", socket_path=d.path)
    assert code == 0 and f"removed {SESSION_DONE}" in out
    assert [r["args"]["dry_run"] for r in d.requests if r["verb"] == "gc"] == [True, False]
    code, out, err = cli("prune", "--yes", "--json", socket_path=d.path)
    assert jdoc(out)["dry_run"] is False


def test_prune_declined_or_unconfirmed_removes_nothing(cli, daemon, monkeypatch):
    d = daemon()
    code, out, err = cli("prune", socket_path=d.path)
    assert code == 2 and "stdin is not interactive" in err
    monkeypatch.setattr(cgprofilectl.CliRuntime, "confirm", lambda self, prompt: False)
    code, out, err = cli("prune", socket_path=d.path)
    assert code == 2 and "declined" in err
    assert [r["args"]["dry_run"] for r in d.requests if r["verb"] == "gc"] == [True, True]


def test_prune_that_removes_nothing_in_the_second_call(cli, daemon):
    calls = iter([{"ok": True, "removed": [SESSION_DONE], "kept": 1}, {"ok": True, "removed": [], "kept": 2}])
    d = daemon(handlers(gc=lambda a, c: next(calls)))
    code, out, err = cli("prune", "--yes", socket_path=d.path)
    assert "nothing removed" in out


def test_prune_against_a_1x_daemon_sends_nothing_destructive(cli, daemon):
    d = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    code, out, err = cli("prune", "--yes", socket_path=d.path)
    assert code == 3 and [r["verb"] for r in d.requests] == ["version"]


# ── the local verbs live on the end-user client ──────────────────────────────

@pytest.mark.parametrize("verb", ["run", "mark", "report", "analyze", "skills"])
def test_the_local_verbs_are_not_on_the_socket_client(cli, verb):
    code, out, err = cli(verb)
    assert code == 2 and verb in err


# ── config ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,text,expected", [
    ("interval", "2.5", 2.5), ("keep_days", "14", 14), ("damon_default", "on", "on"),
    ("allow_uids", "1003, 1004", [1003, 1004]), ("allow_uids", "[5, 6]", [5, 6]), ("allow_uids", "", []),
])
def test_parse_config_value(key, text, expected):
    assert cgprofilectl.parse_config_value(key, text) == expected


@pytest.mark.parametrize("key,text", [
    ("bogus", "1"), ("interval", "fast"), ("keep_days", "1.5"), ("damon_default", "maybe"),
    ("allow_uids", "a,b"), ("allow_uids", "[-1]"), ("allow_uids", "[1, true]"), ("allow_uids", "[1,"),
])
def test_parse_config_value_rejects(key, text):
    with pytest.raises(WireFailure) as caught:
        cgprofilectl.parse_config_value(key, text)
    assert caught.value.exit_code == 2


def test_parse_config_value_rejects_a_non_list_json_value(monkeypatch):
    monkeypatch.setattr(cgprofilectl.json, "loads", lambda text: {"a": 1})
    with pytest.raises(WireFailure):
        cgprofilectl.parse_config_value("allow_uids", "[1]")


def test_config_show(cli, daemon):
    d = daemon()
    code, out, err = cli("config", "show", "--json", socket_path=d.path)
    doc = jdoc(out)
    assert code == 0 and doc["digest"] == "sha256:abc" and doc["keys"]["interval"]["source"] == "default"
    code, out, err = cli("config", "show", socket_path=d.path)
    assert "interval  1.0  (default)" in out


def test_config_show_against_a_1x_daemon(cli, daemon):
    d = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    code, out, err = cli("config", "show", socket_path=d.path)
    assert code == 3 and "contract-unsupported" in err


def test_config_set_and_dry_run(cli, daemon):
    d = daemon()
    code, out, err = cli("config", "set", "interval", "2", "--persist", "--json", socket_path=d.path)
    doc = jdoc(out)
    assert code == 0 and doc["new"] == 2.0 and doc["persisted"] is True and doc["audit_id"] == "a1"
    sent = [r for r in d.requests if r["verb"] == "config-set"][-1]["args"]
    assert sent == {"key": "interval", "value": 2.0, "persist": True, "dry_run": False}
    code, out, err = cli("config", "set", "interval", "3", "--dry-run", socket_path=d.path)
    assert "(dry run)" in out
    assert [r for r in d.requests if r["verb"] == "config-set"][-1]["args"]["dry_run"] is True
    code, out, err = cli("config", "set", "interval", "3", socket_path=d.path)
    assert out.strip() == "interval: 1.0 -> 3.0"


def test_config_set_refusals(cli, daemon):
    refuse = {"ok": False, "error": {"code": "peer-refused", "message": "root only"}}
    d = daemon(handlers(**{"config-set": lambda a, c: refuse}))
    assert cli("config", "set", "interval", "2", socket_path=d.path)[0] == 2
    assert cli("config", "set", "nope", "2", socket_path=d.path)[0] == 2


def test_config_reset(cli, daemon):
    d = daemon()
    code, out, err = cli("config", "reset", "interval", "--json", socket_path=d.path)
    assert jdoc(out)["reset"] == ["interval"]
    code, out, err = cli("config", "reset", "--all", socket_path=d.path)
    assert "reset interval" in out
    assert [r["args"] for r in d.requests if r["verb"] == "config-reset"] == [{"key": "interval"}, {"all": True}]
    d2 = daemon(handlers(**{"config-reset": lambda a, c: {"ok": True, "config-reset": {}}}), name="n.sock")
    code, out, err = cli("config", "reset", "--all", socket_path=d2.path)
    assert "no overrides were set" in out
    assert cli("config", "reset", "interval", "--all", socket_path=d.path)[0] == 2


# ── doctor ───────────────────────────────────────────────────────────────────

def _check(name, args):
    check = next(c for c in cgprofilectl.DOCTOR_CHECKS if c.name == name)
    return check.run(None, args)


def _ns(path):
    return argparse.Namespace(socket=path)


def test_doctor_names_every_check():
    assert [c.name for c in cgprofilectl.DOCTOR_CHECKS] == [
        "endpoint-env", "socket", "caller-group", "daemon-version", "contracts", "observer-dir", "slice", "damon",
        "sessions-dir", "overrides", "report-extra"]


def test_doctor_against_a_healthy_daemon(cli, daemon, monkeypatch):
    d = daemon()
    monkeypatch.setenv("CGPROFILE_CONTROL_SOCKET", d.path)
    monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", "/tmp/cgprofile-observer.sock")
    code, out, err = cli("doctor", "--json", socket_path=d.path)
    doc = jdoc(out)
    names = {c["name"]: c for c in doc["checks"]}
    assert names["socket"]["status"] == "ok" and names["contracts"]["status"] == "ok"
    assert "skills" not in names
    assert [r["verb"] for r in d.requests].count("version") == 1


def test_endpoint_env_doctor_check_reports_missing_and_present_values(monkeypatch):
    monkeypatch.delenv("CGPROFILE_CONTROL_SOCKET", raising=False)
    monkeypatch.delenv("CGPROFILE_OBSERVER_SOCKET", raising=False)
    result = _check("endpoint-env", argparse.Namespace(socket=None))
    assert result.status == "fail" and "CGPROFILE_CONTROL_SOCKET" in result.summary
    monkeypatch.setenv("CGPROFILE_CONTROL_SOCKET", "/host/control.sock")
    monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", "/host/observer.sock")
    result = _check("endpoint-env", argparse.Namespace(socket=None))
    assert result.status == "ok" and result.details["CGPROFILE_OBSERVER_SOCKET"] == "/host/observer.sock"


def test_doctor_without_a_daemon_degrades_to_skips_and_names_the_fix(cli, sockdir):
    code, out, err = cli("doctor", socket_path=os.path.join(sockdir, "absent.sock"))
    assert code != 0
    assert "socket-missing" in out + err and "systemctl" in out + err


def test_each_check_branch_against_a_daemon(daemon):
    d = daemon()
    ns = _ns(d.path)
    assert _check("socket", ns).status == "ok"
    assert _check("caller-group", ns).status in ("ok", "fail")
    assert _check("daemon-version", ns).status in ("ok", "warn")
    assert _check("contracts", ns).status == "ok"
    assert _check("damon", ns).status == "ok"
    assert _check("sessions-dir", ns).status == "ok"
    assert _check("overrides", ns).status == "ok"


def test_checks_degrade_when_the_daemon_is_missing(sockdir):
    ns = _ns(os.path.join(sockdir, "absent.sock"))
    assert _check("socket", ns).status == "fail"
    assert _check("caller-group", ns).status == "skip"
    for name in ("daemon-version", "contracts", "damon", "sessions-dir", "overrides"):
        result = _check(name, ns)
        assert result.status == "skip" and "systemctl" in result.remedy


def test_caller_group_fail_and_not_in_group(daemon, monkeypatch):
    d = daemon()
    monkeypatch.setattr(cgprofilectl.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cgprofilectl, "caller_in_group", lambda gid: False)
    result = _check("caller-group", _ns(d.path))
    assert result.status == "fail" and "re-login" in result.remedy


def test_root_has_administrative_socket_access_without_group_membership(daemon, monkeypatch):
    d = daemon()
    monkeypatch.setattr(cgprofilectl.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cgprofilectl, "caller_in_group", lambda gid: False)
    result = _check("caller-group", _ns(d.path))
    assert result.status == "ok" and "root has administrative access" in result.summary


def test_daemon_version_equal_is_ok(daemon, monkeypatch):
    d = daemon()
    monkeypatch.setattr(cgprofilectl, "installed_version", lambda: "2.0.0")
    assert _check("daemon-version", _ns(d.path)).status == "ok"


def test_daemon_version_skew_warns(daemon):
    older = dict(_fakectl.V2_VERSION, daemon={"version": "9.0.0"})
    d = daemon({"version": lambda a, c: older})
    assert _check("daemon-version", _ns(d.path)).status == "warn"


def test_contracts_check_variants(daemon):
    none_shared = dict(_fakectl.V2_VERSION, contracts=[7])
    d = daemon({"version": lambda a, c: none_shared})
    assert _check("contracts", _ns(d.path)).status == "fail"
    d.close()
    old_observer = dict(_fakectl.V2_VERSION, resource_observer={"contracts": [1]})
    d2 = daemon({"version": lambda a, c: old_observer}, name="o.sock")
    assert _check("contracts", _ns(d2.path)).status == "warn"
    d3 = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION)}, name="p.sock")
    assert _check("contracts", _ns(d3.path)).status == "ok"


def test_damon_unavailable_warns(daemon):
    version = dict(_fakectl.V2_VERSION, daemon="text")
    d = daemon({"version": lambda a, c: version})
    assert _check("damon", _ns(d.path)).status == "warn"


def test_overrides_check_variants(daemon):
    d1 = daemon({"version": lambda a, c: dict(_fakectl.V1_VERSION)})
    assert _check("overrides", _ns(d1.path)).status == "skip"
    shadow = {"ok": True, "config": {"keys": {
        "interval": {"value": 2, "source": "override", "shadows": {"source": "file"}},
        "keep_days": {"value": 1, "source": "override"}, "x": "junk"}, "overrides_ignored": 2}}
    d2 = daemon({"version": lambda a, c: dict(_fakectl.V2_VERSION), "config": lambda a, c: shadow}, name="s.sock")
    result = _check("overrides", _ns(d2.path))
    assert result.status == "warn" and "cgprofilectl config reset interval" in result.remedy
    assert "2 stored override entries were ignored" in result.summary
    only_shadow = {"ok": True, "config": {"keys": {
        "interval": {"value": 2, "source": "override", "shadows": {"source": "file"}}}}}
    d4 = daemon({"version": lambda a, c: dict(_fakectl.V2_VERSION), "config": lambda a, c: only_shadow},
                name="u.sock")
    assert "ignored" not in _check("overrides", _ns(d4.path)).summary
    ignored_only = {"ok": True, "config": {"keys": {}, "overrides_ignored": 1}}
    d3 = daemon({"version": lambda a, c: dict(_fakectl.V2_VERSION), "config": lambda a, c: ignored_only},
                name="t.sock")
    result = _check("overrides", _ns(d3.path))
    assert result.status == "warn" and result.remedy == "inspect the overrides file"


def test_observer_dir_check(monkeypatch, tmp_path):
    from types import SimpleNamespace

    observer = tmp_path / "observer"
    observer.mkdir()
    observer_socket = observer / "cgprofile.sock"
    monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", str(observer_socket))
    ns = _ns(str(tmp_path / "absent.sock"))
    monkeypatch.setattr(cgprofilectl, "_probe", lambda _args: SimpleNamespace(
        error=None, path="/tmp/control.sock", version={"resource_observer": {"path": str(observer_socket)}},
        config=lambda: None))
    control_gid = 4242
    observer_gid = 4243
    real_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        if str(path) == str(observer):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        if str(path) == str(observer_socket):
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=observer_gid)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(cgprofilectl.os, "lstat", fake_lstat)
    monkeypatch.setattr(cgprofilectl, "socket_group", lambda path: {"group": "control", "gid": control_gid})
    monkeypatch.setattr(cgprofilectl.grp, "getgrgid", lambda gid: SimpleNamespace(gr_name="cgprofile-observer"))
    assert _check("observer-dir", ns).status == "ok"

    def invalid_directory(path, *args, **kwargs):
        if str(path) == str(observer):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o751, st_uid=0, st_gid=0)
        return fake_lstat(path, *args, **kwargs)

    monkeypatch.setattr(cgprofilectl.os, "lstat", invalid_directory)
    assert "0751" in _check("observer-dir", ns).summary

    monkeypatch.setattr(cgprofilectl.os, "lstat", fake_lstat)
    monkeypatch.setattr(cgprofilectl, "socket_group", lambda path: {"group": "control", "gid": observer_gid})
    assert "control socket group" in _check("observer-dir", ns).summary


def test_observer_dir_check_refuses_advertisement_mismatch(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", str(tmp_path / "env.sock"))
    monkeypatch.setattr(cgprofilectl, "_probe", lambda _args: SimpleNamespace(
        error=None, version={"resource_observer": {"path": str(tmp_path / "daemon.sock")}}))
    result = _check("observer-dir", _ns("/tmp/control.sock"))
    assert result.status == "fail" and "does not match daemon advertisement" in result.summary


@pytest.mark.parametrize(
    "environment,advertised,expected",
    [
        (None, "/tmp/observer.sock", "CGPROFILE_OBSERVER_SOCKET is unset"),
        ("/tmp/observer.sock", "relative.sock", "does not advertise an absolute observer socket path"),
    ],
)
def test_observer_dir_check_requires_environment_and_absolute_advertisement(
    monkeypatch, tmp_path, environment, advertised, expected,
):
    if environment is None:
        monkeypatch.delenv("CGPROFILE_OBSERVER_SOCKET", raising=False)
    else:
        monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", environment)
    monkeypatch.setattr(cgprofilectl, "_probe", lambda _args: types.SimpleNamespace(
        error=None, version={"resource_observer": {"path": advertised}}))

    result = _check("observer-dir", _ns(str(tmp_path / "control.sock")))

    assert result.status == "fail" and expected in result.summary


def test_observer_dir_check_uses_configured_path_group_and_mode(monkeypatch, tmp_path):
    from types import SimpleNamespace

    observer = tmp_path / "custom-observer"
    observer.mkdir()
    path = observer / "custom.sock"
    control_gid, observer_gid = 4242, 4243
    probe = SimpleNamespace(
        error=None,
        path="/tmp/control.sock",
        version={"resource_observer": {"path": str(path)}},
        config=lambda: {"keys": {"sockets.observer": {
            "value": {"path": str(path), "group": "custom-observer", "mode": "0600"},
        }}},
    )
    monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", str(path))
    monkeypatch.setattr(cgprofilectl, "_probe", lambda _args: probe)
    real_lstat = os.lstat

    def fake_lstat(target, *args, **kwargs):
        if str(target) == str(observer):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        if str(target) == str(path):
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=0, st_gid=observer_gid)
        return real_lstat(target, *args, **kwargs)

    monkeypatch.setattr(cgprofilectl.os, "lstat", fake_lstat)
    monkeypatch.setattr(cgprofilectl, "socket_group", lambda _path: {"group": "control", "gid": control_gid})
    def getgrnam(name):
        if name != "custom-observer":
            raise KeyError(name)
        return SimpleNamespace(gr_gid=observer_gid)

    monkeypatch.setattr(cgprofilectl.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(cgprofilectl.grp, "getgrgid", lambda gid: SimpleNamespace(gr_name="custom-observer"))

    assert _check("observer-dir", _ns("/tmp/control.sock")).status == "ok"


def test_observer_dir_check_reports_each_way_the_socket_can_be_wrong(monkeypatch, tmp_path):
    from types import SimpleNamespace

    observer_dir = tmp_path / "observer"
    observer_dir.mkdir()
    path = observer_dir / "cgprofile.sock"
    control_gid, observer_gid = 4242, 4243
    state = {"config": {"keys": {"sockets.observer": {"value": {"group": "obs", "mode": "0660"}}}}}

    def config():
        if state["config"] is None:
            raise WireFailure("no config", exit_code=1, code="daemon-error")
        return state["config"]

    probe = SimpleNamespace(error=None, path="/tmp/control.sock",
                            version={"resource_observer": {"path": str(path)}},
                            config=config)
    monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", str(path))
    monkeypatch.setattr(cgprofilectl, "_probe", lambda _args: probe)
    nodes = {
        str(observer_dir): SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0),
        str(path): SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=observer_gid),
    }
    real_lstat = os.lstat

    def fake_lstat(target, *args, **kwargs):
        if str(target) in nodes:
            return nodes[str(target)]
        if str(target) in {str(observer_dir), str(path)}:
            raise FileNotFoundError(target)
        return real_lstat(target, *args, **kwargs)

    monkeypatch.setattr(cgprofilectl.os, "lstat", fake_lstat)
    control = {"group": "control", "gid": control_gid}
    monkeypatch.setattr(cgprofilectl, "socket_group", lambda _path: control)
    names = {"obs": observer_gid, "docker": 99}

    def getgrnam(name):
        if name not in names:
            raise KeyError(name)
        return SimpleNamespace(gr_gid=names[name])

    gid_names = {observer_gid: "obs", 99: "docker"}

    def getgrgid(gid):
        if gid not in gid_names:
            raise KeyError(gid)
        return SimpleNamespace(gr_name=gid_names[gid])

    monkeypatch.setattr(cgprofilectl.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(cgprofilectl.grp, "getgrgid", getgrgid)
    run = lambda: _check("observer-dir", _ns("/tmp/control.sock"))  # noqa: E731

    assert run().status == "ok"
    state["config"] = None  # the daemon cannot report its config: defaults apply
    assert run().status == "ok"
    state["config"] = {"keys": {"sockets.observer": {"value": {"group": 3, "mode": 4}}}}
    assert run().status == "ok"
    state["config"] = {"keys": {"sockets.observer": {"value": {"group": "obs", "mode": "0660"}}}}

    nodes[str(path)] = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=5, st_gid=control_gid)
    problems = run().summary
    assert "not a root-owned socket" in problems and "mode is 0600, expected 0660" in problems
    assert "uses the control socket group" in problems and "gid is 4242, expected obs (4243)" in problems
    state["config"] = {"keys": {"sockets.observer": {"value": {"group": "obs", "mode": "bad"}}}}
    assert "expected 0660" in run().summary  # an unparsable configured mode falls back to 0660
    state["config"] = {"keys": {"sockets.observer": {"value": {"group": "gone", "mode": "0660"}}}}
    nodes[str(path)] = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=99)
    assert "'gone' does not exist" in run().summary and "docker group" in run().summary
    nodes[str(path)] = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=777)
    assert run().status == "warn"  # a gid with no name: nothing to say about docker, the group still mismatches
    state["config"] = {"keys": {"sockets.observer": {"value": {"group": "docker-alias", "mode": "0660"}}}}
    names["docker-alias"] = 99
    gid_names[99] = "docker-alias"
    nodes[str(path)] = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=99)
    assert "docker GID" in run().summary
    nodes[str(path)] = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=0)
    gid_names[0] = "root"
    assert "GID 0" in run().summary
    control.update(group="docker", gid=99)
    nodes[str(path)] = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=observer_gid)
    state["config"] = {"keys": {"sockets.observer": {"value": {"group": "obs", "mode": "0660"}}}}
    assert "control socket uses the docker group" in run().summary
    control.update(group="control", gid=99)
    assert "control socket group resolves to the docker GID" in run().summary
    control.update(gid=0)
    assert "control socket group resolves to GID 0" in run().summary
    del nodes[str(path)]
    assert run().status == "warn" and "is absent" in run().summary
    del nodes[str(observer_dir)]
    assert run().status == "skip"


def test_slice_check(monkeypatch, tmp_path):
    ns = _ns("/none")
    monkeypatch.setattr(cgprofilectl, "SLICE_DIR", str(tmp_path / "missing.slice"))
    assert _check("slice", ns).status == "skip"
    base = tmp_path / "cgprofile.slice"
    base.mkdir()
    monkeypatch.setattr(cgprofilectl, "SLICE_DIR", str(base))
    assert _check("slice", ns).status == "warn"
    (base / "memory.max").write_text("2147483648\n")
    assert _check("slice", ns).status == "ok"


def test_report_extra_check(monkeypatch):
    ns = _ns("/none")
    monkeypatch.setitem(sys.modules, "pandas", None)
    result = _check("report-extra", ns)
    assert result.status == "warn" and "report" in result.remedy
    for name in ("matplotlib", "pandas", "plotly", "ruptures", "scipy"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    assert _check("report-extra", ns).status == "ok"


def test_probe_is_cached_on_the_namespace(daemon):
    d = daemon()
    ns = _ns(d.path)
    assert cgprofilectl._probe(ns) is cgprofilectl._probe(ns)
    assert cgprofilectl._probe(_ns(d.path)) is not cgprofilectl._probe(ns)
    assert ctlwire.resolve_socket(d.path)[1] == "flag"
