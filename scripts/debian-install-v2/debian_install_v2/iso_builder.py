"""Interactive builder for private NoCloud seed ISOs."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from cli_extended import CliFailure

from .config import ConfigError, load_config, require_notify_credentials
from .customscript import REPO_BRANCH_DEFAULT, REPO_URL_DEFAULT, resolve_bootstrap_url
from .profiles import (
    BUILTIN_PROFILE_DIR,
    load_builtin_profile,
    parse_profile_jsonc,
    list_builtin_profiles,
)

_PAYLOAD_PATH = "/run/vbpub-iso-payload.json"
_LAUNCHER_PATH = "/run/vbpub-iso-bootstrap.py"
_SEED_SCHEMA_VERSION = 1
_CLI_EXTENDED_LATEST_URL_DEFAULT = (
    "https://github.com/volkb79-2/vbpub/releases/download/"
    "cli-extended-latest/latest.json"
)

_ISO_LAUNCHER = r'''#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys
import urllib.request

payload_path = Path("/run/vbpub-iso-payload.json")
try:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
finally:
    payload_path.unlink(missing_ok=True)
if not isinstance(payload, dict) or payload.get("schema_version") != 1:
    print("vbpub ISO bootstrap: invalid private payload", file=sys.stderr)
    raise SystemExit(1)
for name, value in payload.get("environment", {}).items():
    if not isinstance(name, str) or not isinstance(value, str):
        print("vbpub ISO bootstrap: invalid environment entry", file=sys.stderr)
        raise SystemExit(1)
    os.environ[name] = value
url = payload.get("bootstrap_url")
if not isinstance(url, str) or not url.startswith("https://"):
    print("vbpub ISO bootstrap: invalid bootstrap URL", file=sys.stderr)
    raise SystemExit(1)
try:
    request = urllib.request.Request(url, headers={"User-Agent": "vbpub-iso-bootstrap"})
    with urllib.request.urlopen(request, timeout=60) as response:
        source = response.read(1024 * 1024 + 1)
    if len(source) > 1024 * 1024:
        raise ValueError("bootstrap exceeds the 1 MiB limit")
    if not source.strip():
        raise ValueError("downloaded bootstrap source was empty")
except Exception as exc:
    print(f"vbpub ISO bootstrap: could not fetch bootstrap: {exc}", file=sys.stderr)
    raise SystemExit(1)
exec(compile(source, url, "exec"), {"__name__": "__main__", "__file__": url})
'''


def _validate_profile_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise ConfigError("profile URL is not valid") from None
    if (
        parts.scheme != "https" or not parts.hostname or parts.username
        or parts.password or parts.query or parts.fragment
        or any(char.isspace() for char in url)
    ):
        raise ConfigError(
            "profile URL must be HTTPS and must not contain userinfo, query, "
            "fragment, or whitespace; enter Basic Auth separately"
        )
    return url


def _validate_repo(repo_url: str, branch: str) -> None:
    try:
        parts = urllib.parse.urlsplit(repo_url)
    except ValueError:
        raise ConfigError("repository URL is not valid") from None
    if (
        parts.scheme != "https" or not parts.hostname or parts.username
        or parts.password or parts.query or parts.fragment
        or any(char.isspace() for char in repo_url)
    ):
        raise ConfigError(
            "repository must be a public HTTPS Git remote without userinfo, "
            "query, fragment, or whitespace"
        )
    if not branch or branch.startswith("-") or any(char.isspace() for char in branch):
        raise ConfigError("branch must be a non-empty Git branch name without whitespace")


def _ask(questionary: Any, kind: str, prompt: str, *, default: str = "") -> str:
    method = getattr(questionary, kind)
    kwargs = {"default": default} if kind != "password" else {}
    value = method(prompt, **kwargs).ask(kbi_msg="")
    if value is None:
        raise KeyboardInterrupt
    return str(value)


def _read_local_profile(path_text: str) -> str:
    path = Path(path_text).expanduser()
    if path.is_symlink() or not path.is_file():
        raise CliFailure(f"profile file is missing or is a symlink: {path}", exit_code=2)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CliFailure(f"could not read profile file: {exc}", exit_code=2) from None
    if len(data) > 1024 * 1024:
        raise CliFailure("profile file exceeds the 1 MiB limit", exit_code=2)
    try:
        text = data.decode("utf-8")
        parse_profile_jsonc(text, source="local profile")
    except (UnicodeDecodeError, ConfigError) as exc:
        raise CliFailure(f"local profile is invalid: {exc}", exit_code=2) from None
    return text


def _choose_profile(questionary: Any) -> tuple[str, dict[str, str]]:
    source_choice = questionary.select(
        "Profile source",
        choices=(
            "Released profile (follows the selected repository branch)",
            "Local JSONC profile (embedded in this ISO)",
            "HTTPS JSONC profile (fetched when the VM boots)",
        ),
        default="Released profile (follows the selected repository branch)",
    ).ask(kbi_msg="")
    if source_choice is None:
        raise KeyboardInterrupt

    if source_choice.startswith("Released profile"):
        profile_ids = list_builtin_profiles(profile_dir=BUILTIN_PROFILE_DIR)
        profile_id = questionary.select(
            "Released profile id",
            choices=list(profile_ids),
            default="default" if "default" in profile_ids else profile_ids[0],
        ).ask(kbi_msg="")
        if profile_id is None:
            raise KeyboardInterrupt
        load_builtin_profile(profile_id, profile_dir=BUILTIN_PROFILE_DIR)
        return "builtin", {"VBPUB_PROFILE_KIND": "builtin", "VBPUB_PROFILE_ID": profile_id}

    if source_choice.startswith("Local JSONC"):
        path_text = _ask(questionary, "text", "Path to local JSONC profile")
        text = _read_local_profile(path_text)
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return "embedded", {
            "VBPUB_PROFILE_KIND": "embedded",
            "VBPUB_PROFILE_JSONC_B64": encoded,
        }

    if source_choice.startswith("HTTPS JSONC"):
        url = _validate_profile_url(_ask(questionary, "text", "HTTPS profile URL"))
        username = _ask(questionary, "text", "Basic Auth username (blank for public)")
        password = _ask(questionary, "password", "Basic Auth password") if username else ""
        if ":" in username or any(ord(char) < 0x20 or ord(char) == 0x7f for char in username + password):
            raise CliFailure("Basic Auth username/password contains an unsupported character", exit_code=2)
        return "https", {
            "VBPUB_PROFILE_KIND": "https",
            "VBPUB_PROFILE_URL": url,
            "VBPUB_PROFILE_AUTH_USERNAME": username,
            "VBPUB_PROFILE_AUTH_PASSWORD": password,
        }
    raise CliFailure("unknown profile source choice", exit_code=2)


def _host_settings(questionary: Any, *, require_ssh_key: bool) -> dict[str, Any]:
    keys: list[str] = []
    while True:
        key = _ask(
            questionary,
            "text",
            "Persistent root SSH public-key line (blank finishes the list)",
        ).strip()
        if not key:
            break
        try:
            config = load_config(raw_json=json.dumps({"ssh_authorized_keys": [*keys, key]}))
        except ConfigError as exc:
            raise CliFailure(
                f"persistent SSH key {len(keys) + 1} is invalid: {exc}", exit_code=2
            ) from None
        keys = config.ssh_authorized_keys
    if require_ssh_key and not keys:
        raise CliFailure(
            "the vanilla profile requires at least one persistent SSH public key",
            exit_code=2,
        )
    _validate_ssh_keys_with_ssh_keygen(keys)

    backend = questionary.select(
        "Notification backend",
        choices=("none", "mattermost", "telegram"),
        default="none",
    ).ask(kbi_msg="")
    if backend is None:
        raise KeyboardInterrupt
    result: dict[str, Any] = {"notify_backend": backend}
    if backend == "mattermost":
        result["mattermost_webhook_url"] = _ask(
            questionary, "password", "Mattermost incoming-webhook URL (hidden input)"
        )
        result["notify_host_label"] = _ask(
            questionary, "text", "Notification host label (optional)"
        )
    elif backend == "telegram":
        result["telegram_bot_token"] = _ask(
            questionary, "password", "Telegram bot token (hidden input)"
        )
        result["telegram_chat_id"] = _ask(questionary, "text", "Telegram chat id")

    if keys:
        result["ssh_authorized_keys"] = keys
    try:
        config = load_config(raw_json=json.dumps(result))
        require_notify_credentials(config)
    except ConfigError as exc:
        raise CliFailure(f"per-host settings are invalid: {exc}", exit_code=2) from None
    return result


def _validate_ssh_keys_with_ssh_keygen(keys: list[str]) -> None:
    """Check key usability with OpenSSH's parser before putting keys on an ISO."""
    if not keys:
        return
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise CliFailure(
            "ssh-keygen is required to validate persistent SSH public keys",
            exit_code=2,
            hint="install openssh-client on the ISO build host",
        )
    try:
        with tempfile.TemporaryDirectory(prefix="vbpub-ssh-key-check-") as temporary:
            for index, key in enumerate(keys, start=1):
                key_path = Path(temporary) / f"key-{index}.pub"
                key_path.write_text(key + "\n", encoding="utf-8")
                key_path.chmod(0o600)
                checked = subprocess.run(
                    [ssh_keygen, "-lf", str(key_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if checked.returncode:
                    raise CliFailure(
                        f"persistent SSH key {index} is not accepted by ssh-keygen",
                        exit_code=2,
                    )
    except CliFailure:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise CliFailure(f"could not validate SSH public keys: {exc}", exit_code=2) from None


def _source_settings(questionary: Any) -> tuple[str, str, str, str]:
    repo_url = _ask(questionary, "text", "HTTPS Git repository", default=REPO_URL_DEFAULT).strip()
    branch = _ask(questionary, "text", "Moving branch/channel", default=REPO_BRANCH_DEFAULT).strip()
    _validate_repo(repo_url, branch)
    bootstrap_url = None
    try:
        bootstrap_url = resolve_bootstrap_url(repo_url=repo_url, repo_branch=branch)
    except ValueError:
        bootstrap_url = _ask(
            questionary,
            "text",
            "HTTPS URL for bootstrap-remote.py (required for a custom repository)",
        ).strip()
        try:
            parts = urllib.parse.urlsplit(bootstrap_url)
        except ValueError:
            raise CliFailure("bootstrap URL is not valid", exit_code=2) from None
        if (
            parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment
            or any(char.isspace() for char in bootstrap_url)
        ):
            raise CliFailure("bootstrap URL must be HTTPS without userinfo/query/fragment", exit_code=2)
    wheel_manifest = _ask(
        questionary,
        "text",
        "HTTPS cli-extended release manifest URL",
        default=_CLI_EXTENDED_LATEST_URL_DEFAULT,
    ).strip()
    try:
        parts = urllib.parse.urlsplit(wheel_manifest)
    except ValueError:
        raise CliFailure("cli-extended manifest URL is not valid", exit_code=2) from None
    if (
        parts.scheme != "https" or not parts.hostname or parts.username
        or parts.password or parts.query or parts.fragment
        or any(char.isspace() for char in wheel_manifest)
    ):
        raise CliFailure(
            "cli-extended manifest URL must be HTTPS without userinfo/query/fragment",
            exit_code=2,
        )
    return repo_url, branch, bootstrap_url, wheel_manifest


def render_nocloud_user_data(payload: dict[str, Any]) -> tuple[str, str]:
    """Return cloud-config user-data and NoCloud meta-data as text."""
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    cloud_config = {
        "write_files": [
            {
                "path": _PAYLOAD_PATH,
                "owner": "root:root",
                "permissions": "0600",
                "encoding": "b64",
                "content": base64.b64encode(payload_json).decode("ascii"),
            },
            {
                "path": _LAUNCHER_PATH,
                "owner": "root:root",
                "permissions": "0700",
                "encoding": "b64",
                "content": base64.b64encode(_ISO_LAUNCHER.encode("utf-8")).decode("ascii"),
            },
        ],
        "runcmd": [["/usr/bin/python3", _LAUNCHER_PATH]],
    }
    user_data = "#cloud-config\n" + json.dumps(cloud_config, indent=2) + "\n"
    meta_data = json.dumps(
        {"instance-id": f"iid-vbpub-{uuid.uuid4()}", "local-hostname": "debian-vbpub"},
        indent=2,
    ) + "\n"
    return user_data, meta_data


def _preflight_iso_output(output_path: str | Path) -> tuple[Path, str]:
    output = Path(output_path).expanduser().absolute()
    if output.suffix.lower() != ".iso":
        raise CliFailure("output path must end in .iso", exit_code=2)
    if not output.parent.is_dir():
        raise CliFailure(f"output directory does not exist: {output.parent}", exit_code=2)
    if output.exists() or output.is_symlink():
        raise CliFailure(f"refusing to overwrite existing ISO artifact: {output}", exit_code=2)
    cloud_localds = shutil.which("cloud-localds")
    if not cloud_localds:
        raise CliFailure(
            "cloud-localds is required to build the NoCloud seed ISO",
            exit_code=2,
            hint="install cloud-image-utils on the Linux build host",
        )
    return output, cloud_localds


def build_iso(output_path: str | Path, payload: dict[str, Any]) -> Path:
    """Create a mode-0600 NoCloud seed ISO without replacing existing artifacts."""
    output, cloud_localds = _preflight_iso_output(output_path)

    user_data, meta_data = render_nocloud_user_data(payload)
    try:
        with tempfile.TemporaryDirectory(prefix=".vbpub-iso-", dir=output.parent) as temporary:
            temp_dir = Path(temporary)
            user_path = temp_dir / "user-data"
            meta_path = temp_dir / "meta-data"
            iso_path = temp_dir / "seed.iso"
            user_path.write_text(user_data, encoding="utf-8")
            meta_path.write_text(meta_data, encoding="utf-8")
            completed = subprocess.run(
                [cloud_localds, str(iso_path), str(user_path), str(meta_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode:
                detail = (completed.stderr or completed.stdout).strip().splitlines()
                message = detail[-1] if detail else f"exit {completed.returncode}"
                raise CliFailure(f"cloud-localds failed: {message}", exit_code=1)
            if not iso_path.is_file() or iso_path.stat().st_size == 0:
                raise CliFailure("cloud-localds succeeded without producing a non-empty ISO", exit_code=1)
            iso_path.chmod(0o600)
            try:
                os.link(iso_path, output)
            except FileExistsError:
                raise CliFailure(f"refusing to overwrite existing ISO artifact: {output}", exit_code=2) from None
            except OSError as exc:
                raise CliFailure(f"could not publish ISO artifact at {output}: {exc}", exit_code=1) from None
    except CliFailure:
        raise
    except OSError as exc:
        raise CliFailure(f"could not build ISO artifact: {exc}", exit_code=1) from None
    return output


def run_build_iso_wizard(*, output_path: str, runtime: Any) -> int:
    if not runtime.output.is_interactive:
        raise CliFailure(
            "build-iso requires an interactive terminal",
            exit_code=2,
            hint="run it in a terminal so host-specific credentials and SSH access are collected safely",
        )
    _preflight_iso_output(output_path)
    from .wizard import _load_questionary

    questionary = _load_questionary()
    try:
        profile_kind, profile_environment = _choose_profile(questionary)
        if profile_kind == "builtin":
            profile_id = profile_environment["VBPUB_PROFILE_ID"]
            require_ssh_key = profile_id == "vanilla"
        else:
            require_ssh_key = False
        repo_url, branch, bootstrap_url, wheel_manifest = _source_settings(questionary)
        host_settings = _host_settings(questionary, require_ssh_key=require_ssh_key)
        secrets = tuple(
            value for name, value in host_settings.items()
            if name in {
                "mattermost_webhook_url", "telegram_bot_token",
                "telegram_chat_id",
            } and isinstance(value, str) and value
        )
        secrets += tuple(
            value for name, value in profile_environment.items()
            if name in {"VBPUB_PROFILE_AUTH_USERNAME", "VBPUB_PROFILE_AUTH_PASSWORD"}
            and value
        )
        runtime.output.secrets = (*runtime.output.secrets, *secrets)
        environment = {
            "REPO_URL": repo_url,
            "REPO_BRANCH": branch,
            "CLI_EXTENDED_LATEST_URL": wheel_manifest,
            **profile_environment,
            "VBPUB_CONFIG_EXTRA_JSON": json.dumps(host_settings, sort_keys=True, separators=(",", ":")),
        }
        payload = {
            "schema_version": _SEED_SCHEMA_VERSION,
            "bootstrap_url": bootstrap_url,
            "environment": environment,
        }
        output = Path(output_path).expanduser().absolute()
        runtime.output.emit(
            "info",
            "Private NoCloud ISO summary:\n"
            f"  profile source: {profile_kind}"
            + (f" ({profile_environment['VBPUB_PROFILE_ID']})" if profile_kind == "builtin" else "")
            + f"\n  source: {repo_url} ({branch})"
            + f"\n  notifications: {host_settings['notify_backend']}"
            + f"\n  persistent root SSH keys: {len(host_settings.get('ssh_authorized_keys', []))}"
            + f"\n  output: {output}\n"
            "The ISO contains private user-data. Protect it like the credentials it carries.",
            force=True,
        )
        if not runtime.confirm(f"Build the private ISO at {output}?"):
            return 0
        result = build_iso(output, payload)
    except KeyboardInterrupt:
        raise
    except ConfigError as exc:
        raise CliFailure(str(exc), exit_code=2) from None
    runtime.output.info(f"Created private NoCloud ISO: {result} (mode 0600).")
    return 0
