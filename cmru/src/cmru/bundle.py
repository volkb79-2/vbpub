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
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from string import Formatter
from typing import Callable, List, Mapping, Optional, Sequence

import tomllib

from cmru.config import VariantConfig


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

_NEVER_KEEP_SUFFIXES = (".pyc",)
_KEEPABLE_EXCLUDE_SUFFIXES = tuple(
    suffix for suffix in _HARD_EXCLUDE_SUFFIXES if suffix not in _NEVER_KEEP_SUFFIXES
)
_KEEP_GLOB_CHARS = frozenset("*?[]{}!")

# Specific filename exclusions (exact match on name, not suffix).
_HARD_EXCLUDE_EXACT = frozenset({
    "ciu.env",
    "minisign.key",       # secret signing key — must never be bundled
})


def _has_hard_excluded_name(rel_path: str) -> bool:
    parts = PurePosixPath(rel_path).parts
    for part in parts:
        if part in _HARD_EXCLUDE_NAMES:
            return True
        if part in _HARD_EXCLUDE_EXACT:
            return True
    return False


def _is_excluded(rel_path: str, keep_paths: Sequence[str] = ()) -> bool:
    """The shared bundle-relative hard-exclude predicate used by every build phase."""
    path = PurePosixPath(rel_path)
    if _has_hard_excluded_name(rel_path):
        return True
    name = path.name
    if any(name.endswith(suffix) for suffix in _NEVER_KEEP_SUFFIXES):
        return True
    # A bundled source client must retain its build metadata.  This is an
    # explicitly allowlisted source file, not a rendered runtime config.
    if name == "pyproject.toml":
        return False
    if any(name.endswith(suffix) for suffix in _KEEPABLE_EXCLUDE_SUFFIXES):
        return path.as_posix() not in keep_paths
    return False


