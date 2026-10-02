"""Use CMRU's resolved GitHub token for its own matching HTTPS Git operations.

Project step subprocesses must not inherit this transport credential. Call
``run_remote_git`` for CMRU-owned fetch/push operations; it scopes the token to
one Git process and only when ``origin`` is the configured GitHub repository.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class GitHubGitAuth:
    """The already-resolved repository credential used by CMRU's Git transport."""

    owner: str
    repo: str
    token: str = field(repr=False)


_ASKPASS_SOURCE = """import os
import re
import sys
from urllib.parse import urlsplit

prompt = " ".join(sys.argv[1:]).lower()
match = re.search(r"https://\\S+", prompt)
if match is None:
    raise SystemExit(1)
try:
    prompted_host = urlsplit(match.group(0).rstrip("':>")).hostname
except ValueError:
    raise SystemExit(1)
if (prompted_host or "").casefold() != "github.com":
    raise SystemExit(1)
if "username" in prompt:
    # GitHub requires a non-empty username for PAT authentication but does not
    # use it to authenticate the HTTPS Git operation.
    answer = "cmru"
elif "password" in prompt or "token" in prompt:
    answer = os.environ.get("CMRU_GIT_AUTH_TOKEN", "")
else:
    raise SystemExit(1)
if not answer:
    raise SystemExit(1)
sys.stdout.write(answer)
"""

_PUBLISHER_ENV_KEYS = ("GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN")


@contextmanager
def without_publisher_tokens() -> Iterator[None]:
    """Temporarily withhold CMRU publisher tokens from inherited hook processes."""
    missing = object()
    saved = {key: os.environ.get(key, missing) for key in _PUBLISHER_ENV_KEYS}
    try:
        for key in _PUBLISHER_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in saved.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def github_https_origin_matches(origin_url: str, auth: GitHubGitAuth) -> bool:
    """Whether a remote URL is the HTTPS GitHub repository named by ``auth``."""
    try:
        parsed = urlsplit(origin_url.strip())
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").lower() != "github.com"
            or parsed.port not in (None, 443)
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not auth.owner.strip()
            or not auth.repo.strip()
            or "/" in auth.owner
            or "\\" in auth.owner
            or "/" in auth.repo
            or "\\" in auth.repo
        ):
            return False
        if not parsed.path.startswith("/"):
            return False
        raw_parts = parsed.path[1:].split("/")
        if raw_parts[-1] == "":
            raw_parts.pop()
        if len(raw_parts) != 2 or any(not part for part in raw_parts):
            return False
        parts = [unquote(part) for part in raw_parts]
        if any("/" in part or "\\" in part for part in parts):
            return False
        remote_owner, remote_repo = parts
        if remote_repo.lower().endswith(".git"):
            remote_repo = remote_repo[:-4]
        return (
            remote_owner.casefold() == auth.owner.strip().casefold()
            and remote_repo.casefold() == auth.repo.strip().casefold()
        )
    except (ValueError, UnicodeError):
        return False


def _origin_urls(repo_root: Path, *, for_push: bool) -> list[str]:
    command = ["git", "remote", "get-url"]
    if for_push:
        command.append("--push")
    command.extend(("--all", "origin"))
    result = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@contextmanager
def _git_environment(
    repo_root: Path, auth: GitHubGitAuth | None, *, for_push: bool,
) -> Iterator[dict[str, str] | None]:
    """Yield a Git-only askpass environment for the configured HTTPS origin."""
    if auth is None or not auth.token.strip():
        yield None
        return
    origin_urls = _origin_urls(repo_root, for_push=for_push)
    if not origin_urls or not all(
        github_https_origin_matches(url, auth) for url in origin_urls
    ):
        # Do not send a GitHub token to SSH remotes, other hosts, or another
        # GitHub repository. Those remotes keep their own configured auth.
        yield None
        return

    with tempfile.TemporaryDirectory(prefix="cmru-git-askpass-") as tmp:
        helper = Path(tmp) / "askpass"
        helper.write_text(
            f"#!{sys.executable}\n" + _ASKPASS_SOURCE,
            encoding="utf-8",
        )
        helper.chmod(0o700)
        child_env: dict[str, str] = {}
        child_env["GIT_ASKPASS"] = str(helper)
        child_env["GIT_ASKPASS_REQUIRE"] = "force"
        child_env["GIT_TERMINAL_PROMPT"] = "0"
        child_env["GIT_TRACE_REDACT"] = "1"
        child_env["CMRU_GIT_AUTH_TOKEN"] = auth.token
        child_env["CMRU_GIT_AUTH_HOOKS_PATH"] = tmp
        yield child_env


def run_remote_git(
    repo_root: Path,
    *args: str,
    auth: GitHubGitAuth | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run one CMRU-owned Git operation, applying scoped HTTPS auth if eligible."""
    with _git_environment(
        repo_root, auth, for_push=bool(args and args[0] == "push"),
    ) as git_env:
        command = ["git"]
        supplied_env = kwargs.get("env")
        child_env = os.environ.copy() if supplied_env is None else dict(supplied_env)
        # CMRU's Git operations must not inherit a publisher token installed by
        # apply_release_env (possibly a project-local override). Git authentication
        # for a different host remains available through Git's own configured
        # helpers/SSH agent, but CMRU's API token is never forwarded to that host.
        for key in _PUBLISHER_ENV_KEYS:
            child_env.pop(key, None)
        kwargs["env"] = child_env
        if git_env is not None:
            # The resolved secret is authoritative for this exact repository;
            # don't let a machine-level helper silently substitute another PAT.
            # Git hooks inherit this process environment, including the askpass
            # token. Disable them for credential-bearing transport commands so
            # a pre-push hook cannot reuse the repository secret for another URL.
            hooks_path = git_env.pop("CMRU_GIT_AUTH_HOOKS_PATH")
            command.extend((
                "-c", "credential.helper=",
                "-c", f"core.hooksPath={hooks_path}",
            ))
            child_env.update(git_env)
        command.extend(args)
        return subprocess.run(command, cwd=repo_root, **kwargs)


def run_local_git(repo_root: Path, *args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a local Git operation without CMRU's publisher credentials.

    Local commands such as commit, revert, and rebase may invoke user Git hooks.
    The hook child process inherits this environment, so publisher credentials
    must be removed even though the Git operation itself needs no remote auth.
    """
    supplied_env = kwargs.get("env")
    child_env = os.environ.copy() if supplied_env is None else dict(supplied_env)
    for key in _PUBLISHER_ENV_KEYS:
        child_env.pop(key, None)
    kwargs["env"] = child_env
    return subprocess.run(["git", *args], cwd=repo_root, **kwargs)
