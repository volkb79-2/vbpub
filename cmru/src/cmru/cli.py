#!/usr/bin/env python3
"""Unified release orchestration for vbpub projects.

Moved from ``release_manager.cli`` in P1; ``release_manager.cli``
is now a re-export shim kept for backwards compatibility until P6.

NOTE: This module currently contains the legacy ``vbpub-release`` CLI (P1 faithful move).
The new ``cmru`` CLI verb structure (run/build/publish/resolve/get-sh/release/status)
is introduced in P3. Until P3, the ``cmru`` entry point invokes this same ``main``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import tomllib

from cmru.runner import StepConfig, execute_step


@dataclass(frozen=True)
class Command:
    label: str
    argv: List[str]
    cwd: Path


@dataclass(frozen=True)
class VersionSpec:
    """Per-project versioning rules (S12). Defaults match cmru's historical behaviour."""
    strategy: str = "scm"            # "scm" | "counter" | "file:<PATH>"
    bump: str = "conventional"       # "conventional" | "patch"
    paths: tuple = ()                # extra subtrees to watch for change detection
    base_version: str = "1.0.0"      # counter strategy: <base>-r<N>
    file: str = "VERSION"            # file strategy fallback filename


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    env: Mapping[str, str]
    steps: Mapping[str, List[Command]]
    prefix: Optional[str] = None    # git tag prefix, e.g. "ciu-v"  (S12; required for auto-version)
    scm_dist: Optional[str] = None  # setuptools dist name for SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*
    cwd: Optional[str] = None       # build working dir (relative to repo root); default = name
    artifact: Optional[str] = None  # wheel | oci | tarball | bundle (S1.2)
    version: Optional[VersionSpec] = None
    paths: Optional[List[str]] = None  # change-detection watch paths; default = [cwd]


@dataclass(frozen=True)
class CleanupConfig:
    release_tag_prefixes: List[str]
    keep_release_tags: List[str]
    ghcr_packages: List[str]
    ghcr_delete_packages: List[str]


@dataclass(frozen=True)
class GitHubConfig:
    username: str
    repo: str
    token: str
    owner_type: str  # required: "user" | "org"  (V03; replaces the modern-debian-tools probe)


@dataclass(frozen=True)
class ReleaseEnvConfig:
    env: Mapping[str, str]
    registry_url: Optional[str]


def log_info(message: str) -> None:
    print(f"[INFO] {message}")


def log_warn(message: str) -> None:
    print(f"[WARN] {message}")


def log_error(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)


def parse_duration(value: str) -> timedelta:
    value = value.strip().lower().replace(" ", "")
    if not value:
        raise ValueError("Duration value is empty")

    units = {
        "s": 1,
        "sec": 1,
        "secs": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "mins": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "w": 604800,
        "week": 604800,
        "weeks": 604800,
    }

    total_seconds = 0
    idx = 0
    while idx < len(value):
        if not value[idx].isdigit():
            raise ValueError(f"Invalid duration syntax: {value}")
        num_start = idx
        while idx < len(value) and value[idx].isdigit():
            idx += 1
        number = int(value[num_start:idx])
        unit_start = idx
        while idx < len(value) and value[idx].isalpha():
            idx += 1
        unit = value[unit_start:idx]
        if unit not in units:
            raise ValueError(f"Unknown duration unit '{unit}' in {value}")
        total_seconds += number * units[unit]

    if total_seconds <= 0:
        raise ValueError(f"Duration must be positive: {value}")
    return timedelta(seconds=total_seconds)