def _validate_keep_paths(value: Sequence[str]) -> list[str]:
    """Validate the exact, bundle-relative ``[archive].keep`` allowlist."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        path = PurePosixPath(raw)
        parts = raw.split("/")
        if (
            not raw
            or raw.startswith("/")
            or "\\" in raw
            or PureWindowsPath(raw).drive
            or any(part in {"", ".", ".."} for part in parts)
            or any(char in _KEEP_GLOB_CHARS for char in raw)
            or any(ord(char) < 32 for char in raw)
        ):
            raise ValueError(
                f"[archive].keep entry {raw!r} must be an exact relative POSIX file path "
                "without glob syntax"
            )
        if path.is_absolute() or _has_hard_excluded_name(raw) or any(
            path.name.endswith(suffix) for suffix in _NEVER_KEEP_SUFFIXES
        ):
            raise ValueError(
                f"[archive].keep entry {raw!r} names a hard-excluded, non-keepable path"
            )
        normalized = path.as_posix()
        if normalized in seen:
            raise ValueError(f"[archive].keep contains duplicate path {normalized!r}")
        seen.add(normalized)
        result.append(normalized)
    return result


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
    vendor: Optional["VendorConfig"] = None
    stage: Optional["StageConfig"] = None
    exec_prefix: Optional["ExecConfig"] = None
    manifest_name: Optional[str] = None
    archive_keep: list[str] = field(default_factory=list)
    keep_tree: bool = False


@dataclass(frozen=True)
class VendorConfig:
    """``[vendor]``: the dependency closure for one interpreter ABI (KI-126)."""
    requirements: list[str]   # project-relative requirement manifests
    platforms: list[str]      # pip --platform values
    dest: str                 # bundle-relative directory the wheels land in


@dataclass(frozen=True)
class StageConfig:
    """``[stage]``: one argv run between copy/vendor and the manifest (KI-126)."""
    argv: list[str]


@dataclass(frozen=True)
class ExecConfig:
    """``[exec]``: runner prefix for the vendor and stage argv only (KI-126).

    ``env`` maps each prefix token (``root``, ``container``) to the NAME of the
    environment variable that holds its value, so no container name is stored in the file.
    """
    prefix: list[str]
    env: Mapping[str, str]


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
    *,
    keep_paths: Sequence[str] = (),
    bundle_prefix: Optional[str] = None,
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

    # Filter hard-excludes, then sort by archive_path in byte (C) order. Archive
    # members carry the bundle directory prefix, while keep entries do not.
    def bundle_relative(archive_path: str) -> str:
        if bundle_prefix and archive_path.startswith(bundle_prefix + "/"):
            return archive_path[len(bundle_prefix) + 1:]
        return archive_path

    filtered = [
        member for member in members
        if not _is_excluded(bundle_relative(member.archive_path), keep_paths)
    ]
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
    keep_paths: Sequence[str] = (),
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
        if _is_excluded(entry, keep_paths):
            continue

        if abs_path.is_dir():
            for child in sorted(abs_path.rglob("*")):
                if not child.is_file():
                    continue
                rel = child.relative_to(project_root).as_posix()
                if _is_excluded(rel, keep_paths):
                    continue
                arc = f"{archive_prefix}/{rel}" if archive_prefix else rel
                exe = bool(child.stat().st_mode & stat.S_IXUSR)
                result.append(BundleMember(archive_path=arc, source_path=child, executable=exe))
        else:
            rel = abs_path.relative_to(project_root).as_posix()
            arc = f"{archive_prefix}/{rel}" if archive_prefix else rel
            exe = bool(abs_path.stat().st_mode & stat.S_IXUSR)
            if not _is_excluded(rel, keep_paths):
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


def _copy_path(base: Path, raw: str) -> Path:
    """Resolve a configured copy path lexically so symlink components remain visible."""
    path = Path(raw)
    return path.absolute() if path.is_absolute() else (base / path).absolute()


def _has_symlink_component(path: Path) -> bool:
    return any(component.is_symlink() for component in (path, *path.parents))


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
    """Require one plain version field (and at most one plain variant field) in a filename-only template."""
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
        fields not in ([("version", "", None)],
                       [("version", "", None), ("variant", "", None)],
                       [("variant", "", None), ("version", "", None)])
        or "/" in template
        or "\\" in template
    ):
        raise ValueError(
            "archive.name_template must be a filename containing exactly one plain {version} "
            "field and at most one plain {variant} field"
        )


_STAGE_TOKENS = frozenset({"bundle_dir", "variant", "python_version"})
_EXEC_TOKENS = frozenset({"root", "container"})
_ENV_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


def _tokens_of(text: str, allowed: frozenset, where: str) -> set[str]:
    """The plain ``{name}`` fields of ``text``; any other field, spec or conversion is refused."""
    try:
        parsed = list(Formatter().parse(text))
    except ValueError as exc:
        raise ValueError(f"{where}: malformed token syntax in {text!r}") from exc
    found: set[str] = set()
    for _, field, format_spec, conversion in parsed:
        if field is None:
            continue
        if field not in allowed or format_spec or conversion:
            raise ValueError(
                f"{where}: unknown token {{{field}}} in {text!r} (allowed: {', '.join(sorted(allowed))})"
            )
        found.add(field)
    return found


def _table(config: dict, key: str) -> Optional[dict]:
    value = config.get(key)
    if value is not None and not isinstance(value, dict):
        raise ValueError(f"[{key}] must be a table")
    return value


def _parse_vendor(config: dict) -> Optional[VendorConfig]:
    table = _table(config, "vendor")
    if table is None:
        return None
    _reject_unknown_keys(table, {"requirements", "platforms", "dest"}, "vendor")
    requirements = _string_list_value(table, "requirements", "vendor")
    platforms = _string_list_value(table, "platforms", "vendor")
    if not requirements or not platforms:
        raise ValueError("vendor.requirements and vendor.platforms must each name at least one entry")
    dest = _string_value(table, "dest", "vendor", default="vendor")
    if Path(dest).is_absolute() or ".." in Path(dest).parts:
        raise ValueError("vendor.dest must be a relative path inside the bundle")
    if _is_excluded(dest):
        raise ValueError("vendor.dest must not name a path excluded from the bundle archive")
    return VendorConfig(requirements=requirements, platforms=platforms, dest=dest)


def _parse_stage(config: dict) -> Optional[StageConfig]:
    table = _table(config, "stage")
    if table is None:
        return None
    _reject_unknown_keys(table, {"argv"}, "stage")
    argv = _string_list_value(table, "argv", "stage")
    if not argv:
        raise ValueError("stage.argv must name a command")
    for item in argv:
        _tokens_of(item, _STAGE_TOKENS, "stage.argv")
    return StageConfig(argv=argv)


def _parse_exec(config: dict) -> Optional[ExecConfig]:
    table = _table(config, "exec")
    if table is None:
        return None
    _reject_unknown_keys(table, {"prefix", "env"}, "exec")
    prefix = _string_list_value(table, "prefix", "exec")
    if not prefix:
        raise ValueError("exec.prefix must name a command")
    used: set[str] = set()
    for item in prefix:
        used |= _tokens_of(item, _EXEC_TOKENS, "exec.prefix")
    env = table.get("env", {})
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v
        or not set(v) <= _ENV_NAME_CHARS
        for k, v in env.items()
    ):
        raise ValueError("exec.env must map token names to environment variable names")
    _reject_unknown_keys(env, set(_EXEC_TOKENS), "exec.env")
    missing = sorted(used - set(env))
    if missing:
        raise ValueError(f"exec.prefix uses {{{missing[0]}}} but exec.env does not name its environment variable")
    return ExecConfig(prefix=prefix, env=dict(env))


def parse_config(config_path: Path) -> BundleConfig:
    config = load_toml(config_path)
    _reject_unknown_keys(
        config,
        {"project_root", "dist_dir", "bundle_dir", "client_dir", "wheel", "archive", "copy",
         "vendor", "stage", "exec", "manifest"},
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
    _reject_unknown_keys(
        archive, {"name_template", "version_env", "format", "keep", "keep_tree"}, "archive"
    )
    archive_template = _string_value(archive, "name_template", "archive")
    _validate_archive_template(archive_template)
    archive_version_env = _string_value(archive, "version_env", "archive")
    _valid_formats = {"tar", "gztar", "bztar", "xztar", "zip"}
    archive_format = _string_value(archive, "format", "archive", default="gztar")
    if archive_format not in _valid_formats:
        raise ValueError(f"[archive].format must be one of {sorted(_valid_formats)}, got {archive_format!r}")
    archive_keep = _validate_keep_paths(_string_list_value(archive, "keep", "archive"))
    keep_tree = archive.get("keep_tree", False)
    if not isinstance(keep_tree, bool):
        raise ValueError("archive.keep_tree must be true or false")

    copy = config.get("copy")
    if not isinstance(copy, dict):
        raise ValueError("[copy] section is required in bundle config")
    _reject_unknown_keys(copy, {"files", "dirs"}, "copy")
    copy_files = _string_list_value(copy, "files", "copy")
    copy_dirs = _string_list_value(copy, "dirs", "copy")

    manifest = _table(config, "manifest")
    manifest_name: Optional[str] = None
    if manifest is not None:
        _reject_unknown_keys(manifest, {"name"}, "manifest")
        manifest_name = _string_value(manifest, "name", "manifest", default="manifest.json")
        if "/" in manifest_name or "\\" in manifest_name or manifest_name in {".", ".."}:
            raise ValueError("manifest.name must be a single filename")
        if _is_excluded(manifest_name):
            raise ValueError("manifest.name must not be excluded from the bundle archive")

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
        vendor=_parse_vendor(config),
        stage=_parse_stage(config),
        exec_prefix=_parse_exec(config),
        manifest_name=manifest_name,
        archive_keep=archive_keep,
        keep_tree=keep_tree,
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


def _warn_excluded_copy(path: str) -> None:
    print(f"[WARN] Skipping hard-excluded bundle path: {path}", file=sys.stderr)


def _copy_ignore(
    config: BundleConfig, source_root: Path, bundle_prefix: str,
) -> Callable[[str, list[str]], set[str]]:
    def ignore_excluded(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        base = Path(directory)
        relative_dir = base.relative_to(source_root)
        for name in names:
            output_parts = [bundle_prefix]
            if relative_dir != Path("."):
                output_parts.extend(relative_dir.parts)
            output_parts.append(name)
            rel = PurePosixPath(*output_parts).as_posix()
            if _is_excluded(rel, config.archive_keep):
                _warn_excluded_copy(rel)
                ignored.add(name)
        return ignored

    return ignore_excluded


def _reject_copy_symlinks(config: BundleConfig, source_root: Path, bundle_prefix: str) -> None:
    """Do not dereference source symlinks into bundle files during copytree/copy2."""
    for path in (source_root, *source_root.rglob("*")):
        relative = path.relative_to(source_root)
        output_parts = [bundle_prefix, *relative.parts] if relative != Path(".") else [bundle_prefix]
        rel = PurePosixPath(*output_parts).as_posix()
        if _is_excluded(rel, config.archive_keep):
            continue
        if _has_symlink_component(path):
            raise ValueError(f"bundle source path {rel!r} is a symlink; refusing to copy its target")
        if not path.is_dir() and not path.is_file():
            raise ValueError(f"bundle source path {rel!r} is not a regular file or directory")


def copy_sources(config: BundleConfig) -> None:
    for file_path in config.copy_files:
        source = _copy_path(config.project_root, file_path)
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(f"Bundle source file not found: {source}")
        rel = PurePosixPath(source.name).as_posix()
        if _is_excluded(rel, config.archive_keep):
            _warn_excluded_copy(rel)
            continue
        if _has_symlink_component(source) or not source.is_file():
            raise ValueError(f"bundle source path {rel!r} is not a regular file")
        shutil.copy2(source, config.bundle_dir / source.name)

    for dir_path in config.copy_dirs:
        source = _copy_path(config.project_root, dir_path)
        if not source.exists():
            raise FileNotFoundError(f"Bundle source dir not found: {source}")
        if _has_symlink_component(source) or not source.is_dir():
            raise ValueError(f"Bundle source dir is not a directory: {source}")
        if _has_hard_excluded_name(source.name):
            _warn_excluded_copy(PurePosixPath(source.name).as_posix())
            continue
        _reject_copy_symlinks(config, source, source.name)
        shutil.copytree(
            source, config.bundle_dir / source.name,
            ignore=_copy_ignore(config, source, source.name),
        )

    if config.client_dir.exists():
        _reject_copy_symlinks(config, config.client_dir, config.client_dir.name)
        shutil.copytree(
            config.client_dir,
            config.bundle_dir / config.client_dir.name,
            ignore=_copy_ignore(config, config.client_dir, config.client_dir.name),
        )


def archive_path(
    config: BundleConfig,
    variant_name: Optional[str] = None,
    *,
    version_fallback: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """The archive this build writes; validated before any vendor/stage command runs."""
    environment = os.environ if environ is None else environ
    version_value = environment.get(config.archive_version_env) if config.archive_version_env else None
    if not version_value:
        version_value = version_fallback
    if not version_value:
        raise RuntimeError(
            f"{config.archive_version_env} must be set for archive naming"
        )

    tarball_name = config.archive_template.format(
        version=version_value, variant=variant_name or "",
    )
    if (
        not tarball_name
        or tarball_name in {".", ".."}
        or "/" in tarball_name
        or "\\" in tarball_name
        or Path(tarball_name).is_absolute()
    ):
        raise ValueError("archive name after version substitution must be a single filename")
    return config.dist_dir / tarball_name


def create_archive(
    config: BundleConfig,
    variant_name: Optional[str] = None,
    *,
    version_fallback: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    tarball_path = archive_path(
        config, variant_name, version_fallback=version_fallback, environ=environ,
    )

    log_info(f"Creating archive {tarball_path}")
    if config.archive_format == "xztar":
        members = collect_allowlist_members(
            config.bundle_dir,
            ["."],
            archive_prefix=config.bundle_dir.name,
            keep_paths=config.archive_keep,
        )
        return write_deterministic_tar(
            members, tarball_path, keep_paths=config.archive_keep,
            bundle_prefix=config.bundle_dir.name,
        )

    with tempfile.TemporaryDirectory(prefix="cmru-bundle-archive-", dir=config.dist_dir) as temp:
        temp_root = Path(temp)
        shutil.copytree(
            config.bundle_dir,
            temp_root / config.bundle_dir.name,
            ignore=_copy_ignore(config, config.bundle_dir, config.bundle_dir.name),
        )
        shutil.make_archive(
            tarball_path.with_suffix("").with_suffix(""),
            config.archive_format,
            root_dir=temp_root,
            base_dir=config.bundle_dir.name,
        )
    return tarball_path


Runner = Callable[[Sequence[str], Path], None]


def _default_runner(argv: Sequence[str], cwd: Path) -> None:
    subprocess.run(list(argv), check=True, cwd=str(cwd))


def _resolved_prefix(exec_config: Optional[ExecConfig], environ: Mapping[str, str]) -> list[str]:
    """The runner prefix with each ``{root}``/``{container}`` taken from its named env var."""
    if exec_config is None:
        return []
    values: dict[str, str] = {}
    used: list[str] = []
    for item in exec_config.prefix:
        for token in _tokens_of(item, _EXEC_TOKENS, "exec.prefix"):
            if token not in used:
                used.append(token)
    for token in used:
        env_name = exec_config.env[token]
        value = environ.get(env_name)
        if not value:
            raise RuntimeError(f"[exec] needs the environment variable {env_name} ({{{token}}}) to be set")
        values[token] = value
    return [item.format(**values) for item in exec_config.prefix]


def _runner_path(config: BundleConfig, path: Path, prefixed: bool) -> str:
    """A path as the vendor/stage command sees it: absolute locally, project-root relative in a runner."""
    if not prefixed:
        return str(path)
    try:
        return path.relative_to(config.project_root).as_posix() or "."
    except ValueError:
        raise ValueError(
            f"with [exec].prefix the path {path} must lie inside project_root {config.project_root}"
        ) from None


_INTERPRETER_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*")


def _interpreter_version(variant: Optional[VariantConfig], why: str) -> str:
    """The variant's interpreter version: ``build_arg`` is ``3.11`` or ``PYTHON_VERSION=3.11``."""
    if variant is None:
        raise ValueError(f"{why} needs a variant (the interpreter version comes from the project's variants)")
    if not variant.build_arg:
        raise ValueError(
            f"{why} needs the interpreter version in variant {variant.name!r} build_arg (e.g. \"3.11\")"
        )
    key, separator, value = variant.build_arg.partition("=")
    version = value if separator and key == "PYTHON_VERSION" else variant.build_arg
    if not _INTERPRETER_VERSION.fullmatch(version):
        raise ValueError(
            f"variant {variant.name!r} build_arg {variant.build_arg!r} is not an interpreter "
            "version (use \"3.11\" or \"PYTHON_VERSION=3.11\")"
        )
    return version


