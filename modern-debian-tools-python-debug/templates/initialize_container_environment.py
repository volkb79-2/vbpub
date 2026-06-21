#!/usr/bin/env python3
"""mdt devcontainer host bootstrap ("initialize_container_environment.py").

Runs ON THE HOST (wired via devcontainer.json `initializeCommand`) BEFORE the
container is created, so every bind-mount *source* directory exists with sane
permissions. A missing bind source makes Docker either fail to start the
container or silently create the path as **root** — after which the in-container
`vscode` user cannot write to its own `~/.codex`, `~/.minisign`, etc.

Design:
- stdlib-only, idempotent, best-effort (never blocks container start: always exits 0).
- Reads the sibling `devcontainer.json`, finds every `type=bind` mount whose source
  is under the host `$HOME`, and `mkdir -p`s it — so the dir list stays DRY with the
  mounts (add a mount, its dir is auto-created next start). Falls back to a canonical
  agent-state set if the file can't be parsed.
- Applies tight modes (0700) to secret dirs (`.ssh`/`.gnupg`/`.minisign`) that their
  tools refuse to use when world/group-readable.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Secret dirs whose tools require 0700 (ssh/gpg/minisign reject loose perms).
MODE_OVERRIDES = {".ssh": 0o700, ".gnupg": 0o700, ".minisign": 0o700}
DEFAULT_MODE = 0o755

# Canonical agent/tool state set — used only if the devcontainer.json can't be read.
FALLBACK = [".claude", ".codex", ".config", ".minisign", ".gnupg"]

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
    """Resolve a mount source to a host $HOME-relative DIRECTORY path, or None to skip."""
    s = source.replace("${localEnv:HOME}", str(HOME))
    if s.startswith("~"):
        s = str(HOME) + s[1:]
    p = Path(s)
    try:
        p.relative_to(HOME)
    except ValueError:
        return None  # not under HOME (docker.sock, /etc/letsencrypt, workspace repos)
    if p.suffix:
        return None  # has a file extension (e.g. .conf.yml, .sock) -> a file mount, not a state dir
    return p


def ensure(p: Path) -> None:
    mode = MODE_OVERRIDES.get(p.name, DEFAULT_MODE)
    try:
        if p.exists():
            print(f"[mdt-bootstrap] exists  {p}")
            return
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p, mode)
        except OSError:
            pass
        print(f"[mdt-bootstrap] created {p} (mode {oct(mode)})")
    except OSError as exc:
        # Best-effort: warn but never block container start.
        print(f"[mdt-bootstrap] WARN could not create {p}: {exc}", file=sys.stderr)


def main() -> int:
    dc = Path(__file__).resolve().parent / "devcontainer.json"
    dirs = [d for d in (to_home_dir(s) for s in host_bind_sources(dc)) if d is not None]
    if not dirs:
        print("[mdt-bootstrap] no parseable $HOME bind mounts; using fallback set", file=sys.stderr)
        dirs = [HOME / name for name in FALLBACK]
    seen = set()
    for p in dirs:
        if p in seen:
            continue
        seen.add(p)
        ensure(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