def http_request(method: str, url: str, token: str) -> tuple[int, str, dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    req = Request(url, method=method, headers=headers)
    try:
        with urlopen(req) as response:
            body = response.read().decode("utf-8")
            return response.status, body, dict(response.headers)
    except HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        return exc.code, body, dict(exc.headers or {})


def load_json(url: str, token: str) -> tuple[list, dict]:
    status, body, headers = http_request("GET", url, token)
    if status >= 400:
        raise RuntimeError(f"GitHub API error {status}: {body}")
    if not body.strip():
        return [], headers
    return json.loads(body), headers


def run_commands(commands: Iterable[Command], project_env: Optional[Mapping[str, str]] = None) -> None:
    """Legacy direct runner. Kept for backwards compatibility; new callers use execute_step."""
    merged_env = os.environ.copy()
    if project_env:
        for key, value in project_env.items():
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                merged_env.setdefault(key, value_str)

    for command in commands:
        log_info(command.label)
        subprocess.run(command.argv, check=True, cwd=str(command.cwd), env=merged_env)


def _build_step_config(step_name: str, commands: List[Command]) -> StepConfig:
    """Convert orchestrator Command objects to a StepConfig for the unified runner."""
    return StepConfig(
        name=step_name,
        commands=[
            {"label": cmd.label, "argv": cmd.argv, "cwd": str(cmd.cwd)}
            for cmd in commands
        ],
        bake_set_prefix=None,
        bake_set_vars=[],
        no_cache_env=None,
        clean_dirs=[],
        required_env=[],
        login=None,
        step_env={},
        env_command=None,
    )


def run_project_step(
    project: "ProjectConfig",
    step_name: str,
    repo_root: Path,
    log_dir: Path,
) -> None:
    """Route a project step through the unified runner contract (S3)."""
    commands = project.steps.get(step_name, [])
    if not commands:
        return
    step = _build_step_config(step_name, list(commands))
    execute_step(step, repo_root, log_dir, extra_env=dict(project.env) if project.env else None)


def resolve_repo_root(config_path: Path, raw_value: str) -> Path:
    repo = Path(raw_value)
    if repo.is_absolute():
        return repo
    return (config_path.parent / repo).resolve()


def resolve_cwd(repo_root: Path, raw_cwd: str) -> Path:
    cwd_path = Path(raw_cwd)
    if cwd_path.is_absolute():
        return cwd_path
    return (repo_root / cwd_path).resolve()


def parse_commands(config_path: Path, repo_root: Path, step_name: str, raw_commands: list) -> List[Command]:
    if not raw_commands:
        raise ValueError(f"Step '{step_name}' must define at least one command")
    commands: List[Command] = []
    for idx, command in enumerate(raw_commands, start=1):
        if not isinstance(command, dict):
            raise ValueError(f"Step '{step_name}' command {idx} must be a table")
        label = command.get("label")
        argv = command.get("argv")
        cwd = command.get("cwd")
        if not label or not isinstance(label, str):
            raise ValueError(f"Step '{step_name}' command {idx} missing label")
        if not argv or not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ValueError(f"Step '{step_name}' command {idx} must define argv list")
        if not cwd or not isinstance(cwd, str):
            raise ValueError(f"Step '{step_name}' command {idx} missing cwd")
        commands.append(Command(label=label, argv=argv, cwd=resolve_cwd(repo_root, cwd)))
    return commands


def _resolve_token(config: dict, config_path: Path) -> str:
    """Resolve the GitHub token per SPEC S2.4 (env → cmru.secret.toml → config).

    Keeps ``cmru.toml`` secret-free: the committed config carries no token; the live
    token comes from the environment or a gitignored ``cmru.secret.toml`` overlay.
    """
    for env_name in ("GITHUB_PUSH_PAT", "GITHUB_TOKEN"):
        val = (os.getenv(env_name) or "").strip()
        if val:
            return val
    secret_path = config_path.parent / "cmru.secret.toml"
    if secret_path.exists():
        try:
            with secret_path.open("rb") as fh:
                secret = tomllib.load(fh)
            tok = ((secret.get("github") or {}).get("token") or "").strip()
            if tok:
                return tok
        except Exception as exc:  # malformed secret file should not crash reads
            log_warn(f"Could not read {secret_path.name}: {exc}")
    return ((config.get("github") or {}).get("token") or "").strip()


def _parse_version_spec(raw: object, name: str) -> Optional[VersionSpec]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"project.{name}.version must be a table")
    strategy = str(raw.get("strategy") or "scm").strip()
    bump = str(raw.get("bump") or "conventional").strip()
    if bump not in ("conventional", "patch"):
        raise ValueError(f"project.{name}.version.bump must be 'conventional' or 'patch'")
    paths = tuple(str(p) for p in (raw.get("paths") or []))
    base_version = str(raw.get("base_version") or "1.0.0").strip()
    version_file = str(raw.get("file") or "VERSION").strip()
    return VersionSpec(strategy=strategy, bump=bump, paths=paths,
                       base_version=base_version, file=version_file)