def vendor_dependencies(
    config: BundleConfig, variant: Optional[VariantConfig], runner: Runner,
    environ: Mapping[str, str],
) -> None:
    """``pip download --only-binary=:all:`` the closure for the variant's ABI into ``vendor.dest``."""
    if config.vendor is None:
        return
    version = _interpreter_version(variant, "[vendor]")
    prefix = _resolved_prefix(config.exec_prefix, environ)
    prefixed = bool(prefix)
    dest = config.bundle_dir / config.vendor.dest
    dest.mkdir(parents=True, exist_ok=True)
    command = [
        *prefix, config.wheel_python_bin, "-m", "pip", "download",
        "--only-binary=:all:", "--python-version", version,
    ]
    for platform in config.vendor.platforms:
        command.extend(["--platform", platform])
    if config.wheel_find_links is not None:
        # In a runner the wheelhouse must be a path the runner sees: only a location
        # inside project_root is mapped (project-root-relative); anything else is refused.
        command.extend(["--no-index", "--find-links",
                        _runner_path(config, config.wheel_find_links, prefixed)])
    command.extend(["--dest", _runner_path(config, dest, prefixed)])
    for requirement in config.vendor.requirements:
        command.extend(["-r", _runner_path(config, resolve_path(config.project_root, requirement), prefixed)])
    log_info(f"Vendoring dependencies for python {version}")
    runner(command, config.project_root)


