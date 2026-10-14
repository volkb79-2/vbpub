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

from .config import (
    CLOUD_INIT_CLEANUP_MODES,
    Config,
    ConfigError,
    load_config,
    validate_authorized_key_line,
)


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
            WizardField("log_dir", "Legacy log directory (not written by v2; logs are custom_script.output*, stage2 output, journald)"),
            WizardField("state_dir", "Persistent installer state directory"),
            WizardField("stage2_output", "Stage-two output log"),
            WizardField("auto_reboot_after_stage1", "Reboot automatically after stage one?", "boolean"),
            WizardField("never_reboot", "Forbid installer-scheduled reboots?", "boolean"),
            WizardField(
                "cloud_init_cleanup_mode",
                "Cloud-init cache policy after successful install (clean modes run cloud-init again on next boot)",
                "choice",
                CLOUD_INIT_CLEANUP_MODES,
            ),
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
                ("run_zswap_config", "Configure zswap and related memory tuning?"),
                ("run_cgroup2_config", "Configure cgroup2 mount flags?"),
            )
        ),
    ),
    WizardSection(
        "swap-memory",
        "Root, swap, and memory policy",
        (
            WizardField("root_resize_policy", "Root partition policy", "choice", ("resize", "untouched", "grow-only")),
            WizardField("swap_policy", "Swap policy", "choice", ("on", "off", "untouched")),
            WizardField("swap_disk_total_gb", "Total swap capacity in GiB (blank or auto derives from disk and RAM)", "optional-integer"),
            WizardField("swap_partition_count", "Number of swap partitions", "integer"),
            WizardField("swap_priority", "Linux swap priority", "integer"),
            WizardField("swap_discard", "Enable discard on swap?", "boolean"),
            WizardField("preserve_root_size_gb", "Root filesystem space to preserve (GiB)", "integer"),
            WizardField("zswap_compressor", "zswap compressor", "choice", ("zstd", "lz4", "lzo-rle")),
            WizardField("zswap_pool_percent", "Maximum zswap pool (% of RAM)", "integer"),
            WizardField("zswap_accept_threshold_percent", "zswap accept threshold (% of pool; 0–100)", "integer"),
            WizardField("zswap_shrinker_enabled", "Enable the zswap writeback shrinker?", "boolean"),
            WizardField("vm_swappiness", "vm.swappiness (0–200)", "integer"),
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
            WizardField(
                "docker_default_address_pools",
                "Docker default address pools as a JSON list of {base, size} (empty list = Docker built-in default)",
                "json",
            ),
            WizardField("docker_cleanup_max_age_hours", "Maximum age for unused images (hours)", "integer"),
        ),
    ),
    WizardSection(
        "updates",
        "Updates and reboot window",
        (
            WizardField("apt_auto_upgrade_mode", "Automatic-upgrade mode", "choice", ("full", "security-only", "notify-only")),
            WizardField("apt_upgrade_at_install", "Run one unattended upgrade during the install (before the stage1 reboot)?", "boolean"),
            WizardField("reboot_window_time", "Preferred reboot window (24-hour HH:MM)"),
        ),
    ),
    WizardSection(
        "notifications",
        "Notifications (Mattermost / Telegram) and credentials",
        (
            WizardField(
                "notify_backend", "Notification backend (auto = infer from the credential given)",
                "choice", ("auto", "mattermost", "telegram", "none"),
            ),
            WizardField("mattermost_webhook_url", "Mattermost incoming-webhook URL (blank = none)", "secret", secret=True),
            WizardField("notify_host_label", "Host label shown first in each Mattermost message (blank = hostname only)"),
            WizardField("telegram_bot_token", "Telegram bot token (blank disables notifications)", "secret", secret=True),
            WizardField("telegram_chat_id", "Telegram chat ID (required with a bot token)"),
            WizardField("telegram_verbose_progress", "Send a notification for every internal step?", "boolean"),
            WizardField("credential_mode", "Credential delivery to stage two", "choice", ("root-storage", "systemd")),
        ),
    ),
    WizardSection(
        "persistent-ssh",
        "Persistent root SSH access",
        (
            WizardField(
                "ssh_authorized_keys",
                "Persistent root SSH public keys (one authorized_keys line each)",
                "ssh-keys",
            ),
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
            WizardField(
                "retain_controller_ssh_key",
                "Keep the controller key on the host after a successful install?",
                "boolean",
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
            WizardField("iocost_enabled", "Configure io.cost for the root disk when a benchmark result exists?", "boolean"),
        ),
    ),
)

_FIXED_FIELDS = {"schema_version", "fresh_install"}
_SECRET_FIELDS = {"telegram_bot_token", "mattermost_webhook_url"}
_SUMMARY_HIDDEN_FIELDS = _SECRET_FIELDS | {"controller_ssh_pubkey", "ssh_authorized_keys"}
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

    if field.kind == "ssh-keys":
        replace_keys = questionary.confirm(
            "Replace the configured persistent root SSH key list?",
            default=False,
        ).ask(kbi_msg="")
        if replace_keys is None:
            raise KeyboardInterrupt
        if not replace_keys:
            return list(current)
        collected: list[str] = []
        while True:
            answer = questionary.text(
                "SSH public-key line (blank finishes the list)", default=""
            ).ask(kbi_msg="")
            if answer is None:
                raise KeyboardInterrupt
            line = answer.strip()
            if not line:
                break
            try:
                validate_authorized_key_line(line, field=f"ssh_authorized_keys[{len(collected)}]")
                if any(existing.split()[:2] == line.split()[:2] for existing in collected):
                    raise ConfigError("this public key was already entered")
            except ConfigError as exc:
                runtime.output.warn(f"{exc}; please try again")
                continue
            collected.append(line)
        return collected

    while True:
        if field.kind == "boolean":
            answer = questionary.confirm(prompt, default=bool(current)).ask(
                kbi_msg=""
            )
        elif field.kind == "choice":
            auto = field.name == "notify_backend"  # "auto" stands for the unset ("") value
            answer = questionary.select(
                prompt, choices=list(field.choices), default=(current or "auto") if auto else current
            ).ask(kbi_msg="")
            if auto and answer == "auto":
                answer = ""
        elif field.kind == "secret":
            answer = questionary.password(prompt, default=str(current)).ask(
                kbi_msg=""
            )
        elif field.kind == "optional-integer":
            default = "auto" if current is None else str(current)
            answer = questionary.text(prompt, default=default).ask(kbi_msg="")
        else:
            default = json.dumps(current) if field.kind == "json" else str(current)
            answer = questionary.text(prompt, default=default).ask(kbi_msg="")

        if answer is None:
            raise KeyboardInterrupt
        if field.kind == "json":
            try:
                return json.loads(answer)
            except (TypeError, ValueError):
                runtime.output.warn(f"{field.name} must be valid JSON; please try again")
                continue
        if field.kind == "optional-integer":
            normalized = str(answer).strip().lower()
            if normalized in {"", "auto"}:
                return None
            try:
                return int(normalized)
            except (TypeError, ValueError):
                runtime.output.warn(f"{field.name} must be an integer, blank, or auto; please try again")
                continue
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
            elif field.name == "swap_disk_total_gb" and value is None:
                shown = "auto (disk and RAM)"
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
