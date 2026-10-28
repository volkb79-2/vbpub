"""The command line: a thin cli-extended shell over the same query layer the TUI uses.

``build_cli(opener=...)`` takes the session opener so tests inject a fake-server-backed one; the CLI never
connects anywhere except through it.
"""
from __future__ import annotations

import sys
import json
import getpass
from pathlib import Path
from typing import Any

from cli_extended import (ArgumentSpec, CheckResult, CliFailure, CliIdentity, CliRegistry, CliRuntime, DoctorCheck, OptionSpec, RegisteredCli,
                          VerbGroup, VerbSpec, register_doctor)

from . import profile as prof
from .errors import TntError
from .query import disk_table
from .session import SessionOpener, open_session
from .actions import apply_plan, make_plan

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
ALLOW_WRITE = _flag(("--allow-write",), "Allow applying the reviewed plan; every write still uses plan/apply.", "SAFETY")
DRY_RUN = _flag(("--dry-run",), "Print the plan without applying it.", "SAFETY")
CONFIRM = _value(("--confirm",), "Exact target name to confirm a destructive plan.", "TARGET", "SAFETY")
SPEC_FILE = _value(("--file",), "JSON object with additional API fields.", "FILE", "WORKFLOW")
ACTION_OPTIONS = (ALLOW_WRITE, DRY_RUN, CONFIRM)


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
        TntApp(session, session_opener=opener, insecure_ack=args.insecure).run()
    finally:
        session.close()
    return 0


