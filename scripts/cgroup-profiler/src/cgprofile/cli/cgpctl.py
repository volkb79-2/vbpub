"""``cgpctl``: the client console script (CGPCTL-SPEC.md section 2).

The registry is built on cli-extended. Daemon verbs speak the ctl wire through
:mod:`cgprofile.ctlwire` (standard library only); the report renderer is
imported only inside ``report``; ``mark`` imports nothing outside the standard
library and cli-extended (a test pins this).
"""

from __future__ import annotations

import argparse
import functools
import grp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
    register_doctor,
    register_skills_verbs,
)
from cli_extended.doctor import CheckResult, DoctorCheck

from .. import ctlwire
from ..ctlwire import CAUSE_REMEDY, CLIENT_CONTRACTS, WireFailure
from ..version import installed_version

SCHEMA = 1
TUNABLE_KEYS = (
    "allow_uids", "interval", "damon_default", "keep_sessions", "keep_days",
    "export_max_bytes", "max_sessions", "max_exports", "max_watchers",
)
_KEY_TYPES = {
    "allow_uids": "uids", "interval": "number", "damon_default": "onoff",
    "keep_sessions": "int", "keep_days": "int", "export_max_bytes": "int",
    "max_sessions": "int", "max_exports": "int", "max_watchers": "int",
}
DIRECT_MODE_REFUSAL = "direct mode needs cgroupfs; use 'cgpctl start' against the host daemon"
REPORT_EXTRA_HINT = "pip install 'cgprofile[report]'"
OBSERVER_DIR = "/run/cgprofile/observer"


def identity() -> CliIdentity:
    return CliIdentity(name="CGPROFILE", version=installed_version(),
                       long_name="cgroup resource profiler client", command="cgpctl")


# ── shared plumbing ─────────────────────────────────────────────────────────

def _bad(message: str, code: str = "bad-argument") -> WireFailure:
    return WireFailure(message, exit_code=2, code=code)


def _fail(runtime: CliRuntime, exc: WireFailure) -> int:
    """Render a failure: the ``--json`` error document, or one ``[ERROR]`` line plus the remedy."""
    if runtime.json_mode:
        runtime.output.error(exc.message)
        runtime.output.primary({"schema": SCHEMA, "ok": False, "error": {
            "code": exc.code, "message": exc.message, "cause": exc.cause}})
        return exc.exit_code
    message = f"{exc.message} (cause: {exc.cause})" if exc.cause else exc.message
    raise CliFailure(message, exit_code=exc.exit_code,
                     hint=CAUSE_REMEDY.get(exc.cause) if exc.cause else None)


def guarded(handler: Callable[[argparse.Namespace, CliRuntime], Optional[int]]):
    """Turn a ``WireFailure`` into the CLI's exit code and, under ``--json``, its error document."""
    @functools.wraps(handler)
    def run(args: argparse.Namespace, runtime: CliRuntime) -> Optional[int]:
        try:
            return handler(args, runtime)
        except WireFailure as exc:
            return _fail(runtime, exc)
    return run


def socket_path(args: argparse.Namespace) -> str:
    return ctlwire.resolve_socket(getattr(args, "socket", None))[0]


def emit(runtime: CliRuntime, doc: Dict[str, Any], lines: Sequence[str]) -> None:
    body = {"schema": SCHEMA, "ok": True, **doc}
    runtime.output.primary(body if runtime.json_mode else "\n".join(lines))


