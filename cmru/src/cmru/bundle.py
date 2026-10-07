#!/usr/bin/env python3
"""Generic bundle builder for stack artifacts (config-driven).

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

import io
import os
import shutil
import stat
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter
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
    # A bundled source client must retain its build metadata.  This is an
    # explicitly allowlisted source file, not a rendered runtime config.
    if name == "pyproject.toml":
        return False
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


def _reject_unknown_keys(table: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"unknown {where} key(s): {', '.join(unknown)}")


def _string_value(table: dict, key: str, where: str, *, default: Optional[str] = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def _string_list_value(table: dict, key: str, where: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{where}.{key} must be an array of non-empty strings")
    return [item.strip() for item in value]


def _validate_archive_template(template: str) -> None:
    """Require one plain version field in a filename-only template."""
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValueError("archive.name_template must contain exactly one {version} field") from exc
    fields = [
        (field, format_spec, conversion)
        for _, field, format_spec, conversion in parsed
        if field is not None
    ]
    if (
        fields != [("version", "", None)]
        or "/" in template
        or "\\" in template
    ):
        raise ValueError(
            "archive.name_template must be a filename containing exactly one plain {version} field"
        )


def parse_config(config_path: Path) -> BundleConfig:
    config = load_toml(config_path)
    _reject_unknown_keys(
        config,
        {"project_root", "dist_dir", "bundle_dir", "client_dir", "wheel", "archive", "copy"},
        "bundle config",
    )
    project_root_raw = _string_value(config, "project_root", "bundle config")
    project_root = resolve_path(config_path.parent, project_root_raw)

    dist_dir = resolve_path(project_root, _string_value(config, "dist_dir", "bundle config", default="dist"))
    bundle_dir = resolve_path(dist_dir, _string_value(config, "bundle_dir", "bundle config", default="bundle"))
    client_dir = resolve_path(dist_dir, _string_value(config, "client_dir", "bundle config", default="client"))

    wheel = config.get("wheel", {})
    if not isinstance(wheel, dict):
        raise ValueError("[wheel] must be a table")
    _reject_unknown_keys(wheel, {"enabled", "python_bin", "project_root", "find_links"}, "wheel")
    wheel_enabled = wheel.get("enabled", False)
    if not isinstance(wheel_enabled, bool):
        raise ValueError("wheel.enabled must be true or false")
    wheel_python_bin = _string_value(wheel, "python_bin", "wheel", default="python3")
    wheel_project_root_raw = wheel.get("project_root")
    if wheel_project_root_raw is not None and (
        not isinstance(wheel_project_root_raw, str) or not wheel_project_root_raw.strip()
    ):
        raise ValueError("wheel.project_root must be a non-empty string when present")
    wheel_project_root = resolve_path(
        config_path.parent, wheel_project_root_raw.strip()
    ) if isinstance(wheel_project_root_raw, str) else project_root
    wheel_find_links_raw = wheel.get("find_links")
    if wheel_find_links_raw is not None and (
        not isinstance(wheel_find_links_raw, str) or not wheel_find_links_raw.strip()
    ):
        raise ValueError("wheel.find_links must be a non-empty string when present")
    wheel_find_links = resolve_path(
        config_path.parent, wheel_find_links_raw.strip()
    ) if isinstance(wheel_find_links_raw, str) else None

    archive = config.get("archive")
    if not isinstance(archive, dict):
        raise ValueError("[archive] section is required in bundle config")
    _reject_unknown_keys(archive, {"name_template", "version_env", "format"}, "archive")
    archive_template = _string_value(archive, "name_template", "archive")
    _validate_archive_template(archive_template)
    archive_version_env = _string_value(archive, "version_env", "archive")
    _valid_formats = {"tar", "gztar", "bztar", "xztar", "zip"}
    archive_format = _string_value(archive, "format", "archive", default="gztar")
    if archive_format not in _valid_formats:
        raise ValueError(f"[archive].format must be one of {sorted(_valid_formats)}, got {archive_format!r}")

    copy = config.get("copy")
    if not isinstance(copy, dict):
        raise ValueError("[copy] section is required in bundle config")
    _reject_unknown_keys(copy, {"files", "dirs"}, "copy")
    copy_files = _string_list_value(copy, "files", "copy")
    copy_dirs = _string_list_value(copy, "dirs", "copy")

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
        archive_format=archive_format,
        copy_files=copy_files,
        copy_dirs=copy_dirs,
    )


def build_wheel(config: BundleConfig) -> None:
    if not config.wheel_enabled:
        return
    log_info("Building client wheel")
    config.client_dir.mkdir(parents=True, exist_ok=True)
    command = [config.wheel_python_bin, "-m", "pip", "wheel", "."]
    if config.wheel_find_links is not None:
        command.append("--no-index")
    command.extend(["-w", str(config.client_dir)])
    if config.wheel_find_links is not None:
        # ``pip wheel .`` also collects the project's DEPENDENCY wheels into
        # client_dir (that is the bundle's purpose), so ``--no-deps`` would
        # change the artifact. When a local wheelhouse is declared it is the
        # ONLY source (``--no-index``): a PyPI-default pip must never resolve an
        # estate-internal name from a public index (dependency confusion, BG-05).
        # The wheelhouse must therefore also hold the build requirements.
        command.extend(["--find-links", str(config.wheel_find_links)])
    subprocess.run(command, check=True, cwd=str(config.wheel_project_root))


def copy_sources(config: BundleConfig) -> None:
    def ignore_excluded(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        base = Path(directory)
        for name in names:
            candidate = base / name
            try:
                rel = candidate.relative_to(config.project_root).as_posix()
            except ValueError:
                rel = candidate.name
            if _is_excluded(rel):
                ignored.add(name)
        return ignored

    for file_path in config.copy_files:
        source = resolve_path(config.project_root, file_path)
        if not source.exists():
            raise FileNotFoundError(f"Bundle source file not found: {source}")
        if _is_excluded(file_path):
            continue
        shutil.copy2(source, config.bundle_dir / source.name)

    for dir_path in config.copy_dirs:
        source = resolve_path(config.project_root, dir_path)
        if not source.exists():
            raise FileNotFoundError(f"Bundle source dir not found: {source}")
        shutil.copytree(source, config.bundle_dir / source.name, ignore=ignore_excluded)

    if config.client_dir.exists():
        shutil.copytree(
            config.client_dir,
            config.bundle_dir / config.client_dir.name,
            ignore=ignore_excluded,
        )


def create_archive(config: BundleConfig) -> Path:
    version_value = os.getenv(config.archive_version_env) if config.archive_version_env else None
    if not version_value:
        raise RuntimeError(
            f"{config.archive_version_env} must be set for archive naming"
        )

    tarball_name = config.archive_template.format(version=version_value)
    if (
        not tarball_name
        or tarball_name in {".", ".."}
        or "/" in tarball_name
        or "\\" in tarball_name
        or Path(tarball_name).is_absolute()
    ):
        raise ValueError("archive name after version substitution must be a single filename")
    tarball_path = config.dist_dir / tarball_name

    log_info(f"Creating archive {tarball_path}")
    if config.archive_format == "xztar":
        members = collect_allowlist_members(
            config.dist_dir,
            [config.bundle_dir.name],
            archive_prefix="",
        )
        return write_deterministic_tar(members, tarball_path)

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


if __name__ == "__main__":
    raise SystemExit(
        "cmru.bundle is a Python library, not a command; call run_bundle(config_path)."
    )