def load_config(
    config_path: Path,
) -> tuple[
    Path,
    dict[str, ProjectConfig],
    list[str],
    list[str],
    list[str],
    str,
    dict[str, list[str]],
    CleanupConfig,
    GitHubConfig,
    ReleaseEnvConfig,
]:
    """Load the cmru config (S2 ``cmru.toml``). Tolerant of the retired legacy keys
    (``[projects]`` plural, ``github.username``, ``[registry].url``) for one deprecation
    release so an old ``release.toml`` still works (S-CLI.4)."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    # repo_root: explicit, else the directory holding the config (cmru.toml lives at root).
    repo_root_value = config.get("repo_root")
    repo_root = resolve_repo_root(config_path, repo_root_value) if repo_root_value else config_path.parent

    orchestration = config.get("orchestration") or {}
    if not isinstance(orchestration, dict):
        raise ValueError("[orchestration] must be a table")

    # projects: S2 [project.<name>] (singular) preferred; legacy [projects] accepted.
    projects_section = config.get("project") or config.get("projects")
    if not projects_section or not isinstance(projects_section, dict):
        raise ValueError("[project.<name>] section is required in config")

    projects: dict[str, ProjectConfig] = {}
    for name, project in projects_section.items():
        if not isinstance(project, dict):
            raise ValueError(f"project.{name} must be a table")

        project_env = project.get("env") or {}
        if not isinstance(project_env, dict):
            raise ValueError(f"project.{name}.env must be a table")

        steps_section = project.get("steps")
        if not steps_section or not isinstance(steps_section, dict):
            raise ValueError(f"project.{name}.steps is required")
        steps: dict[str, List[Command]] = {}
        for step_name, step_config in steps_section.items():
            commands_config = step_config.get("commands") if isinstance(step_config, dict) else None
            if commands_config is None:
                raise ValueError(f"project.{name}.steps.{step_name}.commands is required")
            steps[step_name] = parse_commands(config_path, repo_root, step_name, commands_config)

        proj_prefix = (project.get("prefix") or "").strip() or None
        proj_scm_dist = (project.get("scm_dist") or "").strip() or None
        proj_cwd = (project.get("cwd") or "").strip() or None
        proj_artifact = (project.get("artifact") or "").strip() or None
        version_spec = _parse_version_spec(project.get("version"), name)
        # Change-detection watches the project cwd plus any extra version.paths (S12.3).
        extra_paths = list(version_spec.paths) if (version_spec and version_spec.paths) else []
        watch_paths = [proj_cwd or name] + extra_paths
        projects[name] = ProjectConfig(
            name=name, env=project_env, steps=steps,
            prefix=proj_prefix, scm_dist=proj_scm_dist,
            cwd=proj_cwd, artifact=proj_artifact,
            version=version_spec, paths=watch_paths,
        )

    # orchestration: sensible defaults so a minimal cmru.toml still works.
    project_order = orchestration.get("project_order") or list(projects.keys())
    if not isinstance(project_order, list):
        raise ValueError("orchestration.project_order must be a list")
    default_projects = orchestration.get("default_projects") or list(project_order)
    if not isinstance(default_projects, list):
        raise ValueError("orchestration.default_projects must be a list")
    default_steps = orchestration.get("default_steps") or ["build", "push"]
    if not isinstance(default_steps, list):
        raise ValueError("orchestration.default_steps must be a list")
    execution_mode = (orchestration.get("execution_mode") or "project-first").strip()
    if execution_mode not in {"step-first", "project-first"}:
        raise ValueError("orchestration.execution_mode must be 'step-first' or 'project-first'")

    step_project_order_raw = orchestration.get("step_project_order") or {}
    if not isinstance(step_project_order_raw, dict):
        raise ValueError("orchestration.step_project_order must be a table")
    step_project_order: dict[str, list[str]] = {}
    for step_name, step_projects in step_project_order_raw.items():
        if not isinstance(step_projects, list) or not all(isinstance(i, str) for i in step_projects):
            raise ValueError(f"orchestration.step_project_order.{step_name} must be a list")
        step_project_order[step_name] = step_projects

    # cleanup: optional; wildcards by default.
    cleanup_section = config.get("cleanup") or {}
    if not isinstance(cleanup_section, dict):
        raise ValueError("[cleanup] must be a table")
    cleanup = CleanupConfig(
        release_tag_prefixes=cleanup_section.get("release_tag_prefixes") or ["*"],
        keep_release_tags=cleanup_section.get("keep_release_tags") or [],
        ghcr_packages=cleanup_section.get("ghcr_packages") or ["*"],
        ghcr_delete_packages=cleanup_section.get("ghcr_delete_packages") or [],
    )

    github = config.get("github")
    if not github or not isinstance(github, dict):
        raise ValueError("[github] section is required in config")
    owner = (github.get("owner") or github.get("username") or "").strip()
    repo = (github.get("repo") or "").strip()
    owner_type = (github.get("owner_type") or "").strip()
    token = _resolve_token(config, config_path)
    if not owner or not repo:
        raise ValueError("github.owner and github.repo are required in config")
    if owner_type not in ("user", "org"):
        raise ValueError("github.owner_type must be \"user\" or \"org\" (V03)")

    github_config = GitHubConfig(username=owner, repo=repo, token=token, owner_type=owner_type)

    # registry: S2 [targets].registry (list) preferred; legacy [registry].url accepted.
    registry_url = None
    targets = config.get("targets") or {}
    if isinstance(targets, dict):
        reg = targets.get("registry")
        if isinstance(reg, list) and reg:
            registry_url = str(reg[0]).strip() or None
        elif isinstance(reg, str) and reg.strip():
            registry_url = reg.strip()
    if not registry_url:
        legacy_registry = config.get("registry") or {}
        if isinstance(legacy_registry, dict):
            registry_url = (legacy_registry.get("url") or "").strip() or None

    env_section = config.get("env") or {}
    if not isinstance(env_section, dict):
        raise ValueError("[env] must be a table of key/value pairs")

    env_config = ReleaseEnvConfig(env=env_section, registry_url=registry_url)

    return (
        repo_root,
        projects,
        project_order,
        default_projects,
        default_steps,
        execution_mode,
        step_project_order,
        cleanup,
        github_config,
        env_config,
    )


def apply_release_env(github: GitHubConfig, env_config: ReleaseEnvConfig) -> None:
    if github.username:
        os.environ.setdefault("GITHUB_USERNAME", github.username)
    if github.repo:
        os.environ.setdefault("GITHUB_REPO", github.repo)
    if github.token:
        os.environ.setdefault("GITHUB_PUSH_PAT", github.token)
    os.environ.setdefault("GITHUB_OWNER_TYPE", github.owner_type)
    if env_config.registry_url:
        os.environ.setdefault("REGISTRY", env_config.registry_url)

    for key, value in env_config.env.items():
        if value is None:
            continue
        value_str = str(value).strip()
        if value_str:
            os.environ.setdefault(key, value_str)


def _git(repo_root: Path, *args: str) -> Optional[str]:
    """Run ``git <args>`` under *repo_root*; return stripped stdout or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    out = result.stdout.strip()
    return out if (result.returncode == 0 and out) else None


