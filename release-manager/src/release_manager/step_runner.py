#!/usr/bin/env python3
"""Generic step runner for build/push scripts (config-driven)."""
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
    env: Mapping[str, str]


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


def load_release_secrets(release_path: Path) -> ReleaseSecrets:
    config = load_toml(release_path)

    github = config.get("github")
    if not github or not isinstance(github, dict):
        raise ValueError("[github] section required in release config")

    username = (github.get("username") or "").strip()
    repo = (github.get("repo") or "").strip()
    token = (github.get("token") or "").strip()
    owner_type = (github.get("owner_type") or "").strip() or None

    registry = config.get("registry", {})
    registry_url = None
    if isinstance(registry, dict):
        registry_url = (registry.get("url") or "").strip() or None

    env_section = config.get("env", {})
    if env_section is None:
        env_section = {}
    if not isinstance(env_section, dict):
        raise ValueError("[env] must be a table of key/value pairs")

    return ReleaseSecrets(
        github_username=username,
        github_repo=repo,
        github_token=token,
        github_owner_type=owner_type,
        registry_url=registry_url,
        env=env_section,
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

    for key, value in secrets.env.items():
        if value is None:
            continue
        value_str = str(value).strip()
        if value_str:
            os.environ.setdefault(key, value_str)


def compute_build_date(config: dict, log_dir: Path) -> None:
    metadata = config.get("build_metadata")
    if not metadata:
        return
    if not isinstance(metadata, dict):
        raise ValueError("build_metadata must be a table")

    date_env = (metadata.get("date_env") or "BUILD_DATE").strip()
    date_format = (metadata.get("date_format") or "%Y%m%d").strip()
    auto_increment_env = (metadata.get("auto_increment_env") or "BUILD_AUTO_INCREMENT").strip()
    counter_template = (metadata.get("counter_file_template") or "build-counter-{date}.txt").strip()
    version_env = (metadata.get("version_env") or "").strip()
    version_from_env = (metadata.get("version_from_env") or "").strip()

    base_date = os.getenv(date_env)
    if not base_date:
        base_date = datetime.now(timezone.utc).strftime(date_format)

    build_date = base_date
    if os.getenv(auto_increment_env) == "1":
        counter_file = log_dir / counter_template.format(date=base_date)
        if counter_file.exists():
            counter_value_raw = counter_file.read_text(encoding="utf-8").strip()
        else:
            counter_value_raw = "0"

        if not counter_value_raw.isdigit():
            raise ValueError(f"Invalid build counter value in {counter_file}: {counter_value_raw}")

        counter_value = int(counter_value_raw)
        if counter_value == 0:
            counter_value = 1
            counter_file.write_text(str(counter_value), encoding="utf-8")
        else:
            build_date = f"{base_date}.{counter_value}"
            counter_value += 1
            counter_file.write_text(str(counter_value), encoding="utf-8")

    os.environ[date_env] = build_date

    if version_env and version_from_env and not os.getenv(version_env):
        source_value = os.getenv(version_from_env)
        if source_value:
            os.environ[version_env] = source_value


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
    )


def ensure_required_env(required: Iterable[str]) -> None:
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


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

    log_info(f"Logging into {registry} as {username}")
    subprocess.run(
        ["docker", "login", registry, "-u", username, "--password-stdin"],
        input=f"{token}\n",
        text=True,
        check=True,
    )


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


def run_step(build_config_path: Path, step_name: str, release_config_path: Optional[Path]) -> None:
    build_config = load_toml(build_config_path)
    project_root_raw = build_config.get("project_root")
    if not project_root_raw:
        raise ValueError("project_root is required in build-push config")
    project_root = resolve_path(build_config_path.parent, str(project_root_raw))

    release_config_raw = None
    if release_config_path:
        release_config = release_config_path
    else:
        release_config_raw = build_config.get("release_config") or os.getenv("RELEASE_MANAGER_CONFIG")
        if release_config_raw:
            release_config = resolve_path(build_config_path.parent, str(release_config_raw))
        else:
            release_config = project_root.parent / "release.toml"
    release_config = release_config.expanduser().resolve()

    secrets = load_release_secrets(release_config)
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
    log_dir.mkdir(parents=True, exist_ok=True)

    compute_build_date(build_config, log_dir)

    step = parse_step(build_config, step_name)
    for key, value in step.step_env.items():
        if value is None:
            continue
        value_str = str(value).strip()
        if value_str:
            os.environ.setdefault(key, value_str)
    ensure_required_env(step.required_env)
    maybe_login(step.login)

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
