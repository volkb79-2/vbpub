#!/usr/bin/env python3
"""Generic step runner for build/push scripts (config-driven).

Moved from ``release_manager.step_runner`` in P1; ``release_manager.step_runner``
is now a re-export shim kept for backwards compatibility until P6.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional

import tomllib


@dataclass(frozen=True)
class ReleaseSecrets:
    github_username: str
    github_repo: str
    github_token: str
    github_owner_type: Optional[str]
    registry_url: Optional[str]
    registries: list  # [targets].registry list (S11 multi-push); first entry = REGISTRY
    env: Mapping[str, str]
    project_env: Mapping[str, str]


@dataclass(frozen=True)
class StepConfig:
    name: str
    commands: list[dict]
    bake_set_prefix: Optional[str]
    bake_set_vars: list[str]
    no_cache_env: Optional[str]
    clean_dirs: list[str]
    required_env: list[str]
    login: Optional[dict]
    step_env: Mapping[str, str]
    env_command: Optional[list[str]]
    registries: list = None  # [targets].registry for multi-push (S11); None = single-registry compat


def log_info(message: str) -> None:
    print(f"[INFO] {message}")


def log_error(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)


def resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def load_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _resolve_token(config: dict, github: dict, config_path: Path) -> str:
    """Resolve the GitHub token per SPEC S2.4: env → cmru.secret.toml → config."""
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
        except Exception:
            pass
    return (github.get("token") or "").strip()


def load_release_secrets(release_path: Path, *, project_name: Optional[str] = None) -> ReleaseSecrets:
    config = load_toml(release_path)

    github = config.get("github")
    if not github or not isinstance(github, dict):
        raise ValueError("[github] section required in release config")

    username = (github.get("username") or github.get("owner") or "").strip()
    repo = (github.get("repo") or "").strip()
    token = _resolve_token(config, github, release_path)
    owner_type = (github.get("owner_type") or "").strip() or None

    registry = config.get("registry", {})
    registry_url = None
    if isinstance(registry, dict):
        registry_url = (registry.get("url") or "").strip() or None

    # [targets].registry supersedes [registry].url when present (S11 multi-push)
    registries: list = []
    targets = config.get("targets", {})
    if isinstance(targets, dict):
        reg_list = targets.get("registry")
        if isinstance(reg_list, list):
            registries = [str(r) for r in reg_list if r]
        elif isinstance(reg_list, str) and reg_list:
            registries = [reg_list]
    if registries and not registry_url:
        registry_url = registries[0]

    env_section = config.get("env", {})
    if env_section is None:
        env_section = {}
    if not isinstance(env_section, dict):
        raise ValueError("[env] must be a table of key/value pairs")

    project_env: Mapping[str, str] = {}
    if project_name:
        projects_section = config.get("project") or config.get("projects")
        if projects_section is not None and not isinstance(projects_section, dict):
            raise ValueError("[projects] must be a table")
        if isinstance(projects_section, dict):
            project_section = projects_section.get(project_name)
            if project_section is not None and not isinstance(project_section, dict):
                raise ValueError(f"projects.{project_name} must be a table")
            if isinstance(project_section, dict):
                project_env = project_section.get("env") or {}
                if project_env is None:
                    project_env = {}
                if not isinstance(project_env, dict):
                    raise ValueError(f"projects.{project_name}.env must be a table")

    return ReleaseSecrets(
        github_username=username,
        github_repo=repo,
        github_token=token,
        github_owner_type=owner_type,
        registry_url=registry_url,
        registries=registries,
        env=env_section,
        project_env=project_env,
    )


def apply_release_env(secrets: ReleaseSecrets) -> None:
    if secrets.github_username:
        os.environ.setdefault("GITHUB_USERNAME", secrets.github_username)
    if secrets.github_repo:
        os.environ.setdefault("GITHUB_REPO", secrets.github_repo)
    if secrets.github_token:
        os.environ.setdefault("GITHUB_PUSH_PAT", secrets.github_token)
    if secrets.github_owner_type:
        os.environ.setdefault("GITHUB_OWNER_TYPE", secrets.github_owner_type)
    if secrets.registry_url:
        os.environ.setdefault("REGISTRY", secrets.registry_url)
    if secrets.registries:
        # Expose comma-separated list for bake targets that support multi-registry (S11)
        os.environ.setdefault("REGISTRIES", ",".join(secrets.registries))

    for source in (secrets.project_env, secrets.env):
        for key, value in source.items():
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                os.environ.setdefault(key, value_str)


def _git_out(start: Path, *args: str) -> Optional[str]:
    """Run ``git <args>`` under *start*; return stripped stdout or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    out = result.stdout.strip()
    return out if (result.returncode == 0 and out) else None


