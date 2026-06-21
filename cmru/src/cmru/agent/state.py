"""State-dir paths, observed.json read/write, file locking.

State dir layout (spec §6):
  system scope: /var/lib/cmru-agent/
  user scope:   $XDG_STATE_HOME/cmru-agent/  (default: ~/.local/state/cmru-agent/)

Files:
  node_id              — plain text, the enrolled node ID
  identity.json        — NodeIdentity JSON (token_path reference, pubkey)
  observed.json        — current ObservedState JSON
  current_generation   — plain text, last successfully applied generation number
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from cmru.agent.protocol import ObservedState


_SYSTEM_STATE_DIR = Path("/var/lib/cmru-agent")


def state_dir(scope: str = "user") -> Path:
    """Return the state directory for the given scope ('system' or 'user')."""
    if scope == "system":
        return _SYSTEM_STATE_DIR
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "state"
    return base / "cmru-agent"


def ensure_state_dir(scope: str = "user") -> Path:
    d = state_dir(scope)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# node_id
# ---------------------------------------------------------------------------

def read_node_id(scope: str = "user") -> Optional[str]:
    p = state_dir(scope) / "node_id"
    if not p.exists():
        return None
    return p.read_text().strip() or None


def write_node_id(node_id: str, scope: str = "user") -> None:
    d = ensure_state_dir(scope)
    (d / "node_id").write_text(node_id + "\n")


# ---------------------------------------------------------------------------
# identity.json
# ---------------------------------------------------------------------------

def read_identity(scope: str = "user") -> Optional[dict]:
    p = state_dir(scope) / "identity.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_identity(identity_data: dict, scope: str = "user") -> None:
    d = ensure_state_dir(scope)
    (d / "identity.json").write_text(json.dumps(identity_data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# observed.json
# ---------------------------------------------------------------------------

def read_observed(scope: str = "user") -> Optional[ObservedState]:
    p = state_dir(scope) / "observed.json"
    if not p.exists():
        return None
    try:
        return ObservedState.from_json(p.read_text())
    except (json.JSONDecodeError, TypeError, OSError):
        return None


def write_observed(observed: ObservedState, scope: str = "user") -> None:
    d = ensure_state_dir(scope)
    p = d / "observed.json"
    # Write atomically via a tmp file
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(observed.to_json())
    tmp.replace(p)


# ---------------------------------------------------------------------------
# current_generation
# ---------------------------------------------------------------------------

def read_current_generation(scope: str = "user") -> Optional[int]:
    p = state_dir(scope) / "current_generation"
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except ValueError:
        return None


def write_current_generation(generation: int, scope: str = "user") -> None:
    d = ensure_state_dir(scope)
    (d / "current_generation").write_text(str(generation) + "\n")


# ---------------------------------------------------------------------------
# File lock (prevents concurrent agent instances)
# ---------------------------------------------------------------------------

@contextmanager
def exclusive_agent_lock(scope: str = "user"):
    """Acquire an exclusive flock on the state dir lockfile.
    Raises RuntimeError if already locked (another instance running).
    """
    d = ensure_state_dir(scope)
    lock_path = d / "agent.lock"
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another cmru-agent instance is running (cannot acquire lock)"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
