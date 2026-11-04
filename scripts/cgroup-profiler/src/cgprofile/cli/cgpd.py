"""``cgpd``: the daemon console script (CGPCTL-SPEC.md section 3).

A cli-extended service entrypoint. ``--help``, ``--version`` and malformed
arguments never start the service, create state or touch the socket; only
``run`` does, after the seven preflight checks of section 3.1. The heavy
modules (``serve``, ``access``) are imported inside ``run`` only, so
``unit``, ``config check`` and ``reload`` work for any user without a cgroup
view.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import struct
import sys
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

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
)

from .. import ctlwire, daemon_config, unitfiles
from ..version import installed_version

SCHEMA = 1
#: Where runtime overrides persist; a module constant so tests can repoint it.
OVERRIDES_PATH = daemon_config.DEFAULT_OVERRIDES_PATH
PROBE_TIMEOUT = 2.0


def identity() -> CliIdentity:
    return CliIdentity(name="CGPROFILE", version=installed_version(),
                       long_name="cgroup resource profiler daemon", command="cgpd")


def _opt(flags: str, description: str, *, group: str = "OPTIONS", metavar: Optional[str] = None,
         **parser_kwargs: Any) -> OptionSpec:
    return OptionSpec(tuple(flags.split("/")), description, group=group, metavar=metavar,
                      parser_kwargs=parser_kwargs)


def _refuse(message: str, hint: Optional[str] = None, exit_code: int = 2) -> CliFailure:
    return CliFailure(message, exit_code=exit_code, hint=hint)


def log(event: str, **fields: Any) -> None:
    """One ``key=value`` line on stderr, the journal's format (section 3.1)."""
    parts = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"cgpd: event={event} {parts}".rstrip(), file=sys.stderr, flush=True)


def config_location(args: argparse.Namespace) -> Tuple[str, bool]:
    """``(path, explicit)``: ``--config`` is explicit, the default is not."""
    path = getattr(args, "config", None)
    return (path, True) if path else (daemon_config.DEFAULT_CONFIG_PATH, False)


# ── preflight ───────────────────────────────────────────────────────────────