def run_stage(
    config: BundleConfig, variant: Optional[VariantConfig], runner: Runner,
    environ: Mapping[str, str],
) -> None:
    """Run the ``[stage]`` argv once, after copy and vendor, before the manifest."""
    if config.stage is None:
        return
    prefix = _resolved_prefix(config.exec_prefix, environ)
    prefixed = bool(prefix)
    values = {"bundle_dir": _runner_path(config, config.bundle_dir, prefixed)}
    used = set()
    for item in config.stage.argv:
        used |= _tokens_of(item, _STAGE_TOKENS, "stage.argv")
    if "variant" in used:
        if variant is None:
            raise ValueError("stage.argv uses {variant} but no variant is being built")
        values["variant"] = variant.name
    if "python_version" in used:
        values["python_version"] = _interpreter_version(variant, "stage.argv {python_version}")
    command = [*prefix, *(item.format(**values) for item in config.stage.argv)]
    log_info("Running stage step")
    runner(command, config.project_root if prefixed else config.bundle_dir)


def _validate_final_bundle_tree(config: BundleConfig) -> None:
    """Refuse unkept secret-class files and filesystem entries the bundle cannot safely copy."""
    for keep_path in config.archive_keep:
        kept = config.bundle_dir / keep_path
        if kept.is_symlink() or not kept.is_file():
            raise ValueError(
                f"[archive].keep path {keep_path!r} must exist as a regular file in the bundle tree"
            )

    blocked: list[str] = []
    for path in config.bundle_dir.rglob("*"):
        rel = path.relative_to(config.bundle_dir).as_posix()
        if _is_excluded(rel, config.archive_keep):
            # Cache and private-name exclusions remain silent filters. Suffix
            # exclusions that a stage step leaves behind fail closed instead.
            if (
                not path.is_dir()
                and any(path.name.endswith(suffix) for suffix in _KEEPABLE_EXCLUDE_SUFFIXES)
                and not _has_hard_excluded_name(rel)
                and path.name != "pyproject.toml"
            ):
                blocked.append(rel)
            continue
        if path.is_symlink():
            raise ValueError(f"bundle path {rel!r} is a symlink; refusing to archive its target")
        if not path.is_dir() and not path.is_file():
            raise ValueError(f"bundle path {rel!r} is not a regular file or directory")

    if blocked:
        paths = ", ".join(sorted(blocked))
        raise ValueError(
            "the archive hard-excludes unkept bundle files; add exact "
            f"paths to [archive].keep or remove them: {paths}"
        )


