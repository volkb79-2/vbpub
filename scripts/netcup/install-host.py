#!/usr/bin/env python3
"""Install a selected Netcup SCP image and optionally consume a customScript.

netcup SCP API docs (see `netcup-scp-openapi.json`, last synced 2026-09-07 from
upstream version 2026.0902.083309 - re-fetch periodically, the endpoint below
is public/unauthenticated despite the header in this example):
```
curl 'https://www.servercontrolpanel.de/scp-core/api/v1/openapi' -H "Authorization: Bearer ${ACCESS_TOKEN}"
```
As of the 2026-09-07 sync, the diff from the prior stored spec (2025.1230.142745)
was purely additive and non-breaking for the endpoints this script uses: new
`GET /api/v1/servers/{serverId}/guest-agent/status`, new
`GET /api/v1/servers/{serverId}/gpu-driver`, a new `firewallPolicyId` filter on
`GET /api/v1/servers`, and a new `gpuDriverAvailable` field on `Server`.

Get API access / Authentication:

Run `scp-api.py login` - it automates the whole OAuth2 device-code dance below
and writes the resulting refresh token straight into .env. See the shared
`netcup_scp_client.run_device_code_login()` helper's docstring for the manual
curl-by-curl equivalent (useful if you ever
need to debug the flow itself, or the automated version breaks).

Once a refresh token exists (in .env or $NETCUP_SCP_API_REFRESH_TOKEN), this
script handles getting/refreshing short-lived access tokens itself for every
other mode - no further manual steps needed.
"""

import os
import sys
import json
import re
import shlex
import socket
import argparse
import logging
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.request
from typing import Optional, Dict, Any, List, Set
from datetime import datetime
from pathlib import Path

import netcup_scp_client
from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)
# _load_env_file/_write_env_file have no call site left in this file's own
# code (load_env_file() below covers the real runtime need) -- they stay
# imported anyway because tests/test_scp_api_install_host.py calls them as
# install_host_mod._load_env_file(...)/._write_env_file(...) directly, the
# same re-export-for-test-access pattern already used for get_access_token/
# NetcupSCPClient/HTTPStatusError. Don't remove as "dead" without checking
# the test file first (caught live, 2026-09-09: removing them broke 5 tests).
from netcup_scp_client import (
    HTTPStatusError,
    NetcupSCPClient,
    _load_env_file,
    _load_settings,
    _redact_for_log,
    _write_env_file,
    get_access_token,
    load_env_file,
)


def _normalize_ssh_public_key(public_key: str) -> str:
    """Normalize key material for diagnostics; the installer never registers it."""
    parts = public_key.strip().split()
    return f"{parts[0]} {parts[1]}" if len(parts) >= 2 else public_key.strip()


