"""Build a provider-neutral command/config bundle for remote bootstrap."""

from __future__ import annotations

import json
import shlex
import urllib.parse
from dataclasses import asdict
from typing import Any

from .config import Config, validate_config

REPO_URL_DEFAULT = "https://github.com/volkb79-2/vbpub"
REPO_BRANCH_DEFAULT = "main"
BOOTSTRAP_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/volkb79-2/vbpub/"
    "{branch}/scripts/debian-install-v2/bootstrap-remote.py"
)
CONTROLLER_SSH_PUBKEY_MARKER = "{{CONTROLLER_SSH_PUBKEY}}"


def _validate_source(value: str, name: str, *, url: bool = True) -> str:
    value = str(value).strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError(f"{name} must be non-empty and contain no whitespace")
    if url and not value.startswith("https://"):
        raise ValueError(f"{name} must be an https:// URL without whitespace")
    return value


def _bootstrap_launcher_source(bootstrap_url: str) -> str:
    """Return a Python launcher that downloads and executes the bootstrap.

    Keep download errors concise and nonzero; unlike a shell pipeline, Python
    propagates the fetch failure instead of returning the last command's status.
    """
    return (
        "import sys\n"
        "import urllib.request\n"
        f"url = {bootstrap_url!r}\n"
        "try:\n"
        "    with urllib.request.urlopen(url, timeout=60) as response:\n"
        "        source = response.read()\n"
        "    if not source.strip():\n"
        "        raise ValueError('downloaded bootstrap source was empty')\n"
        "except Exception as exc:\n"
        "    print(\n"
        "        f'debian-install-v2: bootstrap download failed: {exc}',\n"
        "        file=sys.stderr,\n"
        "    )\n"
        "    raise SystemExit(1)\n"
        "namespace = {'__name__': '__main__', '__file__': url}\n"
        "exec(compile(source, url, 'exec'), namespace)\n"
    )


def resolve_bootstrap_url(
    *,
    repo_url: str = REPO_URL_DEFAULT,
    repo_branch: str = REPO_BRANCH_DEFAULT,
    bootstrap_url: str | None = None,
) -> str:
    """Resolve a real bootstrap URL; custom repositories must supply one."""
    repo_url = _validate_source(repo_url, "repo_url").rstrip("/")
    repo_branch = _validate_source(repo_branch, "repo_branch", url=False)
    if bootstrap_url is not None:
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
    repo_url: str | None = None,
    repo_branch: str | None = None,
    bootstrap_url: str | None = None,
    controller_ssh_placeholder: bool = False,
) -> dict[str, Any]:
    """Return validated v2 settings and a paste-ready cloud-init command."""
    validate_config(config)
    if repo_url is None:
        repo_url = REPO_URL_DEFAULT
    if repo_branch is None:
        repo_branch = REPO_BRANCH_DEFAULT
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
    custom_script = " ".join(
        (
            *env_parts,
            "python3",
            "-c",
            shlex.quote(_bootstrap_launcher_source(remote_url)),
        )
    )
    return {
        "config": config_data,
        "customScript": custom_script,
        "completionMarker": f"{config.state_dir}/stage2_done",
    }
