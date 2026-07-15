"""XDG state-directory layout (ARCHITECTURE §1). FROZEN CORE.

Everything runtime lives under the state root; nothing here is ever committed
to a consumer repository. `HANDOFFCTL_STATE` overrides the root (tests use
this via the `tmp_state` fixture in conftest.py).
"""

from __future__ import annotations

import os
from pathlib import Path


def state_root() -> Path:
    env = os.environ.get("HANDOFFCTL_STATE")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(xdg) / "handoffctl"


def registry_path() -> Path:
    return state_root() / "registry.toml"


def routes_path() -> Path:
    return state_root() / "routes.toml"


def prices_path() -> Path:
    return state_root() / "prices.toml"


def leases_dir() -> Path:
    return state_root() / "leases"


def www_dir() -> Path:
    return state_root() / "www"


def daemon_dir() -> Path:
    return state_root() / "daemon"          # pidfile, http port file


def project_dir(project: str) -> Path:
    return state_root() / "projects" / project


def events_path(project: str) -> Path:
    return project_dir(project) / "events.jsonl"


def state_dir(project: str) -> Path:
    return project_dir(project) / "state"


def statefile_path(project: str, task_id: str) -> Path:
    return state_dir(project) / f"{task_id}.json"


def attempts_dir(project: str) -> Path:
    return project_dir(project) / "attempts"


def attempt_dir(project: str, attempt_id: str) -> Path:
    return attempts_dir(project) / attempt_id


def pause_flag(project: str, task_id: str | None = None) -> Path:
    """Project-wide pause when task_id is None, else per-task."""
    if task_id is None:
        return project_dir(project) / "pause"
    return project_dir(project) / f"pause.{task_id}"


def ensure_layout(project: str | None = None) -> None:
    """Create the directory skeleton (idempotent)."""
    for d in (state_root(), leases_dir(), www_dir(), daemon_dir()):
        d.mkdir(parents=True, exist_ok=True)
    if project is not None:
        for d in (project_dir(project), state_dir(project), attempts_dir(project)):
            d.mkdir(parents=True, exist_ok=True)