def _read_public_key_for_identity(identity_file: str) -> str:
    """Read/derive the public key for a private key identity file.

    Derive the public key from the private key itself.  An adjacent ``.pub``
    file is not authoritative: it can be stale or can exist next to a file
    which is not a private key at all.  The derived key is therefore what we
    use for explicit-key validation and host-key discovery.
    """
    identity_path = Path(identity_file).expanduser()
    if not identity_path.exists():
        raise FileNotFoundError(f"SSH identity file not found: {identity_path}")

    result = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(identity_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    derived = result.stdout.strip()
    pub_path = identity_path.with_suffix(identity_path.suffix + ".pub")
    if pub_path.is_file():
        adjacent = pub_path.read_text(encoding="utf-8").strip()
        adjacent_parts = adjacent.split()
        derived_parts = derived.split()
        if len(adjacent_parts) >= 2 and len(derived_parts) >= 2 and adjacent_parts[:2] == derived_parts[:2]:
            return adjacent
    return derived


_PLACEHOLDER_CONTROLLER_FQDN_SUFFIXES = (".invalid", ".example.invalid")

# Same service netcup-api-filter's own shell aliases use for this
# (`alias myip='curl -s ifconfig.me'`, .devcontainer/finalize.post.d/10-netcup-setup.sh) -
# reused here so "automatic" resolves the same way a human checking manually would.
_PUBLIC_IP_SERVICE_URL = "https://ifconfig.me/ip"


def _detect_public_ip(timeout: float = 5.0) -> Optional[str]:
    """Best-effort: this controller's own public/outbound IPv4.

    Returns None on any failure (no network, service down, unexpected body) -
    callers fall back to something else rather than raising.
    """
    try:
        req = urllib.request.Request(_PUBLIC_IP_SERVICE_URL, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ip = resp.read().decode("utf-8", errors="replace").strip()
        if ip.count(".") == 3 and all(part.isdigit() for part in ip.split(".")):
            return ip
    except Exception:
        pass
    return None


def _reverse_dns(ip: str) -> Optional[str]:
    return netcup_scp_client.reverse_dns(ip)


def _resolve_controller_fqdn(controller_fqdn: str) -> str:
    """Resolve install-host.toml's [ssh] controller_fqdn setting.

    The key this produces a comment for lives in *authorized_keys on the
    installed host* - so what's actually meaningful there is how that host
    sees us: the public IP we'll connect from, reverse-DNS'd if possible.
    Not this controller's own internal/container hostname, which means
    nothing to anyone looking at authorized_keys on a different machine.

    "automatic" therefore: (1) detects our public IP the same way a human
    would (`curl -s ifconfig.me`, see _detect_public_ip), (2) reverse-DNS's
    it; if that PTR lookup comes back empty (common - many ISPs/cloud
    providers don't set one), the bare IP is still a meaningful identifier on
    its own, so that's used instead; (3) only if we couldn't even determine
    our public IP (no network egress) does this fall back to
    socket.getfqdn() - purely local, so it always answers, but it's a
    same-machine-only name if that's not where the install is even reachable
    from, so treat it as a last resort, not the primary answer.
    """
    if controller_fqdn != "automatic":
        return controller_fqdn

    ip = _detect_public_ip()
    if ip:
        hostname = _reverse_dns(ip)
        if hostname:
            print(f"[ssh] controller_fqdn=automatic: {ip} reverse-DNS's to {hostname}")
            return hostname
        print(f"[ssh] controller_fqdn=automatic: no reverse DNS for {ip}; using the IP itself")
        return ip

    local = socket.getfqdn()
    print(
        f"[ssh] controller_fqdn=automatic: could not determine our public IP "
        f"(no network egress?); falling back to local hostname {local}"
    )
    return local


_UNKNOWN_HOST_LABEL = "unknown-host"
_IDENTITY_FILE_LABEL_RE = re.compile(r"[^A-Za-z0-9.\-]")


def _render_identity_file_path(template: str, server_name: Optional[str]) -> str:
    """Render {host}/{date} placeholders in an ssh.identity_file template.

    A literal path with no placeholders passes through unchanged (backward
    compatible with a CLI/env override that doesn't want per-host naming).
    `server_name` is sanitized to safe filename characters; falls back to
    _UNKNOWN_HOST_LABEL if unset, so a template can still render to a
    concrete (if less useful) path outside the interactive-gather flow.
    """
    host_label = _IDENTITY_FILE_LABEL_RE.sub("-", server_name) if server_name else _UNKNOWN_HOST_LABEL
    date_label = datetime.now().strftime("%Y%m%d")
    return template.replace("{host}", host_label).replace("{date}", date_label)


def _ensure_local_identity_file_exists(identity_file: str, controller_fqdn: str) -> None:
    """Generate the local SSH identity key if it doesn't exist yet.

    One keypair per (already-rendered, per-host/per-date) identity_file path
    - see _render_identity_file_path(), called before this - not a single
    shared file reused across every install (confirmed live 2026-09-08: that
    let SSH monitoring for one host silently pick up a key netcup had
    actually registered for a different one). The key's *comment* embeds a
    stable "vbpub-controller-ephemeral" marker (so the consumed hook's own
    cleanup - and any human auditing a host's
    authorized_keys - can identify it unambiguously), the same host/date
    labels as the filename, and `controller_fqdn` (resolved via
    _resolve_controller_fqdn - "automatic" or an explicit value) so anyone
    looking at authorized_keys on a host this key touches can tell who/why
    put it there.
    """
    identity_path = Path(identity_file).expanduser()
    if identity_path.exists():
        return

    resolved_fqdn = _resolve_controller_fqdn(controller_fqdn)
    if not resolved_fqdn or any(
        resolved_fqdn.endswith(suffix) for suffix in _PLACEHOLDER_CONTROLLER_FQDN_SUFFIXES
    ):
        raise CliFailure(
            "install-host.toml's [ssh] controller_fqdn is still the "
            "placeholder - set it to this controller's own real hostname (or \"automatic\") "
            f"before a new identity key can be generated (would-be key: {identity_path})."
        )

    identity_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"[ssh] No local identity file at {identity_path}; generating one now.")
    subprocess.run(
        [
            "ssh-keygen", "-t", "ed25519", "-N", "",
            "-f", str(identity_path),
            "-C", f"vbpub-controller-ephemeral-{identity_path.name}@{resolved_fqdn}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    print(f"[ssh] Generated new identity: {identity_path}")


def _validate_existing_identity(identity_file: str) -> None:
    """Require an explicitly selected private key instead of replacing it."""
    path = Path(identity_file).expanduser()
    if not path.is_file():
        raise CliFailure(f"SSH identity file does not exist: {path}", exit_code=2)
    try:
        _read_public_key_for_identity(str(path))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise CliFailure(
            f"SSH identity file is not a readable private key: {path}: {exc}",
            exit_code=2,
        ) from exc


def _identity_is_reusable(path: Path) -> bool:
    if not path.is_file() or path.name.endswith(".pub") or path.name.endswith(".known_hosts"):
        return False
    try:
        _read_public_key_for_identity(str(path))
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return True


def _resolve_or_create_controller_identity(
    identity_template: str,
    host_label: str,
    *,
    explicit: bool,
    controller_fqdn: str,
) -> str:
    """Reuse a valid host-targeted key, or create one after preflight.

    Existing dated filenames from the previous template are intentionally
    considered. An explicit path is authoritative and never substituted.
    """
    rendered = Path(_render_identity_file_path(identity_template, host_label)).expanduser()
    if explicit:
        _validate_existing_identity(str(rendered))
        return str(rendered)

    # A literal configured path is already a complete operator choice.  It
    # cannot contain the selected host label, so handle it before the
    # host-targeted filename search below.
    if "{host}" not in identity_template and "{date}" not in identity_template:
        if _identity_is_reusable(rendered):
            print(f"[ssh] Reusing configured controller identity: {rendered}")
            return str(rendered)
        if rendered.exists():
            raise CliFailure(
                f"local SSH identity path exists but is not a usable private key: {rendered}",
                exit_code=2,
            )

    host_token = _IDENTITY_FILE_LABEL_RE.sub("-", host_label) or _UNKNOWN_HOST_LABEL
    candidates: List[Path] = []
    try:
        entries = sorted(rendered.parent.iterdir(), key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
    except OSError:
        entries = []
    for candidate in entries:
        if host_token in candidate.name and _identity_is_reusable(candidate):
            candidates.append(candidate)
    if candidates:
        chosen = candidates[0]
        print(f"[ssh] Reusing existing host-specific controller identity: {chosen}")
        return str(chosen)

    if rendered.exists():
        raise CliFailure(
            f"local SSH identity path exists but is not a usable private key: {rendered}",
            exit_code=2,
        )
    _ensure_local_identity_file_exists(str(rendered), controller_fqdn)
    return str(rendered)


def _protected_server_policy_configured() -> bool:
    """Validate the local policy and turn malformed configuration into a clean CLI error."""
    try:
        return netcup_scp_client.protected_server_policy_configured()
    except ValueError as exc:
        raise CliFailure(
            f"invalid protected-server denylist: {exc}", exit_code=2
        ) from exc


def _ensure_server_mutation_allowed(server_id: Any, server_name: Any, operation: str) -> None:
    """Apply the shared local denylist without exposing a traceback."""
    try:
        netcup_scp_client.assert_server_mutation_allowed(server_id, server_name, operation)
    except (ValueError, netcup_scp_client.ProtectedServerError) as exc:
        raise CliFailure(str(exc), exit_code=2) from exc


def _strip_jsonc_comments(text: str) -> str:
    """Strip // and /* */ comments from JSON-with-comments (JSONC).

    This is a small, dependency-free helper so our payload files can include
    human-friendly comments while still parsing as JSON.
    """

    out: List[str] = []
    i = 0
    in_string = False
    string_quote = ""
    escape = False

    while i < len(text):
        ch = text[i]

        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == string_quote:
                in_string = False
                string_quote = ""
            i += 1
            continue

        # Not in string
        if ch in ("\"", "'"):
            in_string = True
            string_quote = ch
            out.append(ch)
            i += 1
            continue

        # Line comment
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            i += 2
            while i < len(text) and text[i] not in ("\n", "\r"):
                i += 1
            continue

        # Block comment
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = i + 2 if i + 1 < len(text) else len(text)
            continue

        out.append(ch)
        i += 1

    return "".join(out)

_SETTINGS_EXPECTED_KEYS = {
    "ssh.identity_file",
    "ssh.user",
    "ssh.controller_fqdn",
    "ssh.poll_interval",
    "ssh.attach_initial_delay",
    "ssh.attach_max_wait_seconds",
    "ssh.completion_wait_seconds",
    "ssh.controller_local_key_retention",
}

SETTINGS_PATH = Path(__file__).resolve().parent / "install-host.toml"
SETTINGS: Dict[str, Any] = {}
VERSION_PATH = Path(__file__).resolve().parent / "VERSION"


def _cli_identity() -> CliIdentity:
    version = VERSION_PATH.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"invalid Netcup CLI version in {VERSION_PATH}: {version!r}")
    return CliIdentity(
        name="NETCUP SCP",
        command="install-host",
        version=version,
        long_name="Netcup Server Control Panel installer",
    )


IDENTITY = _cli_identity()
_ACTIVE_RUNTIME: Any | None = None

# Server configuration
# Example server info from `/servers` API:
#  {
#    "id": 799611,
#    "name": "v2202511209318402047",
#    "disabled": false,
#    "hostname": "r1002.vxxu.de",
#    "nickname": "r1002",
#    "template": {
#      "id": 1357,
#      "name": "RS 1000 G12 Pro"
#    }
#  }

SERVER_NAME = None
CONTROLLER_LOCAL_KEY_RETENTION: Optional[str] = None
CONTROLLER_SSH_PUBLIC_KEY = ""


# Installation settings (hostname will be set dynamically from server info).
# The Netcup image-install payload is useful without a customScript.  A hook is
# an opaque provider payload supplied by the caller; this frontend neither
# builds nor interprets an operating-system installer hook.
INSTALLATION_CONFIG = {
    "locale": "en_US.UTF-8",
    "timezone": "Europe/Berlin",
    "rootPartitionFullDiskSize": False,
    "sshPasswordAuthentication": False,
    "emailToExecutingUser": True,
}

DEFAULT_TARGET_CONFIG_NAME = "target-host.jsonc"

def _load_runtime_settings() -> Dict[str, Any]:
    try:
        return _load_settings(SETTINGS_PATH, _SETTINGS_EXPECTED_KEYS)
    except (OSError, ValueError, SystemExit) as exc:
        message = str(exc).removeprefix("ERROR: ")
        raise CliFailure(f"invalid installer settings: {message}", exit_code=2) from exc


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_finite(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed <= 0 or parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def _argument(name, description, *, metavar=None, **kwargs):
    return ArgumentSpec(name, description, metavar=metavar, parser_kwargs=kwargs)


def _option(flags, description, *, group, metavar=None, **kwargs):
    return OptionSpec(tuple(flags), description, group=group, metavar=metavar, parser_kwargs=kwargs)


def _workflow_options(*, target_picker: bool, monitor: bool):
    options = [
        _option(("--config", "--payload"), "config to install or output file to write; --payload is a deprecated alias", group="INPUT AND OUTPUT", metavar="FILE", dest="config_path"),
        _option(("--dry-run",), "validate and display the plan without submitting the image-install request", group="EXECUTION", action="store_true"),
        _option(("--ssh-key-id",), "reuse an existing Netcup account key ID; repeat to select multiple keys", group="SSH ACCESS", metavar="ID", dest="ssh_key_ids", action="append", type=_positive_integer, default=None),
        _option(("--local-controller-key",), "retain or remove the local controller key after the declared completion marker", group="SSH ACCESS", choices=("remove", "retain"), default=None),
        _option(("--poll-interval",), "task-monitor polling interval in seconds (default: install-host.toml)", group="MONITORING", type=_positive_finite, default=None),
        _option(("--completion-wait-seconds",), "maximum wait for the declared completion marker (default: install-host.toml)", group="MONITORING", type=_positive_finite, default=None),
        _option(("--attach-custom-script",), "tail the provider customScript log over SSH while monitoring", group="MONITORING", dest="attach_custom_script", action="store_true", default=argparse.SUPPRESS),
        _option(("--no-attach-custom-script",), "disable SSH customScript log attachment", group="MONITORING", dest="attach_custom_script", action="store_false", default=argparse.SUPPRESS),
        _option(("--ssh-host",), "SSH host/IP; defaults to the resolved server address or NETCUP_SCP_API_SSH_HOST", group="SSH ACCESS", default=None),
        _option(("--ssh-user",), "SSH login user (default: install-host.toml or NETCUP_SCP_API_SSH_USER)", group="SSH ACCESS", default=None),
        _option(("--ssh-identity-file",), "local controller private key; a valid host-specific key is reused when available", group="SSH ACCESS", metavar="PATH", default=None),
    ]
    if target_picker:
        options.extend(
            (
                _option(("--server-id",), "explicit wizard target; otherwise use configured name or interactive server picker", group="TARGET SELECTION", metavar="ID", type=_positive_integer, default=None),
                _option(("--custom-script-file",), "consume an opaque customScript command or producer JSON bundle", group="CUSTOMSCRIPT INPUT", metavar="FILE"),
                _option(("--completion-marker",), "absolute remote completion path declared by the consumed customScript", group="CUSTOMSCRIPT INPUT", metavar="PATH"),
            )
        )
    if monitor:
        options.append(
            _option(("--monitor",), "explicitly poll the task after wizard installation", group="MONITORING", action="store_true")
        )
    options.append(
        _option(("--no-monitor",), "return after task creation instead of following it", group="MONITORING", action="store_true")
    )
    return tuple(options)


def _attach_options():
    return (
        _option(("--attach-task-uuid",), "identifier used for local capture files (default: timestamp)", group="ATTACH SESSION", default=None),
        _option(("--simulate-disconnect-seconds",), "testing aid: force an SSH reconnect after this many seconds", group="ATTACH SESSION", type=float, default=None),
        _option(("--attach-initial-delay",), "delay before the first SSH probe (default: install-host.toml)", group="STOP CONDITIONS", type=_positive_finite, default=None),
        _option(("--attach-max-wait-seconds",), "maximum time to wait for SSH (default: install-host.toml)", group="STOP CONDITIONS", type=_positive_finite, default=None),
        _option(("--poll-interval",), "retry interval for SSH attachment (default: install-host.toml)", group="STOP CONDITIONS", type=_positive_finite, default=None),
        _option(("--ssh-host",), "required SSH host/IP; may come from NETCUP_SCP_API_SSH_HOST", group="SSH ACCESS", default=None),
        _option(("--ssh-user",), "SSH login user (default: install-host.toml or NETCUP_SCP_API_SSH_USER)", group="SSH ACCESS", default=None),
        _option(("--ssh-identity-file",), "exact existing local controller private key for this host", group="SSH ACCESS", metavar="PATH", default=None),
    )


def build_cli():
    registry = CliRegistry(
        IDENTITY,
        prog="install-host.py",
        description="Prepare, submit, and monitor Netcup SCP image installations.",
        getting_started=(
            "./scp-api.py login",
            "./install-host.py wizard",
            "./install-host.py install --config target-host.jsonc --dry-run",
        ),
        logging_logger="netcup.install_host",
    )

    def register(name, synopsis, summary, description, group, handler, *, options=(), examples=(), mutating=False, interactive=False, expensive=False):
        registry.register(
            VerbSpec(
                name,
                synopsis,
                description,
                group=group,
                examples=examples,
                mutating=mutating,
                interactive=interactive,
                expensive=expensive,
                include_json=False,
                include_progress=False,
                options=options,
                handler=handler,
                summary_description=summary,
            )
        )

    def workflow_handler(args, runtime):
        return _run_install_workflow(args, runtime)

    register(
        "wizard", "", "gather a reviewed install plan and optionally install it",
        "Resolve the target, image flavour, account SSH keys, and optional producer customScript; write the reviewed config, then request confirmation before installation.",
        VerbGroup.AUTHENTICATION.value,
        workflow_handler,
        options=_workflow_options(target_picker=True, monitor=True),
        examples=("./scp-api.py login", "./install-host.py wizard", "./install-host.py wizard --custom-script-file debian-v2-customscript.json"),
        mutating=True,
        interactive=True,
    )
    register(
        "configure", "", "compatibility alias for wizard",
        "Run the same interactive gather-and-install workflow as wizard. New usage should prefer wizard.",
        VerbGroup.AUTHENTICATION.value,
        workflow_handler,
        options=_workflow_options(target_picker=True, monitor=True),
        examples=("./install-host.py configure",),
        mutating=True,
        interactive=True,
    )
    register(
        "install", "", "install a validated config and monitor its task",
        "Read target-host.jsonc (or --config FILE), validate it without gathering missing fields, submit the image install, and monitor by default. Use --no-monitor to return after task creation.",
        VerbGroup.MODIFICATION.value,
        workflow_handler,
        options=_workflow_options(target_picker=False, monitor=False),
        examples=("./install-host.py install", "./install-host.py install --config target-host-r1002.jsonc", "./install-host.py install --config target-host.jsonc --dry-run"),
        mutating=True,
        expensive=True,
    )
    register(
        "attach", "", "reattach to a host and follow the provider customScript log",
        "SSH to an already installing/installed host and stream the provider's customScript output, reconnecting across SSH loss and reboots. This command makes no Netcup API calls and never creates an identity key.",
        VerbGroup.EXPLORATION.value,
        workflow_handler,
        options=_attach_options(),
        examples=("./install-host.py attach --ssh-host 192.0.2.10 --ssh-identity-file ~/.ssh/netcup-r1002-ed25519",),
        expensive=True,
    )
    return registry.build()


def parse_args(argv=None):
    """Compatibility parser for tests and internal callers; CLI uses registry.run()."""
    app = build_cli()
    args = app.parser.parse_args(argv)
    args.command = args.verb
    return args


def is_noninteractive(args: argparse.Namespace) -> bool:
    # --yes is consent, not a change in whether prompts or wizard choices exist.
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True


def _confirm_install(prompt: str, args: argparse.Namespace) -> bool:
    if _ACTIVE_RUNTIME is not None:
        return _ACTIVE_RUNTIME.confirm(prompt)
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        raise CliFailure(
            "confirmation is required, but stdin is not interactive; rerun with --yes after reviewing the plan",
            exit_code=2,
        )
    try:
        answer = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return answer.strip().casefold() in {"y", "yes"}


def _display_redaction(value: Any) -> Any:
    return value if netcup_scp_client.DEBUG_RAW else _redact_for_log(value)


def _http_failure_message(exc: HTTPStatusError) -> str:
    message = f"Netcup API request failed (HTTP {exc.status}): {exc}"
    body = getattr(exc, "body", "")
    if body:
        try:
            detail = json.dumps(_display_redaction(json.loads(body)), ensure_ascii=False)
        except (TypeError, ValueError):
            detail = body[:1000]
        message += f"; response: {detail}"
    return message


def _should_monitor(args: argparse.Namespace) -> bool:
    """Whether a started task should be followed by this process."""
    return not getattr(args, "no_monitor", False) and (
        getattr(args, "monitor", False) or is_noninteractive(args)
    )


def _fmt_ts(ts: Optional[str]) -> str:
    if not ts:
        return ""
    return ts


_RE_TELEGRAM_BOT_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")


def _redact_secrets(text: str) -> str:
    # Telegram bot tokens are secrets; avoid printing or persisting them.
    return _RE_TELEGRAM_BOT_TOKEN.sub("***REDACTED_TELEGRAM_BOT_TOKEN***", text)


def _extract_primary_ipv4(server_details: Dict[str, Any]) -> Optional[str]:
    ip_address = None
    if "ipv4Addresses" in server_details and server_details["ipv4Addresses"]:
        ip_address = server_details["ipv4Addresses"][0].get("ip")
    elif "serverLiveInfo" in server_details and "interfaces" in server_details["serverLiveInfo"]:
        interfaces = server_details["serverLiveInfo"]["interfaces"]
        if interfaces and "ipv4Addresses" in interfaces[0] and interfaces[0]["ipv4Addresses"]:
            ip_address = interfaces[0]["ipv4Addresses"][0]
    return ip_address


def _tcp_port_open(host: str, port: int = 22, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _validate_completion_marker(marker: Optional[str]) -> Optional[str]:
    if marker is None:
        return None
    marker = marker.strip()
    if not marker:
        return None
    if not marker.startswith("/") or any(char in marker for char in "\r\n\x00"):
        raise ValueError("completion marker must be an absolute remote path")
    return marker


def _wait_for_completion_marker(
    *,
    host: str,
    user: str,
    identity_file: Optional[str],
    poll_interval: float,
    max_wait_seconds: float,
    marker: str,
    monitor_log_path: Path,
) -> None:
    """Wait for an opaque customScript's declared completion marker."""
    start = time.monotonic()
    poll = max(1.0, float(poll_interval))

    def _emit(line: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = f"[{now}] [customScript-wait] {line}"
        print(out)
        try:
            with open(monitor_log_path, "a", encoding="utf-8") as mf:
                mf.write(out + "\n")
        except Exception:
            pass

    _emit(
        f"Waiting for customScript completion marker on {user}@{host}: {marker} "
        f"(timeout {max_wait_seconds:.0f}s)"
    )
    last_status = None
    while True:
        if time.monotonic() - start > max_wait_seconds:
            raise TimeoutError(
                f"timed out waiting for customScript completion marker {marker!r} "
                f"after {max_wait_seconds:.0f}s"
            )
        if not _tcp_port_open(host, 22, timeout=2.0):
            time.sleep(poll)
            continue
        command = f"test -f {shlex.quote(marker)}"
        try:
            result = subprocess.run(
                _build_ssh_cmd_base(host, user, identity_file) + [command],
                text=True,
                capture_output=True,
                timeout=15,
            )
            status = f"ssh-exit={result.returncode}"
        except Exception as exc:
            status = f"ssh-error: {type(exc).__name__}: {exc}"
            result = None
        if status != last_status:
            _emit(status)
            last_status = status
        if result is not None and result.returncode == 0:
            _emit("customScript completion marker present.")
            return
        time.sleep(poll)


_TASK_TERMINAL_STATES = {"FINISHED", "ERROR", "CANCELED", "ROLLBACK"}


def _parse_iso_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        # Handle both `...Z` and `...+00:00` formats.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _find_active_task_for_server(client: "NetcupSCPClient", server_id: int) -> Optional[Dict[str, Any]]:
    """Best-effort: find the most recent non-terminal task for a server."""
    resp = client.get("/api/v1/tasks", params={"serverId": server_id, "limit": 50})

    # The API is documented as returning a list, but be defensive in case a HAL-ish
    # wrapper appears.
    tasks: Any = resp
    if isinstance(resp, dict):
        embedded = resp.get("_embedded")
        if isinstance(embedded, dict):
            for k in ("tasks", "taskInfoMinimal", "items"):
                if isinstance(embedded.get(k), list):
                    tasks = embedded.get(k)
                    break
        for k in ("tasks", "items", "content"):
            if isinstance(resp.get(k), list):
                tasks = resp.get(k)
                break

    if not isinstance(tasks, list):
        return None

    active: List[Dict[str, Any]] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        state = t.get("state")
        if isinstance(state, str) and state in _TASK_TERMINAL_STATES:
            continue
        active.append(t)

    if not active:
        return None

    def score(task: Dict[str, Any]) -> tuple:
        name = (task.get("name") or "")
        msg = (task.get("message") or "")
        text = f"{name} {msg}".lower()
        prefers_image = 1 if ("image" in text or "install" in text) else 0
        started = _parse_iso_ts(task.get("startedAt")) or datetime.min
        return (prefers_image, started)

    return max(active, key=score)


def _build_ssh_cmd_base(host: str, user: str, identity_file: Optional[str] = None) -> List[str]:
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=3",
        "-o", "LogLevel=ERROR",
    ]
    if identity_file:
        cmd += ["-o", "IdentitiesOnly=yes"]
        cmd += ["-i", identity_file]
    cmd.append(f"{user}@{host}")
    return cmd


def _server_short_name_from_details(server_details: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(server_details, dict):
        return None
    for key in ("nickname", "hostname", "name"):
        val = server_details.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().split(".")[0]
    return None


class _SSHCustomScriptFollower:
    def __init__(
        self,
        task_uuid: str,
        host: str,
        user: str,
        identity_file: Optional[str],
        poll_interval: float,
        initial_delay: float = 10.0,
        max_wait_seconds: float = 300.0,
        simulate_disconnect_seconds: Optional[float] = None,
    ) -> None:
        self.task_uuid = task_uuid
        self.host = host
        self.user = user
        self.identity_file = identity_file
        self.poll_interval = max(1.0, float(poll_interval))
        self.initial_delay = max(0.0, float(initial_delay))
        self.max_wait_seconds = max(5.0, float(max_wait_seconds))
        self.simulate_disconnect_seconds = (
            None
            if simulate_disconnect_seconds is None
            else max(0.0, float(simulate_disconnect_seconds))
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen[str]] = None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.local_log_path = Path.cwd() / f"ssh-tail-{task_uuid}-{ts}.log"
        self.local_custom_script_log_path = Path.cwd() / f"ssh-tail-custom-script-{task_uuid}-{ts}.log"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        banner = f"SSH ATTACH: {self.user}@{self.host}"
        identity_hint = self.identity_file or "(missing identity file)"

        # Always create the local capture file immediately so users have
        # something to inspect even if SSH isn't reachable/auth fails.
        try:
            self.local_log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        def _log(line: str, lf: Optional[Any] = None) -> None:
            print(line)
            if lf is not None:
                try:
                    lf.write(line + "\n")
                    lf.flush()
                except Exception:
                    pass

        with (
            open(self.local_log_path, "a", encoding="utf-8") as lf,
            open(self.local_custom_script_log_path, "a", encoding="utf-8") as lf_custom,
        ):
            _log("=" * 70, lf)
            _log(banner, lf)
            _log("=" * 70, lf)
            _log(f"[attach] Local capture: {self.local_log_path}", lf)
            _log(f"[attach] customScript capture: {self.local_custom_script_log_path}", lf)
            _log(f"[attach] Identity: {identity_hint}", lf)

            if self.initial_delay > 0:
                _log(f"[attach] Waiting {self.initial_delay:.0f}s before first SSH probe...", lf)
                time.sleep(self.initial_delay)

            def _wait_for_ssh(max_wait: float) -> bool:
                """Wait for SSH to become reachable and usable.

                Returns True if usable, False if timed out or stopped.
                """
                last_reason = None
                start = time.monotonic()
                while not self._stop.is_set():
                    if (time.monotonic() - start) > max_wait:
                        _log(f"[attach] Giving up after {max_wait:.0f}s without SSH becoming usable.", lf)
                        return False
                    if not _tcp_port_open(self.host, 22, timeout=2.0):
                        time.sleep(self.poll_interval)
                        continue

                    cmd_probe = _build_ssh_cmd_base(self.host, self.user, self.identity_file) + ["true"]
                    try:
                        r = subprocess.run(cmd_probe, text=True, capture_output=True, timeout=10)
                        if r.returncode == 0:
                            return True

                        reason = (r.stderr or "").strip().splitlines()[-1:]  # last line only
                        reason_txt = reason[0] if reason else f"ssh exit={r.returncode}"
                        if reason_txt != last_reason:
                            last_reason = reason_txt
                            _log(f"[attach] SSH not ready/auth failed: {reason_txt}", lf)
                            if not self.identity_file:
                                _log(
                                    "[attach] Hint: pass --ssh-identity-file or set NETCUP_SCP_API_SSH_IDENTITY_FILE if this is a key auth issue.",
                                    lf,
                                )
                    except Exception as e:
                        reason_txt = f"{type(e).__name__}: {e}"
                        if reason_txt != last_reason:
                            last_reason = reason_txt
                            _log(f"[attach] SSH probe error: {reason_txt}", lf)

                    time.sleep(self.poll_interval)
                return False

            # First attach: wait up to max_wait_seconds. After reboots/disconnects,
            # keep trying in shorter windows.
            first_attach = True
            while not self._stop.is_set():
                max_wait = self.max_wait_seconds if first_attach else max(60.0, self.max_wait_seconds)
                if not _wait_for_ssh(max_wait=max_wait):
                    return
                first_attach = False
                _log("[attach] SSH reachable; starting remote tail.", lf)

                # Netcup writes the provider customScript output here.  The
                # command itself is opaque to this frontend, so there is no
                # second, Debian-specific service or completion marker to tail.
                tail_remote = (
                    "bash -lc 'set -euo pipefail; "
                    "echo \"[attach] Streaming Netcup customScript output\"; "
                    "P=/root/custom_script.output; "
                    "if [ ! -f \"$P\" ]; then "
                    "  echo \"[attach] Waiting for log to appear: $P\"; "
                    "  for i in $(seq 1 300); do [ -f \"$P\" ] && break; sleep 1; done; "
                    "fi; "
                    "if [ -f \"$P\" ]; then "
                    "  echo \"[attach] Tailing: $P\"; "
                    "  tail -n 200 -F \"$P\" 2>&1; "
                    "else "
                    "  echo \"[attach] No customScript log at $P\"; "
                    "fi'"
                )

                cmd_tail = _build_ssh_cmd_base(self.host, self.user, self.identity_file) + [tail_remote]
                try:
                    self._proc = subprocess.Popen(
                        cmd_tail,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                except Exception as e:
                    _log(f"[attach] Failed to start SSH tail: {e}", lf)
                    time.sleep(self.poll_interval)
                    continue

                secs_opt = self.simulate_disconnect_seconds
                if secs_opt is not None and secs_opt > 0:
                    proc = self._proc
                    secs = float(secs_opt)

                    def _simulate_disconnect() -> None:
                        time.sleep(secs)
                        if self._stop.is_set():
                            return
                        try:
                            if proc and proc.poll() is None:
                                _log(
                                    f"[attach] Simulating disconnect: terminating SSH tail after {secs:.1f}s",
                                    lf,
                                )
                                proc.terminate()
                        except Exception:
                            pass

                    threading.Thread(target=_simulate_disconnect, daemon=True).start()

                assert self._proc.stdout is not None
                try:
                    for line in self._proc.stdout:
                        if self._stop.is_set():
                            break
                        out_line = _redact_secrets(f"[remote] {line}")
                        sys.stdout.write(out_line)
                        sys.stdout.flush()
                        lf.write(out_line)
                        lf.flush()

                        lf_custom.write(out_line)
                        lf_custom.flush()
                except Exception as e:
                    _log(f"[attach] Failed while capturing remote output: {e}", lf)

                try:
                    if self._proc and self._proc.poll() is None:
                        self._proc.terminate()
                except Exception:
                    pass

                if self._stop.is_set():
                    return

                # Most common reason: reboot / network hiccup. Loop and reattach.
                _log("[attach] Remote tail ended (likely reboot/disconnect); reattaching...", lf)
                time.sleep(self.poll_interval)


def monitor_task(
    client: "NetcupSCPClient",
    task_uuid: str,
    poll_interval: float = 5.0,
    *,
    ssh_host: Optional[str] = None,
    ssh_user: str = "root",
    ssh_identity_file: Optional[str] = None,
    attach_custom_script: bool = True,
    completion_marker: Optional[str] = None,
    completion_wait_seconds: float = 1800.0,
) -> Dict[str, Any]:
    """Poll the provider task until it reaches a terminal state."""
    last_progress = None
    last_state = None
    last_step_states = {}

    follower: Optional[_SSHCustomScriptFollower] = None
    attach_started = False

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    monitor_log_path = Path.cwd() / f"task-monitor-{task_uuid}-{ts}.log"

    print("=" * 70)
    print(f"MONITORING TASK {task_uuid}")
    print("=" * 70)
    print(f"Monitor capture: {monitor_log_path}")

    if attach_custom_script and not ssh_identity_file:
        print(
            "[attach] NOTE: no --ssh-identity-file provided; attempting SSH attach using default ssh identities/agent. "
            "For deterministic behavior, pass --ssh-identity-file (or set NETCUP_SCP_API_SSH_IDENTITY_FILE)."
        )

    # Start attach early (about 10s after installation kickoff) and retry until SSH becomes usable.
    # This avoids relying on SCP step names / Cloudinit timing.
    if attach_custom_script and not attach_started and ssh_host:
        attach_started = True
        follower = _SSHCustomScriptFollower(
            task_uuid=task_uuid,
            host=ssh_host,
            user=ssh_user,
            identity_file=ssh_identity_file,
            poll_interval=poll_interval,
            initial_delay=10.0,
            max_wait_seconds=300.0,
        )
        follower.start()

    while True:
        task = client.get(f"/api/v1/tasks/{task_uuid}")
        state = task.get("state")
        name = task.get("name")
        msg = task.get("message")
        started_at = task.get("startedAt")
        finished_at = task.get("finishedAt")
        progress = None
        tp = task.get("taskProgress") or {}
        if isinstance(tp, dict):
            progress = tp.get("progressInPercent")

        # Steps (optional)
        steps = task.get("steps") or []
        step_lines = []
        cloudinit_seen = False
        if isinstance(steps, list):
            for s in steps:
                sname = s.get("name")
                sstate = s.get("state")
                suuid = s.get("uuid")
                if sname and sstate:
                    if "Cloudinit" in sname and sstate in ("RUNNING", "FINISHED"):
                        cloudinit_seen = True
                    prev = last_step_states.get(suuid)
                    if prev != sstate:
                        last_step_states[suuid] = sstate
                        step_lines.append(f"  - {sstate:12s} {sname}")

        # (Attach is started early outside the polling loop.)

        changed = False
        if state != last_state:
            changed = True
        if progress != last_progress:
            changed = True
        if step_lines:
            changed = True

        if changed:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ptxt = f"{progress:.0f}%" if isinstance(progress, (int, float)) else "?%"
            out_lines: List[str] = []
            out_lines.append(f"[{now}] {state} {ptxt}  {name or ''}".rstrip())
            if msg:
                out_lines.append(f"  message: {msg}")
            if started_at:
                out_lines.append(f"  started:  {_fmt_ts(started_at)}")
            if finished_at:
                out_lines.append(f"  finished: {_fmt_ts(finished_at)}")
            out_lines.extend(step_lines)

            for ln in out_lines:
                print(ln)

            try:
                with open(monitor_log_path, "a", encoding="utf-8") as mf:
                    for ln in out_lines:
                        mf.write(ln + "\n")
            except Exception:
                pass

            last_state = state
            last_progress = progress

        if state in ("FINISHED", "ERROR", "CANCELED", "ROLLBACK"):
            if state == "FINISHED" and ssh_host and completion_marker:
                try:
                    _wait_for_completion_marker(
                        host=ssh_host,
                        user=ssh_user,
                        identity_file=ssh_identity_file,
                        poll_interval=poll_interval,
                        max_wait_seconds=completion_wait_seconds,
                        marker=completion_marker,
                        monitor_log_path=monitor_log_path,
                    )
                    task["custom_script_completed"] = True
                except Exception as exc:
                    task["custom_script_completed"] = False
                    task["custom_script_completion_error"] = str(exc)
            if follower:
                follower.stop()
            # Print responseError if present
            resp_err = task.get("responseError")
            if resp_err:
                print("=" * 70)
                print("TASK RESPONSE ERROR")
                print(json.dumps(resp_err, indent=2))
            return task

        # Sleep
        try:
            time.sleep(max(0.5, float(poll_interval)))
        except KeyboardInterrupt:
            print("\nInterrupted; task continues server-side.")
            if follower:
                follower.stop()
            return task


def save_payload_with_comments(
    payload: Dict[str, Any],
    filepath: str,
    server_name: str,
    image_name: str,
    user_id: int,
    ssh_key_names: Optional[List[str]] = None,
    hostname_method: Optional[str] = None,
):
    """Save installation payload to JSONC file with helpful comments"""
    lines: List[str] = ["{"]
    items = list(payload.items())
    for idx, (key, value) in enumerate(items):
        # Add comments for IDs to make them more understandable
        if key == "serverId":
            lines.append(f'  // Server ID for: {server_name}')
        elif key == "hostname":
            if hostname_method:
                lines.append(f'  // Hostname determined by: {hostname_method}')
        elif key == "imageFlavourId":
            lines.append(f'  // Image: {image_name}')
        elif key == "sshKeyIds":
            lines.append(f'  // SSH key IDs for user {user_id}')
            if ssh_key_names:
                for key_name in ssh_key_names:
                    lines.append(f'  //   - {key_name}')

        # Format the actual key-value pair
        if isinstance(value, bool):
            value_str = str(value).lower()
        else:
            value_str = json.dumps(value)
        suffix = "," if idx < len(items) - 1 else ""
        lines.append(f'  "{key}": {value_str}{suffix}')

    lines.append("}")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _payload_needs_controller_key(payload: Optional[Dict[str, Any]]) -> bool:
    """Whether an opaque customScript consumes the generic controller key."""
    custom_script = payload.get("customScript") if isinstance(payload, dict) else None
    return isinstance(custom_script, str) and "{{CONTROLLER_SSH_PUBKEY}}" in custom_script


def _load_custom_script_file(path: str) -> tuple[str, Optional[str]]:
    """Read an opaque customScript command or a generator JSON bundle."""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise CliFailure(f"customScript file not found: {path}", exit_code=2) from exc
    except OSError as exc:
        raise CliFailure(f"cannot read customScript file {path}: {exc}", exit_code=2) from exc
    if not raw:
        raise CliFailure(f"customScript file is empty: {path}", exit_code=2)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("customScript"), str):
        raise CliFailure(
            "customScript JSON must be an object containing a string customScript field",
            exit_code=2,
        )
    custom_script = parsed["customScript"].strip()
    if not custom_script:
        raise CliFailure(f"customScript field is empty: {path}", exit_code=2)
    try:
        completion_marker = _validate_completion_marker(parsed.get("completionMarker"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise CliFailure(f"invalid completionMarker in {path}: {exc}", exit_code=2) from exc
    return custom_script, completion_marker


def _prompt_choice(items_desc: List[str], default_index: int, prompt_label: str) -> int:
    """Print a numbered list and prompt for a 1-based selection.

    Returns a 0-based index into items_desc. Enter (empty input) accepts
    default_index. Only call this when the caller has already confirmed the
    run is interactive.
    """
    for i, desc in enumerate(items_desc):
        marker = " (default)" if i == default_index else ""
        print(f"   [{i + 1}] {desc}{marker}")
    while True:
        raw = input(f"{prompt_label} [1-{len(items_desc)}, Enter for default]: ").strip()
        if not raw:
            return default_index
        try:
            choice = int(raw)
        except ValueError:
            print("   Please enter a number.")
            continue
        if 1 <= choice <= len(items_desc):
            return choice - 1
        print(f"   Please enter a number between 1 and {len(items_desc)}.")


def _prompt_text(label: str, default: str) -> str:
    """Prompt for a free-text value; Enter (empty input) accepts default."""
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _prompt_yes_no(label: str, default: bool) -> bool:
    """Prompt for a yes/no value; Enter (empty input) accepts default."""
    hint = "Y/n" if default else "y/N"
    raw = input(f"{label} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _resolve_image_flavour(
    client: "NetcupSCPClient",
    server_id: int,
    preselected: Optional[Dict[str, Any]],
    *,
    interactive: bool,
) -> Dict[str, Any]:
    """Resolve the image flavour to install: use `preselected` as-is if given
    (payload already had `imageFlavourId`), else query and pick.

    Filters to Debian+UEFI images (this tool's whole purpose). Interactive
    runs get a numbered prompt (default: newest); non-interactive runs keep
    auto-selecting newest, matching this tool's prior behavior.

    Returns {"id": int, "name": str}.
    """
    if preselected is not None:
        return preselected

    image_flavours = client.get(f"/api/v1/servers/{server_id}/imageflavours")
    debian_images = [
        img for img in image_flavours
        if "Debian" in img["image"]["name"] and "UEFI" in img["image"]["name"]
    ]
    if not debian_images:
        raise CliFailure("no Debian UEFI image flavours are available for this server")

    print("   Available Debian UEFI images:")
    for img in debian_images:
        print(f"   - ID: {img['id']:3d} | {img['image']['name']}")

    # Sort by version number (extract version from name like "Debian 13.2.0 UEFI amd64").
    debian_images_sorted = sorted(
        debian_images,
        key=lambda img: [int(x) if x.isdigit() else x for x in img["image"]["name"].split() if any(c.isdigit() for c in x)],
        reverse=True,
    )

    if interactive:
        descs = [f"ID {img['id']:3d} | {img['image']['name']}" for img in debian_images_sorted]
        idx = _prompt_choice(descs, default_index=0, prompt_label="   Select image")
        chosen = debian_images_sorted[idx]
    else:
        chosen = debian_images_sorted[0]
        print(f"   ✓ Auto-selected newest (non-interactive): {chosen['image']['name']}")

    print(f"   ✓ Using imageFlavourId {chosen['id']} ({chosen['image']['name']})")
    return {"id": chosen["id"], "name": chosen["image"]["name"]}


def _resolve_ssh_key_ids(
    client: "NetcupSCPClient",
    user_id: int,
    preselected_ids: Optional[List[int]],
    identity_file: Optional[str],
    *,
    interactive: bool,
    dry_run: bool = False,
) -> Optional[List[int]]:
    """Resolve persistent Netcup account keys without creating any.

    The account keys are operator access keys. The temporary controller key is
    deliberately separate and is passed through ``CONTROLLER_SSH_PUBKEY``.
    Interactive input accepts ``all``, ``none``, or a comma-separated list of
    displayed numbers. Non-interactive installs use all currently registered
    account keys unless ``--ssh-key-id`` supplied an explicit selection.
    """
    if preselected_ids is not None:
        if not preselected_ids:
            return None
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in preselected_ids):
            raise ValueError("--ssh-key-id values must be positive integers")
        return list(dict.fromkeys(preselected_ids))

    ssh_keys = client.get(f"/api/v1/users/{user_id}/ssh-keys") or []
    if not isinstance(ssh_keys, list) or any(not isinstance(key, dict) for key in ssh_keys):
        raise CliFailure("Netcup SSH-key response was not a list of objects")
    valid_keys: List[Dict[str, Any]] = []
    for key in ssh_keys:
        key_id = key.get("id")
        if isinstance(key_id, bool) or not isinstance(key_id, int) or key_id <= 0:
            raise CliFailure("Netcup returned an invalid account SSH-key ID")
        valid_keys.append(key)
    ssh_keys = valid_keys
    if ssh_keys:
        print("   Available SSH keys:")
        for index, key in enumerate(ssh_keys, start=1):
            print(f"   [{index}] ID: {key['id']:3d} | {key.get('name', '')}")
    else:
        print("   No persistent account SSH keys found; continuing with controller key only.")
        return None

    if not interactive:
        chosen_ids = [int(key["id"]) for key in ssh_keys]
        print(f"   ✓ Using all {len(chosen_ids)} existing account SSH key(s) (non-interactive)")
        return chosen_ids

    while True:
        raw = input("   Account keys to inject [all, none, or numbers such as 1,2; Enter=all]: ").strip().lower()
        if not raw or raw == "all":
            chosen_ids = [int(key["id"]) for key in ssh_keys]
            print(f"   ✓ Selected {len(chosen_ids)} account SSH key(s)")
            return chosen_ids
        if raw in {"none", "0", "-"}:
            print("   ✓ Selected no persistent account keys")
            return None
        try:
            indexes = [int(part.strip()) for part in raw.split(",")]
        except ValueError:
            print("   Please enter all, none, or a comma-separated list of displayed numbers.")
            continue
        if not indexes or len(set(indexes)) != len(indexes) or any(index < 1 or index > len(ssh_keys) for index in indexes):
            print(f"   Please enter numbers between 1 and {len(ssh_keys)} without duplicates.")
            continue
        chosen_ids = [int(ssh_keys[index - 1]["id"]) for index in indexes]
        print(f"   ✓ Selected account SSH key IDs: {', '.join(map(str, chosen_ids))}")
        return chosen_ids


def _validate_installation_payload(payload: Any, *, require_complete: bool = False) -> List[str]:
    """Local, offline structural checks for an install-image payload.

    This cannot catch invalid IDs (those only fail once netcup validates them),
    but it catches missing/mistyped fields before we ever call the API - the
    kind of mistake that otherwise only surfaces as an opaque 422 or 404.

    The interactive wizard may omit ``imageFlavourId`` and ``sshKeyIds`` while
    it is gathering them live.  A file-driven ``install`` uses
    ``require_complete=True`` and therefore requires the image flavour as
    well; account ``sshKeyIds`` remain optional because a customScript may
    provide the temporary controller access or the operator may intentionally
    install without injected account keys.
    """
    errors: List[str] = []

    if not isinstance(payload, dict):
        return [f"top level must be a JSON object, got {type(payload).__name__}"]

    if "serverId" not in payload:
        hostname = payload.get("hostname")
        if not isinstance(hostname, str) or not hostname.strip():
            errors.append("must contain either 'serverId' (int) or a non-empty 'hostname'")
    if "serverId" in payload and (
        isinstance(payload["serverId"], bool)
        or not isinstance(payload["serverId"], int)
        or payload["serverId"] <= 0
    ):
        errors.append("'serverId' must be a positive integer (not a boolean)")

    if "imageFlavourId" not in payload:
        if require_complete:
            errors.append("missing required 'imageFlavourId' (run wizard or provide it in the config)")
    elif (
        isinstance(payload["imageFlavourId"], bool)
        or not isinstance(payload["imageFlavourId"], int)
        or payload["imageFlavourId"] <= 0
    ):
        errors.append("'imageFlavourId' must be a positive integer (not a boolean)")

    if "diskName" not in payload:
        errors.append("missing required 'diskName' (e.g. 'vda')")
    elif not isinstance(payload["diskName"], str) or not payload["diskName"].strip():
        errors.append("'diskName' must be a non-empty string")

    ssh_key_ids = payload.get("sshKeyIds")
    if ssh_key_ids is not None and not (
        isinstance(ssh_key_ids, list)
        and all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in ssh_key_ids)
    ):
        errors.append("'sshKeyIds' must be a list of positive integer IDs (or omitted)")

    custom_script = payload.get("customScript")
    if custom_script is not None and not isinstance(custom_script, str):
        errors.append("'customScript' must be a string")

    return errors


def install_from_payload(
    client: NetcupSCPClient,
    payload_path: str,
    args: argparse.Namespace,
    *,
    require_complete: bool = False,
):
    """Install directly from a payload JSON file.

    The payload file may contain the generic {{CONTROLLER_SSH_PUBKEY}} marker
    emitted by an external customScript producer. It is expanded only for the
    API request; all other customScript content is opaque here.
    """
    print("=" * 70)
    print("DIRECT INSTALLATION MODE" + ("  [DRY RUN]" if getattr(args, "dry_run", False) else ""))
    print("=" * 70)
    print()

    # Load payload
    print(f"Loading payload from: {payload_path}")
    try:
        with open(payload_path, "r", encoding="utf-8") as f:
            raw_payload = f.read()
        installation_payload = json.loads(_strip_jsonc_comments(raw_payload))
    except FileNotFoundError as exc:
        raise CliFailure(f"config file not found: {payload_path}", exit_code=2) from exc
    except OSError as exc:
        raise CliFailure(f"cannot read config file {payload_path}: {exc}", exit_code=2) from exc
    except json.JSONDecodeError as exc:
        raise CliFailure(f"invalid JSON/JSONC in config file {payload_path}: {exc}", exit_code=2) from exc

    print("✓ Payload loaded successfully")
    print()

    validation_errors = _validate_installation_payload(
        installation_payload,
        require_complete=require_complete,
    )
    if validation_errors:
        raise CliFailure(
            "config failed preflight validation: " + "; ".join(validation_errors),
            exit_code=2,
            show_help=True,
        )

    requested_ssh_key_ids = getattr(args, "ssh_key_ids", None)
    if requested_ssh_key_ids is not None:
        # A CLI selection is an explicit per-run override, including when a
        # checked-in/local payload contains a different stale selection.
        installation_payload["sshKeyIds"] = requested_ssh_key_ids or None
        validation_errors = _validate_installation_payload(installation_payload)
        if validation_errors:
            raise CliFailure(
                "command-line SSH key selection failed validation: "
                + "; ".join(validation_errors),
                exit_code=2,
                show_help=True,
            )

    if "customScript" in installation_payload and installation_payload["customScript"] == "":
        # An empty string is equivalent to omitting the optional hook, but do
        # not silently load a Debian-specific default behind the operator's
        # back.  The API receives no customScript below.
        installation_payload.pop("customScript")

    # Resolve server ID.
    server_id = None
    if "serverId" in installation_payload:
        server_id = installation_payload.get("serverId")
    else:
        hostname = installation_payload.get("hostname")
        if hostname:
            servers = client.get("/api/v1/servers", params={"name": hostname})
            if servers:
                server_id = servers[0].get("id")

    if not server_id:
        raise CliFailure(
            "config must contain serverId or a resolvable hostname",
            exit_code=2,
            show_help=True,
        )

    # Best-effort fetch server details so we can attach via SSH during monitoring.
    server_details = None
    ip_address = None
    try:
        server_details = client.get(f"/api/v1/servers/{server_id}")
        ip_address = _extract_primary_ipv4(server_details)
    except HTTPStatusError as exc:
        raise CliFailure(f"could not fetch target server details: {exc}") from exc
    except OSError as exc:
        if _protected_server_policy_configured() and not getattr(args, "dry_run", False):
            raise CliFailure(
                f"cannot verify server {server_id} against the protected-server denylist: {exc}",
                exit_code=2,
            )
        raise
    if _protected_server_policy_configured() and not getattr(args, "dry_run", False):
        if not isinstance(server_details, dict):
            raise CliFailure(
                f"cannot verify server {server_id} against the protected-server denylist: "
                "the server-details response was not an object",
                exit_code=2,
            )
        _ensure_server_mutation_allowed(
            server_id,
            server_details.get("name"),
            "Netcup image installation",
        )

    # Fill in any fields the payload left out by querying the API and, when
    # interactive, letting the operator pick - rather than silently guessing.
    interactive = not is_noninteractive(args)
    if not require_complete and "imageFlavourId" not in installation_payload:
        print("imageFlavourId not set in payload:")
        flavour = _resolve_image_flavour(client, int(server_id), None, interactive=interactive)
        installation_payload["imageFlavourId"] = flavour["id"]
        print()
    if not require_complete and "sshKeyIds" not in installation_payload:
        print("sshKeyIds not set in payload:")
        user_id = client.get_user_info()["id"]
        installation_payload["sshKeyIds"] = _resolve_ssh_key_ids(
            client, user_id, None,
            getattr(args, "ssh_identity_file", None),
            interactive=interactive,
            dry_run=getattr(args, "dry_run", False),
        )
        if installation_payload["sshKeyIds"] is None:
            installation_payload.pop("sshKeyIds")
        print()

    # Expand placeholders only for the API request, while keeping the loaded
    # payload safe-to-print/save.
    try:
        installation_payload_to_send = _expand_payload_placeholders(installation_payload)
    except ValueError as exc:
        raise CliFailure(f"invalid customScript placeholder configuration: {exc}", exit_code=2) from exc

    # Display payload summary.
    print("=" * 70)
    print("PAYLOAD SUMMARY")
    print("=" * 70)
    print(json.dumps(_display_redaction(installation_payload), indent=2))
    print()

    if getattr(args, "dry_run", False):
        print("=" * 70)
        print(f"[dry-run] Preflight OK. NOT calling POST /api/v1/servers/{server_id}/image.")
        print("=" * 70)
        return

    print("=" * 70)
    if not _confirm_install("Start the Netcup image installation now?", args):
        print("Installation cancelled.")
        return

    # Start installation.
    print()
    print("Starting installation...")
    try:
        result = client.post(f"/api/v1/servers/{server_id}/image", installation_payload_to_send)

        # Avoid leaking secrets (some APIs may include passwords/tokens).
        print(json.dumps(_display_redaction(result), indent=2))
        print()

        if "uuid" in result:
            task_uuid = result["uuid"]
            print("=" * 70)
            print("✓ Installation started successfully!")
            print("=" * 70)
            print(f"Task UUID: {task_uuid}")
            print()
            print("Monitor progress with:")
            print(f"  python3 monitor-task.py watch {task_uuid}")
            if _should_monitor(args):
                task_result = monitor_task(
                    client,
                    task_uuid,
                    poll_interval=args.poll_interval,
                    ssh_host=(getattr(args, "ssh_host", None) or ip_address),
                    ssh_user=args.ssh_user,
                    ssh_identity_file=getattr(args, "ssh_identity_file", None),
                    attach_custom_script=getattr(args, "attach_custom_script", True),
                    completion_marker=getattr(args, "completion_marker", None),
                    completion_wait_seconds=getattr(args, "completion_wait_seconds", 1800.0),
                )
                _apply_local_controller_retention(getattr(args, "ssh_identity_file", None), task_result)
    except HTTPStatusError as exc:
        raise CliFailure(_http_failure_message(exc)) from exc


def _expand_payload_placeholders(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Expand only the provider-neutral controller-key marker.

    The producer of a customScript owns every operating-system-specific
    setting, source URL, notification credential, and retention policy.  An
    unresolved marker is rejected instead of being silently sent to cloud-init.
    """
    expanded: Dict[str, Any] = json.loads(json.dumps(payload))
    cs = expanded.get("customScript")
    if not isinstance(cs, str):
        return expanded

    controller_pubkey = os.environ.get("CONTROLLER_SSH_PUBKEY", "") or CONTROLLER_SSH_PUBLIC_KEY

    def shell_single_quote_contents(value: str) -> str:
        return value.replace("'", "'\"'\"'")

    if "{{CONTROLLER_SSH_PUBKEY}}" in cs:
        if not controller_pubkey:
            raise ValueError(
                "customScript requires {{CONTROLLER_SSH_PUBKEY}}, but no controller identity is available"
            )
        cs = cs.replace(
            "{{CONTROLLER_SSH_PUBKEY}}",
            shell_single_quote_contents(controller_pubkey),
        )

    unresolved = sorted(set(re.findall(r"\{\{[^{}]+\}\}", cs)))
    if unresolved:
        raise ValueError(
            "unsupported or unresolved customScript placeholder(s): "
            + ", ".join(unresolved)
        )
    expanded["customScript"] = cs
    return expanded


def _refuse_unrendered_attach_only_identity(identity_file: str) -> None:
    """Adversarial-review finding, 2026-09-08: without this check,
    the attach verb with no explicit --ssh-identity-file override would fall
    through to SETTINGS["ssh.identity_file"] (now a per-host TEMPLATE, not a
    static path) still containing literal "{host}"/"{date}" text, and
    _ensure_local_identity_file_exists() would silently generate a brand-new
    keypair at that nonsense path -- one that was never installed on any
    host and can never match. That's exactly the SSH-monitoring failure mode
    (key mismatch) this whole redesign exists to close. Refuse loudly
    instead: attach must be told exactly which already-generated key
    to use.
    """
    if "{host}" in identity_file or "{date}" in identity_file:
        raise CliFailure(
            f"attach needs an explicit --ssh-identity-file (or "
            f"NETCUP_SCP_API_SSH_IDENTITY_FILE) pointing at the exact key already "
            f"used for this host's install -- the configured default "
            f"({identity_file!r}) is a per-host/per-date TEMPLATE and "
            f"cannot be resolved without knowing which host and date it was "
            f"generated for.",
            exit_code=2,
            show_help=True,
        )


def _build_authenticated_client() -> "NetcupSCPClient":
    """Refresh-token check + access-token fetch + client construction.

    Factored out so the wizard and file-driven install can build a client
    without duplicating refresh-token error handling.
    """
    try:
        netcup_scp_client.configure_api()
    except (SystemExit, ValueError) as exc:
        message = str(exc).removeprefix("ERROR: ")
        raise CliFailure(
            f"invalid shared Netcup API configuration: {message}", exit_code=2
        ) from exc
    refresh_token = os.environ.get("NETCUP_SCP_API_REFRESH_TOKEN")
    if not refresh_token:
        raise CliFailure(
            "missing NETCUP_SCP_API_REFRESH_TOKEN",
            exit_code=2,
            hint="run ./scp-api.py login first; it stores the refresh token in .env",
        )
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.output.secrets = (*_ACTIVE_RUNTIME.output.secrets, refresh_token)
    try:
        access_token = get_access_token(refresh_token)
    except (RuntimeError, ValueError) as exc:
        raise CliFailure(f"failed to get Netcup access token: {exc}") from exc
    return NetcupSCPClient(access_token, refresh_token=refresh_token)


def _validate_controller_retention(args: argparse.Namespace) -> None:
    local = getattr(args, "local_controller_key", None)
    if local is None:
        local = SETTINGS.get("ssh.controller_local_key_retention")
    if local is None:
        raise CliFailure(
            "local controller-key retention is missing from installer settings",
            exit_code=2,
        )
    args.local_controller_key = local
    if local not in {"remove", "retain"}:
        raise CliFailure(
            "local controller-key retention must be remove or retain",
            exit_code=2,
        )


def _set_controller_retention_environment(args: argparse.Namespace) -> None:
    global CONTROLLER_LOCAL_KEY_RETENTION
    _validate_controller_retention(args)
    CONTROLLER_LOCAL_KEY_RETENTION = args.local_controller_key


def _remove_local_controller_identity(identity_file: Optional[str]) -> None:
    if not identity_file:
        return
    path = Path(identity_file).expanduser()
    for candidate in (path, Path(str(path) + ".pub")):
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            print(f"[ssh] Retention requested removal but refusing non-regular path: {candidate}", file=sys.stderr)
            continue
        try:
            candidate.unlink()
            print(f"[ssh] Removed local controller key material: {candidate}")
        except OSError as exc:
            print(f"[ssh] WARNING: could not remove local controller key {candidate}: {exc}", file=sys.stderr)


def _apply_local_controller_retention(
    identity_file: Optional[str],
    task: Optional[Dict[str, Any]],
    local_retention: Optional[str] = None,
) -> None:
    if (local_retention or CONTROLLER_LOCAL_KEY_RETENTION).strip().lower() != "remove":
        return
    if not isinstance(task, dict) or task.get("custom_script_completed") is not True:
        print(
            "[ssh] Local controller key retained: the declared customScript "
            "completion marker was not observed; remove it manually only after "
            "checking the host.",
            file=sys.stderr,
        )
        return
    _remove_local_controller_identity(identity_file)


def _peek_payload_host_label(payload_path: str) -> Optional[str]:
    """Cheap, local, no-API-call peek at a config file's own "hostname"
    (or "serverId", as "netcup<id>") so the per-host SSH identity file gets
    a meaningful label before any network call happens - exactly the fields
    a wizard-produced payload already bakes in.
    Returns None (falls back to SERVER_NAME / _UNKNOWN_HOST_LABEL) if the
    file can't be read/parsed or has neither field.
    """
    try:
        with open(payload_path, "r", encoding="utf-8") as f:
            raw = f.read()
        payload = json.loads(_strip_jsonc_comments(raw))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        # Valid JSON but not an object (e.g. a bare array/string/number) -
        # not a real payload; let install_from_payload's own validation
        # produce the real error later instead of raising here.
        return None
    hostname = payload.get("hostname")
    if isinstance(hostname, str) and hostname:
        return hostname
    server_id = payload.get("serverId")
    if isinstance(server_id, int):
        return f"netcup{server_id}"
    return None


def _load_payload_for_target(
    payload_path: str,
    *,
    require_complete: bool = False,
) -> Dict[str, Any]:
    """Load and validate a payload before any controller key work."""
    try:
        raw = Path(payload_path).read_text(encoding="utf-8")
        payload = json.loads(_strip_jsonc_comments(raw))
    except FileNotFoundError as exc:
        raise CliFailure(f"config file not found: {payload_path}", exit_code=2) from exc
    except OSError as exc:
        raise CliFailure(f"cannot read config file {payload_path}: {exc}", exit_code=2) from exc
    except json.JSONDecodeError as exc:
        raise CliFailure(f"invalid JSON/JSONC in config file {payload_path}: {exc}", exit_code=2) from exc
    errors = _validate_installation_payload(payload, require_complete=require_complete)
    if errors:
        raise CliFailure(
            "config failed preflight validation: " + "; ".join(errors),
            exit_code=2,
            show_help=True,
        )
    return payload


def _target_server(
    client: "NetcupSCPClient",
    args: argparse.Namespace,
    payload: Optional[Dict[str, Any]],
) -> tuple[int, Dict[str, Any]]:
    """Resolve one target server, using the API picker when needed."""
    requested_name = SERVER_NAME
    requested_id = getattr(args, "server_id", None)
    if payload is not None:
        requested_id = payload.get("serverId")
        requested_name = payload.get("hostname") if requested_id is None else None

    if requested_id is not None:
        if isinstance(requested_id, bool) or not isinstance(requested_id, int) or requested_id <= 0:
            raise CliFailure("target server ID must be a positive integer", exit_code=2)
        record = client.get(f"/api/v1/servers/{requested_id}")
        if not isinstance(record, dict):
            raise CliFailure(f"server-details for server ID {requested_id} was not an object")
        return requested_id, record

    if requested_name:
        records = client.get("/api/v1/servers", params={"name": requested_name})
        if not isinstance(records, list) or not records:
            raise CliFailure(f"server {requested_name!r} was not found", exit_code=2)
        if len(records) != 1:
            raise CliFailure(f"server name {requested_name!r} is ambiguous; use --server-id", exit_code=2)
        record = records[0]
        if not isinstance(record, dict):
            raise CliFailure("server inventory returned a malformed record")
        return record.get("id"), record

    if is_noninteractive(args):
        raise CliFailure(
            "no target server supplied; set NETCUP_SCP_API_SERVER_NAME, use --server-id, "
            "or provide a config target",
            exit_code=2,
        )

    records = client.get("/api/v1/servers")
    if not isinstance(records, list) or not records:
        raise CliFailure("Netcup returned no selectable servers")
    choices: List[str] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), int):
            continue
        addresses = [
            item.get("ip") for item in record.get("ipv4Addresses", [])
            if isinstance(item, dict) and isinstance(item.get("ip"), str)
        ]
        label = record.get("hostname") or record.get("nickname") or "-"
        choices.append(f"{record.get('name', '-')} (id {record['id']}, {label}, {', '.join(addresses) or '-'})")
    if not choices:
        raise CliFailure("Netcup returned no valid selectable server records")
    index = _prompt_choice(choices, 0, "Select target server")
    selected = [record for record in records if isinstance(record, dict) and isinstance(record.get("id"), int)][index]
    return int(selected["id"]), selected


def _prepare_runtime_arguments(cli_args, runtime):
    global SETTINGS, SERVER_NAME
    load_env_file()
    SETTINGS = _load_runtime_settings()
    SERVER_NAME = os.environ.get("NETCUP_SCP_API_SERVER_NAME")
    cli_args.command = cli_args.verb
    cli_args.yes = runtime.yes
    cli_args.debug = runtime.debug
    cli_args.attach_only = cli_args.command == "attach"
    cli_args.config_path = getattr(cli_args, "config_path", None)
    cli_args.custom_script_file = getattr(cli_args, "custom_script_file", None)
    cli_args.completion_marker = getattr(cli_args, "completion_marker", None)
    try:
        cli_args.completion_marker = _validate_completion_marker(cli_args.completion_marker)
    except ValueError as exc:
        raise CliFailure(
            f"invalid --completion-marker: {exc}", exit_code=2, show_help=True
        ) from exc
    cli_args.dry_run = bool(getattr(cli_args, "dry_run", False))
    cli_args.ssh_key_ids = getattr(cli_args, "ssh_key_ids", None)
    cli_args.no_monitor = bool(getattr(cli_args, "no_monitor", False))
    cli_args.monitor = bool(getattr(cli_args, "monitor", False))
    cli_args.attach_custom_script = getattr(cli_args, "attach_custom_script", True)
    cli_args.ssh_host = getattr(cli_args, "ssh_host", None) or os.environ.get(
        "NETCUP_SCP_API_SSH_HOST"
    )
    cli_args.ssh_user = (
        getattr(cli_args, "ssh_user", None)
        or os.environ.get("NETCUP_SCP_API_SSH_USER")
        or SETTINGS["ssh.user"]
    )
    explicit_identity = getattr(cli_args, "ssh_identity_file", None) is not None or bool(
        os.environ.get("NETCUP_SCP_API_SSH_IDENTITY_FILE")
    )
    cli_args.identity_explicit = explicit_identity
    cli_args.ssh_identity_file = (
        getattr(cli_args, "ssh_identity_file", None)
        or os.environ.get("NETCUP_SCP_API_SSH_IDENTITY_FILE")
        or SETTINGS["ssh.identity_file"]
    )
    cli_args.server_id = getattr(cli_args, "server_id", None)
    if cli_args.server_id is None:
        configured_server_id = os.environ.get("NETCUP_SCP_API_SERVER_ID")
        if configured_server_id:
            try:
                cli_args.server_id = _positive_integer(configured_server_id)
            except argparse.ArgumentTypeError as exc:
                raise CliFailure(
                    f"invalid NETCUP_SCP_API_SERVER_ID: {exc}", exit_code=2
                ) from exc
    if cli_args.ssh_key_ids is not None:
        if len(set(cli_args.ssh_key_ids)) != len(cli_args.ssh_key_ids):
            raise CliFailure(
                "--ssh-key-id values must not contain duplicates",
                exit_code=2,
                show_help=True,
            )
    if cli_args.command == "install" and cli_args.server_id is not None:
        raise CliFailure(
            "install reads its target from --config/target-host.jsonc; do not combine it with --server-id",
            exit_code=2,
            show_help=True,
        )
    if cli_args.custom_script_file and cli_args.command not in {"wizard", "configure"}:
        raise CliFailure(
            "--custom-script-file is only valid with wizard or configure",
            exit_code=2,
            show_help=True,
        )
    if cli_args.config_path and cli_args.server_id is not None and cli_args.command not in {"wizard", "configure"}:
        raise CliFailure(
            "--config/--payload supplies its own target; do not combine it with --server-id",
            exit_code=2,
            show_help=True,
        )
    if cli_args.no_monitor and cli_args.monitor:
        raise CliFailure(
            "--monitor and --no-monitor cannot be combined",
            exit_code=2,
            show_help=True,
        )
    if cli_args.command == "install" and not cli_args.no_monitor:
        cli_args.monitor = True
    if getattr(cli_args, "poll_interval", None) is None:
        cli_args.poll_interval = SETTINGS["ssh.poll_interval"]
    if getattr(cli_args, "completion_wait_seconds", None) is None:
        cli_args.completion_wait_seconds = SETTINGS["ssh.completion_wait_seconds"]
    if getattr(cli_args, "attach_initial_delay", None) is None:
        cli_args.attach_initial_delay = SETTINGS["ssh.attach_initial_delay"]
    if getattr(cli_args, "attach_max_wait_seconds", None) is None:
        cli_args.attach_max_wait_seconds = SETTINGS["ssh.attach_max_wait_seconds"]
    if getattr(cli_args, "simulate_disconnect_seconds", None) is None:
        cli_args.simulate_disconnect_seconds = None
    if getattr(cli_args, "attach_task_uuid", None) is None:
        cli_args.attach_task_uuid = None
    if getattr(cli_args, "local_controller_key", None) is None:
        cli_args.local_controller_key = os.environ.get(
            "NETCUP_SCP_API_CONTROLLER_KEY_LOCAL_RETENTION",
            SETTINGS["ssh.controller_local_key_retention"],
        )
    _set_controller_retention_environment(cli_args)
    return cli_args


def _run_install_workflow(cli_args, runtime):
    global args, SERVER_NAME, CONTROLLER_SSH_PUBLIC_KEY, _ACTIVE_RUNTIME
    previous = (
        netcup_scp_client.DEBUG,
        netcup_scp_client.DEBUG_RAW,
        netcup_scp_client.DEBUG_LOGGER,
        _ACTIVE_RUNTIME,
    )
    _ACTIVE_RUNTIME = runtime
    try:
        args = _prepare_runtime_arguments(cli_args, runtime)
        netcup_scp_client.DEBUG = (
            netcup_scp_client.DEBUG
            or os.environ.get("NETCUP_SCP_API_DEBUG", "no").lower()
            in ("yes", "true", "1")
            or runtime.debug
        )
        netcup_scp_client.DEBUG_RAW = runtime.debug_raw
        netcup_scp_client.DEBUG_LOGGER = logging.getLogger("netcup.install_host.client")
        return _run_install_workflow_body(args, runtime)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        message = str(exc.code or "installation failed").removeprefix("ERROR: ")
        raise CliFailure(message) from exc
    finally:
        (
            netcup_scp_client.DEBUG,
            netcup_scp_client.DEBUG_RAW,
            netcup_scp_client.DEBUG_LOGGER,
            _ACTIVE_RUNTIME,
        ) = previous


def _run_install_workflow_body(args, runtime):
    global SERVER_NAME, CONTROLLER_SSH_PUBLIC_KEY
    command = args.command
    config_path = getattr(args, "config_path", None) or getattr(args, "payload", None)
    if command == "install" and not config_path:
        config_path = DEFAULT_TARGET_CONFIG_NAME
    input_config_path = config_path if command in {None, "install"} else None
    if input_config_path:
        # Keep the existing internal payload attribute for helper/test callers;
        # the public spelling is now --config.
        args.payload = input_config_path
        if command == "install" and not getattr(args, "no_monitor", False):
            # File-driven installation is an end-to-end operation by default:
            # create the task and follow it.  --no-monitor is the explicit
            # escape hatch for callers that only need task creation.
            args.monitor = True

    identity_explicit = args.identity_explicit

    if command == "configure":
        print("[compat] configure is the interactive installer wizard; use `wizard` for the same flow.")

    # Refuse malformed safety configuration before rendering a local key or
    # doing any installer-side API work.  A malformed denylist is never
    # treated as an empty policy.
    attach_only = getattr(args, "attach_only", False)
    client: Optional["NetcupSCPClient"] = None
    server_lookup: Optional[List[Dict[str, Any]]] = None
    if attach_only:
        if not getattr(args, "ssh_host", None):
            raise CliFailure(
                "attach requires --ssh-host or NETCUP_SCP_API_SSH_HOST",
                exit_code=2,
                show_help=True,
            )
        if identity_explicit:
            _refuse_unrendered_attach_only_identity(args.ssh_identity_file)
            _validate_existing_identity(args.ssh_identity_file)
        else:
            args.ssh_identity_file = None
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        attach_task_uuid = getattr(args, "attach_task_uuid", None) or f"attach-only-{ts}"
        follower = _SSHCustomScriptFollower(
            task_uuid=attach_task_uuid,
            host=getattr(args, "ssh_host"),
            user=args.ssh_user,
            identity_file=getattr(args, "ssh_identity_file", None),
            poll_interval=args.poll_interval,
            initial_delay=args.attach_initial_delay,
            max_wait_seconds=args.attach_max_wait_seconds,
            simulate_disconnect_seconds=getattr(args, "simulate_disconnect_seconds", None),
        )
        follower.start()
        try:
            print("[attach] Streaming until Ctrl-C.")
            while True:
                time.sleep(1)
        finally:
            follower.stop()
        return

    # Refuse malformed local protection before any API request or key work.
    _protected_server_policy_configured()

    # Parse/validate a file-driven request locally before authentication.  A
    # typo in a saved config should not be masked by a missing/expired token,
    # and it must never reach controller-key handling.
    payload_for_target = (
        _load_payload_for_target(
            input_config_path,
            require_complete=command == "install",
        )
        if input_config_path
        else None
    )
    wizard_custom_script = (
        _load_custom_script_file(args.custom_script_file)
        if not input_config_path and getattr(args, "custom_script_file", None)
        else (None, None)
    )
    wizard_custom_script_text, wizard_completion_marker = wizard_custom_script
    if wizard_completion_marker and not getattr(args, "completion_marker", None):
        args.completion_marker = wizard_completion_marker

    # Authentication and target/protection checks deliberately precede every
    # controller-key read or generation.
    client = _build_authenticated_client()
    try:
        server_id, target_record = _target_server(client, args, payload_for_target)
        server_details = (
            target_record
            if isinstance(target_record, dict) and "serverLiveInfo" in target_record
            else client.get(f"/api/v1/servers/{server_id}")
        )
    except HTTPStatusError as exc:
        raise CliFailure(f"Netcup target lookup failed: {_http_failure_message(exc)}") from exc
    if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id <= 0:
        raise CliFailure("API returned an invalid target server ID")
    if not isinstance(server_details, dict):
        raise CliFailure(f"server-details for server ID {server_id} was not an object")
    merged_target = {**target_record, **server_details}
    server_lookup = [merged_target]
    if not getattr(args, "dry_run", False):
        _ensure_server_mutation_allowed(server_id, merged_target.get("name"), "Netcup image installation")
    SERVER_NAME = str(merged_target.get("name") or SERVER_NAME or f"netcup{server_id}")
    os.environ["NETCUP_SCP_API_SERVER_NAME"] = SERVER_NAME

    host_label = (
        (payload_for_target or {}).get("hostname")
        or
        merged_target.get("hostname")
        or merged_target.get("nickname")
        or merged_target.get("name")
        or f"netcup{server_id}"
    )

    # A plain Netcup image install does not need a controller key.  Generate or
    # reuse one only when the selected customScript consumes it, or when the
    # operator explicitly supplied an existing identity for post-install SSH.
    needs_controller_key = _payload_needs_controller_key(payload_for_target)
    if wizard_custom_script_text:
        needs_controller_key = "{{CONTROLLER_SSH_PUBKEY}}" in wizard_custom_script_text
    if needs_controller_key:
        args.ssh_identity_file = _resolve_or_create_controller_identity(
            args.ssh_identity_file,
            str(host_label),
            explicit=identity_explicit,
            controller_fqdn=SETTINGS["ssh.controller_fqdn"],
        )
        CONTROLLER_SSH_PUBLIC_KEY = _read_public_key_for_identity(args.ssh_identity_file)
    elif identity_explicit:
        _validate_existing_identity(args.ssh_identity_file)
        CONTROLLER_SSH_PUBLIC_KEY = ""
    else:
        args.ssh_identity_file = None
        CONTROLLER_SSH_PUBLIC_KEY = ""

    # If payload file is provided, use direct installation mode
    if input_config_path:
        install_from_payload(
            client,
            input_config_path,
            args,
            require_complete=command == "install",
        )
        return

    print("=" * 70)
    print(f"Gathering installation information for server:   {SERVER_NAME}")
    print("=" * 70)
    print()

    try:
        # 1. Find server by name (reuse the identity-labeling lookup above,
        # if one already happened, instead of a second/duplicate GET)
        print(f"1. Finding server '{SERVER_NAME}'...")
        servers = server_lookup if server_lookup is not None else client.get(
            "/api/v1/servers", params={"name": SERVER_NAME}
        )

        if not servers:
            raise CliFailure(f"server {SERVER_NAME!r} was not found", exit_code=2)

        server_id = servers[0]["id"]
        print(f"   ✓ Server ID: {server_id}")
        print()

        # 2. Use the already validated server details (target resolution ran
        # before controller-key work and intentionally avoids a duplicate GET).
        print("2. Using validated server details...")
        # Example response:
        # {
        #   "id": 804027,
        #   "name": "v2202511209318406253",
        #   "disabled": false,
        #   "hostname": "v1001.vxxu.de",
        #   "nickname": "v1001.vxxu.de",
        #   "template": {
        #     "id": 1538,
        #     "name": "VPS 1000 G12 Pro"
        #   },
        #   "architecture": "AMD64",
        #   "disksAvailableSpaceInMiB": 7864320,
        #   "firewallFeatureActive": true,
        #   "ipv4Addresses": [
        #     {
        #       "broadcast": "152.53.167.255",
        #       "gateway": "152.53.164.1",
        #       "id": 227115,
        #       "ip": "152.53.166.181",
        #       "netmask": "255.255.252.0"
        #     }
        #   ],
        #   "ipv6Addresses": [
        #     {
        #       "gateway": "fe80::1",
        #       "id": 990975,
        #       "networkPrefix": "2a0a:4cc0:2000:9798::",
        #       "networkPrefixLength": 64
        #     }
        #   ],
        #   "maxCpuCount": 4,
        #   "rescueSystemActive": false,
        #   "serverLiveInfo": {
        #     "autostart": true,
        #     "bootorder": [
        #       "HDD",
        #       "CDROM",
        #       "NETWORK"
        #     ],
        #     "cloudinitAttached": false,
        #     "configChanged": false,
        #     "coresPerSocket": 1,
        #     "cpuCount": 4,
        #     "cpuMaxCount": 4,
        #     "currentServerMemoryInMiB": 8192,
        #     "disks": [
        #       {
        #         "allocationInMiB": 1275,
        #         "capacityInMiB": 524288,
        #         "dev": "vda",
        #         "driver": "virtio"
        #       }
        #     ],
        #     "interfaces": [
        #       {
        #         "driver": "virtio",
        #         "ipv4Addresses": [
        #           "152.53.166.181"
        #         ],
        #         "ipv6LinkLocalAddresses": [
        #           "fe80::6820:60ff:fee8:068f"
        #         ],
        #         "ipv6NetworkPrefixes": [
        #           "2a0a:4cc0:2000:9798::/64"
        #         ],
        #         "mac": "6a:20:60:e8:06:8f",
        #         "mtu": 1500,
        #         "rxMonthlyInMiB": 1838,
        #         "speedInMBits": 2500,
        #         "trafficThrottled": false,
        #         "txMonthlyInMiB": 443,
        #         "vlanId": null,
        #         "vlanInterface": false
        #       }
        #     ],
        #     "keyboardLayout": "en-us",
        #     "latestQemu": true,
        #     "machineType": "pc-i440fx-9.2",
        #     "maxServerMemoryInMiB": 8192,
        #     "nestedGuest": false,
        #     "osOptimization": "LINUX",
        #     "requiredStorageOptimization": "NO",
        #     "sockets": 4,
        #     "state": "RUNNING",
        #     "template": "VPS 1000 G12 Pro",
        #     "uefi": true,
        #     "uptimeInSeconds": 235018
        #   },
        #   "site": {
        #     "city": "Manassas",
        #     "id": 6
        #   },
        #   "snapshotAllowed": true,
        #   "snapshotCount": 1
        # }        

        disk_dev = server_details["serverLiveInfo"]["disks"][0]["dev"]
        disk_capacity_gib = server_details["serverLiveInfo"]["disks"][0]["capacityInMiB"] / 1024
        
        # Get IP address and perform reverse DNS lookup
        # Try to get IPv4 address from either ipv4Addresses or interfaces
        ip_address = _extract_primary_ipv4(server_details)
        hostname_method = None
        
        if ip_address:
            try:
                hostname_result = socket.gethostbyaddr(ip_address)
                hostname = hostname_result[0]  # FQDN from reverse DNS
                hostname_method = f"reverse DNS lookup on {ip_address}"
                print(f"   ✓ IP Address: {ip_address}")
                print(f"   ✓ Hostname (reverse DNS): {hostname}")
            except (socket.herror, socket.gaierror) as e:
                # Fallback to configured hostname if reverse DNS fails
                hostname = server_details.get("hostname", SERVER_NAME)
                hostname_method = f"configured hostname (reverse DNS failed for {ip_address})"
                print(f"   ⚠ Reverse DNS failed for {ip_address}: {e}")
                print(f"   ✓ Using configured hostname: {hostname}")
        else:
            # No IP found, use configured hostname
            hostname = server_details.get("hostname", SERVER_NAME)
            hostname_method = "configured hostname (no IP address found)"
            print(f"   ⚠ No IP address found in server details")
            print(f"   ✓ Using configured hostname: {hostname}")
        
        print(f"   ✓ Primary Disk: {disk_dev} ({disk_capacity_gib:.0f} GiB)")
        print()

        # 3. Resolve image flavour (interactive: numbered prompt; else: newest)
        print("3. Resolving Debian UEFI image flavour...")
        interactive = not is_noninteractive(args)
        flavour = _resolve_image_flavour(client, int(server_id), None, interactive=interactive)
        image_flavour_id = flavour["id"]
        image_flavour_name = flavour["name"]
        print()

        # 4. Get user ID
        print("4. Getting user information...")
        user_info = client.get_user_info()
        user_id = user_info["id"]
        print(f"   ✓ User ID: {user_id}")
        print()

        # 5. Resolve SSH keys (identity-file key merged with existing keys;
        # interactive: numbered prompt over existing keys when no identity file)
        print("5. Resolving SSH keys...")
        ssh_key_ids = _resolve_ssh_key_ids(
            client, user_id, getattr(args, "ssh_key_ids", None),
            getattr(args, "ssh_identity_file", None),
            interactive=interactive,
            dry_run=getattr(args, "dry_run", False),
        )
        print()

        # 6. Prepare installation payload.  A wizard customScript is opaque:
        # it was produced by another tool and is passed through unchanged
        # apart from the generic controller-key marker expansion below.
        installation_payload = {
            **INSTALLATION_CONFIG,
            "serverId": server_id,  # Include for --payload mode
            "hostname": hostname,  # Use reverse DNS hostname from server info
            "imageFlavourId": image_flavour_id,
            "diskName": disk_dev,
            "sshKeyIds": ssh_key_ids,
        }
        if wizard_custom_script_text:
            installation_payload["customScript"] = wizard_custom_script_text
        if ssh_key_ids is None:
            installation_payload.pop("sshKeyIds")

        # Expand placeholders only for the API request, while keeping the
        # payload safe-to-print/save.
        try:
            installation_payload_to_send = _expand_payload_placeholders(installation_payload)
        except ValueError as exc:
            raise CliFailure(
                f"invalid customScript placeholder configuration: {exc}",
                exit_code=2,
            ) from exc

        # 7. Display summary
        print("=" * 70)
        print("INSTALLATION PARAMETERS SUMMARY")
        print("=" * 70)
        print(json.dumps(_display_redaction(installation_payload), indent=2))
        print()

        if getattr(args, "dry_run", False):
            print("=" * 70)
            print(
                f"[dry-run] NOT saving to {getattr(args, 'config_path', None) or DEFAULT_TARGET_CONFIG_NAME} "
                "(would overwrite any existing "
                "file, and any not-yet-created SSH key above is only a placeholder id)."
            )
            print(f"[dry-run] Preflight OK. NOT calling POST /api/v1/servers/{server_id}/image.")
            print("=" * 70)
            return

        # 8. Save payload to file with comments
        output_config_path = getattr(args, "config_path", None) or DEFAULT_TARGET_CONFIG_NAME
        save_payload_with_comments(
            installation_payload,
            output_config_path,
            SERVER_NAME,
            image_flavour_name,
            user_id,
            ssh_key_names=None,
            hostname_method=hostname_method
        )
        print(f"✓ Installation payload saved to:  {output_config_path}")
        print()

        # Consent is required even when stdin is redirected; --yes is the
        # explicit automation opt-in and does not bypass preflight checks.
        print("=" * 70)
        if not _confirm_install("Start the Netcup image installation now?", args):
            print("Installation cancelled.")
            return

        # 10. Start installation
        print()
        print("Starting installation...")
        try:
            result = client.post(f"/api/v1/servers/{server_id}/image", installation_payload_to_send)

            print(json.dumps(_display_redaction(result), indent=2))
            print()

            if "uuid" in result:
                task_uuid = result["uuid"]
                print("=" * 70)
                print("✓ Installation started successfully!")
                print("=" * 70)
                print(f"Task UUID: {task_uuid}")
                print()
                print("Monitor progress with:")
                print(f"  python3 monitor-task.py watch {task_uuid}")
                if _should_monitor(args):
                    ssh_identity = getattr(args, "ssh_identity_file", None)
                    task_result = monitor_task(
                        client,
                        task_uuid,
                        poll_interval=args.poll_interval,
                        ssh_host=(getattr(args, "ssh_host", None) or ip_address),
                        ssh_user=args.ssh_user,
                        ssh_identity_file=ssh_identity,
                        attach_custom_script=getattr(args, "attach_custom_script", True),
                        completion_marker=getattr(args, "completion_marker", None),
                        completion_wait_seconds=getattr(args, "completion_wait_seconds", 1800.0),
                    )
                    _apply_local_controller_retention(ssh_identity, task_result)
        except HTTPStatusError as e:
            status = getattr(e, "status", None)
            if status == 409:
                try:
                    error_data = json.loads(e.body) if getattr(e, "body", "") else None
                except Exception:
                    error_data = None

                code = error_data.get("code") if isinstance(error_data, dict) else None
                if code == "server.lock.error":
                    active_task = _find_active_task_for_server(client, int(server_id))
                    if active_task and isinstance(active_task.get("uuid"), str):
                        task_uuid = active_task["uuid"]
                        print("⚠ Server is locked (installation already running).")
                        print(f"✓ Monitoring existing task instead: {task_uuid}")

                        if _should_monitor(args):
                            ssh_identity = getattr(args, "ssh_identity_file", None)
                            task_result = monitor_task(
                                client,
                                task_uuid,
                                poll_interval=args.poll_interval,
                                ssh_host=(getattr(args, "ssh_host", None) or ip_address),
                                ssh_user=args.ssh_user,
                                ssh_identity_file=ssh_identity,
                                attach_custom_script=getattr(args, "attach_custom_script", True),
                                completion_marker=getattr(args, "completion_marker", None),
                                completion_wait_seconds=getattr(args, "completion_wait_seconds", 1800.0),
                            )
                            _apply_local_controller_retention(ssh_identity, task_result)
                            return

                        print("Monitor progress with:")
                        print(f"  python3 monitor-task.py watch {task_uuid}")
                        return
            raise

    except HTTPStatusError as exc:
        raise CliFailure(_http_failure_message(exc)) from exc
    except KeyError as e:
        raise CliFailure(f"Netcup API response is missing expected field: {e}") from e


def main(argv=None) -> int:
    return build_cli().run(
        argv=argv,
        expected_exceptions=(
            netcup_scp_client.NetcupAPIError,
            OSError,
            subprocess.SubprocessError,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
