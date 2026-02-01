#!/usr/bin/env python3
"""Unified release orchestration for vbpub projects."""
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


@dataclass(frozen=True)
class Command:
    label: str
    argv: List[str]
    cwd: Path


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    steps: Mapping[str, List[Command]]


@dataclass(frozen=True)
class CleanupConfig:
    release_tag_prefixes: List[str]
    keep_release_tags: List[str]
    ghcr_packages: List[str]


@dataclass(frozen=True)
class GitHubConfig:
    username: str
    repo: str
    token: str
    owner_type: Optional[str]


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


def run_commands(commands: Iterable[Command]) -> None:
    for command in commands:
        log_info(command.label)
        subprocess.run(command.argv, check=True, cwd=str(command.cwd))


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


def load_config(
    config_path: Path,
) -> tuple[Path, dict[str, ProjectConfig], list[str], list[str], CleanupConfig, GitHubConfig, ReleaseEnvConfig]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    repo_root_value = config.get("repo_root")
    if not repo_root_value:
        raise ValueError("repo_root is required in config")
    repo_root = resolve_repo_root(config_path, repo_root_value)

    orchestration = config.get("orchestration")
    if not orchestration:
        raise ValueError("[orchestration] section is required in config")
    project_order = orchestration.get("project_order")
    if not project_order or not isinstance(project_order, list):
        raise ValueError("orchestration.project_order must be a list")
    default_projects = orchestration.get("default_projects")
    if not default_projects or not isinstance(default_projects, list):
        raise ValueError("orchestration.default_projects must be a list")
    default_steps = orchestration.get("default_steps")
    if not default_steps or not isinstance(default_steps, list):
        raise ValueError("orchestration.default_steps must be a list")

    projects_section = config.get("projects")
    if not projects_section or not isinstance(projects_section, dict):
        raise ValueError("[projects] section is required in config")

    projects: dict[str, ProjectConfig] = {}
    for name, project in projects_section.items():
        steps_section = project.get("steps") if isinstance(project, dict) else None
        if not steps_section or not isinstance(steps_section, dict):
            raise ValueError(f"projects.{name}.steps is required")
        steps: dict[str, List[Command]] = {}
        for step_name, step_config in steps_section.items():
            commands_config = step_config.get("commands") if isinstance(step_config, dict) else None
            if commands_config is None:
                raise ValueError(f"projects.{name}.steps.{step_name}.commands is required")
            steps[step_name] = parse_commands(config_path, repo_root, step_name, commands_config)
        projects[name] = ProjectConfig(name=name, steps=steps)

    cleanup_section = config.get("cleanup")
    if not cleanup_section or not isinstance(cleanup_section, dict):
        raise ValueError("[cleanup] section is required in config")
    release_tag_prefixes = cleanup_section.get("release_tag_prefixes")
    keep_release_tags = cleanup_section.get("keep_release_tags")
    ghcr_packages = cleanup_section.get("ghcr_packages")
    if not release_tag_prefixes or not isinstance(release_tag_prefixes, list):
        raise ValueError("cleanup.release_tag_prefixes must be a list")
    if not keep_release_tags or not isinstance(keep_release_tags, list):
        raise ValueError("cleanup.keep_release_tags must be a list")
    if not ghcr_packages or not isinstance(ghcr_packages, list):
        raise ValueError("cleanup.ghcr_packages must be a list")

    cleanup = CleanupConfig(
        release_tag_prefixes=release_tag_prefixes,
        keep_release_tags=keep_release_tags,
        ghcr_packages=ghcr_packages,
    )

    github = config.get("github")
    if not github or not isinstance(github, dict):
        raise ValueError("[github] section is required in config")
    username = (github.get("username") or "").strip()
    repo = (github.get("repo") or "").strip()
    token = (github.get("token") or "").strip()
    owner_type = (github.get("owner_type") or "").strip() or None
    if not username or not repo:
        raise ValueError("github.username and github.repo are required in config")

    github_config = GitHubConfig(
        username=username,
        repo=repo,
        token=token,
        owner_type=owner_type,
    )

    registry = config.get("registry", {})
    registry_url = None
    if isinstance(registry, dict):
        registry_url = (registry.get("url") or "").strip() or None

    env_section = config.get("env", {})
    if env_section is None:
        env_section = {}
    if not isinstance(env_section, dict):
        raise ValueError("[env] must be a table of key/value pairs")

    env_config = ReleaseEnvConfig(env=env_section, registry_url=registry_url)

    return repo_root, projects, project_order, default_projects, default_steps, cleanup, github_config, env_config


def apply_release_env(github: GitHubConfig, env_config: ReleaseEnvConfig) -> None:
    if github.username:
        os.environ.setdefault("GITHUB_USERNAME", github.username)
    if github.repo:
        os.environ.setdefault("GITHUB_REPO", github.repo)
    if github.token:
        os.environ.setdefault("GITHUB_PUSH_PAT", github.token)
    if github.owner_type:
        os.environ.setdefault("GITHUB_OWNER_TYPE", github.owner_type)
    if env_config.registry_url:
        os.environ.setdefault("REGISTRY", env_config.registry_url)

    for key, value in env_config.env.items():
        if value is None:
            continue
        value_str = str(value).strip()
        if value_str:
            os.environ.setdefault(key, value_str)