def _read_json_object(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(Path(path).read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliFailure(f"cannot read JSON object from {path}: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise CliFailure(f"{path} must contain a JSON object")
    return value


def _workflow_read(args: Any, runtime: CliRuntime, opener: SessionOpener, call: Any) -> int:
    session = opener(_profile(args), insecure_ack=args.insecure)
    try:
        result = call(session)
    finally:
        session.close()
    from .workflows import _safe_value
    result = _safe_value(result)
    _emit(runtime, result, json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def _workflow_action(args: Any, runtime: CliRuntime, opener: SessionOpener, action_factory: Any) -> int:
    session = opener(_profile(args), insecure_ack=args.insecure, allow_write=args.allow_write)
    try:
        action = action_factory(session)
        plan = make_plan(action, session.rpc)
        if args.dry_run:
            _emit(runtime, {"plan": plan.render(), "applied": False}, plan.render())
            return 0
        if not args.allow_write:
            raise CliFailure(f"plan was not applied; pass --allow-write to apply it\n{plan.render()}")
        result = apply_plan(plan, session.rpc, allow_write=args.allow_write, typed_confirmation=args.confirm)
    finally:
        session.close()
    from .workflows import _safe_value
    safe_result = _safe_value(result)
    payload = {"plan": plan.render(), "result": safe_result}
    _emit(runtime, payload, f"{plan.render()}\nResult: {json.dumps(safe_result, indent=2, sort_keys=True, default=str)}")
    return 0


def _system_action(args: Any, runtime: CliRuntime, opener: SessionOpener, action_factory: Any) -> int:
    session = opener(_profile(args), insecure_ack=args.insecure, allow_write=args.allow_write)
    try:
        action = action_factory(session)
        plan = make_plan(action, session.rpc)
        if args.dry_run:
            _emit(runtime, {"plan": plan.render(), "applied": False}, plan.render())
            return 0
        if not args.allow_write:
            raise CliFailure(f"plan was not applied; pass --allow-write to apply it\n{plan.render()}")
        methods = {call.method for call in action.calls}

        def progress(job: Any) -> None:
            detail = job.get("progress") if isinstance(job, dict) else None
            percent = detail.get("percent") if isinstance(detail, dict) else None
            if isinstance(percent, (int, float)) and not isinstance(percent, bool):
                print(f"\rUpdate job progress: {percent:.0f}%", file=sys.stderr, end="", flush=True)

        result = apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=args.confirm,
                            job_progress=progress if "update.run" in methods else None)
        if "update.run" in methods:
            print(file=sys.stderr, flush=True)
        payload = {"plan": plan.render(), "result": result, "completed": True}
        text = f"{plan.render()}\nSystem action completed."
        if "system.reboot" in methods:
            text += " The host is rebooting; the CLI session will close."
        elif "system.shutdown" in methods:
            text += " The host is shutting down."
        _emit(runtime, payload, text)
        return 0
    finally:
        session.close()


def _system_config_export(args: Any, runtime: CliRuntime, opener: SessionOpener) -> int:
    from .config_export import download_config_export

    session = opener(_profile(args), insecure_ack=args.insecure, allow_write=args.allow_write)
    result = None
    download_url = None
    try:
        action = session.workflows.config_export_action(args.path, root_authorized_keys=args.include_root_authorized_keys)
        plan = make_plan(action, session.rpc)
        if args.dry_run:
            _emit(runtime, {"plan": plan.render(), "applied": False}, plan.render())
            return 0
        if not args.allow_write:
            raise CliFailure(f"plan was not applied; pass --allow-write to apply it\n{plan.render()}")
        result = apply_plan(plan, session.rpc, allow_write=True)
        returned = result[0] if isinstance(result, list) and result else None
        if not isinstance(returned, (list, tuple)) or len(returned) != 2:
            raise CliFailure("core.download returned an invalid config export result; the URL was not displayed")
        job_id, download_url = returned
        saved_to = download_config_export(session.profile, session.insecure_tls, job_id, download_url, args.path)
        payload = {"plan": plan.render(), "saved_to": saved_to, "mode": "0600", "includes_secret_seed": True}
        _emit(runtime, payload, f"{plan.render()}\nConfiguration tar saved to {saved_to} with mode 0600. "
                               "It contains the TrueNAS secret seed; store it securely.")
        return 0
    finally:
        result = None
        download_url = None
        session.close()


def _api_key_create(args: Any, runtime: CliRuntime, opener: SessionOpener) -> int:
    """Apply a create plan, then route the raw response only through the one-time key display."""
    from .keyfiles import write_api_key_file

    if runtime.json_mode:
        raise CliFailure("API key creation uses a one-time warning display; omit --json")
    session = opener(_profile(args), insecure_ack=args.insecure, allow_write=args.allow_write)
    plan = None
    result = None
    entry = None
    key = None
    try:
        action = session.workflows.api_key_create_action(args.name, args.username, args.expires_at)
        plan = make_plan(action, session.rpc)
        if args.dry_run:
            _emit(runtime, {"plan": plan.render(), "applied": False}, plan.render())
            return 0
        if not args.allow_write:
            raise CliFailure(f"plan was not applied; pass --allow-write to apply it\n{plan.render()}")
        result = apply_plan(plan, session.rpc, allow_write=True)
        entry = result[0] if isinstance(result, list) and result else None
        key = entry.get("key") if isinstance(entry, dict) else None
        if not isinstance(key, str) or not key:
            raise CliFailure("api_key.create returned no one-time key value; inspect the API key list and rotate manually")
        saved_to = None
        save_problem = None
        if args.write_key:
            try:
                saved_to = write_api_key_file(args.write_key, key)
            except TntError as exc:
                # A failed optional save must not hide the only plaintext copy returned by TrueNAS.
                save_problem = str(exc)
        warning = "WARNING: This API key is shown only once. Copy or save it now."
        if saved_to:
            configure = (f"Saved mode 0600 to {saved_to}. Add api_key_file = {json.dumps(saved_to)} under "
                         f"[profiles.{session.profile.name}] in tnt.toml (open it with $EDITOR tnt.toml).")
        else:
            configure = ("To use this key with tnt, save it to a private file, then add api_key_file = \"/absolute/path\" "
                         f"under [profiles.{session.profile.name}] in tnt.toml (open it with $EDITOR tnt.toml).")
        text = f"{plan.render()}\n\033[1;31m{warning}\033[0m\nAPI key (one-time): {key}\n{configure}"
        if save_problem:
            text += f"\nPrivate file was not written: {save_problem}"
        text += "\nThe key was not added to tnt.secrets.toml."
        _emit(runtime, {"plan": plan.render(), "warning": warning, "key": key, "saved_to": saved_to,
                        "save_problem": save_problem, "configuration": configure}, text)
        return 0
    finally:
        key = None
        entry = None
        result = None
        session.close()


def _secret_file(path: str | None) -> prof.Secret:
    if path is None:
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Repeat password: ")
        if first != second:
            raise CliFailure("passwords do not match")
        value = first
    else:
        problem = prof.key_file_problem(path)
        if problem:
            raise CliFailure(f"password file must be private: {problem}")
        try:
            value = Path(path).read_text("utf-8").rstrip("\r\n")
        except (OSError, UnicodeDecodeError) as exc:
            raise CliFailure(f"cannot read password file {path}: {type(exc).__name__}") from None
    if not value:
        raise CliFailure("password must not be empty")
    return prof.Secret(value)


def _group_ids(value: str) -> list[int]:
    try:
        groups = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError:
        raise ValueError("groups must be comma-separated integer IDs") from None
    if not groups:
        raise ValueError("at least one group ID is required")
    if any(group < 0 for group in groups):
        raise ValueError("group IDs must not be negative")
    return groups


def _workflow_group(registry: CliRegistry, ident: CliIdentity, name: str, description: str, verbs: list[VerbSpec]) -> None:
    child = CliRegistry(ident, prog=f"tnt {name}", description=description,
                        unexpected_exceptions="report", expected_exceptions=(TntError,))
    for verb in verbs:
        child.register(verb)
    registry.register(VerbSpec(name, None, description, group=VerbGroup.EXPLORATION.value, delegate=child.build()))


def _share_protocol_verbs(ident: CliIdentity, opener: SessionOpener, protocol: str) -> Any:
    """Construct one protocol's share subcommands. Create payloads use API v27 field names in a JSON file."""
    child = CliRegistry(ident, prog=f"tnt share {protocol}", description=f"Inspect and manage {protocol.upper()} shares.",
                        unexpected_exceptions="report", expected_exceptions=(TntError,))
    read_opts = HOST_OPTIONS
    write_opts = HOST_OPTIONS + ACTION_OPTIONS
    key = protocol.lower()
    if key == "smb":
        list_call = lambda s: s.workflows.shares_smb()
        show_arg = ArgumentSpec("name", "SMB share name.", metavar="NAME")
        show_call = lambda s, a: s.workflows.share_smb(a.name)
        create_action = lambda s, a: s.workflows.share_smb_create_action(_read_json_object(a.file))
        delete_action = lambda s, a: s.workflows.share_smb_delete_action(a.name)
        label = "SMB"
    else:
        list_call = lambda s: s.workflows.shares_nfs()
        show_arg = ArgumentSpec("path", "Export path.", metavar="PATH")
        show_call = lambda s, a: s.workflows.share_nfs(a.path)
        create_action = lambda s, a: s.workflows.share_nfs_create_action(_read_json_object(a.file))
        delete_action = lambda s, a: s.workflows.share_nfs_delete_action(a.path)
        label = "NFS"
    child.register(VerbSpec("list", None, f"List {label} shares.", group=VerbGroup.EXPLORATION.value,
                            options=read_opts, handler=lambda a, r: _workflow_read(a, r, opener, list_call)))
    child.register(VerbSpec("show", None, f"Show one {label} share.", group=VerbGroup.EXPLORATION.value,
                            arguments=(show_arg,), options=read_opts,
                            handler=lambda a, r: _workflow_read(a, r, opener, lambda s: show_call(s, a))))
    child.register(VerbSpec("create", None, f"Create a {label} share from a JSON object using v27 fields.",
                            group=VerbGroup.MODIFICATION.value, options=write_opts + (_value(("--file",), "JSON create payload.", "FILE", "WORKFLOW"),),
                            handler=lambda a, r: _workflow_action(a, r, opener, lambda s: create_action(s, a))))
    child.register(VerbSpec("delete", None, f"Delete a {label} share configuration; its data remains on disk.",
                            group=VerbGroup.MODIFICATION.value, arguments=(show_arg,), options=write_opts,
                            handler=lambda a, r: _workflow_action(a, r, opener, lambda s: delete_action(s, a))))
    return child.build()


def _share_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    return [
        VerbSpec("smb", None, "Manage SMB shares.", group=VerbGroup.EXPLORATION.value,
                 delegate=_share_protocol_verbs(ident, opener, "smb")),
        VerbSpec("nfs", None, "Manage NFS shares.", group=VerbGroup.EXPLORATION.value,
                 delegate=_share_protocol_verbs(ident, opener, "nfs")),
    ]


def _task_protocol_cli(ident: CliIdentity, opener: SessionOpener, kind: str) -> Any:
    child = CliRegistry(ident, prog=f"tnt task {kind}", description=f"Inspect and operate {kind} tasks.",
                        unexpected_exceptions="report", expected_exceptions=(TntError,))
    write_options = HOST_OPTIONS + ACTION_OPTIONS
    read_options = HOST_OPTIONS
    if kind == "snapshot":
        id_arg = ArgumentSpec("task_id", "Periodic snapshot task ID.", metavar="ID", parser_kwargs={"type": int})
        child.register(VerbSpec("list", None, "List periodic snapshot tasks.", group=VerbGroup.EXPLORATION.value,
                                options=read_options, handler=lambda a, r: _workflow_read(a, r, opener,
                                    lambda s: s.workflows.snapshot_tasks())))
        child.register(VerbSpec("show", None, "Show one periodic snapshot task.", group=VerbGroup.EXPLORATION.value,
                                arguments=(id_arg,), options=read_options, handler=lambda a, r: _workflow_read(a, r, opener,
                                    lambda s: s.workflows.snapshot_task(a.task_id))))
        child.register(VerbSpec("create", None, "Create a periodic snapshot task from a v27 JSON payload.",
                                group=VerbGroup.MODIFICATION.value,
                                options=write_options + (_value(("--file",), "JSON task payload.", "FILE", "WORKFLOW"),),
                                handler=lambda a, r: _workflow_action(a, r, opener,
                                    lambda s: s.workflows.snapshot_task_create_action(_read_json_object(a.file)))))
        child.register(VerbSpec("run", None, "Run a periodic snapshot task now.", group=VerbGroup.MODIFICATION.value,
                                arguments=(id_arg,), options=write_options, handler=lambda a, r: _workflow_action(a, r, opener,
                                    lambda s: s.workflows.snapshot_task_run_action(a.task_id))))
        for verb, enabled in (("enable", True), ("disable", False)):
            child.register(VerbSpec(verb, None, f"{verb.title()} a periodic snapshot task.", group=VerbGroup.MODIFICATION.value,
                                    arguments=(id_arg,), options=write_options,
                                    handler=lambda a, r, value=enabled: _workflow_action(a, r, opener,
                                        lambda s: s.workflows.snapshot_task_enable_action(a.task_id, value))))
        child.register(VerbSpec("delete", None, "Delete a periodic snapshot task.", group=VerbGroup.MODIFICATION.value,
                                arguments=(id_arg,), options=write_options, handler=lambda a, r: _workflow_action(a, r, opener,
                                    lambda s: s.workflows.snapshot_task_delete_action(a.task_id))))
    elif kind == "replication":
        name_arg = ArgumentSpec("name", "Replication task name.", metavar="NAME")
        child.register(VerbSpec("list", None, "List replication tasks.", group=VerbGroup.EXPLORATION.value,
                                options=read_options, handler=lambda a, r: _workflow_read(a, r, opener,
                                    lambda s: s.workflows.replication_tasks())))
        child.register(VerbSpec("show", None, "Show one replication task.", group=VerbGroup.EXPLORATION.value,
                                arguments=(name_arg,), options=read_options, handler=lambda a, r: _workflow_read(a, r, opener,
                                    lambda s: s.workflows.replication_task(a.name))))
        child.register(VerbSpec("run", None, "Run a replication task now after showing source and target.",
                                group=VerbGroup.MODIFICATION.value, arguments=(name_arg,), options=write_options,
                                handler=lambda a, r: _workflow_action(a, r, opener,
                                    lambda s: s.workflows.replication_run_action(a.name))))
        for verb, enabled in (("enable", True), ("disable", False)):
            child.register(VerbSpec(verb, None, f"{verb.title()} a replication task.", group=VerbGroup.MODIFICATION.value,
                                    arguments=(name_arg,), options=write_options,
                                    handler=lambda a, r, value=enabled: _workflow_action(a, r, opener,
                                        lambda s: s.workflows.replication_enable_action(a.name, value))))
    else:
        name_arg = ArgumentSpec("description", "Cloud-sync task description.", metavar="NAME")
        child.register(VerbSpec("list", None, "List cloud-sync tasks with all credentials masked.",
                                group=VerbGroup.EXPLORATION.value, options=read_options,
                                handler=lambda a, r: _workflow_read(a, r, opener, lambda s: s.workflows.cloudsync_tasks())))
        child.register(VerbSpec("show", None, "Show one cloud-sync task with all credentials masked.",
                                group=VerbGroup.EXPLORATION.value, arguments=(name_arg,), options=read_options,
                                handler=lambda a, r: _workflow_read(a, r, opener, lambda s: s.workflows.cloudsync_task(a.description))))
        child.register(VerbSpec("run", None, "Run a cloud-sync task now.", group=VerbGroup.MODIFICATION.value,
                                arguments=(name_arg,), options=write_options,
                                handler=lambda a, r: _workflow_action(a, r, opener,
                                    lambda s: s.workflows.cloudsync_run_action(a.description))))
        for verb, enabled in (("enable", True), ("disable", False)):
            child.register(VerbSpec(verb, None, f"{verb.title()} a cloud-sync task.", group=VerbGroup.MODIFICATION.value,
                                    arguments=(name_arg,), options=write_options,
                                    handler=lambda a, r, value=enabled: _workflow_action(a, r, opener,
                                        lambda s: s.workflows.cloudsync_enable_action(a.description, value))))
    return child.build()


def _task_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    return [VerbSpec(name, None, description, group=VerbGroup.EXPLORATION.value,
                     delegate=_task_protocol_cli(ident, opener, name))
            for name, description in (("snapshot", "Manage periodic snapshot tasks."),
                                      ("replication", "Inspect and run replication tasks."),
                                      ("cloudsync", "Inspect and run cloud-sync tasks."))]


def _app_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    app_id = ArgumentSpec("app_id", "Installed app ID (the v27 app_name identifier).", metavar="APP_ID")
    writes = HOST_OPTIONS + ACTION_OPTIONS
    return [
        VerbSpec("list", None, "List installed apps, state, and the v27 upgrade-available flag.",
                 group=VerbGroup.EXPLORATION.value, options=HOST_OPTIONS,
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener, lambda session: session.workflows.apps())),
        VerbSpec("show", None, "Show one installed app by v27 app ID.", group=VerbGroup.EXPLORATION.value,
                 arguments=(app_id,), options=HOST_OPTIONS,
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener, lambda session: session.workflows.app(args.app_id))),
        *[VerbSpec(operation, None, f"{label} an installed app through a reviewed plan.",
                   group=VerbGroup.MODIFICATION.value, arguments=(app_id,), options=writes,
                   handler=lambda args, runtime, op=operation: _workflow_action(
                       args, runtime, opener, lambda session: session.workflows.app_lifecycle_action(args.app_id, op)))
          for operation, label in (("start", "Start"), ("stop", "Stop"), ("restart", "Restart"))],
        VerbSpec("uninstall", None, "Uninstall an app (destructive; app data volumes default to preserved).",
                 group=VerbGroup.MODIFICATION.value, arguments=(app_id,),
                 options=writes + (_flag(("--delete-data-volumes",), "Also delete TrueNAS-managed app data volumes (default: preserve).", "SAFETY"),),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                     lambda session: session.workflows.app_delete_action(args.app_id,
                                                                          delete_data_volumes=args.delete_data_volumes))),
    ]


