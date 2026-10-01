"""Interactive, validated configuration wizard for Debian install v2."""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cli_extended import CliFailure, LogLevel

from .config import Config, ConfigError, load_config


@dataclass(frozen=True)
class WizardField:
    name: str
    prompt: str
    kind: str = "text"
    choices: tuple[str, ...] = ()
    secret: bool = False


@dataclass(frozen=True)
class WizardSection:
    key: str
    title: str
    fields: tuple[WizardField, ...]


WIZARD_SECTIONS = (
    WizardSection(
        "install",
        "Install behavior and paths",
        (
            WizardField("log_dir", "Installer log directory"),
            WizardField("state_dir", "Persistent installer state directory"),
            WizardField("stage2_output", "Stage-two output log"),
            WizardField("auto_reboot_after_stage1", "Reboot automatically after stage one?", "boolean"),
            WizardField("never_reboot", "Forbid installer-scheduled reboots?", "boolean"),
        ),
    ),
    WizardSection(
        "components",
        "Installation components",
        tuple(
            WizardField(name, prompt, "boolean")
            for name, prompt in (
                ("run_user_config", "Configure root and operator defaults?"),
                ("run_apt_config", "Configure Debian APT sources and policy?"),
                ("run_journald_config", "Configure persistent journald logging?"),
                ("run_docker_install", "Install Docker?"),
                ("run_ksm", "Enable kernel same-page merging (KSM)?"),
                ("run_oomd_config", "Configure systemd-oomd?"),
                ("run_fstrim", "Enable periodic filesystem trim?"),
                ("run_docker_cleanup", "Enable scheduled Docker image cleanup?"),
                ("run_apt_auto_upgrade", "Enable unattended APT upgrades?"),
                ("run_auto_reboot", "Enable scheduled reboot-window checks?"),
            )
        ),
    ),
    WizardSection(
        "swap-memory",
        "Swap and memory policy",
        (
            WizardField("swap_disk_total_gb", "Total swap capacity in GiB", "integer"),
            WizardField("swap_file_count", "Number of swap partitions", "integer"),
            WizardField("swap_priority", "Linux swap priority", "integer"),
            WizardField("swap_discard", "Enable discard on swap?", "boolean"),
            WizardField("preserve_root_size_gb", "Root filesystem space to preserve (GiB)", "integer"),
            WizardField("zswap_compressor", "zswap compressor", "choice", ("zstd", "lz4", "lzo-rle")),
            WizardField("zswap_zpool", "zswap memory allocator", "choice", ("z3fold", "zbud", "zsmalloc")),
            WizardField("zswap_pool_percent", "Maximum zswap pool (% of RAM)", "integer"),
            WizardField("vm_swappiness", "vm.swappiness (0–100)", "integer"),
        ),
    ),
    WizardSection(
        "docker",
        "Docker behavior and logs",
        (
            WizardField("docker_live_restore", "Keep containers running across daemon restarts?", "boolean"),
            WizardField("docker_log_driver", "Docker log driver"),
            WizardField("docker_log_max_size", "Per-file limit for drivers that support it"),
            WizardField("docker_log_max_file", "Number of retained files for drivers that support it"),
            WizardField("docker_cleanup_max_age_hours", "Maximum age for unused images (hours)", "integer"),
        ),
    ),
    WizardSection(
        "updates",
        "Updates and reboot window",
        (
            WizardField("apt_auto_upgrade_mode", "Automatic-upgrade mode", "choice", ("full", "security-only", "notify-only")),
            WizardField("reboot_window_time", "Preferred reboot window (24-hour HH:MM)"),
        ),
    ),
    WizardSection(
        "notifications",
        "Telegram notifications and credentials",
        (
            WizardField("telegram_bot_token", "Telegram bot token (blank disables notifications)", "secret", secret=True),
            WizardField("telegram_chat_id", "Telegram chat ID (required with a bot token)"),
            WizardField("telegram_verbose_progress", "Send a notification for every internal step?", "boolean"),
            WizardField("credential_mode", "Credential delivery to stage two", "choice", ("root-storage", "systemd")),
        ),
    ),
    WizardSection(
        "controller-ssh",
        "Temporary controller SSH access",
        (
            WizardField(
                "controller_ssh_pubkey",
                "One authorized_keys public-key line (blank disables temporary access)",
            ),
        ),
    ),
    WizardSection(
        "benchmark",
        "Optional I/O-cost benchmark",
        (
            WizardField("run_io_benchmark", "Run the destructive temporary-partition benchmark?", "boolean"),
            WizardField("io_benchmark_duration_s", "Duration per benchmark phase (seconds)", "integer"),
            WizardField("io_benchmark_max_size_gb", "Maximum temporary benchmark partition (GiB)", "integer"),
        ),
    ),
)

_FIXED_FIELDS = {"schema_version", "fresh_install"}
_SECRET_FIELDS = {"telegram_bot_token"}
_SUMMARY_HIDDEN_FIELDS = _SECRET_FIELDS | {"controller_ssh_pubkey"}
_WIZARD_REQUIREMENTS = Path(__file__).resolve().parents[1] / "wizard-requirements.txt"