def write_bundle_manifest(config: BundleConfig, *, project: str, tag: str) -> Path:
    """Write the installer manifest over the FINAL bundle tree (after vendor and stage)."""
    from cmru.manifest import bundle_tag_problem, build_bundle_manifest, write_manifest

    assert config.manifest_name is not None
    problem = bundle_tag_problem(tag)
    if problem:
        raise ValueError(problem)
    _validate_final_bundle_tree(config)
    manifest = build_bundle_manifest(
        project=project, tag=tag, bundle_root=config.bundle_dir,
        exclude=(config.manifest_name, config.manifest_name + ".minisig"),
        exclude_path=lambda rel: _is_excluded(rel, config.archive_keep),
    )
    return write_manifest(manifest, config.bundle_dir / config.manifest_name)


def _preflight_bundle_steps(
    config: BundleConfig,
    variant: Optional[VariantConfig],
    *,
    manifest_project: Optional[str],
    manifest_tag: Optional[str],
    environ: Mapping[str, str],
) -> None:
    """Refuse invalid step inputs before a build can remove an existing archive."""
    prefix: list[str] = []
    if config.vendor is not None or config.stage is not None:
        prefix = _resolved_prefix(config.exec_prefix, environ)
    prefixed = bool(prefix)

    if config.vendor is not None:
        _interpreter_version(variant, "[vendor]")
        _runner_path(config, config.bundle_dir / config.vendor.dest, prefixed)
        for requirement in config.vendor.requirements:
            _runner_path(config, resolve_path(config.project_root, requirement), prefixed)
        if config.wheel_find_links is not None:
            _runner_path(config, config.wheel_find_links, prefixed)

    if config.stage is not None:
        # run_stage constructs this value even when the command does not reference it.
        _runner_path(config, config.bundle_dir, prefixed)
        used: set[str] = set()
        for item in config.stage.argv:
            used |= _tokens_of(item, _STAGE_TOKENS, "stage.argv")
        if "variant" in used and variant is None:
            raise ValueError("stage.argv uses {variant} but no variant is being built")
        if "python_version" in used:
            _interpreter_version(variant, "stage.argv {python_version}")

    if config.manifest_name is not None:
        if not (manifest_project and manifest_tag):
            raise ValueError("[manifest] needs manifest_project and manifest_tag")
        from cmru.manifest import bundle_tag_problem

        problem = bundle_tag_problem(manifest_tag)
        if problem:
            raise ValueError(problem)