def _system_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    force_jobs = _flag(("--force-with-jobs",), "Allow a system action to proceed with listed running jobs; typed confirmation remains required.", "SAFETY")
    delay = _value(("--delay",), "Delay before reboot or shutdown, in seconds (0–86400).", "SECONDS", "WORKFLOW", type=int)
    update_registry = CliRegistry(ident, prog="tnt system update", description="Check and apply v27 system updates.",
                                  unexpected_exceptions="report", expected_exceptions=(TntError,))
    update_registry.register(VerbSpec("check", None, "Check for an update and show train, versions, and release notes.",
                                      group=VerbGroup.EXPLORATION.value, options=HOST_OPTIONS,
                                      handler=lambda args, runtime: _workflow_read(
                                          args, runtime, opener, lambda session: session.workflows.update_check())))
    update_registry.register(VerbSpec(
        "apply", None, "Apply the exact currently available update as a destructive job; reboot is separate.",
        group=VerbGroup.MODIFICATION.value,
        arguments=(ArgumentSpec("version", "Exact available target version; also the typed confirmation.", metavar="VERSION"),),
        options=HOST_OPTIONS + ACTION_OPTIONS + (force_jobs,),
        handler=lambda args, runtime: _system_action(
            args, runtime, opener,
            lambda session: session.workflows.update_apply_action(args.version, force_with_jobs=args.force_with_jobs))))
    return [
        VerbSpec("update", None, "Check and apply a v27 system update.", group=VerbGroup.EXPLORATION.value,
                 delegate=update_registry.build()),
        *[VerbSpec(
            operation, None, f"{operation.title()} the host as a destructive v27 system job.",
            group=VerbGroup.MODIFICATION.value,
            options=HOST_OPTIONS + ACTION_OPTIONS + (delay, force_jobs),
            handler=lambda args, runtime, op=operation: _system_action(
                args, runtime, opener,
                lambda session: session.workflows.system_power_action(
                    op, delay=args.delay, force_with_jobs=args.force_with_jobs)))
          for operation in ("reboot", "shutdown")],
        VerbSpec("config-export", None, "Download a configuration tar to a new local mode-0600 file.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("path", "New absolute local .tar destination.", metavar="PATH"),),
                 options=HOST_OPTIONS + ACTION_OPTIONS + (_flag(
                     ("--include-root-authorized-keys",), "Include the host root authorized_keys file in the tar export.", "WORKFLOW"),),
                 handler=lambda args, runtime: _system_config_export(args, runtime, opener)),
    ]