def _iso(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _seconds_since(stamp: Optional[str], now: Optional[float] = None) -> Optional[float]:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round((time.time() if now is None else now) - parsed.timestamp(), 3)


def socket_group(path: str) -> Dict[str, Any]:
    """The socket's owning group, by ``stat`` (never configured)."""
    try:
        gid = os.stat(path).st_gid
    except OSError:
        return {"group": None, "gid": None}
    try:
        name: Optional[str] = grp.getgrgid(gid).gr_name
    except KeyError:
        name = None
    return {"group": name, "gid": gid}


def caller_in_group(gid: Optional[int]) -> bool:
    if gid is None:
        return False
    return gid == os.getegid() or gid in os.getgroups()


def _mine(entry: Dict[str, Any]) -> bool:
    return entry.get("owner_uid") == os.getuid()


# ── read verbs ──────────────────────────────────────────────────────────────

def _skew(client: str, daemon: Optional[str]) -> str:
    if daemon is None or daemon == client:
        return "equal"

    def key(text: str) -> List[int]:
        return [int(part) for part in re.findall(r"\d+", text)[:3]]

    return "client-older" if key(client) < key(daemon) else "client-newer"


@guarded
def _status(args: argparse.Namespace, runtime: CliRuntime) -> None:
    path, source = ctlwire.resolve_socket(getattr(args, "socket", None))
    version = ctlwire.call(path, "version", None, 1)
    contracts = ctlwire.daemon_contracts(version)
    daemon_doc = version.get("daemon") if isinstance(version.get("daemon"), dict) else {}
    observer = version.get("resource_observer") if isinstance(version.get("resource_observer"), dict) else None
    config: Optional[Dict[str, Any]] = None
    allow_uids: List[Any] = []
    if 2 in contracts:
        conf = ctlwire.call(path, "config", None, 2).get("config") or {}
        keys = conf.get("keys") or {}
        allow_uids = list((keys.get("allow_uids") or {}).get("value") or [])
        brief = version.get("config") if isinstance(version.get("config"), dict) else {}
        config = {"file": conf.get("file"), "digest": conf.get("digest"),
                  "loaded_at": conf.get("loaded_at"), "reloads": conf.get("reloads"),
                  "last_reload_error": conf.get("last_reload_error"),
                  "overrides": brief.get("overrides")}
    daemon_version = ctlwire.daemon_version(version)
    client = installed_version()
    group = socket_group(path)
    doc = {
        "client": {"version": client, "contracts": list(CLIENT_CONTRACTS)},
        "daemon": {
            "version": daemon_version, "contracts": contracts,
            "started_at": _iso(daemon_doc.get("started_at")),
            "uptime_seconds": _seconds_since(_iso(daemon_doc.get("started_at"))),
            "damon": daemon_doc.get("damon"), "damon_default": daemon_doc.get("damon_default"),
            "sessions_live": daemon_doc.get("sessions_live"), "max_sessions": daemon_doc.get("max_sessions"),
        },
        "socket": {"path": path, "source": source, **group, "listening": True, "allow_uids": allow_uids},
        "resource_observer": observer,
        "config": config,
        "skew": {"client_vs_daemon": _skew(client, daemon_version),
                 "contract_ok": bool(set(contracts) & set(CLIENT_CONTRACTS))},
    }
    d = doc["daemon"]
    emit(runtime, doc, [
        f"daemon {d['version']} contracts {d['contracts']} up {d['uptime_seconds']}s",
        f"sessions {d['sessions_live']}/{d['max_sessions']}  damon {d['damon']} (default {d['damon_default']})",
        f"socket {path} ({source}) group {group['group']} gid {group['gid']}",
        f"client {client} contracts {list(CLIENT_CONTRACTS)} skew {doc['skew']['client_vs_daemon']}",
        f"config {config['digest'] if config else 'n/a (contract 1 daemon)'}",
    ])


@guarded
def _host(args: argparse.Namespace, runtime: CliRuntime) -> None:
    doc = ctlwire.call(socket_path(args), "host", None, 1)
    host = {k: v for k, v in doc.items() if k not in ("ok", "contract")}
    emit(runtime, host, [json.dumps(host, indent=2, sort_keys=True)])


def _row(entry: Dict[str, Any]) -> Dict[str, Any]:
    target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
    if target.get("container_id"):
        spec: Optional[str] = f"containerid:{target['container_id']}"
    elif target.get("cgroup"):
        spec = f"cgroup:{target['cgroup']}"
    else:
        spec = None
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    return {
        "session": entry.get("session"), "state": entry.get("state", "live"),
        "owner_uid": entry.get("owner_uid"), "mine": _mine(entry), "target": spec,
        "scope": entry.get("scope"), "started_at": entry.get("started_at"),
        "ended_at": entry.get("ended_at"), "elapsed_seconds": entry.get("elapsed_seconds"),
        "label": meta.get("lane"),
    }


@guarded
def _sessions(args: argparse.Namespace, runtime: CliRuntime) -> None:
    path = socket_path(args)
    ctlwire.need_contract(path, "sessions", 2)
    doc = ctlwire.call(path, "status", {"state": args.state}, 2)
    rows = [_row(e) for e in doc.get("sessions", []) if isinstance(e, dict)]
    if args.mine:
        rows = [r for r in rows if r["mine"]]
    lines = [f"{r['session']}  {r['state']:<7} uid={r['owner_uid']}  {r['target']}  {r['started_at']}"
             for r in rows] or ["no sessions"]
    emit(runtime, {"at": doc.get("at"), "sessions": rows}, lines)


@guarded
def _show(args: argparse.Namespace, runtime: CliRuntime) -> None:
    path = socket_path(args)
    ctlwire.need_contract(path, "show", 2)
    live = ctlwire.call(path, "status", {"session": args.session}, 2)
    entries = [e for e in live.get("sessions", []) if isinstance(e, dict) and e.get("session") == args.session]
    if entries:
        body: Dict[str, Any] = {"state": entries[0].get("state", "live"), "session": entries[0]}
    else:
        done = ctlwire.call(path, "status", {"state": "all"}, 2)
        stored = [e for e in done.get("sessions", []) if isinstance(e, dict) and e.get("session") == args.session]
        if not stored:
            raise _bad(f"unknown-session: no session {args.session}", "unknown-session")
        # PROTOCOL.md section 10.4 does not put the Summary on listing entries: read it from the export.
        parts = ctlwire.export_parts(path, args.session)
        try:
            summary = json.loads(parts["summary.json"].decode("utf-8"))
        except (KeyError, ValueError) as exc:
            raise _bad(f"the export of {args.session} carries no readable summary.json") from exc
        body = {"state": stored[0].get("state", "done"), "summary": summary}
    emit(runtime, body, [json.dumps(body, indent=2, sort_keys=True)])


# ── start / stop / watch ────────────────────────────────────────────────────

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")


def resolve_container(name: str) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise _bad(f"container:{name} needs docker to resolve the name; pass containerid:<64 hex> instead")
    result = subprocess.run([docker, "inspect", "--format", "{{.Id}}", name],
                            capture_output=True, text=True, check=False)
    cid = result.stdout.strip()
    if result.returncode or not _CONTAINER_ID.fullmatch(cid):
        raise _bad(f"container:{name} does not resolve to a container id (docker inspect failed)")
    return cid


def _start_request(args: argparse.Namespace) -> Dict[str, Any]:
    target = args.target
    if target.startswith("container:"):
        target = "containerid:" + resolve_container(target[len("container:"):])
    observe = target.startswith(("cgroup:", "slice:"))
    scope = args.scope or ("observe" if observe else "container")
    try:
        meta = json.loads(args.meta) if args.meta else {}
    except json.JSONDecodeError as exc:
        raise _bad(f"--meta must be valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise _bad("--meta must be a JSON object")
    meta = {"kind": "adhoc", "client": f"cgpctl {installed_version()}", "uid": os.getuid(), **meta}
    wire: Dict[str, Any] = {"target": target, "scope": scope, "token": args.token}
    if args.damon is not None:
        wire["damon"] = args.damon
    wire.update({
        "interval": args.interval, "meta": meta, "progress_stream": args.progress_stream,
        "idle_bound": args.idle_bound, "ceiling": args.ceiling, "on_stall": args.on_stall,
        "place": args.place, "memory_high": args.memory_high, "memory_max": args.memory_max,
        "cpu_weight": args.cpu_weight,
    })
    return wire


@guarded
def _start(args: argparse.Namespace, runtime: CliRuntime) -> None:
    wire = _start_request(args)
    path = socket_path(args)
    if runtime.dry_run:
        emit(runtime, {"dry_run": True, "request": wire}, [json.dumps(wire, indent=2, sort_keys=True)])
        return
    ctlwire.need_contract(path, "start", 2)
    doc = ctlwire.call(path, "start", wire, 2)
    body = {"session": doc.get("session"), "started_at": doc.get("started_at"),
            "target": doc.get("target"), "placement": doc.get("placement")}
    emit(runtime, body, [str(doc.get("session"))])


def _session_owner(path: str, session: str) -> Optional[int]:
    doc = ctlwire.call(path, "status", {"state": "all"}, 2)
    for entry in doc.get("sessions", []):
        if isinstance(entry, dict) and entry.get("session") == session:
            owner = entry.get("owner_uid")
            return owner if isinstance(owner, int) else None
    return None


@guarded
def _stop(args: argparse.Namespace, runtime: CliRuntime) -> None:
    path = socket_path(args)
    if os.getuid() == 0 and ctlwire.daemon_contracts(ctlwire.call(path, "version", None, 1)).count(2):
        owner = _session_owner(path, args.session)
        if owner not in (None, 0) and not runtime.confirm(
                f"session {args.session} belongs to uid {owner}; stop it anyway?"):
            raise _bad("stop declined; nothing stopped")
    doc = ctlwire.call(path, "stop", {"session": args.session}, 1)
    body = {"session": doc.get("session"), "already_stopped": doc.get("already_stopped", False),
            "summary": doc.get("summary")}
    emit(runtime, body, [f"stopped {doc.get('session')}"])


def _watch_line(doc: Dict[str, Any]) -> str:
    event = doc.get("event")
    live = doc.get("live") if isinstance(doc.get("live"), dict) else {}
    if event == "reading":
        return (f"{doc.get('at')} mem={live.get('memory_current_bytes')} "
                f"cpu={live.get('cpu_cores_recent')} samples={live.get('samples')}")
    return f"{doc.get('at', '')} {event} {doc.get('verdict', doc.get('status', ''))}".strip()


@guarded
def _watch(args: argparse.Namespace, runtime: CliRuntime) -> None:
    for doc in ctlwire.watch_lines(socket_path(args), args.session, args.watch_interval):
        if runtime.json_mode:
            runtime.output.primary({"schema": SCHEMA, **doc})
        else:
            runtime.output.primary(_watch_line(doc))


# ── report ──────────────────────────────────────────────────────────────────

def _render(run_path: str, out_dir: str, html: bool, md: bool) -> List[Dict[str, Any]]:
    """Render a run directory into ``out_dir``. Needs the ``report`` extra."""
    try:
        from .. import analyze, report_html, report_md, store
    except ImportError as exc:
        raise WireFailure(f"the report extra is not installed ({exc}); install it with {REPORT_EXTRA_HINT}",
                          exit_code=1, code="report-extra-missing") from exc
    os.makedirs(out_dir, exist_ok=True)
    run = store.RunDir(os.path.dirname(run_path), os.path.basename(run_path), create=False)
    analysis = analyze.build(run)
    written: List[str] = []
    if html:
        written.append(report_html.render(analysis, os.path.join(out_dir, "report.html")))
    if md:
        written.append(report_md.render(analysis, out_dir))
    return [{"path": os.path.basename(p), "bytes": os.path.getsize(p)} for p in written]


def _write_parts(parts: Dict[str, bytes], directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    for name, body in parts.items():
        with open(os.path.join(directory, os.path.basename(name)), "wb") as handle:
            handle.write(body)


@guarded
def _report(args: argparse.Namespace, runtime: CliRuntime) -> None:
    html, md = not args.md_only, not args.html_only
    if args.run_dir:
        if args.session or args.export_only:
            raise _bad("report --run-dir takes no session id and no --export-only")
        run_path = os.path.realpath(args.run_dir)
        if not os.path.isdir(run_path):
            raise _bad(f"run directory does not exist: {args.run_dir}")
        out_dir = args.out or run_path
        label = os.path.basename(run_path)
        files = _render(run_path, out_dir, html, md)
    else:
        if not args.session:
            raise _bad("report needs a session id or --run-dir DIR")
        label = args.session
        parts = ctlwire.export_parts(socket_path(args), args.session)
        if args.export_only:
            _write_parts(parts, args.export_only)
            out_dir = args.export_only
            files = [{"path": n, "bytes": len(b)} for n, b in sorted(parts.items())]
        else:
            out_dir = args.out or f"./cgprofile-report-{args.session}"
            with tempfile.TemporaryDirectory(prefix="cgprofile-export-") as scratch:
                run_path = os.path.join(scratch, args.session)
                _write_parts(parts, run_path)
                files = _render(run_path, out_dir, html, md)
    emit(runtime, {"session": label, "out_dir": out_dir, "files": files},
         [os.path.join(out_dir, f["path"]) for f in files])


# ── prune ───────────────────────────────────────────────────────────────────

@guarded
def _prune(args: argparse.Namespace, runtime: CliRuntime) -> None:
    path = socket_path(args)
    wire: Dict[str, Any] = {"dry_run": True}
    if args.keep is not None:
        wire["keep"] = args.keep
    if args.keep_days is not None:
        wire["keep_days"] = args.keep_days
    ctlwire.need_contract(path, "prune", 2)
    preview = ctlwire.call(path, "gc", wire, 2)
    candidates = list(preview.get("removed", []))
    if runtime.dry_run or not candidates:
        emit(runtime, {"dry_run": True, "removed": candidates, "kept": preview.get("kept")},
             [f"would remove {s}" for s in candidates] or ["nothing to remove"])
        return
    if not runtime.confirm(f"remove {len(candidates)} stored session(s): {', '.join(candidates)}?"):
        raise _bad("prune declined; nothing removed")
    wire["dry_run"] = False
    doc = ctlwire.call(path, "gc", wire, 2)
    removed = list(doc.get("removed", []))
    emit(runtime, {"dry_run": False, "removed": removed, "kept": doc.get("kept")},
         [f"removed {s}" for s in removed] or ["nothing removed"])


# ── run / mark (no daemon) ──────────────────────────────────────────────────

@guarded
def _run(args: argparse.Namespace, runtime: CliRuntime) -> int:
    from .. import access, direct

    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise _bad("nothing to run: put the command after `--`")
    if not access.have_host_cgroup_view():
        raise _bad(DIRECT_MODE_REFUSAL, "direct-mode-unavailable")
    outcome = direct.run_command(command, out_dir=os.path.realpath(args.out_dir), phase=args.phase,
                                 cgpctl_dir=os.path.dirname(os.path.realpath(sys.argv[0])))
    emit(runtime, {"run_dir": outcome.run_dir, "exit_status": outcome.exit_status,
                   "duration_seconds": outcome.duration_seconds},
         [outcome.run_dir, f"command exited {outcome.exit_status}"])
    return outcome.exit_status if args.propagate_exit else 0


@guarded
def _mark(args: argparse.Namespace, runtime: CliRuntime) -> None:
    run_path = os.environ.get("CGPROFILE_RUN_DIR")
    if not run_path:
        raise _bad("CGPROFILE_RUN_DIR is not set: run this inside `cgpctl run`", "no-run-dir")
    if not os.path.isdir(run_path):
        raise _bad(f"run directory does not exist: {run_path}", "no-run-dir")
    try:
        meta = json.loads(args.meta) if args.meta else {}
    except json.JSONDecodeError as exc:
        raise _bad(f"--meta must be valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise _bad("--meta must be a JSON object")
    from .. import phases

    mark = phases.emit_mark(run_path, args.name, kind=args.kind, meta=meta)
    emit(runtime, {"name": mark.name, "kind": mark.kind}, [f"mark {mark.kind}:{mark.name}"])


# ── config ──────────────────────────────────────────────────────────────────

def parse_config_value(key: str, text: str) -> Any:
    """Client-side typing of ``config set`` values; the daemon validates again."""
    if key not in _KEY_TYPES:
        raise _bad(f"{key} is not runtime-tunable; tunable keys: {', '.join(TUNABLE_KEYS)}")
    kind = _KEY_TYPES[key]
    try:
        if kind == "number":
            return float(text)
        if kind == "int":
            return int(text)
        if kind == "onoff":
            if text not in ("on", "off"):
                raise ValueError("expected on or off")
            return text
        stripped = text.strip()
        value = json.loads(stripped) if stripped.startswith("[") else (
            [int(p) for p in stripped.split(",") if p.strip()])
        if not isinstance(value, list) or not all(type(v) is int and v >= 0 for v in value):
            raise ValueError("expected non-negative integers")
        return value
    except ValueError as exc:
        raise _bad(f"{key}: invalid value {text!r} ({exc})") from exc


@guarded
def _config_show(args: argparse.Namespace, runtime: CliRuntime) -> None:
    path = socket_path(args)
    ctlwire.need_contract(path, "config show", 2)
    conf = ctlwire.call(path, "config", None, 2)["config"]
    body = {"file": conf.get("file"), "digest": conf.get("digest"),
            "overrides_file": conf.get("overrides_file"), "keys": conf.get("keys", {})}
    lines = [f"{k}  {v.get('value')}  ({v.get('source')})" for k, v in sorted(body["keys"].items())]
    emit(runtime, body, [f"file {body['file']}  digest {body['digest']}", *lines])


@guarded
def _config_set(args: argparse.Namespace, runtime: CliRuntime) -> None:
    value = parse_config_value(args.key, args.value)
    path = socket_path(args)
    ctlwire.need_contract(path, "config set", 2)
    wire = {"key": args.key, "value": value, "persist": args.persist, "dry_run": runtime.dry_run}
    doc = ctlwire.call(path, "config-set", wire, 2)["config-set"]
    emit(runtime, {"key": doc.get("key"), "old": doc.get("old"), "new": doc.get("new"),
                   "persisted": doc.get("persisted"), "audit_id": doc.get("audit_id")},
         [f"{doc.get('key')}: {doc.get('old')} -> {doc.get('new')}"
          + (" (dry run)" if runtime.dry_run else "")])


@guarded
def _config_reset(args: argparse.Namespace, runtime: CliRuntime) -> None:
    if bool(args.key) == bool(args.all):
        raise _bad("config reset needs exactly one of KEY or --all")
    path = socket_path(args)
    ctlwire.need_contract(path, "config reset", 2)
    wire = {"all": True} if args.all else {"key": args.key}
    doc = ctlwire.call(path, "config-reset", wire, 2)["config-reset"]
    emit(runtime, {"reset": doc.get("reset", []), "values": doc.get("values", {})},
         [f"reset {k}" for k in doc.get("reset", [])] or ["no overrides were set"])


# ── doctor ──────────────────────────────────────────────────────────────────

class _Probe:
    """One doctor run's shared daemon facts (a handshake is done once)."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.path, self.source = ctlwire.resolve_socket(getattr(args, "socket", None))
        self.error: Optional[WireFailure] = None
        self.version: Optional[Dict[str, Any]] = None
        self.contracts: List[int] = []
        try:
            self.version = ctlwire.call(self.path, "version", None, 1)
            self.contracts = ctlwire.daemon_contracts(self.version)
        except WireFailure as exc:
            self.error = exc

    def config(self) -> Optional[Dict[str, Any]]:
        if self.error or 2 not in self.contracts:
            return None
        return ctlwire.call(self.path, "config", None, 2).get("config")


_PROBES: Dict[int, _Probe] = {}


def _probe(args: argparse.Namespace) -> _Probe:
    # one probe per Namespace object, i.e. per doctor invocation
    if id(args) not in _PROBES:
        _PROBES.clear()
        _PROBES[id(args)] = _Probe(args)
    return _PROBES[id(args)]


def _skip_no_daemon(probe: _Probe) -> CheckResult:
    return CheckResult("skip", "no daemon access", remedy=_no_daemon_remedy(probe))


def _no_daemon_remedy(probe: _Probe) -> Optional[str]:
    return CAUSE_REMEDY.get(probe.error.cause or "") if probe.error else None


def _check_socket(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    probe = _probe(args)
    if probe.error is None:
        return CheckResult("ok", f"{probe.path} accepts connections (source: {probe.source})",
                           details={"path": probe.path, "source": probe.source})
    return CheckResult("fail", f"{probe.error.message} (cause: {probe.error.cause or probe.error.code})",
                       remedy=_no_daemon_remedy(probe), details={"path": probe.path, "source": probe.source,
                                                                 "cause": probe.error.cause})


def _check_caller_group(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    probe = _probe(args)
    group = socket_group(probe.path)
    if group["gid"] is None:
        return CheckResult("skip", "the socket is absent", details=group)
    if caller_in_group(group["gid"]):
        return CheckResult("ok", f"caller is in group {group['group'] or group['gid']}", details=group)
    return CheckResult("fail", f"caller is not in group {group['group']} (gid {group['gid']})",
                       remedy=f"add yourself to {group['group'] or 'the socket group'} "
                              f"(gid {group['gid']}) and re-login, or ask root to add your uid to allow_uids",
                       details=group)


def _check_daemon_version(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    probe = _probe(args)
    if probe.error or probe.version is None:
        return _skip_no_daemon(probe)
    daemon = ctlwire.daemon_version(probe.version)
    skew = _skew(installed_version(), daemon)
    if skew == "equal":
        return CheckResult("ok", f"daemon and client are {daemon}")
    return CheckResult("warn", f"client {installed_version()} is {skew} than daemon {daemon}",
                       remedy="install matching cgprofile versions on client and daemon host")


def _check_contracts(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    probe = _probe(args)
    if probe.error or probe.version is None:
        return _skip_no_daemon(probe)
    shared = sorted(set(probe.contracts) & set(CLIENT_CONTRACTS))
    observer = probe.version.get("resource_observer")
    observer_contracts = observer.get("contracts") if isinstance(observer, dict) else None
    details = {"daemon": probe.contracts, "client": list(CLIENT_CONTRACTS), "observer": observer_contracts}
    if not shared:
        return CheckResult("fail", f"no shared ctl contract (daemon {probe.contracts})",
                           remedy=CAUSE_REMEDY["contract-unsupported"], details=details)
    if isinstance(observer_contracts, list) and not {2, 3} <= set(observer_contracts):
        return CheckResult("warn", f"observer contracts {observer_contracts} lack 2 and 3",
                           remedy="upgrade the daemon", details=details)
    return CheckResult("ok", f"ctl contracts {shared} shared", details=details)


def _check_observer_dir(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    try:
        info = os.stat(OBSERVER_DIR)
    except OSError:
        return CheckResult("skip", f"{OBSERVER_DIR} is not visible here")
    mode = info.st_mode & 0o7777
    ctl_gid = socket_group(_probe(args).path)["gid"]
    problems = []
    if mode != 0o751:
        problems.append(f"mode is {mode:04o}, expected 0751")
    if info.st_gid == ctl_gid:
        problems.append("group is the ctl group")
    if problems:
        return CheckResult("warn", "; ".join(problems), remedy="re-run host-setup to restore the observer directory")
    return CheckResult("ok", f"{OBSERVER_DIR} mode 0751")


def _check_slice(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    base = "/sys/fs/cgroup/cgprofile.slice"
    if not os.path.isdir(base):
        return CheckResult("skip", "cgprofile.slice is not visible here")
    bounds = {}
    for name in ("memory.max", "memory.high"):
        try:
            with open(os.path.join(base, name), encoding="utf-8") as handle:
                bounds[name] = handle.read().strip()
        except OSError:
            bounds[name] = None
    if all(v in (None, "max") for v in bounds.values()):
        return CheckResult("warn", "cgprofile.slice is unbounded", remedy="re-run host-setup", details=bounds)
    return CheckResult("ok", "cgprofile.slice is bounded", details=bounds)


def _check_damon(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    probe = _probe(args)
    if probe.error or probe.version is None:
        return _skip_no_daemon(probe)
    daemon = probe.version.get("daemon") if isinstance(probe.version.get("daemon"), dict) else {}
    state = daemon.get("damon")
    if state == "available":
        return CheckResult("ok", "DAMON is available")
    return CheckResult("warn", f"DAMON is {state}", remedy="DAMON needs a kernel with CONFIG_DAMON_SYSFS")


def _check_sessions_dir(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    probe = _probe(args)
    if probe.error:
        return _skip_no_daemon(probe)
    return CheckResult("ok", "reported by the daemon's status (not tested from the client)")


def _check_overrides(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    probe = _probe(args)
    if probe.error:
        return _skip_no_daemon(probe)
    conf = probe.config()
    if conf is None:
        return CheckResult("skip", "the daemon has no runtime overrides (contract 1)")
    keys = conf.get("keys") or {}
    shadowing = sorted(k for k, v in keys.items()
                       if isinstance(v, dict) and v.get("source") == "override"
                       and isinstance(v.get("shadows"), dict) and v["shadows"].get("source") == "file")
    ignored = conf.get("overrides_ignored") or 0
    if shadowing or ignored:
        parts = [f"override shadows the file value of {k}" for k in shadowing]
        if ignored:
            parts.append(f"{ignored} stored override entries were ignored")
        return CheckResult("warn", "; ".join(parts),
                           remedy="; ".join(f"cgpctl config reset {k}" for k in shadowing) or "inspect the overrides file",
                           details={"shadowing": shadowing, "ignored": ignored})
    return CheckResult("ok", "no runtime override shadows the config file")


def _check_report_extra(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    try:
        import matplotlib  # noqa: F401
        import pandas  # noqa: F401
        import plotly  # noqa: F401
        import ruptures  # noqa: F401
        import scipy  # noqa: F401
    except ImportError as exc:
        return CheckResult("warn", f"the report extra is incomplete ({exc.name})", remedy=REPORT_EXTRA_HINT)
    return CheckResult("ok", "the report extra imports")


DOCTOR_CHECKS = (
    DoctorCheck("socket", "the socket exists and accepts a connection", _check_socket),
    DoctorCheck("caller-group", "the caller is in the socket's group", _check_caller_group),
    DoctorCheck("daemon-version", "daemon and client versions agree", _check_daemon_version),
    DoctorCheck("contracts", "ctl and observer contract sets overlap", _check_contracts),
    DoctorCheck("observer-dir", "the observer directory is 0751 with its own group", _check_observer_dir),
    DoctorCheck("slice", "cgprofile.slice exists and is bounded", _check_slice),
    DoctorCheck("damon", "the daemon reports DAMON available", _check_damon),
    DoctorCheck("sessions-dir", "the daemon's sessions directory is usable", _check_sessions_dir),
    DoctorCheck("overrides", "no runtime override shadows the config file", _check_overrides),
    DoctorCheck("report-extra", "the report extra imports", _check_report_extra),
)


# ── registry ────────────────────────────────────────────────────────────────

def _opt(flags: str, description: str, *, group: str = "OPTIONS", metavar: Optional[str] = None,
         mutually_exclusive_group: Optional[str] = None, **parser_kwargs: Any) -> OptionSpec:
    return OptionSpec(tuple(flags.split("/")), description, group=group, metavar=metavar,
                      parser_kwargs=parser_kwargs, mutually_exclusive_group=mutually_exclusive_group)


def _session_arg() -> ArgumentSpec:
    return ArgumentSpec("session", "the session id (s-...)", metavar="ID")


def build_cli(cli_identity: Optional[CliIdentity] = None) -> RegisteredCli:
    """Build the registered CLI; import-safe (nothing runs until ``.run()``)."""
    registry = CliRegistry(
        cli_identity or identity(), prog="cgpctl",
        description="Control the cgprofile host daemon and profile commands locally.",
        getting_started=("cgpctl status", "cgpctl sessions", "cgpctl doctor"),
        global_options=(_opt("--socket", "ctl socket path (default: $CGPCTL_SOCKET, else /run/cgprofile/ctl.sock)",
                             group="CONNECTION", metavar="PATH"),),
        unexpected_exceptions="report",
    )
    read = VerbGroup.EXPLORATION.value
    modify = VerbGroup.MODIFICATION.value
    registry.register(VerbSpec("status", description="daemon and client versions, contracts, slots and config digest",
                               group=read, examples=("cgpctl status", "cgpctl status --json"),
                               include_progress=False, handler=_status))
    registry.register(VerbSpec("host", description="host facts: load, memory, pressure, observed slices, DAMON",
                               group=read, examples=("cgpctl host --json",), include_progress=False, handler=_host))
    registry.register(VerbSpec(
        "sessions", description="one row per session, live and stored", group=read,
        examples=("cgpctl sessions", "cgpctl sessions --state live --mine"), include_progress=False,
        options=(_opt("--state", "which sessions to list", group="FILTER", metavar="STATE",
                      choices=("live", "done", "all"), default="all"),
                 _opt("--mine", "only sessions owned by the calling uid", group="FILTER", action="store_true")),
        handler=_sessions))
    registry.register(VerbSpec("show", description="live counters of a live session, the summary of a finished one",
                               group=read, examples=("cgpctl show s-20261104T101500Z-9f01",),
                               include_progress=False, arguments=(_session_arg(),), handler=_show))
    registry.register(VerbSpec(
        "start", description="start a daemon session on a container, cgroup or slice", group=modify,
        mutating=True, dry_run=True, include_progress=False,
        dry_run_help="resolve the target and print the request without starting a session",
        examples=("cgpctl start --target container:web", "cgpctl start --target cgroup:/dev.slice --dry-run"),
        options=(
            _opt("--target", "containerid:<64hex>, container:NAME, cgroup:/abs/path or slice:NAME.slice",
                 group="TARGET", metavar="TARGET", required=True),
            _opt("--scope", "scope (default: container, or observe for cgroup:/slice: targets)",
                 group="TARGET", metavar="SCOPE", choices=("container", "container-shared")),
            _opt("--token", "caller token echoed in the session", group="TARGET", metavar="TOKEN"),
            _opt("--interval", "sampling interval in seconds", group="SAMPLING", metavar="SECONDS", type=float),
            _opt("--damon", "force DAMON on or off for this session", group="SAMPLING", metavar="on|off",
                 choices=("on", "off")),
            _opt("--place", "place the target under the gates slice with the caps below", group="PLACEMENT",
                 action="store_true"),
            _opt("--memory-high", "memory.high of the placement leaf, bytes", group="PLACEMENT", metavar="BYTES", type=int),
            _opt("--memory-max", "memory.max of the placement leaf, bytes", group="PLACEMENT", metavar="BYTES", type=int),
            _opt("--cpu-weight", "cpu.weight of the placement leaf, 1..10000", group="PLACEMENT", metavar="WEIGHT", type=int),
            _opt("--idle-bound", "auto or seconds without progress before a stall verdict", group="STALL", metavar="auto|SECONDS"),
            _opt("--ceiling", "auto or seconds of total runtime before a stall verdict", group="STALL", metavar="auto|SECONDS"),
            _opt("--on-stall", "what an actionable verdict does (default report)", group="STALL", metavar="ACTION",
                 choices=("report", "kill")),
            _opt("--progress-stream", "the lane's progress NDJSON path as the lane sees it", group="STALL", metavar="PATH"),
            _opt("--meta", "JSON object merged over the client's meta", group="TARGET", metavar="JSON"),
        ), handler=_start))
    registry.register(VerbSpec("stop", description="stop and finalize a session; prints the summary", group=modify,
                               mutating=True, confirmation_required=False, include_progress=False,
                               examples=("cgpctl stop s-20261104T101500Z-9f01",),
                               arguments=(_session_arg(),), handler=_stop))
    registry.register(VerbSpec(
        "watch", description="stream readings until the session ends", group=read, include_json=True,
        include_progress=False, examples=("cgpctl watch s-20261104T101500Z-9f01 --json",),
        arguments=(_session_arg(),),
        options=(_opt("--watch-interval", "seconds between readings (daemon clamps to 5..300)", group="STREAM",
                      metavar="SECONDS", type=float, default=30),),
        handler=_watch))
    registry.register(VerbSpec(
        "report", description="export a stored session and render its report locally", group=read,
        expensive=True, include_progress=False,
        examples=("cgpctl report s-20261104T101500Z-9f01", "cgpctl report --run-dir ./runs/run-1 --md-only"),
        arguments=(ArgumentSpec("session", "the session id (omit with --run-dir)", metavar="ID",
                                parser_kwargs={"nargs": "?"}),),
        options=(
            _opt("--out", "output directory (default ./cgprofile-report-<session>/)", group="OUTPUT", metavar="DIR"),
            _opt("--run-dir", "render a standalone run result; needs no daemon", group="SOURCE", metavar="DIR"),
            _opt("--export-only", "write the raw exported parts here and render nothing", group="SOURCE", metavar="DIR"),
            _opt("--html-only", "write only report.html", group="OUTPUT", action="store_true",
                 mutually_exclusive_group="format"),
            _opt("--md-only", "write only report.md", group="OUTPUT", action="store_true",
                 mutually_exclusive_group="format"),
        ), handler=_report))
    registry.register(VerbSpec(
        "prune", description="apply stored-session retention now", group=modify, mutating=True, dry_run=True,
        confirmation_required=True, include_progress=False,
        dry_run_help="list the sessions that would be removed",
        examples=("cgpctl prune --dry-run", "cgpctl prune --keep 50 --yes"),
        options=(_opt("--keep", "keep at most this many sessions for this call", group="RETENTION", metavar="N", type=int),
                 _opt("--keep-days", "keep sessions at most this many days for this call", group="RETENTION",
                      metavar="DAYS", type=int)),
        handler=_prune))
    registry.register(VerbSpec(
        "run", description="profile one command locally (direct mode, needs cgroupfs)", group=modify,
        mutating=True, expensive=True, include_progress=False, confirmation_required=False,
        examples=("cgpctl run --phase build -- make",),
        arguments=(ArgumentSpec("command", "the command to profile, after --", metavar="CMD",
                                parser_kwargs={"nargs": argparse.REMAINDER}),),
        options=(_opt("--phase", "phase name of the whole command (default: its basename)", group="RUN", metavar="NAME"),
                 _opt("--out-dir", "directory receiving the run directory", group="RUN", metavar="DIR",
                      default="./cgprofile-runs"),
                 _opt("--propagate-exit", "exit with the command's own status", group="RUN", action="store_true")),
        handler=_run))
    registry.register(VerbSpec(
        "mark", description="append a phase mark to the current run ($CGPROFILE_RUN_DIR)", group=modify,
        mutating=True, confirmation_required=False, include_progress=False,
        examples=("cgpctl mark restoring-database",),
        arguments=(ArgumentSpec("name", "the mark name", metavar="NAME"),),
        options=(_opt("--kind", "phase or event", group="MARK", metavar="KIND", choices=("phase", "event"), default="phase"),
                 _opt("--meta", "JSON object of extra fields", group="MARK", metavar="JSON")),
        handler=_mark))
    config = CliRegistry(
        registry.identity, prog="cgpctl config", description="Show and change the daemon's settings.",
        global_options=registry.global_options, unexpected_exceptions="report")
    config.register(VerbSpec("show", description="effective settings with their source and the config digest",
                             group=read, include_progress=False, handler=_config_show,
                             examples=("cgpctl config show --json",)))
    config.register(VerbSpec(
        "set", description="change one runtime-tunable setting live (uid 0 only)", group=modify, mutating=True,
        dry_run=True, confirmation_required=False, include_json=True, include_progress=False,
        dry_run_help="validate on the client and the daemon, change nothing",
        examples=("cgpctl config set interval 2", "cgpctl config set allow_uids 1003,1004 --persist"),
        arguments=(ArgumentSpec("key", "a runtime-tunable key: " + ", ".join(TUNABLE_KEYS), metavar="KEY"),
                   ArgumentSpec("value", "the new value (number, integer, on|off, or a comma list)", metavar="VALUE")),
        options=(_opt("--persist", "also write the overrides file so it survives restarts", group="SCOPE",
                      action="store_true"),),
        handler=_config_set))
    config.register(VerbSpec(
        "reset", description="drop one runtime override, or all of them (uid 0 only)", group=modify, mutating=True,
        confirmation_required=False, include_progress=False, examples=("cgpctl config reset interval", "cgpctl config reset --all"),
        arguments=(ArgumentSpec("key", "the key whose override to drop", metavar="KEY", parser_kwargs={"nargs": "?"}),),
        options=(_opt("--all", "drop every override", group="SCOPE", action="store_true"),),
        handler=_config_reset))
    registry.register(VerbSpec("config", None, "show and change the daemon's settings", group=VerbGroup.MIXED.value,
                               delegate=config.build()))
    register_doctor(registry, DOCTOR_CHECKS,
                    description="diagnose the install, the socket, the caller and the daemon")
    register_skills_verbs(registry, package="cgprofile")
    return registry.build()


def main(argv: Optional[Sequence[str]] = None) -> int:
    return build_cli().run(argv=None if argv is None else list(argv))


if __name__ == "__main__":
    raise SystemExit(main())
