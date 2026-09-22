"""Build the Debian-install-v2 remote bootstrap contract.

The Netcup installer is deliberately not the owner of this code.  This module
validates a v2 JSON configuration and renders the two things a provider
integration needs: the JSON configuration object and the cloud-init command
which feeds that object to ``bootstrap-remote.py``.

The command can optionally contain the generic
``{{CONTROLLER_SSH_PUBKEY}}`` marker.  A provider integration may replace that
marker with the public half of a temporary controller key immediately before
submitting its own API request; v2 itself does not know anything about that
provider or its API.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import shlex
from typing import Any
import urllib.parse

from .config import Config


REPO_URL_DEFAULT = "https://github.com/volkb79-2/vbpub"
REPO_BRANCH_DEFAULT = "main"
BOOTSTRAP_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/volkb79-2/vbpub/"
    "{branch}/scripts/debian-install-v2/bootstrap-remote.py"
)
CONTROLLER_SSH_PUBKEY_MARKER = "{{CONTROLLER_SSH_PUBKEY}}"
COMPLETION_MARKER = "/var/lib/vbpub/bootstrap/stage2_done"


def _validate_source(value: str, name: str, *, url: bool = True) -> str:
    value = str(value).strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError(f"{name} must be non-empty and contain no whitespace")
    if url and not value.startswith("https://"):
        raise ValueError(f"{name} must be an https:// URL without whitespace")
    return value


def resolve_bootstrap_url(
    *,
    repo_url: str = REPO_URL_DEFAULT,
    repo_branch: str = REPO_BRANCH_DEFAULT,
    bootstrap_url: str | None = None,
) -> str:
    """Resolve and validate the wrapper URL used by the generated command."""
    repo_url = _validate_source(repo_url, "repo_url").rstrip("/")
    repo_branch = _validate_source(repo_branch, "repo_branch", url=False)
    if bootstrap_url:
        return _validate_source(bootstrap_url, "bootstrap_url")
    if repo_url != REPO_URL_DEFAULT:
        raise ValueError(
            "bootstrap_url is required when repo_url is not the canonical vbpub repository"
        )
    return BOOTSTRAP_URL_TEMPLATE.format(
        branch=urllib.parse.quote(repo_branch, safe="/")
    )


def build_customscript_bundle(
    config: Config,
    *,
    repo_url: str = REPO_URL_DEFAULT,
    repo_branch: str = REPO_BRANCH_DEFAULT,
    bootstrap_url: str | None = None,
    controller_ssh_placeholder: bool = False,
) -> dict[str, Any]:
    """Return a JSON-serializable config/customScript bundle.

    ``VBPUB_CONFIG_EXTRA_JSON`` is used intentionally: it is the remote
    wrapper's escape hatch for every strict v2 field, so this renderer does
    not need a second, drifting list of environment-variable mappings.
    """
    repo_url = _validate_source(repo_url, "repo_url").rstrip("/")
    repo_branch = _validate_source(repo_branch, "repo_branch", url=False)
    remote_url = resolve_bootstrap_url(
        repo_url=repo_url,
        repo_branch=repo_branch,
        bootstrap_url=bootstrap_url,
    )

    config_data = asdict(config)
    if controller_ssh_placeholder:
        if config_data.get("controller_ssh_pubkey"):
            raise ValueError(
                "controller_ssh_pubkey must be empty when using "
                "--controller-ssh-placeholder"
            )
        config_data["controller_ssh_pubkey"] = CONTROLLER_SSH_PUBKEY_MARKER

    config_json = json.dumps(config_data, sort_keys=True, separators=(",", ":"))
    env_parts = [
        f"REPO_URL={shlex.quote(repo_url)}",
        f"REPO_BRANCH={shlex.quote(repo_branch)}",
        f"VBPUB_CONFIG_EXTRA_JSON={shlex.quote(config_json)}",
    ]
    custom_script = (
        f"curl -fsSL {shlex.quote(remote_url)} | "
        + " ".join(env_parts)
        + " python3 -"
    )
    return {
        "config": config_data,
        "customScript": custom_script,
        "completionMarker": COMPLETION_MARKER,
    }