def _dataset_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    def options(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + extra

    def write_options(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + ACTION_OPTIONS + extra

    def create_action(args: Any, session: Any):
        data = _read_json_object(args.file)
        data["name"] = args.name
        data["type"] = "VOLUME" if args.type == "zvol" else "FILESYSTEM"
        if args.type == "zvol":
            if args.volsize is None:
                raise CliFailure("--volsize BYTES is required for --type zvol")
            data["volsize"] = args.volsize
        elif args.volsize is not None:
            raise CliFailure("--volsize is only valid with --type zvol")
        for key in ("compression", "recordsize", "quota", "refquota", "comments", "readonly"):
            value = getattr(args, key)
            if value is not None:
                data[key] = value
        if args.create_ancestors:
            data["create_ancestors"] = True
        return session.workflows.dataset_create_action(data)

    def update_action(args: Any, session: Any):
        data = _read_json_object(args.file)
        for key in ("compression", "recordsize", "quota", "refquota", "comments", "readonly"):
            value = getattr(args, key)
            if value is not None:
                data[key] = value
        if not data:
            raise CliFailure("provide at least one property option or --file spec.json")
        return session.workflows.dataset_update_action(args.name, data)

    def delete_action(args: Any, session: Any):
        return session.workflows.dataset_delete_action(args.name, recursive=args.recursive)

    return [
        VerbSpec("list", None, "List datasets as a tree with usage, properties, mount state, and attachments.",
                 group=VerbGroup.EXPLORATION.value, examples=("tnt dataset list --profile lab --json",),
                 options=options(), handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                                                   lambda session: session.workflows.datasets())),
        VerbSpec("show", None, "Show one dataset by full name.", group=VerbGroup.EXPLORATION.value,
                 arguments=(ArgumentSpec("name", "Full dataset name, such as tank/home.", metavar="NAME"),),
                 options=options(), handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                                                  lambda session: session.workflows.dataset(args.name))),
        VerbSpec("create", None, "Create a filesystem dataset or zvol through a reviewed plan.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "Full dataset name under a data pool.", metavar="NAME"),),
                 options=write_options(_value(("--type",), "Dataset kind: filesystem or zvol.", "KIND", "WORKFLOW",
                                              choices=("filesystem", "zvol")),
                                       _value(("--volsize",), "Zvol size in bytes.", "BYTES", "WORKFLOW", type=int),
                                       _value(("--compression",), "ZFS compression value.", "VALUE", "WORKFLOW"),
                                       _value(("--recordsize",), "Filesystem record size, in bytes.", "BYTES", "WORKFLOW", type=int),
                                       _value(("--quota",), "Dataset quota in bytes.", "BYTES", "WORKFLOW", type=int),
                                       _value(("--refquota",), "Dataset reference quota in bytes.", "BYTES", "WORKFLOW", type=int),
                                       _value(("--comments",), "Dataset comment.", "TEXT", "WORKFLOW"),
                                       _value(("--readonly",), "Readonly property: ON, OFF, or INHERIT.", "VALUE", "WORKFLOW",
                                              choices=("ON", "OFF", "INHERIT")),
                                       _flag(("--create-ancestors",), "Create missing parent datasets.", "WORKFLOW"), SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: create_action(args, session))),
        VerbSpec("update", None, "Update dataset properties through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "Full dataset name.", metavar="NAME"),),
                 options=write_options(_value(("--compression",), "ZFS compression value.", "VALUE", "WORKFLOW"),
                                       _value(("--recordsize",), "Filesystem record size, in bytes.", "BYTES", "WORKFLOW", type=int),
                                       _value(("--quota",), "Dataset quota in bytes.", "BYTES", "WORKFLOW", type=int),
                                       _value(("--refquota",), "Dataset reference quota in bytes.", "BYTES", "WORKFLOW", type=int),
                                       _value(("--comments",), "Dataset comment.", "TEXT", "WORKFLOW"),
                                       _value(("--readonly",), "Readonly property: ON, OFF, or INHERIT.", "VALUE", "WORKFLOW",
                                              choices=("ON", "OFF", "INHERIT")), SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: update_action(args, session))),
        VerbSpec("delete", None, "Delete a dataset; recursive deletion needs exact typed confirmation.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "Full dataset name; this exact name is the confirmation target.", metavar="NAME"),),
                 options=write_options(_flag(("--recursive",), "Allow deleting children, mounts, or running workload references.", "WORKFLOW")),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener, lambda session: delete_action(args, session))),
    ]