def resolve_versions_from_git(
    repo_root: Path,
    projects: Optional[Mapping[str, "ProjectConfig"]] = None,
) -> None:
    """Export reproducible-build + git-derived version env for every project (no clock).

    - ``SOURCE_DATE_EPOCH`` = HEAD commit time → reproducible wheel/image timestamps.
    - ``OCI_REVISION`` / ``OCI_CREATED`` = HEAD sha + RFC3339(commit time) for image labels.
    - ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>`` only when HEAD is exactly on that
      project's ``<prefix>*`` tag and the project has ``scm_dist`` set.

    ``projects``: pass the loaded project config map; projects with both ``prefix`` and
    ``scm_dist`` set get the pretend-version treatment (S12). Without ``projects``,
    only SOURCE_DATE_EPOCH / OCI_* are set.
    """
    epoch = _git(repo_root, "log", "-1", "--format=%ct")
    if epoch:
        os.environ.setdefault("SOURCE_DATE_EPOCH", epoch)
        created = datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        os.environ.setdefault("OCI_CREATED", created)
    revision = _git(repo_root, "rev-parse", "HEAD")
    if revision:
        os.environ.setdefault("OCI_REVISION", revision)

    if not projects:
        return
    for project in projects.values():
        if not project.prefix or not project.scm_dist:
            continue
        prefix_tag = f"{project.prefix}"
        exact = _git(repo_root, "describe", "--tags", "--exact-match", "--match", f"{prefix_tag}*")
        if not exact:
            continue
        semver = exact[len(prefix_tag):]
        env_name = "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_" + project.scm_dist.upper().replace("-", "_")
        os.environ.setdefault(env_name, semver)
        log_info(f"{project.scm_dist}: HEAD on {exact} → {env_name}={semver}")


def list_releases(owner: str, repo: str, token: str) -> list[dict]:
    releases: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100&page={page}"
        items, _ = load_json(url, token)
        if not items:
            break
        releases.extend(items)
        if len(items) < 100:
            break
        page += 1
    return releases


def delete_release(owner: str, repo: str, token: str, release_id: int, dry_run: bool) -> None:
    if dry_run:
        log_info(f"[DRY RUN] Would delete release {release_id}")
        return
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/{release_id}"
    status, body, _ = http_request("DELETE", url, token)
    if status >= 400:
        raise RuntimeError(f"Failed to delete release {release_id}: {body}")


def cleanup_releases(
    owner: str,
    repo: str,
    token: str,
    cutoff: datetime,
    dry_run: bool,
    cleanup: CleanupConfig,
) -> None:
    releases = list_releases(owner, repo, token)
    wildcard_prefixes = not cleanup.release_tag_prefixes or "*" in cleanup.release_tag_prefixes
    for release in releases:
        tag = release.get("tag_name") or ""
        if tag in cleanup.keep_release_tags:
            continue
        if not wildcard_prefixes and not any(tag.startswith(prefix) for prefix in cleanup.release_tag_prefixes):
            continue
        published_at = release.get("published_at") or release.get("created_at") or release.get("updated_at")
        if not published_at:
            continue
        published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published_dt >= cutoff:
            continue
        release_id = release.get("id")
        if not release_id:
            continue
        log_info(f"Deleting release tag {tag} (published {published_at})")
        delete_release(owner, repo, token, int(release_id), dry_run)


def list_package_versions(owner: str, package: str, token: str, owner_type: str) -> list[dict]:
    versions: list[dict] = []
    page = 1
    while True:
        if owner_type == "org":
            url = f"https://api.github.com/orgs/{owner}/packages/container/{package}/versions?per_page=100&page={page}"
        else:
            url = f"https://api.github.com/users/{owner}/packages/container/{package}/versions?per_page=100&page={page}"
        items, _ = load_json(url, token)
        if not items:
            break
        versions.extend(items)
        if len(items) < 100:
            break
        page += 1
    return versions


