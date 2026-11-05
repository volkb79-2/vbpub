"""The ``toposd`` service entry point.

Keep this module light: the daemon implementation is imported only when
``toposd run`` is selected, so config and unit commands work without loading
the collector, UI, or daemon stack.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from cli_extended import (
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
)

from .activation import ActivationError, adopt_listeners
from .config import DEFAULT_CONFIG_PATH, ConfigError, ServiceConfig, load_config
from .units import render_units


def identity() -> CliIdentity:
    from topos import __version__

    return CliIdentity(name="TOPOS", version=__version__, long_name="topos host daemon", command="toposd")


def _opt(flags: str, description: str, *, group: str = "OPTIONS", metavar: str | None = None, **kwargs: Any) -> OptionSpec:
    return OptionSpec(tuple(flags.split("/")), description, group=group, metavar=metavar, parser_kwargs=kwargs)


def _refuse(message: str, exit_code: int = 2) -> CliFailure:
    return CliFailure(message, exit_code=exit_code)


def _config_or_refuse(path: str | Path | None) -> ServiceConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        raise _refuse(str(exc)) from None


def _run(args: argparse.Namespace, _runtime: CliRuntime) -> int:
    if args.socket is not None and not args.self_bind:
        raise _refuse("--socket is accepted only with --self-bind")

    cfg = _config_or_refuse(args.config)
    try:
        listeners = adopt_listeners({"observe"})
    except ActivationError as exc:
        raise _refuse(str(exc)) from None

    listener = None
    if listeners is None:
        if not args.self_bind:
            raise _refuse(
                "toposd run needs the socket of topos-observe.socket (systemd socket activation); "
                "for tests and development pass --self-bind"
            )
    else:
        observe = listeners.get("observe", [])
        if not observe:
            raise _refuse("no fd named observe passed; check topos-observe.socket")
        if len(observe) > 1:
            for extra in observe:
                extra.close()
            raise _refuse(
                "more than one fd named observe passed; topos-observe.socket must have exactly one ListenStream"
            )
        listener = observe[0]

    from topos.daemon.run import run_daemon

    socket_path = Path(args.socket) if args.socket is not None else cfg.sockets.observe.path
    try:
        return run_daemon(
            socket_path,
            listener=listener,
            socket_mode=cfg.sockets.observe.mode_bits,
            topos_config=Path(args.topos_config) if args.topos_config else None,
            cgroup_root=Path(args.cgroup_root) if args.cgroup_root else None,
            history_size=args.history_size,
            bpf_root=Path(args.bpf_root) if args.bpf_root else None,
            bpf_interval=args.bpf_interval,
            bpf_state_dir=Path(args.bpf_state_dir) if args.bpf_state_dir else None,
        )
    except OSError as exc:
        if listener is not None:
            listener.close()
        raise _refuse(f"toposd run: {exc}") from None


def _config_check(args: argparse.Namespace, runtime: CliRuntime) -> int:
    cfg = _config_or_refuse(args.config)
    if runtime.json_mode:
        runtime.output.primary(
            {
                "ok": True,
                "config": str(args.config or DEFAULT_CONFIG_PATH),
                "state_dir": str(cfg.state_dir),
                "sockets": {
                    "observe": {
                        "path": str(cfg.sockets.observe.path),
                        "group": cfg.sockets.observe.group,
                        "mode": cfg.sockets.observe.mode,
                    }
                },
            }
        )
    else:
        runtime.output.primary("ok")
    return 0


def _unit_print(args: argparse.Namespace, runtime: CliRuntime) -> int:
    cfg = _config_or_refuse(args.config)
    units = render_units(cfg)
    if args.unit:
        selected = {args.unit: units[args.unit]}
    else:
        selected = units
    if runtime.json_mode:
        runtime.output.primary({"ok": True, "units": selected})
        return 0
    for name, content in selected.items():
        if args.unit is None:
            sys.stdout.write(f"# --- {name}\n")
        sys.stdout.write(content)
    return 0


def _unit_destinations(root: Path) -> dict[str, Path]:
    return {
        "topos-observe.socket": root / "etc/systemd/system/topos-observe.socket",
        "topos.service": root / "etc/systemd/system/topos.service",
        "topos.slice": root / "etc/systemd/system/topos.slice",
        "topos.sysusers": root / "etc/sysusers.d/topos.conf",
    }


def _unit_check(args: argparse.Namespace, runtime: CliRuntime) -> int:
    cfg = _config_or_refuse(args.config)
    expected = render_units(cfg)
    destinations = _unit_destinations(Path(args.root))
    problems: list[tuple[str, Path, str | None]] = []
    for name, path in destinations.items():
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            problems.append((name, path, None))
        except OSError as exc:
            raise _refuse(f"cannot read installed file {path}: {exc.strerror or exc}") from None
        else:
            if actual != expected[name]:
                problems.append((name, path, actual))

    if not problems:
        runtime.output.primary("ok")
        return 0
    for name, path, actual in problems:
        runtime.output.primary(f"{'missing' if actual is None else 'drifted'}: {path}")
        if args.diff and actual is not None:
            diff = difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected[name].splitlines(keepends=True),
                fromfile=str(path),
                tofile=f"rendered:{name}",
            )
            for line in diff:
                sys.stdout.write(line)
        elif args.diff and actual is None:
            diff = difflib.unified_diff(
                [],
                expected[name].splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=str(path),
            )
            for line in diff:
                sys.stdout.write(line)
    return 1


def build_cli(cli_identity: Optional[CliIdentity] = None) -> RegisteredCli:
    registry = CliRegistry(
        cli_identity or identity(),
        prog="toposd",
        description="Run the topos host daemon, render its systemd units, and check its config.",
        getting_started=("toposd config check", "toposd unit print", "toposd run"),
        global_options=(
            _opt("--config", "service config (default /etc/topos/toposd.toml; absent default uses built-ins)",
                 group="CONFIG", metavar="PATH"),
        ),
        unexpected_exceptions="report",
    )
    read = VerbGroup.EXPLORATION.value
    modify = VerbGroup.MODIFICATION.value

    registry.register(
        VerbSpec(
            "run",
            description="serve the read-only daemon on the systemd-passed observe socket",
            group=modify,
            mutating=True,
            include_json=False,
            include_progress=False,
            options=(
                _opt("--self-bind", "bind the configured socket for tests and development", action="store_true"),
                _opt("--socket", "override sockets.observe.path (requires --self-bind)", metavar="PATH"),
                _opt("--topos-config", "topos collector config path", metavar="PATH"),
                _opt("--history-size", "maximum in-memory frame history", metavar="N", type=int, default=120),
                _opt("--cgroup-root", "cgroup filesystem root", metavar="PATH"),
                _opt("--bpf-root", "BPF filesystem root", metavar="PATH"),
                _opt("--bpf-interval", "BPF snapshot interval in seconds", metavar="S", type=float, default=30.0),
                _opt("--bpf-state-dir", "BPF snapshot state directory (default /run/topos/state/bpf)", metavar="PATH"),
            ),
            handler=_run,
        )
    )

    unit_registry = CliRegistry(
        registry.identity,
        prog="toposd unit",
        description="Render and verify the systemd units.",
        global_options=registry.global_options,
        unexpected_exceptions="report",
    )
    unit_registry.register(
        VerbSpec(
            "print",
            description="print systemd unit and sysusers files rendered from toposd.toml",
            group=read,
            include_progress=False,
            options=(
                _opt("--unit", "print one rendered file", metavar="NAME",
                     choices=("topos-observe.socket", "topos.service", "topos.slice", "topos.sysusers")),
            ),
            handler=_unit_print,
        )
    )
    unit_registry.register(
        VerbSpec(
            "check",
            description="compare installed systemd and sysusers files with rendered output",
            group=read,
            include_progress=False,
            options=(
                _opt("--root", "installation root (default /)", metavar="DIR", default="/"),
                _opt("--diff", "show unified diffs for drifted files", action="store_true"),
            ),
            handler=_unit_check,
        )
    )
    registry.register(
        VerbSpec("unit", None, "render and verify systemd unit files", group=VerbGroup.MIXED.value,
                 delegate=unit_registry.build())
    )

    config_registry = CliRegistry(
        registry.identity,
        prog="toposd config",
        description="Validate the daemon service config.",
        global_options=registry.global_options,
        unexpected_exceptions="report",
    )
    config_registry.register(
        VerbSpec(
            "check",
            description="validate the config file without starting the daemon",
            group=read,
            include_progress=False,
            handler=_config_check,
        )
    )
    registry.register(
        VerbSpec("config", None, "validate the daemon config", group=VerbGroup.MIXED.value,
                 delegate=config_registry.build())
    )
    return registry.build()


def main(argv: Optional[Sequence[str]] = None) -> int:
    return build_cli().run(argv=None if argv is None else list(argv))


if __name__ == "__main__":
    raise SystemExit(main())