def _snapshot_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    def read_options() -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS

    def write_options(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + ACTION_OPTIONS + extra

    return [
        VerbSpec("list", None, "List snapshots, optionally filtered to a dataset.", group=VerbGroup.EXPLORATION.value,
                 arguments=(ArgumentSpec("dataset", "Optional full dataset name.", metavar="DATASET", parser_kwargs={"nargs": "?"}),),
                 options=read_options(), handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                                                       lambda session: session.workflows.snapshots(args.dataset))),
        VerbSpec("create", None, "Create a snapshot by name through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("dataset", "Full dataset name.", metavar="DATASET"),
                            ArgumentSpec("name", "Snapshot name.", metavar="NAME")),
                 options=write_options(_flag(("--recursive",), "Include child datasets.", "WORKFLOW"), SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener, lambda session: session.workflows.snapshot_create_action(
                     {**_read_json_object(args.file), "dataset": args.dataset, "name": args.name, **({"recursive": True} if args.recursive else {})}))),
        VerbSpec("clone", None, "Clone a snapshot to a new dataset through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("snapshot", "Full snapshot name.", metavar="SNAPSHOT"),
                            ArgumentSpec("dataset_dst", "Full name for the new dataset.", metavar="DATASET")),
                 options=write_options(), handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                                         lambda session: session.workflows.snapshot_clone_action(args.snapshot, args.dataset_dst))),
        VerbSpec("hold", None, "Hold a snapshot through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("snapshot", "Full snapshot name.", metavar="SNAPSHOT"),), options=write_options(),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: session.workflows.snapshot_hold_action(args.snapshot))),
        VerbSpec("release", None, "Release a snapshot hold through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("snapshot", "Full snapshot name.", metavar="SNAPSHOT"),), options=write_options(),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: session.workflows.snapshot_release_action(args.snapshot))),
        VerbSpec("rollback", None, "Roll a dataset back to a snapshot; requires exact typed confirmation.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("snapshot", "Full snapshot name; this exact name is the confirmation target.", metavar="SNAPSHOT"),),
                 options=write_options(), handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                                         lambda session: session.workflows.snapshot_rollback_action(args.snapshot))),
        VerbSpec("delete", None, "Delete a snapshot; requires exact typed confirmation.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("snapshot", "Full snapshot name; this exact name is the confirmation target.", metavar="SNAPSHOT"),),
                 options=write_options(_flag(("--recursive",), "Also delete child snapshots.", "WORKFLOW")),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: session.workflows.snapshot_delete_action(args.snapshot, recursive=args.recursive))),
    ]


def _user_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    def reads(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + extra

    def writes(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + ACTION_OPTIONS + extra

    def create(args: Any, session: Any):
        data = _read_json_object(args.file)
        data.update(username=args.username, full_name=args.full_name)
        if args.group_create:
            data["group_create"] = True
        elif args.group is not None:
            data["group"] = args.group
        if args.groups is not None:
            data["groups"] = args.groups
        if args.shell is not None:
            data["shell"] = args.shell
        if args.home is not None:
            data["home"] = args.home
        if args.home_create:
            data["home_create"] = True
        if args.password_disabled:
            data["password_disabled"] = True
        elif args.password_file:
            data["password"] = _secret_file(args.password_file)
        else:
            data["password"] = _secret_file(None)
        return session.workflows.user_create_action(data)

    def update(args: Any, session: Any):
        data = _read_json_object(args.file)
        if args.full_name is not None:
            data["full_name"] = args.full_name
        if args.shell is not None:
            data["shell"] = args.shell
        if args.home is not None:
            data["home"] = args.home
        if args.groups is not None:
            data["groups"] = args.groups
        if not data:
            raise CliFailure("provide at least one user property option or --file spec.json")
        return session.workflows.user_update_action(args.username, data, builtin=args.builtin)

    def delete(args: Any, session: Any):
        return session.workflows.user_delete_action(args.username)

    def set_password(args: Any, session: Any):
        secret = _secret_file(args.password_file)
        return session.workflows.user_password_action(args.username, secret, confirm_self=args.confirm_self,
                                                      builtin=args.builtin)

    def set_key(args: Any, session: Any):
        if bool(args.file) == bool(args.key):
            raise CliFailure("provide exactly one of --file KEY.pub or --key 'ssh-ed25519 ...'")
        if args.file:
            try:
                text = Path(args.file).read_text("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise CliFailure(f"cannot read public key file {args.file}: {type(exc).__name__}") from None
        else:
            text = args.key
        from .workflows import validate_ssh_public_keys
        keys = validate_ssh_public_keys(text)
        return session.workflows.user_ssh_key_action(args.username, keys, replace=not args.add,
                                                     confirm_self=args.confirm_self, builtin=args.builtin)

    def remove_key(args: Any, session: Any):
        return session.workflows.user_ssh_key_action(args.username, [], remove=True,
                                                     confirm_self=args.confirm_self, builtin=args.builtin)

    ssh_registry = CliRegistry(ident, prog="tnt user ssh-key", description="Manage SSH public keys without displaying key material.",
                               unexpected_exceptions="report", expected_exceptions=(TntError,))
    ssh_registry.register(VerbSpec("set", None, "Replace or add validated SSH public keys through a reviewed plan.",
                                   group=VerbGroup.MODIFICATION.value,
                                   arguments=(ArgumentSpec("username", "Local account name.", metavar="USER"),),
                                   options=writes(_value(("--file",), "Read public keys from an OpenSSH .pub file.", "FILE", "WORKFLOW"),
                                                  _value(("--key",), "One OpenSSH public-key line. Public keys are never printed.", "KEY", "WORKFLOW"),
                                                  _flag(("--add",), "Add keys while preserving the currently configured keys.", "WORKFLOW"),
                                                  _flag(("--builtin",), "Allow editing a built-in user account.", "WORKFLOW"),
                                                  _flag(("--confirm-self",), "Explicitly allow changing the current login user's SSH key.", "SAFETY")),
                                   handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                                  lambda session: set_key(args, session))))
    ssh_registry.register(VerbSpec("remove", None, "Remove all SSH public keys from a user through a reviewed plan.",
                                   group=VerbGroup.MODIFICATION.value,
                                   arguments=(ArgumentSpec("username", "Local account name.", metavar="USER"),),
                                   options=writes(_flag(("--builtin",), "Allow editing a built-in user account.", "WORKFLOW"),
                                                  _flag(("--confirm-self",), "Explicitly allow changing the current login user's SSH key.", "SAFETY")),
                                   handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                                  lambda session: remove_key(args, session))))

    return [
        VerbSpec("list", None, "List local and built-in users; credential and SSH key material is masked.",
                 group=VerbGroup.EXPLORATION.value, options=reads(),
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener, lambda session: session.workflows.users())),
        VerbSpec("show", None, "Show a user record with credential material masked.", group=VerbGroup.EXPLORATION.value,
                 arguments=(ArgumentSpec("username", "Account name.", metavar="USER"),), options=reads(),
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                               lambda session: session.workflows.user(args.username))),
        VerbSpec("create", None, "Create a local user through a reviewed plan; password input is hidden or read from a file.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("username", "New local username.", metavar="USER"),
                            ArgumentSpec("full_name", "Full name or account description.", metavar="FULL_NAME")),
                 options=writes(_value(("--group",), "Primary group entry ID.", "ID", "WORKFLOW", type=int),
                                _flag(("--group-create",), "Create a primary group with the same name as the user.", "WORKFLOW"),
                                _value(("--groups",), "Comma-separated supplementary group entry IDs.", "IDS", "WORKFLOW", type=_group_ids),
                                _value(("--shell",), "Login shell.", "PATH", "WORKFLOW"),
                                _value(("--home",), "Home directory path.", "PATH", "WORKFLOW"),
                                _flag(("--home-create",), "Create the home directory.", "WORKFLOW"),
                                _flag(("--password-disabled",), "Disable password authentication for the new user.", "WORKFLOW"),
                                _value(("--password-file",), "Read password from a file; otherwise prompt without echo.", "FILE", "WORKFLOW"),
                                SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener, lambda session: create(args, session))),
        VerbSpec("update", None, "Update basic properties and group membership through a reviewed plan.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("username", "Local account name.", metavar="USER"),),
                 options=writes(_value(("--full-name",), "New full name.", "TEXT", "WORKFLOW"),
                                _value(("--shell",), "New login shell.", "PATH", "WORKFLOW"),
                                _value(("--home",), "New home directory path.", "PATH", "WORKFLOW"),
                                _value(("--groups",), "Replace supplementary group IDs.", "IDS", "WORKFLOW", type=_group_ids),
                                _flag(("--builtin",), "Allow editing a built-in user account.", "WORKFLOW"), SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener, lambda session: update(args, session))),
        VerbSpec("delete", None, "Delete a local non-built-in user; requires exact typed confirmation.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("username", "Account name; type this exact name to confirm.", metavar="USER"),),
                 options=writes(), handler=lambda args, runtime: _workflow_action(args, runtime, opener, lambda session: delete(args, session))),
        VerbSpec("password", None, "Set a user's password through a reviewed plan; input is hidden or read from a file.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("username", "Local account name.", metavar="USER"),),
                 options=writes(_value(("--password-file",), "Read the new password from a file; otherwise prompt without echo.", "FILE", "WORKFLOW"),
                                _flag(("--builtin",), "Allow editing a built-in user account.", "WORKFLOW"),
                                _flag(("--confirm-self",), "Explicitly allow changing the current login user's password.", "SAFETY")),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: set_password(args, session))),
        VerbSpec("ssh-key", None, "Manage SSH public keys.", group=VerbGroup.MODIFICATION.value,
                 delegate=ssh_registry.build()),
    ]