def run_bundle(
    config_path: Path,
    *,
    variant: Optional[VariantConfig] = None,
    manifest_project: Optional[str] = None,
    manifest_tag: Optional[str] = None,
    version_fallback: Optional[str] = None,
    runner: Optional[Runner] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """Build one archive. Order: clean, wheel, copy, vendor, stage, manifest, archive.

    ``variant`` is the project's declared ``VariantConfig``: its ``build_arg`` is the
    interpreter version for ``[vendor]``/``[stage]`` and its name fills ``{variant}`` in
    ``archive.name_template``. With a variant only ``bundle_dir``/``client_dir`` are
    cleaned, so the archives of sibling variants in ``dist_dir`` survive.
    """
    config = parse_config(config_path)
    environment = os.environ if environ is None else environ
    run = _default_runner if runner is None else runner
    has_variant_field = "{variant}" in config.archive_template
    if variant is not None and not has_variant_field:
        raise ValueError("archive.name_template needs a {variant} field to build a variant")
    if variant is None and has_variant_field:
        raise ValueError("archive.name_template has {variant} but no variant is being built")
    if config.manifest_name is not None and not (manifest_project and manifest_tag):
        raise ValueError("[manifest] needs manifest_project and manifest_tag")

    own_archive = archive_path(
        config,
        variant.name if variant is not None else None,
        version_fallback=version_fallback,
        environ=environment,
    )
    _preflight_bundle_steps(
        config, variant, manifest_project=manifest_project, manifest_tag=manifest_tag,
        environ=environment,
    )

    log_info("Preparing dist directories")
    if variant is None:
        if config.dist_dir.exists():
            shutil.rmtree(config.dist_dir)
    else:
        for stale in (config.bundle_dir, config.client_dir):
            if stale.exists():
                shutil.rmtree(stale)
        own_archive.unlink(missing_ok=True)
    config.bundle_dir.mkdir(parents=True, exist_ok=True)

    build_wheel(config)
    copy_sources(config)
    vendor_dependencies(config, variant, run, environment)
    run_stage(config, variant, run, environment)
    _validate_final_bundle_tree(config)
    if config.manifest_name is not None:
        write_bundle_manifest(config, project=manifest_project, tag=manifest_tag)
    archive = create_archive(
        config,
        variant.name if variant is not None else None,
        version_fallback=version_fallback,
        environ=environment,
    )
    if not config.keep_tree:
        shutil.rmtree(config.bundle_dir)
    return archive


if __name__ == "__main__":
    raise SystemExit(
        "cmru.bundle is a Python library, not a command; call run_bundle(config_path)."
    )
