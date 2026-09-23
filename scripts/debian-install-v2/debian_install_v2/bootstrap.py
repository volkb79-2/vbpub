from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from cli_extended import (
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)

from . import __version__
from .actions import ActionError, HostActions
from .config import Config, ConfigError, load_config, save_config
from .customscript import build_customscript_bundle
from .installer import Installer, InstallerError
from .state import StateError, StateStore

PROG = "debian-install-v2"
IDENTITY = CliIdentity(
    name="DEBIAN-INSTALL-V2",
    command=PROG,
    version=__version__,
    long_name="Debian host installer",
)


def _config_options() -> tuple[OptionSpec, ...]:
    return (
        OptionSpec(
            ("--config",),
            "read a validated JSON configuration file (preferred over inline JSON)",
            group="CONFIGURATION",
            metavar="FILE",
            mutually_exclusive_group="configuration-source",
            mutually_exclusive_required=True,
        ),
        OptionSpec(
            ("--config-json",),
            "read a JSON object from this argument; avoid secrets because argv may be visible",
            group="CONFIGURATION",
            metavar="JSON",
            mutually_exclusive_group="configuration-source",
            mutually_exclusive_required=True,
        ),
    )


def _dry_run_option() -> OptionSpec:
    return OptionSpec(
        ("--dry-run",),
        "record intended host operations without executing them",
        group="EXECUTION",
        parser_kwargs={"action": "store_true", "default": False},
    )


def _load_config(args: Any, runtime: Any) -> Config:
    config_path = getattr(args, "config", None)
    config_json = getattr(args, "config_json", None)
    if not config_path and not config_json:
        raise CliFailure(
            "this verb requires --config FILE or --config-json JSON",
            exit_code=2,
            show_help=True,
        )
    try:
        config = load_config(config_path, config_json)
    except ConfigError as exc:
        raise CliFailure(f"invalid installation configuration: {exc}", exit_code=2) from exc
    if config.telegram_bot_token:
        runtime.output.secrets = (*runtime.output.secrets, config.telegram_bot_token)
    return config


def _stage2_config(state_dir: str) -> Config:
    """Load and validate the persisted stage-two config, not a partial dict."""
    manifest = StateStore(state_dir).load()
    saved = manifest.get("config")
    if not isinstance(saved, dict):
        raise StateError("state manifest does not contain a configuration object")
    # Credentials are delivered separately through systemd credentials or the
    # root-only credentials directory. Older v2 manifests persisted only the
    # Telegram chat id, so discard both halves before strict config validation.
    config_data = {
        key: value
        for key, value in saved.items()
        if key not in {"telegram_bot_token", "telegram_chat_id"}
    }
    try:
        config = load_config(raw_json=json.dumps(config_data))
    except ConfigError as exc:
        raise StateError(f"saved installation configuration is invalid: {exc}") from exc
    if config.state_dir != state_dir:
        raise StateError(
            "VBPUB_STATE_DIR does not match state_dir in the saved configuration"
        )
    return config


def _make_installer(
    args: Any, runtime: Any, *, inspect_host: bool = True
) -> Installer:
    config = _load_config(args, runtime)
    return Installer(
        config,
        HostActions(dry_run=bool(getattr(args, "dry_run", False))),
        inspect_host=inspect_host,
    )


def _make_resume_installer(args: Any) -> Installer:
    state_dir = os.environ.get("VBPUB_STATE_DIR", "")
    if not state_dir.startswith("/"):
        raise CliFailure(
            "VBPUB_STATE_DIR must be set to an absolute path for resume",
            exit_code=2,
            show_help=True,
        )
    try:
        config = _stage2_config(state_dir)
    except StateError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    return Installer(config, HostActions(dry_run=bool(getattr(args, "dry_run", False))))


def _render_status(status: dict[str, Any]) -> str:
    lines = [
        f"status: {status.get('status', 'unknown')}",
        f"phase: {status.get('phase', 'unknown')}",
        f"run_id: {status.get('run_id', 'unknown')}",
        f"started_at: {status.get('started_at', 'unknown')}",
        f"dry_run: {'yes' if status.get('dry_run') else 'no'}",
        f"planned_actions: {status.get('planned_action_count', 0)}",
    ]
    last_error = status.get("last_error")
    if last_error:
        lines.append(f"last_error: {last_error}")
    steps = status.get("steps")
    if isinstance(steps, dict) and steps:
        lines.append("steps:")
        for name, value in sorted(steps.items()):
            if isinstance(value, dict):
                detail = value.get("detail", "")
                tail = f" — {detail}" if detail else ""
                lines.append(f"  {name}: {value.get('status', 'unknown')}{tail}")
            else:
                lines.append(f"  {name}: {value}")
    logs = status.get("logs")
    if isinstance(logs, list) and logs:
        lines.append("recent_logs:")
        lines.extend(f"  {path}" for path in logs)
    return "\n".join(lines)


