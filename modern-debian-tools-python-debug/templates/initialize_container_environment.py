#!/usr/bin/env python3
"""mdt devcontainer host bootstrap ("get.py").

Runs ON THE HOST (wired via devcontainer.json `initializeCommand`) BEFORE the
container is created, so every bind-mount *source* directory exists with sane
permissions. A missing bind source makes Docker either fail to start the
container or silently create the path as **root** — after which the in-container
`vscode` user cannot write to its own `~/.codex`, `~/.minisign`, etc.

Layout (grouped persistence):
- Devcontainer-persisted state is grouped under `~/mdt--mounted-folders/` so a rebuild never
  wipes it and one `ls -la ~/mdt--mounted-folders/` shows the whole set. These are REAL dirs
  (NOT symlinks): `.ssh .claude .codex .reasonix .openclaw .config .minisign .gnupg` (+ `tmp`).
- `tmp` is the host-backed persisted `/tmp`: a REAL dir at mode 1777, so `/tmp` worktrees survive
  rebuilds and are visible to the sibling test-runner container (which bind-mounts the same host path).
- EXCEPTION: the host's NATIVE `~/.ssh` is also bind-mounted (readonly) at `/home/vscode/.ssh-host`,
  so the same host keys work both natively and inside the devcontainer (dual-use). That source is the
  host `~/.ssh`, not the grouped copy.

Design: stdlib-only, idempotent, best-effort (never blocks container start: always exits 0). Reads the
sibling `devcontainer.json`, finds every `type=bind` mount whose source is under the host `$HOME`, and
`mkdir -p`s it with the right mode — so the dir list stays DRY with the mounts. Secret dirs
(`.ssh`/`.gnupg`/`.minisign`) get 0700; `tmp` gets 1777; everything else 0755.

NOTE on data migration: this script only ENSURES the source dirs EXIST — it does NOT copy your
existing `~/.claude` / `~/.gnupg` / `~/.minisign` / `~/.codex` / `~/.reasonix` / `~/.openclaw`
/ `~/.config` state into the grouped parent. If you want that state to carry over, migrate it ONCE
on the host before the first rebuild, e.g.:  for d in .claude .codex .reasonix .openclaw .config .minisign .gnupg; do cp -a ~/$d/. ~/mdt--mounted-folders/$d/; done
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
FALLBACK = [".ssh", ".claude", ".codex", ".reasonix", ".openclaw", ".config", ".minisign", ".gnupg"]

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


def _mode_for(p: Path) -> int:
    if p.name == "tmp" and p.parent.name == PARENT_NAME:
        return TMP_MODE
    return MODE_OVERRIDES.get(p.name, DEFAULT_MODE)


def ensure(p: Path) -> None:
    """Create a $HOME bind-source DIR (real dir) with the right mode. Idempotent, best-effort."""
    mode = _mode_for(p)
    is_tmp = mode == TMP_MODE
    try:
        if p.exists():
            # Re-assert 1777 on tmp every run (worktree tooling + other users rely on it); leave
            # other dirs' modes alone so we never fight perms the user set deliberately.
            if is_tmp:
                try:
                    os.chmod(p, mode)
                except OSError:
                    pass
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
        parent = HOME / PARENT_NAME
        dirs = [HOME / ".ssh"] + [parent / name for name in FALLBACK] + [parent / "tmp"]
    seen = set()
    for p in dirs:
        if p in seen:
            continue
        seen.add(p)
        ensure(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