def list_container_packages(owner: str, token: str, owner_type: str) -> list[str]:
    packages: list[str] = []
    page = 1
    while True:
        if owner_type == "org":
            url = (
                f"https://api.github.com/orgs/{owner}/packages"
                f"?package_type=container&per_page=100&page={page}"
            )
        else:
            url = (
                f"https://api.github.com/users/{owner}/packages"
                f"?package_type=container&per_page=100&page={page}"
            )
        items, _ = load_json(url, token)
        if not items:
            break
        for item in items:
            name = (item.get("name") or "").strip()
            if name:
                packages.append(name)
        if len(items) < 100:
            break
        page += 1
    return packages


def delete_package_version(owner: str, package: str, token: str, version_id: int, owner_type: str, dry_run: bool) -> None:
    if dry_run:
        log_info(f"[DRY RUN] Would delete {package} version {version_id}")
        return
    if owner_type == "org":
        url = f"https://api.github.com/orgs/{owner}/packages/container/{package}/versions/{version_id}"
    else:
        url = f"https://api.github.com/users/{owner}/packages/container/{package}/versions/{version_id}"
    status, body, _ = http_request("DELETE", url, token)
    if status >= 400:
        if status == 400 and "cannot be deleted" in body:
            log_warn(
                "Skipping GHCR cleanup for "
                f"{package} version {version_id}: {body}"
            )
            return
        if status == 403:
            log_warn(
                "Skipping GHCR cleanup for "
                f"{package} version {version_id}: missing package delete scope."
            )
            return
        raise RuntimeError(f"Failed to delete {package} version {version_id}: {body}")


def delete_package(owner: str, package: str, token: str, owner_type: str, dry_run: bool) -> None:
    if dry_run:
        log_info(f"[DRY RUN] Would delete {package} package")
        return
    if owner_type == "org":
        url = f"https://api.github.com/orgs/{owner}/packages/container/{package}"
    else:
        url = f"https://api.github.com/users/{owner}/packages/container/{package}"
    status, body, _ = http_request("DELETE", url, token)
    if status >= 400:
        if status == 403:
            log_warn(
                "Skipping GHCR package delete for "
                f"{package}: missing package delete scope."
            )
            return
        if status == 404:
            log_warn(f"Skipping GHCR package delete for {package}: not found")
            return
        raise RuntimeError(f"Failed to delete {package} package: {body}")


def cleanup_ghcr(owner: str, token: str, owner_type: str, cutoff: datetime, dry_run: bool, cleanup: CleanupConfig) -> None:

    wildcard_packages = not cleanup.ghcr_packages or "*" in cleanup.ghcr_packages
    packages = list_container_packages(owner, token, owner_type) if wildcard_packages else cleanup.ghcr_packages
    for package in packages:
        if package in cleanup.ghcr_delete_packages:
            log_info(f"Deleting GHCR package {package} (explicit cleanup list)")
            delete_package(owner, package, token, owner_type, dry_run)
            continue
        versions = list_package_versions(owner, package, token, owner_type)
        for version in versions:
            version_id = version.get("id")
            updated_at = version.get("updated_at") or version.get("created_at")
            if not version_id or not updated_at:
                continue
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated_dt >= cutoff:
                continue
            log_info(f"Deleting GHCR {package} version {version_id} (updated {updated_at})")
            delete_package_version(owner, package, token, int(version_id), owner_type, dry_run)


def remove_assets(
    age: str,
    dry_run: bool,
    cleanup: CleanupConfig,
    github: GitHubConfig,
    env_config: ReleaseEnvConfig,
) -> None:
    duration = parse_duration(age)
    cutoff = datetime.now(timezone.utc) - duration

    apply_release_env(github, env_config)
    owner = github.username
    repo = github.repo
    token = github.token
    if not token:
        raise RuntimeError("github.token is required for cleanup")

    log_info(f"Removing assets older than {age} (cutoff {cutoff.isoformat()})")
    cleanup_releases(owner, repo, token, cutoff, dry_run, cleanup)
    cleanup_ghcr(owner, token, github.owner_type, cutoff, dry_run, cleanup)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="vbpub release manager")
    parser.add_argument(
        "--config",
        help="Path to release manager config TOML",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=None,
        help="Project to operate on (default: all)",
    )
    parser.add_argument("--run-tests", action="store_true", help="Run tests")
    parser.add_argument("--build", action="store_true", help="Build artifacts")
    parser.add_argument("--push", action="store_true", help="Push artifacts")
    parser.add_argument("--validate", action="store_true", help="Validate releases")
    parser.add_argument("--remove-assets", metavar="AGE", help="Remove assets/images older than AGE (e.g., 1h, 2d)")
    parser.add_argument("--dry-run", action="store_true", help="Show cleanup actions without deleting")
    return parser


