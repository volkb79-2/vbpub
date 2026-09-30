#!/usr/bin/env python3
"""mdt devcontainer host bootstrap ("get.py").

Runs ON THE HOST (wired via devcontainer.json `initializeCommand`) BEFORE the
container is created, so every `$HOME` bind-mount source is prepared with sane
permissions. Docker's `--mount` form refuses a missing source; this script
applies MDT's configured policy before Docker starts.

Layout (grouped persistence):
- Devcontainer-persisted state is grouped under `~/mdt--mounted-folders/` so a rebuild never
  wipes it and one `ls -la ~/mdt--mounted-folders/` shows the whole set. These are REAL dirs
  (NOT symlinks): `.claude .claudelink .codex .codex2 .config .gnupg .local .minisign .openclaw`
  `.pi .reasonix .ssh` plus `opencode-data` and `tmp`.
- `tmp` is the host-backed persisted `/tmp`: a REAL dir at mode 1777, so `/tmp` worktrees survive
  rebuilds and are visible to the sibling test-runner container (which bind-mounts the same host path).
- EXCEPTION: the host's NATIVE `~/.ssh` is also bind-mounted (readonly) at `/home/vscode/.ssh-host`,
  so the same host keys work both natively and inside the devcontainer (dual-use). That source is the
  host `~/.ssh`, not the grouped copy.

Design: stdlib-only and idempotent. Reads the sibling `devcontainer.json` and inspects each `type=bind`
source under the host `$HOME`. Existing regular files and directories are handled according to their
actual type, regardless of their names or suffixes. For a missing source, the host config policy
defaults to `create-by-spelling`: a source ending in `/` creates a directory; a source without `/`
creates an empty regular file. Set `DEVCONTAINER_MISSING_BIND_SOURCE_POLICY=fail` in the host config
to require every `$HOME` source managed here to exist already. Secret dirs
(`.ssh`/`.gnupg`/`.minisign`) get 0700; `tmp` gets 1777; everything else 0755.

NOTE on data migration: this script only ENSURES the source paths EXIST — it does NOT copy your
existing `~/.claude`, `~/.gnupg`, `~/.minisign`, `~/.codex`, `~/.codex2`, `~/.reasonix`, `~/.openclaw`,
`~/.pi`, `~/.claudelink`, `~/.config`, or `~/.local/share/opencode` state into the grouped
parent. If you want that state to carry over, migrate it ONCE on the host before the first
rebuild, e.g.:  for d in .claude .claudelink .codex .config .gnupg .local .minisign .openclaw .pi .reasonix; do cp -a ~/$d/. ~/mdt--mounted-folders/$d/; done
(and copy `~/.local/share/opencode/.` to `~/mdt--mounted-folders/opencode-data/`)
(the grouped `.ssh` is independent of the readonly native `.ssh-host` mount).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Secret dirs whose tools require 0700 (ssh/gpg/minisign reject loose perms).
MODE_OVERRIDES = {".ssh": 0o700, ".gnupg": 0o700, ".minisign": 0o700}
DEFAULT_MODE = 0o755
TMP_MODE = 0o1777  # persisted host-backed /tmp: sticky + world-writable, like a normal /tmp

# Name of the grouped-persistence parent under $HOME.
PARENT_NAME = "mdt--mounted-folders"

# Canonical set under the parent — used only if devcontainer.json can't be read.
FALLBACK = [".claude", ".claudelink", ".codex", ".codex2", ".config", ".gnupg", ".local", ".minisign", ".openclaw", ".pi", ".reasonix", ".ssh", "opencode-data"]
# Standard file-level state mounts.
FALLBACK_FILES = [".claude.json", ".reasonix.toml"]
KNOWN_FILE_SOURCE_BASENAMES = set(FALLBACK_FILES) | {".gitconfig"}
KNOWN_DIRECTORY_SOURCE_NAMES = set(FALLBACK) | {"tmp"}

MISSING_SOURCE_POLICY_KEY = "DEVCONTAINER_MISSING_BIND_SOURCE_POLICY"
DEFAULT_MISSING_SOURCE_POLICY = "create-by-spelling"
MISSING_SOURCE_POLICIES = {DEFAULT_MISSING_SOURCE_POLICY, "fail"}
HOST_CONFIG_PATH = Path("/etc/mdt/host-setup.env")

HOME = Path(os.path.expanduser("~"))
# Matches the devcontainer mount string: "source=...,target=...,type=bind[,...]"
BIND_RE = re.compile(r'source=([^,"]+),target=[^,"]+,type=bind')


def host_bind_sources(dc_path: Path) -> list:
    """Return bind-mount source strings from devcontainer.json (skipping // comment lines)."""
    sources = []
    try:
        for line in dc_path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("//"):
                continue  # commented-out mount
            sources.extend(BIND_RE.findall(line))
    except OSError:
        return []
    return sources


def to_home_dir(source: str):
    """Resolve a mount source to a host $HOME-relative path, or None to skip."""
    s = source.replace("${localEnv:HOME}", str(HOME))
    if s.startswith("~"):
        s = str(HOME) + s[1:]
    p = Path(s)
    try:
        p.relative_to(HOME)
    except ValueError:
        return None  # not under HOME (docker.sock, /etc/letsencrypt, workspace repos)
    return p


def has_directory_marker(source: str) -> bool:
    """Whether the raw mount source ends in `/`, MDT's missing-dir hint."""
    s = source.replace("${localEnv:HOME}", str(HOME))
    if s.startswith("~"):
        s = str(HOME) + s[1:]
    return s.endswith("/")


def _mode_for(p: Path) -> int:
    if p.name == "tmp" and p.parent.name == PARENT_NAME:
        return TMP_MODE
    return MODE_OVERRIDES.get(p.name, DEFAULT_MODE)


def _is_known_directory_source(p: Path) -> bool:
    """Return whether a missing source is one of MDT's directory mounts."""
    if p == HOME / ".ssh":
        return True
    return p.parent == HOME / PARENT_NAME and p.name in KNOWN_DIRECTORY_SOURCE_NAMES


def load_missing_source_policy(config_path: Path | None = None) -> str:
    """Read the optional bootstrap policy without sourcing the shell config.

    A host with no MDT config uses the shipped default. If the config exists but
    this newly introduced key is absent, the shipped default also applies until
    the operator runs the wizard. Duplicate or invalid declared values fail closed.
    """
    config_path = config_path or HOST_CONFIG_PATH
    try:
        config_path.lstat()
    except FileNotFoundError:
        return DEFAULT_MISSING_SOURCE_POLICY
    except OSError as exc:
        raise ValueError(f"could not inspect host config {config_path}: {exc}") from exc
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"could not read host config {config_path}: {exc}") from exc

    declared: list[str] = []
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw_value = stripped.partition("=")
        if key.strip() != MISSING_SOURCE_POLICY_KEY:
            continue
        if not separator:
            raise ValueError(
                f"invalid {MISSING_SOURCE_POLICY_KEY} assignment on line {line_number} of {config_path}"
            )
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '\"'):
            value = value[1:-1]
        declared.append(value)

    if len(declared) > 1:
        raise ValueError(f"duplicate {MISSING_SOURCE_POLICY_KEY} in {config_path}")
    policy = declared[0] if declared else DEFAULT_MISSING_SOURCE_POLICY
    if policy not in MISSING_SOURCE_POLICIES:
        choices = " or ".join(sorted(MISSING_SOURCE_POLICIES))
        raise ValueError(
            f"{MISSING_SOURCE_POLICY_KEY} in {config_path} must be {choices}; found {policy!r}"
        )
    return policy


