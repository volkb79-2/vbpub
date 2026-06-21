#!/usr/bin/env python3
"""Generic bundle builder for stack artifacts (config-driven).

Moved from ``release_manager.bundle_builder`` in P1; ``release_manager.bundle_builder``
is now a re-export shim kept for backwards compatibility until P6.

Deterministic archive support (SPEC B §4)
------------------------------------------
``write_deterministic_tar(members, out_path, source_date_epoch)`` produces a
byte-identical tar.xz across builds given the same inputs:

1. Allowlist-driven membership (never recursive walk).
2. Hard excludes (.git, .ciu, *.toml renders, secrets, caches, logs, …).
3. Normalized TarInfo: mtime=SOURCE_DATE_EPOCH, uid=gid=0, uname=gname="",
   mode=0o644 (files) / 0o755 (dirs), executable bit preserved where intended,
   members sorted by path in byte (C) order.
4. Fixed compression: tarfile xz (equivalent to xz -6), no timestamp in container.

``SOURCE_DATE_EPOCH`` is read from the environment (set by the cmru runner, S3.3).
It is REQUIRED for deterministic builds; the function raises clearly if unset.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import stat
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

import tomllib


# ---------------------------------------------------------------------------
# Hard-exclude patterns (§4.2) — belt-and-suspenders even if the allowlist
# would never include them.
# ---------------------------------------------------------------------------
_HARD_EXCLUDE_NAMES = frozenset({
    ".git", ".ciu",
    "ciu.env", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules",
})

_HARD_EXCLUDE_SUFFIXES = (
    # rendered compose / config outputs
    ".toml",          # rendered *.toml outputs; note: source cmru.toml is also excluded
    ".env",
    # secret stores / certificates
    ".pem", ".crt", ".key", ".p12", ".pfx",
    # runtime logs / test output
    ".log",
    # caches
    ".pyc",
)

# Specific filename exclusions (exact match on name, not suffix).
_HARD_EXCLUDE_EXACT = frozenset({
    "ciu.env",
    "minisign.key",       # secret signing key — must never be bundled
})


def _is_excluded(rel_path: str) -> bool:
    """Return True if rel_path should be excluded from the archive."""
    parts = Path(rel_path).parts
    for part in parts:
        if part in _HARD_EXCLUDE_NAMES:
            return True
        if part in _HARD_EXCLUDE_EXACT:
            return True
    name = Path(rel_path).name
    if name in _HARD_EXCLUDE_EXACT:
        return True
    for suffix in _HARD_EXCLUDE_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False


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
    archive_format: str
    copy_files: list[str]
    copy_dirs: list[str]


@dataclass
class BundleMember:
    """A single file to include in the deterministic archive.

    archive_path: the path inside the archive (e.g. "bundle/config.py").
    source_path:  the absolute path on disk (or None for in-memory content).
    content:      in-memory bytes (used when source_path is None).
    executable:   if True, mode is set to 0o755; otherwise 0o644.
    """
    archive_path: str
    source_path: Optional[Path] = None
    content: Optional[bytes] = None
    executable: bool = False

    def __post_init__(self) -> None:
        if self.source_path is None and self.content is None:
            raise ValueError(f"BundleMember({self.archive_path!r}): either source_path or content is required")


def _read_source_date_epoch() -> int:
    """Read SOURCE_DATE_EPOCH from env; raise clearly if unset."""
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if not raw:
        raise RuntimeError(
            "SOURCE_DATE_EPOCH is not set. The cmru runner sets it automatically "
            "(SPEC.md S3.3). For standalone use: "
            "export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)"
        )
    return int(raw)


def write_deterministic_tar(
    members: Sequence[BundleMember],
    out_path: Path,
    source_date_epoch: Optional[int] = None,
) -> Path:
    """Write a byte-deterministic tar.xz to out_path (SPEC B §4).

    Determinism contract:
    - Members sorted by archive_path in byte order (C locale, no locale-dependent collation).
    - mtime = source_date_epoch for every member.
    - uid = gid = 0; uname = gname = "".
    - mode = 0o644 for files (0o755 if executable=True); 0o755 for dirs.
    - No device/char/fifo nodes.
    - Fixed compression: xz (tarfile w:xz).

    Excluded paths (hard excludes, §4.2) are silently dropped before writing.

    Args:
        members:            Ordered-by-caller or unsorted list of BundleMembers.
        out_path:           Destination .tar.xz path (parent must exist or be created).
        source_date_epoch:  Unix timestamp; if None, reads from SOURCE_DATE_EPOCH env.

    Returns:
        out_path
    """
    if source_date_epoch is None:
        source_date_epoch = _read_source_date_epoch()

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Filter hard-excludes, then sort by archive_path in byte (C) order.
    filtered = [m for m in members if not _is_excluded(m.archive_path)]
    sorted_members = sorted(filtered, key=lambda m: m.archive_path.encode())

    with tarfile.open(str(out_path), mode="w:xz") as tf:
        for member in sorted_members:
            if member.source_path is not None:
                data = member.source_path.read_bytes()
            else:
                assert member.content is not None
                data = member.content

            info = tarfile.TarInfo(name=member.archive_path)
            info.size = len(data)
            info.mtime = source_date_epoch
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o755 if member.executable else 0o644
            info.type = tarfile.REGTYPE

            tf.addfile(info, io.BytesIO(data))

    return out_path


def collect_allowlist_members(
    project_root: Path,
    allowlist: Sequence[str],
    *,
    archive_prefix: str = "bundle",
    extra_members: Optional[Sequence[BundleMember]] = None,
) -> List[BundleMember]:
    """Expand an allowlist of project-relative paths to BundleMembers.

    Each entry in allowlist is a path relative to project_root.  Directories
    are expanded to all contained files (recursively).  Hard excludes are
    applied at collection time (and again at write time — belt-and-suspenders).

    archive_prefix: every member's archive path is prefixed with this string
                    (e.g. "bundle" → "bundle/src/app.py").
    extra_members:  additional members (e.g. generated manifest, built wheels)
                    appended after the allowlist expansion.

    Returns a list of BundleMembers (unsorted — write_deterministic_tar sorts them).
    """
    result: List[BundleMember] = []

    for entry in allowlist:
        abs_path = (project_root / entry).resolve()
        if not abs_path.exists():
            raise FileNotFoundError(
                f"Allowlisted path does not exist: {abs_path} (from {entry!r})"
            )
        if _is_excluded(entry):
            continue

        if abs_path.is_dir():
            for child in sorted(abs_path.rglob("*")):
                if not child.is_file():
                    continue
                rel = child.relative_to(project_root).as_posix()
                if _is_excluded(rel):
                    continue
                arc = f"{archive_prefix}/{rel}" if archive_prefix else rel
                exe = bool(child.stat().st_mode & stat.S_IXUSR)
                result.append(BundleMember(archive_path=arc, source_path=child, executable=exe))
        else:
            rel = abs_path.relative_to(project_root).as_posix()
            arc = f"{archive_prefix}/{rel}" if archive_prefix else rel
            exe = bool(abs_path.stat().st_mode & stat.S_IXUSR)
            result.append(BundleMember(archive_path=arc, source_path=abs_path, executable=exe))

    if extra_members:
        result.extend(extra_members)

    return result


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
    _valid_formats = {"tar", "gztar", "bztar", "xztar", "zip"}
    archive_format = str(archive.get("format") or "gztar")
    if archive_format not in _valid_formats:
        raise ValueError(f"[archive].format must be one of {sorted(_valid_formats)}, got {archive_format!r}")

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
        archive_format=archive_format,
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
        config.archive_format,
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
