#!/usr/bin/env python3
"""Generic bundle builder for stack artifacts (config-driven)."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tomllib


@dataclass(frozen=True)
class BundleConfig:
    project_root: Path
    wheel_project_root: Path
    dist_dir: Path
    bundle_dir: Path
    client_dir: Path
    wheel_enabled: bool
    wheel_python_bin: str
    wheel_find_links: Optional[Path]
    archive_template: str
    archive_version_env: str
    archive_fallback_env: str
    copy_files: list[str]
    copy_dirs: list[str]


def log_info(message: str) -> None:
    print(f"[INFO] {message}")


def load_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def parse_config(config_path: Path) -> BundleConfig:
    config = load_toml(config_path)
    project_root_raw = config.get("project_root")
    if not project_root_raw:
        raise ValueError("project_root is required in bundle config")
    project_root = resolve_path(config_path.parent, str(project_root_raw))

    dist_dir = resolve_path(project_root, str(config.get("dist_dir") or "dist"))
    bundle_dir = resolve_path(dist_dir, str(config.get("bundle_dir") or "bundle"))
    client_dir = resolve_path(dist_dir, str(config.get("client_dir") or "client"))

    wheel = config.get("wheel", {})
    if wheel is None:
        wheel = {}
    if not isinstance(wheel, dict):
        raise ValueError("[wheel] must be a table")
    wheel_enabled = bool(wheel.get("enabled", False))
    wheel_python_bin = str(wheel.get("python_bin") or "python3")
    wheel_project_root_raw = wheel.get("project_root")
    if wheel_project_root_raw:
        wheel_project_root = resolve_path(config_path.parent, str(wheel_project_root_raw))
    else:
        wheel_project_root = project_root
    wheel_find_links_raw = wheel.get("find_links")
    if wheel_find_links_raw:
        wheel_find_links = resolve_path(config_path.parent, str(wheel_find_links_raw))
    else:
        wheel_find_links = None

    archive = config.get("archive")
    if not archive or not isinstance(archive, dict):
        raise ValueError("[archive] section is required in bundle config")
    archive_template = str(archive.get("name_template") or "bundle-{version}.tar.gz")
    archive_version_env = str(archive.get("version_env") or "VERSION")
    archive_fallback_env = str(archive.get("fallback_env") or "BUILD_DATE")

    copy = config.get("copy")
    if not copy or not isinstance(copy, dict):
        raise ValueError("[copy] section is required in bundle config")
    copy_files = copy.get("files") or []
    copy_dirs = copy.get("dirs") or []
    if not isinstance(copy_files, list) or not isinstance(copy_dirs, list):
        raise ValueError("copy.files and copy.dirs must be lists")

    return BundleConfig(
        project_root=project_root,
        wheel_project_root=wheel_project_root,
        dist_dir=dist_dir,
        bundle_dir=bundle_dir,
        client_dir=client_dir,
        wheel_enabled=wheel_enabled,
        wheel_python_bin=wheel_python_bin,
        wheel_find_links=wheel_find_links,
        archive_template=archive_template,
        archive_version_env=archive_version_env,
        archive_fallback_env=archive_fallback_env,
        copy_files=[str(item) for item in copy_files],
        copy_dirs=[str(item) for item in copy_dirs],
    )


def build_wheel(config: BundleConfig) -> None:
    if not config.wheel_enabled:
        return
    log_info("Building client wheel")
    config.client_dir.mkdir(parents=True, exist_ok=True)
    command = [config.wheel_python_bin, "-m", "pip", "wheel", ".", "-w", str(config.client_dir)]
    if config.wheel_find_links is not None:
        command.extend(["--find-links", str(config.wheel_find_links)])
    subprocess.run(command, check=True, cwd=str(config.wheel_project_root))


def copy_sources(config: BundleConfig) -> None:
    for file_path in config.copy_files:
        source = resolve_path(config.project_root, file_path)
        if not source.exists():
            raise FileNotFoundError(f"Bundle source file not found: {source}")
        shutil.copy2(source, config.bundle_dir / source.name)

    for dir_path in config.copy_dirs:
        source = resolve_path(config.project_root, dir_path)
        if not source.exists():
            raise FileNotFoundError(f"Bundle source dir not found: {source}")
        shutil.copytree(source, config.bundle_dir / source.name)

    if config.client_dir.exists():
        shutil.copytree(config.client_dir, config.bundle_dir / config.client_dir.name)


def create_archive(config: BundleConfig) -> Path:
    version_value = os.getenv(config.archive_version_env) if config.archive_version_env else None
    if not version_value:
        version_value = os.getenv(config.archive_fallback_env)
    if not version_value:
        raise RuntimeError(
            f"{config.archive_version_env} or {config.archive_fallback_env} must be set for archive naming"
        )

    tarball_name = config.archive_template.format(version=version_value)
    tarball_path = config.dist_dir / tarball_name

    log_info(f"Creating archive {tarball_path}")
    shutil.make_archive(
        tarball_path.with_suffix("").with_suffix(""),
        "gztar",
        root_dir=config.dist_dir,
        base_dir=config.bundle_dir.name,
    )
    return tarball_path


def run_bundle(config_path: Path) -> Path:
    config = parse_config(config_path)

    log_info("Preparing dist directories")
    if config.dist_dir.exists():
        shutil.rmtree(config.dist_dir)
    config.bundle_dir.mkdir(parents=True, exist_ok=True)

    build_wheel(config)
    copy_sources(config)
    return create_archive(config)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a stack bundle from TOML config")
    parser.add_argument("--config", required=True, help="Path to bundle TOML config")
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    archive = run_bundle(Path(args.config).expanduser().resolve())
    log_info(f"Done: {archive}")


if __name__ == "__main__":
    main()