def ensure(
    p: Path,
    *,
    expected_type: str | None = None,
    missing_policy: str = DEFAULT_MISSING_SOURCE_POLICY,
) -> bool:
    """Accept existing sources by actual type and prepare missing ones by policy.

    For missing paths, `expected_type` comes from the raw source spelling: a
    trailing `/` means directory and no trailing `/` means file. Existing
    custom sources are classified from the host filesystem instead.
    """
    if expected_type not in (None, "file", "directory"):
        raise ValueError(f"unsupported bind-source type {expected_type!r}")
    if missing_policy not in MISSING_SOURCE_POLICIES:
        raise ValueError(f"unsupported missing bind-source policy {missing_policy!r}")

    expects_directory = expected_type == "directory"
    if expected_type == "directory" and p.name in KNOWN_FILE_SOURCE_BASENAMES:
        print(
            f"[mdt-bootstrap] ERROR {p.name} is a file-level mount source and cannot use the "
            f"directory `/` marker: {p}. No path was created.",
            file=sys.stderr,
        )
        return False
    if p.is_file():
        if expects_directory:
            print(
                f"[mdt-bootstrap] ERROR expected a directory bind source at {p}; found a file. "
                "No path was changed. Correct the mount source and intended type.",
                file=sys.stderr,
            )
            return False
        print(f"[mdt-bootstrap] exists  {p} (file)")
        return True
    if p.is_dir():
        if p.name in KNOWN_FILE_SOURCE_BASENAMES:
            print(
                f"[mdt-bootstrap] ERROR expected a file bind source at {p}; found a directory. "
                "No path was changed. Inspect the directory, then provide the intended regular "
                "file or remove the file mount from devcontainer.json.",
                file=sys.stderr,
            )
            return False
        if expects_directory:
            target = p
        else:
            print(f"[mdt-bootstrap] exists  {p} (dir; existing host type accepted)")
            return True
    elif p.exists():
        # Sockets and other special sources are valid bind mounts too. They already
        # have a concrete type, so leave them unchanged.
        if expects_directory:
            print(
                f"[mdt-bootstrap] ERROR expected a directory bind source at {p}; found a special "
                "filesystem object. No path was changed.",
                file=sys.stderr,
            )
            return False
        if p.name in KNOWN_FILE_SOURCE_BASENAMES:
            print(
                f"[mdt-bootstrap] ERROR expected a regular file bind source at {p}; found a special "
                "filesystem object. No path was changed.",
                file=sys.stderr,
            )
            return False
        print(f"[mdt-bootstrap] exists  {p} (special source; unchanged)")
        return True
    else:
        if missing_policy == "fail":
            print(
                f"[mdt-bootstrap] ERROR missing bind source {p}; policy is `fail`, so no path was "
                "created. Create the intended host source or choose "
                "`create-by-spelling` in the MDT host-setup wizard.",
                file=sys.stderr,
            )
            return False
        created_type = expected_type or (
            "directory" if _is_known_directory_source(p) else "file"
        )
        if created_type == "file":
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
            except FileExistsError:
                if p.is_file():
                    print(f"[mdt-bootstrap] exists  {p} (file)")
                    return True
                print(
                    f"[mdt-bootstrap] ERROR expected a regular file bind source at {p}; "
                    "another filesystem object appeared. No path was changed.",
                    file=sys.stderr,
                )
                return False
            except OSError as exc:
                print(f"[mdt-bootstrap] ERROR could not prepare {p}: {exc}", file=sys.stderr)
                return False
            print(f"[mdt-bootstrap] created {p} (mode 0o600) (empty file)")
            return True
        target = p

    mode = _mode_for(p)
    is_tmp = mode == TMP_MODE
    try:
        if target.exists():
            # Re-assert 1777 on tmp every run (worktree tooling + other users rely on it); leave
            # other dirs' modes alone so we never fight perms the user set deliberately.
            if is_tmp:
                try:
                    os.chmod(target, mode)
                except OSError:
                    pass
            print(f"[mdt-bootstrap] exists  {target} (dir)")
            return True
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target, mode)
        except OSError:
            pass
        print(f"[mdt-bootstrap] created {target} (mode {oct(mode)}) (dir)")
    except OSError as exc:
        print(f"[mdt-bootstrap] ERROR could not prepare {target}: {exc}", file=sys.stderr)
        return False
    return True