def _render_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"Release: {plan['release']}",
        f"Root device: {plan['root_device']}",
        f"New root size: {plan['new_root_size_sectors']} sectors",
        "Swap partitions:",
    ]
    for swap in plan["swap_partitions"]:
        lines.append(
            f"  {swap['device']}: start={swap['start']}, sectors={swap['sectors']}"
        )
    lines.extend(("Partition-table plan:", plan["sfdisk_plan"].rstrip()))
    return "\n".join(lines)


def _planned_actions(actions: HostActions) -> list[dict[str, Any]]:
    return [asdict(action) for action in actions.planned]


def _wizard(args: Any, runtime: Any) -> int:
    from .wizard import run_configuration_wizard

    return run_configuration_wizard(
        output_path=args.output,
        from_config=getattr(args, "from_config", None),
        runtime=runtime,
        save=save_config,
    )


def _install(args: Any, runtime: Any) -> int:
    installer = _make_installer(args, runtime)
    actions = installer.actions
    plan = installer.show_plan()
    if not actions.dry_run:
        runtime.output.emit(
            "info",
            "This fresh install will modify the root disk and create the configured swap layout.\n"
            + _render_plan(plan),
            force=True,
        )
        if not runtime.confirm("Start this Debian host installation?"):
            return 0

    installer.install()
    if actions.dry_run:
        runtime.output.primary_json({"result": "planned", "actions": _planned_actions(actions)})
    else:
        runtime.output.info("Stage-one installation completed; stage two is scheduled as configured.")
    return 0


def _resume(args: Any, runtime: Any) -> int:
    installer = _make_resume_installer(args)
    if not installer.actions.dry_run and not runtime.confirm(
        "Resume stage two and apply the remaining host changes?"
    ):
        return 0
    installer.resume()
    if installer.actions.dry_run:
        runtime.output.primary_json(
            {"result": "planned", "actions": _planned_actions(installer.actions)}
        )
    else:
        runtime.output.info("Stage-two installation completed.")
    return 0


def _status(args: Any, runtime: Any) -> int:
    status = _make_installer(args, runtime, inspect_host=False).status()
    if runtime.json_mode:
        runtime.output.primary(status)
    else:
        runtime.output.primary(_render_status(status))
    return 0


def _verify(args: Any, runtime: Any) -> int:
    _make_installer(args, runtime, inspect_host=False).verify()
    runtime.output.info("Disk transaction and post-install health checks passed.")
    return 0


def _disable_stage2(args: Any, runtime: Any) -> int:
    installer = _make_installer(args, runtime, inspect_host=False)
    if not installer.actions.dry_run and not runtime.confirm(
        "Disable the automatic stage-two service and mark it complete?"
    ):
        return 0
    installer.disable_stage2()
    runtime.output.info("Stage-two service disabled and completion marker recorded.")
    return 0


def _plan(args: Any, runtime: Any) -> int:
    plan = _make_installer(args, runtime).show_plan()
    if runtime.json_mode:
        runtime.output.primary(plan)
    else:
        runtime.output.primary(_render_plan(plan))
    return 0


def _build_customscript(args: Any, runtime: Any) -> int:
    config = _load_config(args, runtime)
    try:
        bundle = build_customscript_bundle(
            config,
            repo_url=getattr(args, "repo_url", None),
            repo_branch=getattr(args, "repo_branch", None),
            bootstrap_url=getattr(args, "bootstrap_url", None),
            controller_ssh_placeholder=getattr(
                args, "controller_ssh_placeholder", False
            ),
        )
    except (ValueError, ConfigError) as exc:
        raise CliFailure(str(exc), exit_code=2, show_help=True) from exc
    if config.telegram_bot_token and not runtime.debug_raw:
        raise CliFailure(
            "the generated bundle contains the Telegram bot token and must not be redacted",
            exit_code=2,
            hint=(
                "write it to a protected file with umask 077, or explicitly use "
                "--debug-raw and protect stdout yourself"
            ),
        )
    runtime.output.primary_json(bundle)
    return 0


