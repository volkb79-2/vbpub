"""The command line: a thin cli-extended shell over the same query layer the TUI uses.

``build_cli(opener=...)`` takes the session opener so tests inject a fake-server-backed one; the CLI never
connects anywhere except through it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from cli_extended import (ArgumentSpec, CheckResult, CliFailure, CliIdentity, CliRegistry, CliRuntime, DoctorCheck, OptionSpec, RegisteredCli,
                          VerbGroup, VerbSpec, register_doctor)

from . import profile as prof
from .errors import TntError
from .query import disk_table
from .session import SessionOpener, open_session

MIN_PYTHON = (3, 11)


def identity() -> CliIdentity:
    """The version is the installed distribution's; there is no literal fallback (an uninstalled tree fails loudly)."""
    return CliIdentity.resolve(name="tnt", long_name="TrueNAS terminal UI and CLI", command="tnt", distribution="tnt")


def _flag(flags: tuple[str, ...], text: str, group: str = "TARGET HOST", **kw: Any) -> OptionSpec:
    return OptionSpec(flags, text, group=group, parser_kwargs={"action": "store_true", **kw})


def _value(flags: tuple[str, ...], text: str, metavar: str, group: str = "TARGET HOST", **kw: Any) -> OptionSpec:
    return OptionSpec(flags, text, group=group, metavar=metavar, parser_kwargs={"default": None, **kw})


CONFIG = _value(("--config",), "Profile file (default: $TNT_CONFIG, else ./tnt.toml, then $XDG_CONFIG_HOME/tnt/tnt.toml).", "FILE")
PROFILE = _value(("--profile",), "Profile name (default: the file's `default`, or its only profile).", "NAME")
INSECURE = _flag(("--insecure",), "Confirm TLS verification is OFF for a profile with tls = \"insecure\" (throwaway test hosts only).")
HOST_OPTIONS = (PROFILE, CONFIG, INSECURE)


def _config_path(args: Any) -> Path:
    return Path(args.config) if args.config else prof.default_config_path()


def _profile(args: Any) -> prof.Profile:
    profiles, default = prof.load_profiles(_config_path(args))
    return prof.pick(profiles, default, args.profile)


def _emit(runtime: CliRuntime, obj: Any, text: str) -> None:
    runtime.output.primary(obj if runtime.json_mode else text)


# -- doctor ---------------------------------------------------------------------------------------------------
def _check_python(runtime: CliRuntime, args: Any) -> CheckResult:
    if sys.version_info < MIN_PYTHON:
        return CheckResult("fail", f"Python {sys.version_info.major}.{sys.version_info.minor} is too old", remedy="use Python >= 3.11")
    return CheckResult("ok", f"Python {sys.version_info.major}.{sys.version_info.minor}")


def _check_deps(runtime: CliRuntime, args: Any) -> CheckResult:
    try:
        import truenas_api_client  # noqa: F401
    except ImportError:
        return CheckResult("fail", "truenas_api_client is not installed", remedy="reinstall tnt; the client installs from the pinned git sha")
    try:
        import textual  # noqa: F401
    except ImportError:
        return CheckResult("warn", "textual is not installed: `tnt tui` is unavailable, the CLI works", remedy="pip install 'tnt[tui]'")
    return CheckResult("ok", "truenas_api_client and textual are importable")


def _check_profiles(runtime: CliRuntime, args: Any) -> CheckResult:
    path = _config_path(args)
    try:
        profiles, _ = prof.load_profiles(path)
    except TntError as exc:
        return CheckResult("warn", str(exc), remedy="create the profile file (see README)")
    return CheckResult("ok", f"{len(profiles)} profile(s) in {path}", details={"profiles": sorted(profiles)})


def _check_credentials(runtime: CliRuntime, args: Any) -> CheckResult:
    try:
        p = _profile(args)
        prof.read_credential(p)
    except TntError as exc:
        return CheckResult("skip" if "profile" in str(exc).lower() and "no profile file" in str(exc) else "fail", str(exc),
                           remedy="chmod 600 tnt.toml and any referenced key file; configure the selected credential on its profile")
    return CheckResult("ok", f"credentials for {p.name} are configured and private")


def _check_connect(runtime: CliRuntime, args: Any, opener: SessionOpener) -> CheckResult:
    if not args.online:
        return CheckResult("skip", "not contacting the host (pass --online)")
    try:
        session = opener(_profile(args), insecure_ack=args.insecure)
    except TntError as exc:
        return CheckResult("fail", str(exc), remedy="check url, configured credential and TLS; an auth failure is never retried (rate limit)")
    try:
        return CheckResult("ok", session.banner(), details={"generation": session.backend.generation, "manifest": session.backend.verified})
    finally:
        session.close()