def wizard_field_names() -> frozenset[str]:
    """Expose the reviewed field coverage for tests and generated help."""
    return frozenset(field.name for section in WIZARD_SECTIONS for field in section.fields)


def _load_questionary():
    try:
        import questionary
    except ImportError as exc:
        raise CliFailure(
            "the configuration wizard cannot load its optional "
            "Questionary prompt dependency",
            exit_code=2,
            hint=(
                f"install it with: {shlex.quote(sys.executable)} -m pip install "
                f"-r {_WIZARD_REQUIREMENTS}"
            ),
        ) from exc
    return questionary


def _ask_field(questionary: Any, field: WizardField, current: Any, runtime: Any) -> Any:
    prompt = field.prompt
    if field.secret:
        prompt += " (input is hidden; Enter keeps the current value)"
    elif field.name == "controller_ssh_pubkey":
        prompt += " (Enter keeps the current value)"

    while True:
        if field.kind == "boolean":
            answer = questionary.confirm(prompt, default=bool(current)).ask(
                kbi_msg=""
            )
        elif field.kind == "choice":
            answer = questionary.select(
                prompt, choices=list(field.choices), default=current
            ).ask(kbi_msg="")
        elif field.kind == "secret":
            answer = questionary.password(prompt, default=str(current)).ask(
                kbi_msg=""
            )
        else:
            answer = questionary.text(prompt, default=str(current)).ask(kbi_msg="")

        if answer is None:
            raise KeyboardInterrupt
        if field.kind != "integer":
            return answer.strip() if isinstance(answer, str) else answer
        try:
            return int(answer)
        except (TypeError, ValueError):
            runtime.output.warn(f"{field.name} must be an integer; please try again")


def _summary(config: Config) -> str:
    values = asdict(config)
    lines = [
        "Configuration to write (secret values are not shown):",
        "  schema_version: 1",
        "  fresh_install: true (required by this v2 release)",
    ]
    for section in WIZARD_SECTIONS:
        lines.append(f"  {section.title}:")
        for field in section.fields:
            value = values[field.name]
            if field.name in _SUMMARY_HIDDEN_FIELDS:
                shown = "<configured>" if value else "<not set>"
            elif isinstance(value, bool):
                shown = "yes" if value else "no"
            else:
                shown = str(value)
            lines.append(f"    {field.name}: {shown}")
    return "\n".join(lines)


def _select_sections(questionary: Any, selected: set[str] | tuple[str, ...] = ()):
    choices = [
        {
            "name": section.title,
            "value": section.key,
            "checked": section.key in selected,
        }
        for section in WIZARD_SECTIONS
    ]
    return questionary.checkbox(
        "Choose sections to customize; unselected sections keep their current values:",
        choices=choices,
    ).ask(kbi_msg="")


def run_configuration_wizard(
    *,
    output_path: str,
    from_config: str | None,
    runtime: Any,
    questionary_module: Any | None = None,
    save: Callable[..., None],
) -> int:
    """Collect selected config sections, validate with the shipped loader, save."""
    if not runtime.output.is_interactive:
        raise CliFailure(
            "wizard requires an interactive terminal",
            exit_code=2,
            hint=(
                "run wizard in a terminal; for scripted setup, create JSON and "
                "run plan --config FILE on the intended target to validate and "
                "inspect it without applying changes"
            ),
        )

    questionary = questionary_module or _load_questionary()
    if from_config:
        try:
            current_config = load_config(path=from_config)
        except ConfigError as exc:
            raise CliFailure(f"cannot use starting configuration: {exc}", exit_code=2) from exc
    else:
        current_config = Config()
    values = asdict(current_config)

    selected = _select_sections(questionary)
    if selected is None:
        raise KeyboardInterrupt

    while True:
        for section in WIZARD_SECTIONS:
            if section.key not in selected:
                continue
            for field in section.fields:
                values[field.name] = _ask_field(
                    questionary, field, values[field.name], runtime
                )
        try:
            config = load_config(raw_json=json.dumps(values))
            break
        except ConfigError as exc:
            runtime.output.warn(
                f"configuration is not valid ({exc}); choose the section(s) to review"
            )
            selected = _select_sections(questionary, set(selected))
            if selected is None:
                raise KeyboardInterrupt
            if not selected:
                raise CliFailure(
                    f"configuration is invalid: {exc}",
                    exit_code=2,
                    hint="rerun the wizard and select the sections related to this setting",
                ) from exc

    runtime.output.emit(LogLevel.INFO, _summary(config), force=True)
    if not runtime.confirm(f"Write the validated configuration to {output_path}?"):
        return 0
    try:
        # runtime.confirm() above is the explicit authorization to replace an
        # existing file; --yes is only the non-interactive way to provide it.
        save(output_path, config, overwrite=True)
    except (OSError, ConfigError) as exc:
        raise CliFailure(f"could not save configuration: {exc}") from exc
    runtime.output.info(f"Saved mode-0600 configuration to {output_path}.")
    return 0


def uncovered_config_fields() -> frozenset[str]:
    """Return Config fields that are neither fixed nor promptable."""
    from dataclasses import fields

    all_fields = {item.name for item in fields(Config)}
    return frozenset(all_fields - _FIXED_FIELDS - wizard_field_names())