def fallback_bind_paths() -> list[tuple[Path, str]]:
    """Return typed canonical sources when the sibling template is unavailable."""
    parent = HOME / PARENT_NAME
    return (
        [(HOME / ".ssh", "directory")]
        + [(parent / name, "directory") for name in FALLBACK]
        + [(parent / "tmp", "directory")]
        + [(parent / name, "file") for name in FALLBACK_FILES]
    )


def main() -> int:
    try:
        missing_policy = load_missing_source_policy(HOST_CONFIG_PATH)
    except ValueError as exc:
        print(f"[mdt-bootstrap] ERROR {exc}", file=sys.stderr)
        return 1
    print(f"[mdt-bootstrap] missing bind-source policy: {missing_policy}")

    dc = Path(__file__).resolve().parent / "devcontainer.json"
    requested: dict[Path, str] = {}
    conflicted: set[Path] = set()
    failed = False
    found_home_source = False
    sources = host_bind_sources(dc)
    for source in sources:
        path = to_home_dir(source)
        if path is not None:
            found_home_source = True
            source_type = "directory" if has_directory_marker(source) else "file"
            previous_type = requested.get(path)
            if previous_type is not None and previous_type != source_type:
                print(
                    f"[mdt-bootstrap] ERROR conflicting file/directory source spellings for {path}; "
                    "no source was prepared.",
                    file=sys.stderr,
                )
                conflicted.add(path)
                requested.pop(path, None)
                failed = True
            elif path not in conflicted:
                requested[path] = source_type
    if not found_home_source:
        print("[mdt-bootstrap] no parseable $HOME bind mounts; using fallback set", file=sys.stderr)
        requested = dict(fallback_bind_paths())
    for p, expected_type in requested.items():
        result = ensure(
            p,
            expected_type=expected_type,
            missing_policy=missing_policy,
        )
        if result is False:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