def _orchestrate() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    config_path = _resolve_config(args.config)

    (
        repo_root,
        configs,
        project_order,
        default_projects,
        default_steps,
        execution_mode,
        step_project_order,
        cleanup,
        github_config,
        env_config,
    ) = load_config(config_path)

    apply_release_env(github_config, env_config)

    projects = args.project or default_projects
    if "all" in projects:
        selected_names = project_order
    else:
        selected_names = projects

    missing = [name for name in selected_names if name not in configs]
    if missing:
        raise ValueError(f"Unknown project(s) in selection: {', '.join(missing)}")

    selected = [configs[name] for name in selected_names]

    steps = []
    if args.run_tests:
        steps.append("run-tests")
    if args.build:
        steps.append("build")
    if args.push:
        steps.append("push")
    if args.validate:
        steps.append("validate")

    if not steps and not args.remove_assets:
        steps = default_steps

    log_dir = repo_root / "logs"

    if steps:
        resolve_versions_from_git(repo_root, configs)

    if execution_mode == "project-first":
        for project in selected:
            for step in steps:
                run_project_step(project, step, repo_root, log_dir)
    else:
        for step in steps:
            ordered_names = step_project_order.get(step) or selected_names
            for project_name in ordered_names:
                if project_name not in configs:
                    raise ValueError(f"Unknown project in step_project_order: {project_name}")
                if project_name not in selected_names:
                    continue
                project = configs[project_name]
                run_project_step(project, step, repo_root, log_dir)

    if args.remove_assets:
        remove_assets(args.remove_assets, args.dry_run, cleanup, github_config, env_config)

    log_info("Release manager complete")


def _cmru_version() -> str:
    try:
        from importlib.metadata import version as _mv
        return _mv("cmru")
    except Exception:
        return "dev"


def _default_config_path() -> Path:
    """Repo-root ``cmru.toml`` (S2). Falls back to a legacy ``release.toml`` for one
    deprecation release (S-CLI.4). Override with ``--config`` / ``RELEASE_MANAGER_CONFIG``."""
    root = Path(__file__).resolve().parents[3]
    cmru_toml = root / "cmru.toml"
    if cmru_toml.exists():
        return cmru_toml
    legacy = root / "release.toml"
    if legacy.exists():
        log_warn("Using legacy release.toml — rename it to cmru.toml (see SPEC S-CLI.4).")
        return legacy
    return cmru_toml


def _resolve_config(config_opt: Optional[str]) -> Path:
    raw = config_opt or os.getenv("RELEASE_MANAGER_CONFIG") or str(_default_config_path())
    return Path(raw).expanduser().resolve()


def _ordered_configs(
    configs: Mapping[str, "ProjectConfig"],
    project_order: List[str],
) -> "dict[str, ProjectConfig]":
    """Project configs limited to ``project_order`` (the orchestrated set), in order.

    ``status``/``release`` use this so they never auto-tag projects that still own a
    bespoke pipeline (tls-edge, empyrion) and are not yet migrated into the orchestrator.
    """
    return {name: configs[name] for name in project_order if name in configs}


def _push_tags(repo_root: Path, tags: List[str]) -> None:
    """Push annotated release tags to origin. A failure is non-fatal: the GitHub
    Release API recreates the tag at publish time, so we warn rather than abort."""
    if not tags:
        return
    log_info(f"Pushing tags to origin: {', '.join(tags)}")
    rc = subprocess.run(["git", "-C", str(repo_root), "push", "origin", *tags]).returncode
    if rc != 0:
        log_warn("git push of tags failed — continuing; publish will create the tag via the API.")


def _tag_on_head(repo_root: Path, prefix: str) -> Optional[str]:
    """Return the project's ``<prefix>*`` tag pointing at HEAD (highest semver), else None."""
    out = _git(repo_root, "tag", "--points-at", "HEAD", "--list", f"{prefix}*")
    if not out:
        return None
    tags = [t for t in out.splitlines() if t.strip() and not t.endswith("-latest")]
    if not tags:
        return None
    from cmru.release import _semver_key
    return max(tags, key=lambda t: _semver_key(t[len(prefix):]))


def _run_project_steps(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: List[str],
    steps: List[str],
) -> None:
    """Run ``steps`` (in order) for each named project through the unified runner (S3).

    Seeds reproducible-build + SETUPTOOLS_SCM pretend-version env first so a wheel
    built here matches the tag on HEAD. Missing steps are skipped with a note."""
    resolve_versions_from_git(repo_root, dict(configs))
    log_dir = repo_root / "logs"
    for name in project_names:
        project = configs[name]
        for step in steps:
            if step in project.steps:
                log_info(f"{name}: running step '{step}'")
                run_project_step(project, step, repo_root, log_dir)
            else:
                log_info(f"{name}: no '{step}' step — skipping")