def compute_release_date(repo_root: Path) -> None:
    date_env = os.getenv("RELEASE_DATE_ENV", "BUILD_DATE").strip() or "BUILD_DATE"
    date_format = os.getenv("RELEASE_DATE_FORMAT", "%Y%m%d").strip() or "%Y%m%d"
    auto_increment = os.getenv("RELEASE_AUTO_INCREMENT", "1").strip()
    counter_template = os.getenv("RELEASE_COUNTER_TEMPLATE", "release-counter-{date}.txt").strip()
    counter_dir_raw = os.getenv("RELEASE_COUNTER_DIR", "")

    base_date = os.getenv(date_env)
    if not base_date:
        base_date = datetime.now(timezone.utc).strftime(date_format)

    if auto_increment != "1":
        os.environ[date_env] = base_date
        return

    counter_dir = Path(counter_dir_raw) if counter_dir_raw else (repo_root / "logs")
    counter_dir.mkdir(parents=True, exist_ok=True)
    counter_file = counter_dir / counter_template.format(date=base_date)

    if counter_file.exists():
        counter_value_raw = counter_file.read_text(encoding="utf-8").strip()
    else:
        counter_value_raw = "0"

    if not counter_value_raw.isdigit():
        raise ValueError(f"Invalid release counter value in {counter_file}: {counter_value_raw}")

    counter_value = int(counter_value_raw)
    build_date = base_date
    if counter_value == 0:
        counter_value = 1
        counter_file.write_text(str(counter_value), encoding="utf-8")
    else:
        build_date = f"{base_date}.{counter_value}"
        counter_value += 1
        counter_file.write_text(str(counter_value), encoding="utf-8")

    os.environ[date_env] = build_date

    os.environ.setdefault("CIU_BUILD_VERSION", build_date)
    os.environ.setdefault("CIU_VERSION", build_date)
    os.environ.setdefault("PWMCP_BUILD_VERSION", build_date)
    os.environ.setdefault("PWMCP_VERSION", build_date)


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
    for release in releases:
        tag = release.get("tag_name") or ""
        if tag in cleanup.keep_release_tags:
            continue
        if not any(tag.startswith(prefix) for prefix in cleanup.release_tag_prefixes):
            continue
        created_at = release.get("created_at")
        if not created_at:
            continue
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_dt >= cutoff:
            continue
        release_id = release.get("id")
        if not release_id:
            continue
        log_info(f"Deleting release tag {tag} (created {created_at})")
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
        if status == 403:
            log_warn(
                "Skipping GHCR cleanup for "
                f"{package} version {version_id}: missing package delete scope."
            )
            return
        raise RuntimeError(f"Failed to delete {package} version {version_id}: {body}")


def resolve_owner_type(owner: str, token: str) -> str:
    for candidate in ("org", "user"):
        try:
            list_package_versions(owner, "vsc-devcontainer", token, candidate)
            return candidate
        except RuntimeError:
            continue
    return "org"


def cleanup_ghcr(owner: str, token: str, cutoff: datetime, dry_run: bool, cleanup: CleanupConfig) -> None:
    owner_type = os.getenv("GITHUB_OWNER_TYPE")
    if not owner_type:
        owner_type = resolve_owner_type(owner, token)

    for package in cleanup.ghcr_packages:
        versions = list_package_versions(owner, package, token, owner_type)
        for version in versions:
            version_id = version.get("id")
            updated_at = version.get("updated_at") or version.get("created_at")
            if not version_id or not updated_at:
                continue
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated_dt >= cutoff:
                continue
            tags = version.get("metadata", {}).get("container", {}).get("tags", [])
            if any("latest" in tag for tag in tags):
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
    cleanup_ghcr(owner, token, cutoff, dry_run, cleanup)


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


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    default_config = Path(__file__).resolve().parents[3] / "release.toml"
    config_path_raw = args.config or os.getenv("RELEASE_MANAGER_CONFIG") or str(default_config)
    if not config_path_raw:
        raise ValueError("Release manager config is required (--config or RELEASE_MANAGER_CONFIG)")
    config_path = Path(config_path_raw).expanduser().resolve()

    (
        repo_root,
        configs,
        project_order,
        default_projects,
        default_steps,
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

    if steps:
        compute_release_date(repo_root)

    for step in steps:
        for project in selected:
            commands = project.steps.get(step, [])
            if not commands:
                continue
            run_commands(commands)

    if args.remove_assets:
        remove_assets(args.remove_assets, args.dry_run, cleanup, github_config, env_config)

    log_info("Release manager complete")


if __name__ == "__main__":
    main()