# -- handlers -------------------------------------------------------------------------------------------------
def _profile_list(args: Any, runtime: CliRuntime) -> int:
    profiles, default = prof.load_profiles(_config_path(args))
    rows = [dict(p.describe(), default=(n == default)) for n, p in sorted(profiles.items())]
    text = "\n".join(f"{'*' if r['default'] else ' '} {r['name']}  {r['url']}  tls={r['tls']}  auth={r['auth']}" for r in rows)
    _emit(runtime, rows, text)
    return 0


def _profile_show(args: Any, runtime: CliRuntime) -> int:
    profiles, default = prof.load_profiles(_config_path(args))
    p = prof.pick(profiles, default, args.name)
    problem = prof.key_file_problem(p.api_key_file) if p.api_key_file else None
    out = dict(p.describe(), key_file_problem=problem)
    _emit(runtime, out, "\n".join(f"{k}: {v}" for k, v in out.items()))
    return 0


def _disks(args: Any, runtime: CliRuntime, opener: SessionOpener) -> int:
    session = opener(_profile(args), insecure_ack=args.insecure)
    try:
        disks = session.disks()
    finally:
        session.close()
    _emit(runtime, [d.as_dict() for d in disks], disk_table(disks))
    return 0


def _tui(args: Any, runtime: CliRuntime, opener: SessionOpener) -> int:
    try:
        from .tui.app import TntApp
    except ImportError:
        raise CliFailure("textual is not installed; run: pip install 'tnt[tui]'", exit_code=2) from None
    session = opener(_profile(args), insecure_ack=args.insecure, allow_write=args.allow_write)
    try:
        TntApp(session).run()
    finally:
        session.close()
    return 0


def build_cli(cli_identity: CliIdentity | None = None, opener: SessionOpener = open_session) -> RegisteredCli:
    ident = cli_identity or identity()
    expected = (TntError,)
    registry = CliRegistry(ident, prog="tnt", description="TrueNAS terminal UI and CLI over the JSON-RPC API; the CLI mirrors the TUI.",
                           getting_started=("tnt profile list", "tnt doctor --online --profile NAME", "tnt disks --profile NAME --json", "tnt tui --profile NAME"),
                           logging_logger="tnt", unexpected_exceptions="report", expected_exceptions=expected)

    register_doctor(registry, [
        DoctorCheck("python", "Python version is supported", _check_python),
        DoctorCheck("deps", "transport and UI libraries import", _check_deps),
        DoctorCheck("profiles", "the profile file parses", _check_profiles),
        DoctorCheck("credentials", "the selected credentials are configured", _check_credentials),
        DoctorCheck("connect", "the host answers and login works", lambda runtime, args: _check_connect(runtime, args, opener)),
    ], options=HOST_OPTIONS + (_flag(("--online",), "Also connect to the host (one login, no retry)."),))

    profile_registry = CliRegistry(ident, prog="tnt profile", description="Inspect connection profiles (host URL, authentication, TLS mode).",
                                   unexpected_exceptions="report", expected_exceptions=expected)
    profile_registry.register(VerbSpec("list", None, "List the profiles in the profile file (never shows key material).",
                                       group=VerbGroup.EXPLORATION.value, examples=("tnt profile list --json",),
                                       options=(CONFIG,), handler=_profile_list))
    profile_registry.register(VerbSpec("show", None, "Show one profile and whether its key file is private (never the key).",
                                       group=VerbGroup.EXPLORATION.value,
                                       arguments=(ArgumentSpec("name", "Profile name (default: the default profile).", metavar="NAME", parser_kwargs={"nargs": "?"}),),
                                       options=(CONFIG,), handler=_profile_show))
    registry.register(VerbSpec("profile", None, "Inspect connection profiles (list, show).", group=VerbGroup.EXPLORATION.value,
                               delegate=profile_registry.build()))

    registry.register(VerbSpec("disks", None, "List disks with their partitions and free space (Storage > Disks).",
                               group=VerbGroup.EXPLORATION.value, examples=("tnt disks --profile lab --json",),
                               options=HOST_OPTIONS, handler=lambda args, runtime: _disks(args, runtime, opener)))
    registry.register(VerbSpec("tui", None, "Open the interactive terminal UI (needs the tui extra).", group=VerbGroup.EXPLORATION.value,
                               examples=("tnt tui --profile lab",),
                               options=HOST_OPTIONS + (_flag(("--allow-write",), "Open a write-capable session; every change is still planned and confirmed.", "SAFETY"),),
                               handler=lambda args, runtime: _tui(args, runtime, opener)))
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)