def _container_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    def reads(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + extra

    def writes(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + ACTION_OPTIONS + extra

    def create_action(args: Any, session: Any):
        data = _read_json_object(args.file)
        data["name"] = args.name
        image_name = args.image or data.get("image", {}).get("name") if isinstance(data.get("image", {}), dict) else args.image
        if image_name:
            version = args.image_version or data.get("image", {}).get("version") or "latest"
            data["image"] = {"name": image_name, "version": version}
        if args.pool is not None:
            data["pool"] = args.pool
        if args.cpuset is not None:
            data["cpuset"] = args.cpuset
        if args.autostart:
            data["autostart"] = True
        if args.no_autostart:
            data["autostart"] = False
        return session.workflows.container_create_action(data)

    def update_action(args: Any, session: Any):
        data = _read_json_object(args.file)
        for key in ("cpuset",):
            value = getattr(args, key)
            if value is not None:
                data[key] = value
        if args.autostart:
            data["autostart"] = True
        if args.no_autostart:
            data["autostart"] = False
        return session.workflows.container_update_action(args.name, data)

    return [
        VerbSpec("list", None, "List containers (v27 does not expose the current image or memory limit in container records).",
                 group=VerbGroup.EXPLORATION.value, options=reads(),
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener, lambda session: session.workflows.containers())),
        VerbSpec("show", None, "Show one container record.", group=VerbGroup.EXPLORATION.value,
                 arguments=(ArgumentSpec("name", "Container name.", metavar="NAME"),), options=reads(),
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                               lambda session: session.workflows.container(args.name))),
        VerbSpec("create", None, "Create a container from a v27 image selector through a reviewed plan.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "Container name.", metavar="NAME"),),
                 options=writes(_value(("--image",), "Image/template name.", "NAME", "WORKFLOW"),
                                _value(("--image-version",), "Image version (defaults to latest).", "VERSION", "WORKFLOW"),
                                _value(("--pool",), "Data pool name.", "POOL", "WORKFLOW"),
                                _value(("--cpuset",), "Host CPU set for affinity.", "SET", "WORKFLOW"),
                                _flag(("--autostart",), "Enable autostart.", "WORKFLOW"),
                                _flag(("--no-autostart",), "Disable autostart.", "WORKFLOW"), SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: create_action(args, session))),
        VerbSpec("update", None, "Update container autostart and CPU affinity through a reviewed plan.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "Container name.", metavar="NAME"),),
                 options=writes(_value(("--cpuset",), "Host CPU set for affinity.", "SET", "WORKFLOW"),
                                _flag(("--autostart",), "Enable autostart.", "WORKFLOW"),
                                _flag(("--no-autostart",), "Disable autostart.", "WORKFLOW"), SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                lambda session: update_action(args, session))),
        *[VerbSpec(operation, None, f"{operation.title()} a container through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                   arguments=(ArgumentSpec("name", "Container name.", metavar="NAME"),),
                   options=writes(), handler=lambda args, runtime, op=operation: _workflow_action(
                       args, runtime, opener, lambda session: session.workflows.container_lifecycle_action(args.name, op)))
          for operation in ("start", "stop", "restart")],
        VerbSpec("delete", None, "Delete a container; requires exact typed confirmation.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "Container name; type this exact name to confirm.", metavar="NAME"),),
                 options=writes(), handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                                  lambda session: session.workflows.container_delete_action(args.name))),
    ]