def probe_daemon(path: str) -> Optional[Tuple[str, Optional[int]]]:
    """``(version, pid)`` of a daemon answering ``version`` on ``path``, else None.

    The pid is the listener's, read from SO_PEERCRED on our own connection."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(PROBE_TIMEOUT)
        client.connect(path)
        client.sendall(ctlwire.request_line("version", None, 1))
        doc = ctlwire.parse_response(ctlwire._read_line(client))
        try:
            pid: Optional[int] = struct.unpack(
                "3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]
        except (OSError, struct.error):  # pragma: no cover - SO_PEERCRED is always readable on Linux
            pid = None
    except (OSError, ctlwire.WireFailure):
        return None
    finally:
        client.close()
    if not doc.get("ok"):
        return None
    return ctlwire.daemon_version(doc) or "unknown", pid


def preflight(config_path: str, explicit: bool, environ: Mapping[str, str]) -> daemon_config.Settings:
    """The seven checks of CGPCTL-SPEC 3.1, in order. Returns the loaded
    settings; every failure is a one-line exit-2 refusal naming the fix."""
    from .. import access

    # 1. effective uid 0
    if os.geteuid() != 0:
        raise _refuse(f"cgpd run needs root (effective uid is {os.geteuid()})",
                      "start it with: systemctl start cgpd")
    # 2. config and stale environment
    stale = daemon_config.stale_env_problems(environ, config_path)
    if stale:
        raise _refuse(stale[0], "remove the variable from the unit's Environment= or the shell")
    settings = daemon_config.Settings(
        config_path, explicit=explicit, overrides_path=OVERRIDES_PATH,
        log=lambda message: log("config", message=json.dumps(message)))
    try:
        settings.load()
    except daemon_config.ConfigError as exc:
        raise _refuse(str(exc), "check it with: cgpd config check")
    proc_root = settings.get("proc_root")
    # 3. pid-namespace identity
    own = access.local_namespace_inode("pid")
    selected = access.namespace_inode(1, "pid", proc_root)
    if own is None or selected is None or own != selected:
        raise _refuse(
            f"the pid namespace of {proc_root}/1 is not this process's",
            "run cgpd natively under systemd, or point proc_root at this namespace's /proc")
    # 4. the daemon's slice is bounded
    from .. import serve
    problem = serve.daemon_slice_problem(access.CGROUP_ROOT, proc_root)
    if problem is not None:
        raise _refuse(
            f"{serve.DAEMON_SLICE_NAME} does not provably contain this daemon: {problem}",
            "install the slice with: cgpd unit print, then systemctl daemon-reload")
    # 5. the sessions directory is creatable and writable
    sessions = settings.get("sessions_dir")
    try:
        os.makedirs(sessions, exist_ok=True)
    except OSError as exc:
        raise _refuse(f"cannot create sessions_dir {sessions}: {exc.strerror}",
                      "create it, or set sessions_dir in the config file")
    if not os.access(sessions, os.W_OK):
        raise _refuse(f"sessions_dir {sessions} is not writable",
                      "fix its ownership, or set sessions_dir in the config file")
    # 6. no live daemon on ctl_socket
    ctl_socket = settings.get("ctl_socket")
    other = probe_daemon(ctl_socket)
    if other is not None:
        version, pid = other
        raise _refuse(f"another daemon already serves {ctl_socket} (version {version}, pid {pid})",
                      "stop it first: systemctl stop cgpd")
    # 7. the socket's directory exists
    socket_dir = os.path.dirname(ctl_socket)
    if not os.path.isdir(socket_dir):
        raise _refuse(f"the directory of ctl_socket does not exist: {socket_dir}",
                      "the unit's RuntimeDirectory=cgprofile creates it; start through systemd")
    return settings


# ── verbs ───────────────────────────────────────────────────────────────────

def _export_fixed_keys(settings: daemon_config.Settings) -> None:
    """The fixed path keys reach the modules that still read them from the
    process environment at call time (damon lock, busctl, proc root). This is
    internal plumbing from the config file, not a configuration input: a
    user-set variable of the same name was refused by the preflight."""
    os.environ["CGPROFILE_PROC_ROOT"] = settings.get("proc_root")
    os.environ["CGPROFILE_DAMON_LOCK_PATH"] = settings.get("damon_lock")
    os.environ["CGPROFILE_BUSCTL"] = settings.get("busctl")


def _run(args: argparse.Namespace, runtime: CliRuntime) -> int:
    path, explicit = config_location(args)
    settings = preflight(path, explicit, os.environ)
    _export_fixed_keys(settings)
    from .. import serve

    server = serve.SessionServer(
        settings=settings, sessions_dir=settings.get("sessions_dir"),
        socket_path=settings.get("ctl_socket"),
        observer_socket_path=settings.get("observer_socket"),
        proc_root=settings.get("proc_root"),
    )
    log("start", version=installed_version(), config=path, digest=settings.digest,
        socket=settings.get("ctl_socket"), sessions=settings.get("sessions_dir"))
    try:
        return server.serve_forever() or 0
    except serve.ControlSocketError as exc:
        raise _refuse(f"cgpd refuses to start: {exc.strerror}") from None


def _config_check(args: argparse.Namespace, runtime: CliRuntime) -> int:
    path = args.file
    explicit = True
    if not path:
        path, explicit = config_location(args)
    try:
        values, digest = daemon_config.read_config_file(path, explicit=explicit)
        for key, value in values.items():
            daemon_config.validate(key, value)
    except daemon_config.ConfigError as exc:
        raise _refuse(str(exc)) from None
    note = "defaults (no file)" if not values and not os.path.exists(path) else f"{len(values)} key(s)"
    body = {"schema": SCHEMA, "ok": True, "file": path, "digest": digest, "keys": sorted(values)}
    runtime.output.primary(body if runtime.json_mode else f"config ok: {path} ({note}, digest {digest})")
    return 0


def _reload(args: argparse.Namespace, runtime: CliRuntime) -> int:
    if os.geteuid() != 0:
        raise _refuse(f"cgpd reload needs root (effective uid is {os.geteuid()})",
                      "use: sudo cgpd reload, or systemctl reload cgpd")
    path, explicit = config_location(args)
    try:
        values, _digest = daemon_config.read_config_file(path, explicit=explicit)
        ctl_socket = values.get("ctl_socket", daemon_config.KEYS["ctl_socket"].default)
    except daemon_config.ConfigError as exc:
        raise _refuse(str(exc), "check it with: cgpd config check") from None
    daemon = probe_daemon(ctl_socket)
    if daemon is None or daemon[1] is None:
        raise _refuse(f"no daemon answers on {ctl_socket}", "start it: systemctl start cgpd", exit_code=3)
    version, pid = daemon
    if args.reset_overrides:
        try:
            ctlwire.need_contract(ctl_socket, "config-reset", 2)
            ctlwire.call(ctl_socket, "config-reset", {"all": True}, 2)
        except ctlwire.WireFailure as exc:
            raise _refuse(exc.message, exit_code=exc.exit_code) from None
    os.kill(pid, signal.SIGHUP)
    body = {"schema": SCHEMA, "ok": True, "pid": pid, "version": version,
            "reset_overrides": bool(args.reset_overrides)}
    runtime.output.primary(body if runtime.json_mode else f"reload signalled to pid {pid} (cgpd {version})")
    return 0


# ── registry ────────────────────────────────────────────────────────────────

def build_cli(cli_identity: Optional[CliIdentity] = None) -> RegisteredCli:
    """Build the registered CLI; import-safe (nothing runs until ``.run()``)."""
    registry = CliRegistry(
        cli_identity or identity(), prog="cgpd",
        description="The cgprofile host daemon: run it, render its units, check its config.",
        getting_started=("cgpd config check", "cgpd unit print", "cgpd run"),
        global_options=(_opt("--config", "config file (default /etc/cgprofile/cgpd.toml; "
                                         "a missing explicit file is an error)",
                             group="CONFIG", metavar="PATH"),),
        unexpected_exceptions="report",
    )
    modify = VerbGroup.MODIFICATION.value
    read = VerbGroup.EXPLORATION.value
    registry.register(VerbSpec(
        "run", description="load the config, run the preflight, serve until SIGTERM (root only)",
        group=modify, mutating=True, include_json=False, include_progress=False,
        examples=("cgpd run", "cgpd --config /etc/cgprofile/cgpd.toml run"), handler=_run))
    _register_unit(registry)
    config = CliRegistry(
        registry.identity, prog="cgpd config", description="Validate the daemon's config file.",
        global_options=registry.global_options, unexpected_exceptions="report")
    config.register(VerbSpec(
        "check", description="validate the config file without starting anything", group=read,
        include_progress=False, examples=("cgpd config check", "cgpd config check --file ./cgpd.toml"),
        options=(_opt("--file", "validate this file instead of --config", group="CONFIG", metavar="PATH"),),
        handler=_config_check))
    registry.register(VerbSpec("config", None, "validate the daemon's config file",
                               group=VerbGroup.MIXED.value, delegate=config.build()))
    registry.register(VerbSpec(
        "reload", description="signal the running daemon to re-read its config (root only)",
        group=modify, mutating=True, include_progress=False,
        examples=("cgpd reload", "cgpd reload --reset-overrides"),
        options=(_opt("--reset-overrides", "drop every runtime override first (config-reset all)",
                      group="RELOAD", action="store_true"),),
        handler=_reload))
    return registry.build()


def _rendered(args: argparse.Namespace) -> Dict[str, str]:
    try:
        return unitfiles.render(args.install_root, args.wheel_venv)
    except unitfiles.UnitError as exc:
        raise _refuse(str(exc)) from None


def _unit_print(args: argparse.Namespace, runtime: CliRuntime) -> int:
    units = _rendered(args)
    if args.unit:
        units = {args.unit: units[args.unit]}
    if runtime.json_mode:
        runtime.output.primary({"schema": SCHEMA, "ok": True, "units": units})
    elif args.unit:
        sys.stdout.write(units[args.unit])
    else:
        for name, text in units.items():
            sys.stdout.write(f"# ---- {name} ----\n{text}")
    return 0


def _unit_check(args: argparse.Namespace, runtime: CliRuntime) -> int:
    rendered = _rendered(args)
    try:
        installed = unitfiles.installed_map(args.installed) if args.installed else {
            name: os.path.join(unitfiles.DEFAULT_UNIT_DIR, name) for name in unitfiles.TEMPLATES}
    except unitfiles.UnitError as exc:
        raise _refuse(str(exc)) from None
    problems = unitfiles.check(installed, rendered)
    if problems:
        raise _refuse(f"unit drift: {problems[0]}" + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""),
                      "reinstall the units: cgpd unit print --unit NAME > /etc/systemd/system/NAME")
    body = {"schema": SCHEMA, "ok": True, "checked": installed}
    runtime.output.primary(body if runtime.json_mode else
                           "units match: " + ", ".join(f"{n} ({p})" for n, p in installed.items()))
    return 0


def _unit_options() -> Tuple[OptionSpec, ...]:
    return (
        _opt("--install-root", "install root of the versioned venvs (default /opt/cgprofile)",
             group="RENDER", metavar="PATH", default=unitfiles.DEFAULT_INSTALL_ROOT),
        _opt("--wheel-venv", "the venv holding bin/cgpd (default <install-root>/current/venv)",
             group="RENDER", metavar="PATH"),
    )


def _register_unit(registry: CliRegistry) -> None:
    unit = CliRegistry(
        registry.identity, prog="cgpd unit", description="Render and verify the systemd units.",
        global_options=registry.global_options, unexpected_exceptions="report")
    read = VerbGroup.EXPLORATION.value
    unit.register(VerbSpec(
        "print", description="print cgprofile.slice and cgpd.service rendered from the wheel's templates",
        group=read, include_progress=False,
        examples=("cgpd unit print", "cgpd unit print --unit cgpd.service --wheel-venv /opt/cgprofile/2.0.0/venv"),
        options=(*_unit_options(),
                 _opt("--unit", "print only this unit, verbatim", group="RENDER", metavar="NAME",
                      choices=tuple(unitfiles.TEMPLATES))),
        handler=_unit_print))
    unit.register(VerbSpec(
        "check", description="compare installed unit files with the rendered output (exit 2 on drift)",
        group=read, include_progress=False,
        examples=("cgpd unit check", "cgpd unit check --installed /etc/systemd/system/cgpd.service"),
        options=(*_unit_options(),
                 _opt("--installed", "installed unit file(s) to compare (default /etc/systemd/system/<both>)",
                      group="RENDER", metavar="PATH", nargs="+")),
        handler=_unit_check))
    registry.register(VerbSpec("unit", None, "render and verify the systemd units",
                               group=VerbGroup.MIXED.value, delegate=unit.build()))


def main(argv: Optional[Sequence[str]] = None) -> int:
    return build_cli().run(argv=None if argv is None else list(argv))


if __name__ == "__main__":
    raise SystemExit(main())