def build_cli():
    registry = CliRegistry(
        IDENTITY,
        prog=f"{PROG}.py",
        description=(
            "Prepare settings, review the host plan, run or monitor the two-stage "
            "Debian installer, and build its cloud-init bundle."
        ),
        getting_started=(
            f"{PROG}.py wizard --output install.json",
            f"{PROG}.py plan --config install.json",
            f"{PROG}.py install --config install.json",
        ),
        logging_logger="debian_install_v2",
    )

    configuration = _config_options()
    registry.register(
        VerbSpec(
            name="wizard",
            description="guide an operator through install settings and securely write JSON; requires Questionary from wizard-requirements.txt",
            summary_description="interactively create validated installation settings",
            group=VerbGroup.MODIFICATION.value,
            examples=(
                f"{PROG}.py wizard --output install.json",
                f"{PROG}.py wizard --output install.json --from-config previous.json",
            ),
            mutating=True,
            interactive=True,
            include_json=False,
            include_progress=False,
            options=(
                OptionSpec(
                    ("--output",),
                    "destination for the mode-0600 validated configuration",
                    group="CONFIGURATION",
                    metavar="FILE",
                    parser_kwargs={"required": True},
                ),
                OptionSpec(
                    ("--from-config",),
                    "validated configuration to use as the starting point",
                    group="CONFIGURATION",
                    metavar="FILE",
                ),
            ),
            handler=_wizard,
        )
    )
    registry.register(
        VerbSpec(
            name="install",
            description="run stage one of a fresh Debian host installation",
            group=VerbGroup.MODIFICATION.value,
            examples=(
                f"{PROG}.py install --config install.json --dry-run",
                f"{PROG}.py install --config install.json --yes",
            ),
            mutating=True,
            expensive=True,
            include_json=False,
            include_progress=False,
            options=(*configuration, _dry_run_option()),
            handler=_install,
        )
    )
    registry.register(
        VerbSpec(
            name="resume",
            description="continue stage two after reboot from state under VBPUB_STATE_DIR (normally systemd-invoked)",
            group=VerbGroup.MODIFICATION.value,
            examples=(f"{PROG}.py resume --yes",),
            mutating=True,
            expensive=True,
            include_json=False,
            include_progress=False,
            options=(_dry_run_option(),),
            handler=_resume,
        )
    )
    registry.register(
        VerbSpec(
            name="status",
            description=(
                "show persisted installation status, recorded steps, and paths to "
                "recent logs"
            ),
            summary_description="show installation status",
            group=VerbGroup.EXPLORATION.value,
            examples=(f"{PROG}.py status --config install.json",),
            include_json=True,
            include_progress=False,
            options=configuration,
            handler=_status,
        )
    )
    registry.register(
        VerbSpec(
            name="verify",
            description="run live checks for the saved disk transaction and post-install health gates; does not write state",
            group=VerbGroup.EXPLORATION.value,
            examples=(f"{PROG}.py verify --config install.json",),
            include_json=False,
            include_progress=False,
            options=configuration,
            handler=_verify,
        )
    )
    registry.register(
        VerbSpec(
            name="plan",
            description="read the target host and show its root-disk/swap plan without applying changes",
            group=VerbGroup.EXPLORATION.value,
            examples=(
                f"{PROG}.py plan --config install.json",
                f"{PROG}.py plan --config install.json --json",
            ),
            include_json=True,
            include_progress=False,
            options=configuration,
            handler=_plan,
        )
    )
    registry.register(
        VerbSpec(
            name="disable-stage2",
            description="disable the automatic stage-two service and mark it complete",
            group=VerbGroup.MAINTENANCE.value,
            examples=(
                f"{PROG}.py disable-stage2 --config install.json",
                f"{PROG}.py disable-stage2 --config install.json --yes",
            ),
            mutating=True,
            include_json=False,
            include_progress=False,
            options=(*configuration, _dry_run_option()),
            handler=_disable_stage2,
        )
    )
    registry.register(
        VerbSpec(
            name="build-customscript",
            description="render validated settings and a provider-neutral cloud-init bootstrap command as JSON",
            group=VerbGroup.EXPLORATION.value,
            examples=(
                f"{PROG}.py build-customscript --config install.json --controller-ssh-placeholder",
                f"umask 077 && {PROG}.py build-customscript --config install.json --debug-raw > bundle.json",
            ),
            include_json=False,
            include_progress=False,
            options=(
                *configuration,
                OptionSpec(
                    ("--repo-url",),
                    "repository containing bootstrap-remote.py",
                    group="BOOTSTRAP SOURCE",
                    metavar="URL",
                    parser_kwargs={"default": "https://github.com/volkb79-2/vbpub"},
                ),
                OptionSpec(
                    ("--repo-branch",),
                    "branch fetched by bootstrap-remote.py",
                    group="BOOTSTRAP SOURCE",
                    metavar="BRANCH",
                    parser_kwargs={"default": "main"},
                ),
                OptionSpec(
                    ("--bootstrap-url",),
                    "explicit bootstrap-remote.py URL (required for a custom repository)",
                    group="BOOTSTRAP SOURCE",
                    metavar="URL",
                ),
                OptionSpec(
                    ("--controller-ssh-placeholder",),
                    "emit {{CONTROLLER_SSH_PUBKEY}} for a provider to replace at install time",
                    group="SSH KEY INTEGRATION",
                    parser_kwargs={"action": "store_true", "default": False},
                ),
            ),
            handler=_build_customscript,
        )
    )
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(
        argv=argv,
        expected_exceptions=(
            ActionError,
            ConfigError,
            InstallerError,
            OSError,
            StateError,
            json.JSONDecodeError,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