def _vm_verbs(ident: CliIdentity, opener: SessionOpener) -> list[VerbSpec]:
    def reads(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + extra

    def writes(*extra: OptionSpec) -> tuple[OptionSpec, ...]:
        return HOST_OPTIONS + ACTION_OPTIONS + extra

    def create_action(args: Any, session: Any):
        data = _read_json_object(args.file)
        data.update(name=args.name, memory=args.memory)
        if args.vcpus is not None:
            data["vcpus"] = args.vcpus
        if args.autostart:
            data["autostart"] = True
        if args.no_autostart:
            data["autostart"] = False
        return session.workflows.vm_create_action(data)

    def create_workflow(args: Any, runtime: CliRuntime):
        if args.disk_zvol is None or args.disk_size is None:
            raise CliFailure("VM creation requires --disk-zvol POOL/NAME and --disk-size BYTES")
        device_attributes = [{"dtype": "DISK", "create_zvol": True, "zvol_name": args.disk_zvol,
                             "zvol_volsize": args.disk_size}]
        if args.iso is not None:
            device_attributes.append({"dtype": "CDROM", "path": args.iso})
        if args.nic_attach is not None:
            device_attributes.append({"dtype": "NIC", "nic_attach": args.nic_attach})

        session = opener(_profile(args), insecure_ack=args.insecure, allow_write=args.allow_write)
        try:
            for attrs in device_attributes:
                session.workflows.validate_vm_device_attributes(attrs)
            action = create_action(args, session)
            plan = make_plan(action, session.rpc)
            if args.dry_run:
                preview = [{"method": "vm.device.create", "vm": "<id returned by vm.create>", "attributes": attrs}
                           for attrs in device_attributes]
                _emit(runtime, {"plan": plan.render(), "device_plan_templates": preview, "applied": False},
                      f"{plan.render()}\nDevice plans will bind to the VM ID returned by create:\n"
                      + "\n".join(f"  vm.device.create({json.dumps(item, sort_keys=True)})" for item in preview))
                return 0
            if not args.allow_write:
                raise CliFailure(f"plan was not applied; pass --allow-write to apply it\n{plan.render()}")
            # vm.create excludes device configuration in v27. Resolve its returned ID, then make and apply one
            # separate exact plan for each device. Each exact plan is printed before its API call.
            print(plan.render(), file=sys.stderr, flush=True)
            created = apply_plan(plan, session.rpc, allow_write=True)
            vm_row = created[0] if created else None
            if not isinstance(vm_row, dict) or not isinstance(vm_row.get("id"), int):
                raise CliFailure("vm.create returned no VM ID; the VM record may exist without its devices")
            device_results = []
            for attrs in device_attributes:
                device_action = session.workflows.vm_device_create_action(args.name, attrs)
                device_plan = make_plan(device_action, session.rpc)
                print(device_plan.render(), file=sys.stderr, flush=True)
                result = apply_plan(device_plan, session.rpc, allow_write=True)
                device_results.append({"plan": device_plan.render(), "result": result})
        finally:
            session.close()
        from .workflows import _safe_value
        payload = {"plan": plan.render(), "result": _safe_value(created), "device_plans": _safe_value(device_results)}
        _emit(runtime, payload, f"{plan.render()}\nCreated VM and applied {len(device_results)} device plan(s).")
        return 0

    def update_action(args: Any, session: Any):
        data = _read_json_object(args.file)
        if args.memory is not None:
            data["memory"] = args.memory
        if args.vcpus is not None:
            data["vcpus"] = args.vcpus
        if args.autostart:
            data["autostart"] = True
        if args.no_autostart:
            data["autostart"] = False
        if not data:
            raise CliFailure("provide at least one VM property option or --file spec.json")
        return session.workflows.vm_update_action(args.name, data)

    def delete_action(args: Any, session: Any):
        return session.workflows.vm_delete_action(args.name, delete_zvols=args.delete_zvols)

    def device_action(args: Any, session: Any):
        attrs = _read_json_object(args.file)
        if args.type == "disk":
            if not args.zvol or args.size is None:
                raise CliFailure("--type disk requires --zvol NAME and --size BYTES")
            attrs.update(dtype="DISK", create_zvol=True, zvol_name=args.zvol, zvol_volsize=args.size)
        elif args.type == "iso":
            if not args.path:
                raise CliFailure("--type iso requires --path /mnt/POOL/path.iso")
            attrs.update(dtype="CDROM", path=args.path)
        elif args.type == "nic":
            attrs.update(dtype="NIC")
            if args.attach:
                attrs["nic_attach"] = args.attach
        return session.workflows.vm_device_create_action(args.name, attrs)

    device_registry = CliRegistry(ident, prog="tnt vm device", description="Attach a disk, ISO, NIC, or schema-complete v27 device.",
                                  unexpected_exceptions="report", expected_exceptions=(TntError,))
    device_registry.register(VerbSpec("add", None, "Add a VM device through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                                      arguments=(ArgumentSpec("name", "VM name.", metavar="NAME"),),
                                      options=writes(_value(("--type",), "Minimal device: disk, iso, or nic.", "TYPE", "WORKFLOW",
                                                            choices=("disk", "iso", "nic")),
                                                     _value(("--zvol",), "New zvol full name for a disk.", "NAME", "WORKFLOW"),
                                                     _value(("--size",), "New disk zvol size in bytes.", "BYTES", "WORKFLOW", type=int),
                                                     _value(("--path",), "ISO path under /mnt/POOL.", "PATH", "WORKFLOW"),
                                                     _value(("--attach",), "Host bridge/interface for a NIC.", "NAME", "WORKFLOW"), SPEC_FILE),
                                      handler=lambda args, runtime: _workflow_action(args, runtime, opener,
                                                                                     lambda session: device_action(args, session))))

    return [
        VerbSpec("list", None, "List virtual machines and their state.", group=VerbGroup.EXPLORATION.value, options=reads(),
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener, lambda session: session.workflows.vms())),
        VerbSpec("show", None, "Show one v27 virtual machine record.", group=VerbGroup.EXPLORATION.value,
                 arguments=(ArgumentSpec("name", "VM name.", metavar="NAME"),), options=reads(),
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener, lambda session: session.workflows.vm(args.name))),
        VerbSpec("create", None, "Create a VM record; then use `tnt vm device add` for its disk, ISO, and NIC.",
                 group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "VM name.", metavar="NAME"),),
                 options=writes(_value(("--memory",), "VM memory in MiB (minimum 20).", "MIB", "WORKFLOW", type=int),
                                _value(("--vcpus",), "Virtual CPU count (defaults to 1).", "COUNT", "WORKFLOW", type=int),
                                _value(("--disk-zvol",), "New disk zvol full name, such as tank/guests/guest0.", "NAME", "WORKFLOW"),
                                _value(("--disk-size",), "New disk zvol size in bytes.", "BYTES", "WORKFLOW", type=int),
                                _value(("--iso",), "Optional installer ISO path under /mnt/POOL.", "PATH", "WORKFLOW"),
                                _value(("--nic-attach",), "Optional host bridge/interface to attach.", "NAME", "WORKFLOW"),
                                _flag(("--autostart",), "Enable autostart.", "WORKFLOW"),
                                _flag(("--no-autostart",), "Disable autostart.", "WORKFLOW"), SPEC_FILE),
                 handler=create_workflow),
        VerbSpec("update", None, "Update VM resources and autostart through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "VM name.", metavar="NAME"),),
                 options=writes(_value(("--memory",), "VM memory in MiB.", "MIB", "WORKFLOW", type=int),
                                _value(("--vcpus",), "Virtual CPU count.", "COUNT", "WORKFLOW", type=int),
                                _flag(("--autostart",), "Enable autostart.", "WORKFLOW"),
                                _flag(("--no-autostart",), "Disable autostart.", "WORKFLOW"), SPEC_FILE),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener, lambda session: update_action(args, session))),
        *[VerbSpec(operation, None, f"{operation.title()} a virtual machine through a reviewed plan.", group=VerbGroup.MODIFICATION.value,
                   arguments=(ArgumentSpec("name", "VM name.", metavar="NAME"),), options=writes(),
                   handler=lambda args, runtime, op=operation: _workflow_action(
                       args, runtime, opener, lambda session: session.workflows.vm_lifecycle_action(args.name, op)))
          for operation in ("start", "stop", "restart", "poweroff", "suspend", "resume")],
        VerbSpec("delete", None, "Delete a VM; requires exact typed confirmation.", group=VerbGroup.MODIFICATION.value,
                 arguments=(ArgumentSpec("name", "VM name; type this exact name to confirm.", metavar="NAME"),),
                 options=writes(_flag(("--delete-zvols",), "Also delete VM-associated ZFS volumes.", "WORKFLOW")),
                 handler=lambda args, runtime: _workflow_action(args, runtime, opener, lambda session: delete_action(args, session))),
        VerbSpec("console", None, "Show the VM's VNC/SPICE console endpoint and display devices.", group=VerbGroup.EXPLORATION.value,
                 arguments=(ArgumentSpec("name", "VM name.", metavar="NAME"),), options=reads(),
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                               lambda session: session.workflows.vm_console(args.name))),
        VerbSpec("device", None, "Manage VM devices.", group=VerbGroup.MODIFICATION.value, delegate=device_registry.build()),
    ]


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
    _workflow_group(registry, ident, "dataset", "Manage datasets and zvols.", _dataset_verbs(ident, opener))
    _workflow_group(registry, ident, "snapshot", "Manage dataset snapshots.", _snapshot_verbs(ident, opener))
    _workflow_group(registry, ident, "user", "Manage local users and SSH public keys.", _user_verbs(ident, opener))
    _workflow_group(registry, ident, "container", "Manage TrueNAS containers.", _container_verbs(ident, opener))
    _workflow_group(registry, ident, "vm", "Manage virtual machines and their devices.", _vm_verbs(ident, opener))
    _workflow_group(registry, ident, "share", "Manage SMB and NFS shares.", _share_verbs(ident, opener))
    _workflow_group(registry, ident, "task", "Inspect and operate data-protection tasks.", _task_verbs(ident, opener))
    _workflow_group(registry, ident, "app", "Inspect and operate installed TrueNAS Apps.", _app_verbs(ident, opener))
    _workflow_group(registry, ident, "system", "Review and run guarded TrueNAS system actions.", _system_verbs(ident, opener))
    _workflow_group(registry, ident, "network", "Read network interfaces, addresses, routes, DNS, and gateways.", [
        VerbSpec("show", None, "Show live network status (read-only).", group=VerbGroup.EXPLORATION.value,
                 options=HOST_OPTIONS,
                 handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                              lambda session: session.workflows.network_show())),
    ])
    audit_filters = (
        _value(("--service",), "Audit database: MIDDLEWARE, S3, SMB, SUDO, or SYSTEM.", "SERVICE", "FILTER",
               choices=("MIDDLEWARE", "S3", "SMB", "SUDO", "SYSTEM"), default="MIDDLEWARE"),
        _value(("--since",), "Inclusive ISO-8601 lower time bound with UTC offset.", "TIMESTAMP", "FILTER"),
        _value(("--until",), "Exclusive ISO-8601 upper time bound with UTC offset.", "TIMESTAMP", "FILTER"),
        _value(("--user",), "Filter by audit username.", "USER", "FILTER"),
        _value(("--event",), "Filter by event name.", "EVENT", "FILTER"),
        _value(("--limit",), "Page size (1–10000).", "COUNT", "FILTER", type=int, default=100),
        _value(("--offset",), "Zero-based result offset.", "COUNT", "FILTER", type=int, default=0),
    )
    registry.register(VerbSpec("audit", None, "Query scrubbed, paginated audit entries (no general syslog-tail API exists in v27).",
                               group=VerbGroup.EXPLORATION.value, options=HOST_OPTIONS + audit_filters,
                               handler=lambda args, runtime: _workflow_read(
                                   args, runtime, opener,
                                   lambda session: session.workflows.audit_entries(
                                       service=args.service, since=args.since, until=args.until, user=args.user,
                                       event=args.event, limit=args.limit, offset=args.offset))))
    apikey_registry = CliRegistry(ident, prog="tnt apikey", description="Manage TrueNAS API keys.",
                                  unexpected_exceptions="report", expected_exceptions=expected)
    apikey_registry.register(VerbSpec(
        "list", None, "List API keys without displaying key verifier material.", group=VerbGroup.EXPLORATION.value,
        options=HOST_OPTIONS,
        handler=lambda args, runtime: _workflow_read(args, runtime, opener,
                                                     lambda session: session.workflows.api_keys())))
    apikey_registry.register(VerbSpec(
        "create", None, "Create an API key and reveal its raw value once in a warning-marked output.",
        group=VerbGroup.MODIFICATION.value,
        arguments=(ArgumentSpec("name", "Human-readable API key name (1–200 characters).", metavar="NAME"),
                   ArgumentSpec("username", "TrueNAS account that owns the key.", metavar="USERNAME")),
        options=HOST_OPTIONS + ACTION_OPTIONS + (
            _value(("--expires-at",), "Expiration time with a UTC offset (default: never expires).", "TIMESTAMP", "WORKFLOW"),
            _value(("--write-key",), "Optional new absolute file path; created with mode 0600, never overwritten.", "FILE", "WORKFLOW"),
        ),
        handler=lambda args, runtime: _api_key_create(args, runtime, opener)))
    apikey_registry.register(VerbSpec(
        "rename", None, "Rename an API key without resetting its secret.", group=VerbGroup.MODIFICATION.value,
        arguments=(ArgumentSpec("key_id", "API key ID.", metavar="ID", parser_kwargs={"type": int}),
                   ArgumentSpec("name", "New human-readable API key name.", metavar="NAME")),
        options=HOST_OPTIONS + ACTION_OPTIONS,
        handler=lambda args, runtime: _workflow_action(
            args, runtime, opener, lambda session: session.workflows.api_key_rename_action(args.key_id, args.name))))
    apikey_registry.register(VerbSpec(
        "revoke", None, "Revoke and remove a key via v27 api_key.delete; type its current name to confirm.",
        group=VerbGroup.MODIFICATION.value,
        arguments=(ArgumentSpec("key_id", "API key ID.", metavar="ID", parser_kwargs={"type": int}),),
        options=HOST_OPTIONS + ACTION_OPTIONS,
        handler=lambda args, runtime: _workflow_action(
            args, runtime, opener, lambda session: session.workflows.api_key_revoke_action(args.key_id))))
    registry.register(VerbSpec("apikey", None, "List, create, rename, and revoke API keys.",
                               group=VerbGroup.EXPLORATION.value, delegate=apikey_registry.build()))
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)