def _run_delegated_project(repo_root: Path, configs: Mapping[str, "ProjectConfig"], name: str) -> None:
    """Release a delegated-versioned project (e.g. pwmcp): build → commit & push any
    build-input edits the build produced → publish. Committing+pushing before publish
    keeps the working tree clean and ensures the release tag points at the exact commit
    whose inputs were built (no tree-dirtying, tag == published version)."""
    resolve_versions_from_git(repo_root, dict(configs))
    log_dir = repo_root / "logs"
    project = configs[name]
    cwd = getattr(project, "cwd", None) or name

    if "build" in project.steps:
        log_info(f"{name}: running step 'build'")
        run_project_step(project, "build", repo_root, log_dir)

    # The build may rewrite tracked inputs (pwmcp's resolver bumps the playwright pin).
    # Commit just this project's subtree (cmru.vars is gitignored) and push before publish.
    dirty = _git(repo_root, "status", "--porcelain", "--", cwd)
    if dirty:
        subprocess.run(["git", "-C", str(repo_root), "add", "--", cwd], check=False)
        rc = subprocess.run(
            ["git", "-C", str(repo_root), "commit", "-m", f"chore({name}): release build inputs"],
        ).returncode
        if rc == 0:
            log_info(f"{name}: committed build-input changes")
            if subprocess.run(["git", "-C", str(repo_root), "push", "origin", "HEAD"]).returncode != 0:
                log_warn(f"{name}: push of build-input commit failed; publish tag may lag remote HEAD.")

    if "push" in project.steps:
        log_info(f"{name}: running step 'push'")
        run_project_step(project, "push", repo_root, log_dir)


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point for the ``cmru`` CLI.

    Verb dispatch. Normal release path:  ``status`` → ``release`` (→ ``cleanup``).
    ``release`` is the one-shot (tag → push → build → publish); ``build``/``publish``
    are the same two steps split out, operating on the tag at HEAD. ``run`` is the
    explicit-steps escape hatch; ``run-step`` is the raw single-step runner.
    """
    import sys as _sys

    av = argv if argv is not None else _sys.argv[1:]
    if not av or av[0] in ("-h", "--help"):
        print(
            f"CMRU {_cmru_version()} — Configurable Multi Release Utility\n"
            "Config: cmru.toml (repo root) — override with --config / RELEASE_MANAGER_CONFIG\n"
            "\n"
            "TYPICAL WORKFLOW  (run from repo root, e.g. ./cmru.py <verb>):\n"
            "  1. status                  preview what changed + the next version (no writes)\n"
            "  2. release                 the one-shot: tag → push tag → build → publish\n"
            "       step-by-step instead: build  then  publish   (act on the tag at HEAD)\n"
            "  3. cleanup --remove-assets 30d     prune old releases/images (optional)\n"
            "\n"
            "PLANNING (read-only)\n"
            "    status   [--project P] [--minor|--major]     preview next releases (dry-run)\n"
            "\n"
            "RELEASE (writes to GitHub)\n"
            "    release  [--project P] [--minor|--major|--set-version V] [--dry-run]\n"
            "                                                  detect → tag → push → build → publish\n"
            "    build    [--project P]                        run the 'build' step (artifact only)\n"
            "    publish  [--project P]                        run the 'push' step (upload + .sha256)\n"
            "    run      [--project P] [--run-tests --build --push --validate]\n"
            "                                                  low-level: explicit steps × projects\n"
            "\n"
            "CONSUMPTION (read-only)\n"
            "    resolve  [--project P] [--format env|json|url]   resolve latest published version\n"
            "    get      [--project P]                        emit standalone get.py installer\n"
            "\n"
            "MAINTENANCE\n"
            "    cleanup  --remove-assets AGE [--dry-run]      age-based release/GHCR cleanup\n"
            "    run-step --config C --step S                  execute one cmru.build.toml step (raw)\n"
        )
        return

    if av[0] == "--version":
        print(f"cmru {_cmru_version()}")
        return

    verb = av[0]
    rest = av[1:]

    if verb == "run":
        _sys.argv = ["cmru"] + rest
        _orchestrate()

    elif verb == "run-step":
        # Raw single-step runner (was the old `cmru build`): needs --config + --step.
        from cmru.runner import main as runner_main
        runner_main(rest)

    elif verb in ("build", "publish"):
        import argparse as _ap
        parser = _ap.ArgumentParser(description=f"cmru {verb}")
        parser.add_argument("--project", help="Limit to one project (default: all orchestrated)")
        parser.add_argument("--config", help="Path to release.toml")
        # Back-compat: `cmru build --config C --step S` still hits the raw runner.
        if verb == "build" and ("--step" in rest):
            from cmru.runner import main as runner_main
            runner_main(rest)
            return
        vargs = parser.parse_args(rest)
        cfg_path = _resolve_config(vargs.config)
        (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
        github_config, env_config = _rest[-2], _rest[-1]
        apply_release_env(github_config, env_config)
        ordered = _ordered_configs(configs, project_order)
        names = [vargs.project] if vargs.project else list(ordered.keys())
        missing = [n for n in names if n not in configs]
        if missing:
            log_error(f"Unknown project(s): {', '.join(missing)}")
            _sys.exit(2)
        step = "build" if verb == "build" else "push"
        _run_project_steps(repo_root, configs, names, [step])
        log_info(f"cmru {verb} complete")

    elif verb == "resolve":
        from cmru.resolve import resolve_main
        resolve_main(rest)

    elif verb in ("get", "get-sh"):
        from cmru.getsh import getsh_main
        getsh_main(rest)

    elif verb in ("release", "status"):
        import argparse as _ap
        parser = _ap.ArgumentParser(description=f"cmru {verb}")
        parser.add_argument("--project", help="Limit to one project")
        parser.add_argument("--minor", action="store_true")
        parser.add_argument("--major", action="store_true")
        parser.add_argument("--set-version", metavar="VER")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-build", action="store_true",
                            help="release: tag + push only; skip build/publish")
        parser.add_argument("--config", help="Path to release.toml")
        vargs = parser.parse_args(rest)

        cfg_path = _resolve_config(vargs.config)
        (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
        github_config, env_config = _rest[-2], _rest[-1]
        apply_release_env(github_config, env_config)

        # Restrict versioning verbs to the orchestrated set so un-migrated projects
        # with their own pipelines (tls-edge, empyrion) are never auto-tagged.
        ordered = _ordered_configs(configs, project_order)

        from cmru.version import status_cmd, release_cmd
        if verb == "status":
            status_cmd(
                repo_root, ordered,
                minor=vargs.minor, major=vargs.major, set_version=vargs.set_version,
            )
            return

        # --- release: detect → tag → push → build → publish -------------------
        from cmru.version import release_cmd, detect_changed_projects

        def _strategy(proj) -> str:
            return proj.version.strategy if getattr(proj, "version", None) else "scm"

        changed = detect_changed_projects(repo_root, ordered)
        if vargs.project:
            changed = [c for c in changed if c[0] == vargs.project]
        changed_names = {c[0] for c in changed}

        # Tag the non-delegated changed projects (clean-tree guard lives in release_cmd).
        created = release_cmd(
            repo_root, ordered,
            project_filter=vargs.project,
            minor=vargs.minor, major=vargs.major, set_version=vargs.set_version,
            dry_run=vargs.dry_run,
        )
        if vargs.dry_run:
            log_info("[DRY RUN] No tags pushed, nothing built/published.")
            return

        # What to build/publish:
        #   tagged  = non-delegated projects whose HEAD now carries their tag
        #             (covers just-created tags AND a half-finished prior release)
        #   delegated = delegated-versioned projects that changed (self-version at build)
        tagged: dict[str, str] = {}
        delegated: list[str] = []
        for name, proj in ordered.items():
            if vargs.project and name != vargs.project:
                continue
            if _strategy(proj) == "delegated":
                if name in changed_names:
                    delegated.append(name)
                continue
            tag = _tag_on_head(repo_root, proj.prefix or f"{name}-v")
            if tag:
                tagged[name] = tag

        if not tagged and not delegated:
            log_info("Nothing to release (no changed/tagged projects).")
            return

        _push_tags(repo_root, list(tagged.values()))

        if vargs.no_build:
            log_info(f"--no-build: tagged + pushed {', '.join(tagged.values())}; skipped build/publish.")
            return

        # Build + publish in project_order; delegated projects self-version.
        released: list[str] = []
        for name in project_order:
            if name in tagged:
                log_info(f"Building + publishing {name} ({tagged[name]})")
                _run_project_steps(repo_root, configs, [name], ["build", "push"])
                released.append(f"{name} ({tagged[name]})")
            elif name in delegated:
                log_info(f"Building + publishing {name} (delegated versioning)")
                _run_delegated_project(repo_root, configs, name)
                released.append(f"{name} (delegated)")
        log_info(f"Released: {', '.join(released)}")

    elif verb == "cleanup":
        import argparse as _ap
        parser = _ap.ArgumentParser(description="cmru cleanup")
        parser.add_argument("--remove-assets", metavar="AGE", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--config", help="Path to cmru.toml or release.toml")
        vargs = parser.parse_args(rest)

        default_config = Path(__file__).resolve().parents[3] / "release.toml"
        cfg_path = Path(vargs.config or os.getenv("RELEASE_MANAGER_CONFIG") or str(default_config))
        cfg_path = cfg_path.expanduser().resolve()

        (_repo_root, _configs, _project_order, _default_projects, _default_steps,
         _execution_mode, _step_project_order, cleanup, github_config, env_config) = load_config(cfg_path)

        remove_assets(vargs.remove_assets, vargs.dry_run, cleanup, github_config, env_config)

    else:
        log_error(f"Unknown verb '{verb}'. Run 'cmru --help' for usage.")
        _sys.exit(2)


if __name__ == "__main__":
    main()
