"""Build a provider-neutral command/config bundle for remote bootstrap."""

from __future__ import annotations

import json
import shlex
import subprocess
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
        # LT-F-r1002-03: first action, before the download can fail.
        "import glob, os, sys\n"
        "for _p in glob.glob('/root/custom_script*'):\n"
        "    try:\n"
        "        if os.path.isfile(_p) and not os.path.islink(_p):\n"
        "            os.chmod(_p, 0o600)\n"
        "    except OSError:\n"
        "        pass\n"
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
    elif config.retain_controller_ssh_key and not config.controller_ssh_pubkey.strip():
        # LT recipe hazard: retention was requested but the script would carry no
        # key (and no marker for the provider to fill), so it could never install
        # one -- the host would be unreachable. There is no legitimate no-key use
        # of retain_controller_ssh_key=true, so no opt-out flag exists.
        raise ValueError(
            "retain_controller_ssh_key is true but no controller key is available: "
            "pass --controller-ssh-placeholder (the provider tool substitutes the key) "
            "or set controller_ssh_pubkey in the config; this script could never "
            "install a key"
        )

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


def _git_output(argv: list[str], cwd: str | None = None) -> str:
    """Best-effort git call: stdout stripped, or "" on any failure."""
    try:
        completed = subprocess.run(
            ["git", *argv], cwd=cwd, capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def describe_fetch_source(
    repo_url: str,
    repo_branch: str,
    *,
    checkout_dir: str | None = None,
    git=None,
) -> tuple[list[str], list[str]]:
    """Describe what the host will fetch, as (info lines, warning lines).

    The recipe hazard (LT-05 attempt 1): the default branch is `main`, so a
    bundle built from a feature-branch checkout silently made the host run OLD
    code. This reports the branch and the commit that branch resolves to on the
    remote, and warns loudly when the local checkout is on another branch.
    It never changes the default and never fails the build (git/network
    problems only degrade the message).
    """
    if git is None:
        git = _git_output  # looked up at call time so tests can stub it
    info: list[str] = []
    warnings: list[str] = []
    remote = git(["ls-remote", repo_url, f"refs/heads/{repo_branch}"])
    remote_commit = remote.split()[0] if remote else ""
    info.append(
        f"the host will fetch branch '{repo_branch}' of {repo_url}, commit "
        + (remote_commit if remote_commit else "(unresolved: could not query the remote)")
    )
    local_branch = git(["rev-parse", "--abbrev-ref", "HEAD"], checkout_dir)
    local_commit = git(["rev-parse", "HEAD"], checkout_dir)
    if local_branch and local_branch != repo_branch:
        warnings.append(
            f"WARNING: the local checkout is on branch '{local_branch}' ({local_commit[:12]}) "
            f"but the host will fetch '{repo_branch}'. Code on '{local_branch}' will NOT run "
            f"on the host unless you pass --repo-branch {local_branch}."
        )
    elif local_commit and remote_commit and local_commit != remote_commit:
        warnings.append(
            f"WARNING: local HEAD {local_commit[:12]} differs from the remote '{repo_branch}' "
            f"commit {remote_commit[:12]}: unpushed or stale; the host fetches the remote one."
        )
    return info, warnings