def apply_reproducible_env(project_root: Path) -> None:
    """Seed SOURCE_DATE_EPOCH / OCI_REVISION / OCI_CREATED from the HEAD commit when
    unset, so a standalone build/publish (not driven by the orchestrator) is still
    reproducible. The orchestrator sets the same vars first; ``setdefault`` avoids
    clobbering them.
    """
    epoch = os.getenv("SOURCE_DATE_EPOCH") or _git_out(project_root, "log", "-1", "--format=%ct")
    if epoch:
        os.environ.setdefault("SOURCE_DATE_EPOCH", epoch)
        os.environ.setdefault(
            "OCI_CREATED",
            datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    head = _git_out(project_root, "rev-parse", "HEAD")
    if head:
        os.environ.setdefault("OCI_REVISION", head)


def compute_build_date(config: dict, project_root: Path) -> None:
    """Seed the reproducible-build env and a commit-derived ``BUILD_DATE``.

    No wall clock and no auto-increment counter: ``BUILD_DATE`` (consumed by docker
    image tags) is derived from the HEAD commit time, so rebuilding the same commit
    yields the same tag. Wheel versions come from setuptools-scm, not from here.
    """
    apply_reproducible_env(project_root)

    metadata = config.get("build_metadata")
    if not metadata:
        return
    if not isinstance(metadata, dict):
        raise ValueError("build_metadata must be a table")

    date_env = (metadata.get("date_env") or "BUILD_DATE").strip()
    date_format = (metadata.get("date_format") or "%Y%m%d").strip()
    if not os.getenv(date_env):
        epoch = os.getenv("SOURCE_DATE_EPOCH")
        base = (
            datetime.fromtimestamp(int(epoch), timezone.utc)
            if epoch
            else datetime.now(timezone.utc)
        )
        os.environ[date_env] = base.strftime(date_format)


def parse_step(config: dict, step_name: str) -> StepConfig:
    steps = config.get("steps")
    if not steps or not isinstance(steps, dict):
        raise ValueError("[steps] section is required in build-push config")
    step = steps.get(step_name)
    if not step or not isinstance(step, dict):
        raise ValueError(f"Step '{step_name}' not found in build-push config")

    commands = step.get("commands")
    if not commands or not isinstance(commands, list):
        raise ValueError(f"steps.{step_name}.commands must be a list")

    bake_set_prefix = step.get("bake_set_prefix")
    if bake_set_prefix is not None:
        bake_set_prefix = str(bake_set_prefix)

    bake_set_vars = step.get("bake_set_vars") or []
    if not isinstance(bake_set_vars, list):
        raise ValueError(f"steps.{step_name}.bake_set_vars must be a list")

    no_cache_env = step.get("no_cache_env")
    if no_cache_env is not None:
        no_cache_env = str(no_cache_env)

    clean_dirs = step.get("clean_dirs") or []
    if not isinstance(clean_dirs, list):
        raise ValueError(f"steps.{step_name}.clean_dirs must be a list")

    required_env = step.get("required_env") or []
    if not isinstance(required_env, list):
        raise ValueError(f"steps.{step_name}.required_env must be a list")

    login = step.get("login")
    if login is not None and not isinstance(login, dict):
        raise ValueError(f"steps.{step_name}.login must be a table")

    step_env = step.get("env") or {}
    if step_env is None:
        step_env = {}
    if not isinstance(step_env, dict):
        raise ValueError(f"steps.{step_name}.env must be a table")

    env_command = step.get("env_command")
    if env_command is not None and not isinstance(env_command, list):
        raise ValueError(f"steps.{step_name}.env_command must be a list")

    return StepConfig(
        name=step_name,
        commands=commands,
        bake_set_prefix=bake_set_prefix,
        bake_set_vars=bake_set_vars,
        no_cache_env=no_cache_env,
        clean_dirs=clean_dirs,
        required_env=required_env,
        login=login,
        step_env=step_env,
        env_command=[str(item) for item in env_command] if env_command else None,
    )


def ensure_required_env(required: Iterable[str]) -> None:
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def apply_env_command(env_command: Optional[list[str]], cwd: Path) -> None:
    if not env_command:
        return
    log_info(f"Resolving dynamic environment via: {' '.join(env_command)}")
    result = subprocess.run(
        env_command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=True,
    )
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"env_command output must be KEY=VALUE lines. Got: {line}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"env_command produced empty key in line: {line}")
        os.environ[key] = value


def _docker_login(registry: str, username: str, token: str) -> None:
    log_info(f"Logging into {registry} as {username}")
    subprocess.run(
        ["docker", "login", registry, "-u", username, "--password-stdin"],
        input=f"{token}\n",
        text=True,
        check=True,
    )


def maybe_login(login: Optional[dict]) -> None:
    if not login:
        return
    registry = login.get("registry") or "ghcr.io"
    username_env = login.get("username_env") or "GITHUB_USERNAME"
    token_env = login.get("token_env") or "GITHUB_PUSH_PAT"
    required = bool(login.get("required", False))

    username = os.getenv(username_env)
    token = os.getenv(token_env)
    if not token:
        if required:
            raise RuntimeError(f"{token_env} is required for registry login")
        return
    if not username:
        raise RuntimeError(f"{username_env} is required for registry login")
    _docker_login(registry, username, token)


def maybe_login_multi(login: Optional[dict], registries: Optional[list]) -> None:
    """Login to the step's single registry then any additional [targets].registry entries (S11)."""
    maybe_login(login)

    if not registries or len(registries) <= 1:
        return

    # Additional registries beyond the first (which is handled by REGISTRY/login above)
    username = os.getenv("GITHUB_USERNAME") or ""
    token = os.getenv("GITHUB_PUSH_PAT") or ""
    if not username or not token:
        return
    for reg in registries[1:]:
        _docker_login(reg, username, token)


def run_command(argv: list[str], cwd: Path, log_handle) -> None:
    log_info(f"Running: {' '.join(argv)}")
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        log_handle.write(line)
    exit_code = process.wait()
    if exit_code != 0:
        raise subprocess.CalledProcessError(exit_code, argv)


def execute_step(
    step: StepConfig,
    project_root: Path,
    log_dir: Path,
    *,
    extra_env: Optional[Mapping[str, str]] = None,
) -> None:
    """Execute a pre-parsed StepConfig. Called by both run_step() and the orchestrator.

    This is the single execution path every build step flows through (S3 contract).
    ``extra_env`` carries project-level env from the orchestrator (applied with setdefault
    so it does not override already-set vars or step_env).
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    if extra_env:
        for key, value in extra_env.items():
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                os.environ.setdefault(key, value_str)

    for key, value in step.step_env.items():
        if value is None:
            continue
        value_str = str(value).strip()
        if value_str:
            os.environ.setdefault(key, value_str)

    apply_env_command(step.env_command, project_root)
    ensure_required_env(step.required_env)
    maybe_login_multi(step.login, step.registries)

    for target in step.clean_dirs:
        clean_path = resolve_path(project_root, str(target))
        if clean_path.exists():
            shutil.rmtree(clean_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"{step.name}-{timestamp}.log"
    log_info(f"Logging to {log_file}")

    with log_file.open("a", encoding="utf-8") as handle:
        for command in step.commands:
            if not isinstance(command, dict):
                raise ValueError(f"Command entry must be a table in step '{step.name}'")
            label = command.get("label") or "command"
            argv = command.get("argv")
            cwd_raw = command.get("cwd")
            if not argv or not isinstance(argv, list):
                raise ValueError(f"Command '{label}' must define argv list")
            if not cwd_raw:
                raise ValueError(f"Command '{label}' must define cwd")
            cwd = resolve_path(project_root, str(cwd_raw))

            effective_argv = [str(item) for item in argv]
            if step.bake_set_prefix and step.bake_set_vars:
                for var_name in step.bake_set_vars:
                    value = os.getenv(var_name)
                    if value:
                        effective_argv.extend([
                            "--set",
                            f"{step.bake_set_prefix}{var_name}={value}",
                        ])

            if step.no_cache_env and os.getenv(step.no_cache_env) == "1":
                effective_argv.append("--no-cache")

            log_info(label)
            run_command(effective_argv, cwd, handle)


def run_step(build_config_path: Path, step_name: str, release_config_path: Optional[Path]) -> None:
    build_config = load_toml(build_config_path)
    project_root_raw = build_config.get("project_root")
    if not project_root_raw:
        raise ValueError("project_root is required in build-push config")
    project_root = resolve_path(build_config_path.parent, str(project_root_raw))
    project_name_raw = (build_config.get("project_name") or "").strip()
    project_name = project_name_raw or project_root.name

    release_config_raw = None
    if release_config_path:
        release_config = release_config_path
    else:
        release_config_raw = build_config.get("release_config") or os.getenv("RELEASE_MANAGER_CONFIG")
        if release_config_raw:
            release_config = resolve_path(build_config_path.parent, str(release_config_raw))
        else:
            parent = project_root.parent
            release_config = parent / "cmru.toml"
            if not release_config.exists() and (parent / "release.toml").exists():
                release_config = parent / "release.toml"  # legacy fallback (S-CLI.4)
    release_config = release_config.expanduser().resolve()

    secrets = load_release_secrets(release_config, project_name=project_name)
    apply_release_env(secrets)

    env_section = build_config.get("env", {})
    if env_section is None:
        env_section = {}
    if not isinstance(env_section, dict):
        raise ValueError("[env] must be a table in build-push config")
    for key, value in env_section.items():
        if value is None:
            continue
        value_str = str(value).strip()
        if value_str:
            os.environ.setdefault(key, value_str)

    log_dir_raw = build_config.get("log_dir") or "logs"
    log_dir = resolve_path(project_root, str(log_dir_raw))

    compute_build_date(build_config, project_root)

    step = parse_step(build_config, step_name)
    execute_step(step, project_root, log_dir)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run build/push steps from TOML config")
    parser.add_argument("--config", required=True, help="Path to build-push TOML config")
    parser.add_argument("--step", required=True, help="Step name to execute")
    parser.add_argument("--release-config", help="Path to release.toml")
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    run_step(
        Path(args.config).expanduser().resolve(),
        args.step,
        Path(args.release_config).expanduser().resolve() if args.release_config else None,
    )


if __name__ == "__main__":
    main()
