"""Where `agctl estate` reads from — resolved once, named per field, never guessed.

Design references: DESIGN section 3 (``CONTROLLER_NAME``, ``$CONTROLLER_HOME``), section 4
(run registry and its one-release legacy window: default ``CONTROLLER_RUNS_DIR``,
``$CONTROLLER_HOME/runs`` and ``~/c5-runs``) and section 4a (peer state dirs).

Rules this module keeps:

* Every path comes from the environment or from ``agctl.toml``; nothing in this package
  hard-codes a checkout, a runs directory or a state directory.
* An UNSET variable resolves to the documented default of section 3/4 — a policy choice that
  is correct in the absence of information. A variable that is SET and names a nonexistent
  directory is not a fallback case: the reader reports that source as missing instead.
* A MALFORMED ``agctl.toml`` refuses (exit 2), naming the file and the key. Ignoring it
  silently would hide a ``legacy_dirs`` set the caller relies on; that is a masked default.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

HOME_VAR = "CONTROLLER_HOME"
RUNS_VAR = "CONTROLLER_RUNS_DIR"
REPO_VAR = "CONTROLLER_REPO"
TOML_VAR = "AGCTL_TOML"
TOML_NAME = "agctl.toml"
REPO_STATE_DIR = ".controller-state"
# The legacy flat-file runs directories of DESIGN section 4, read-only inputs for the
# one-release window. The registry of slice 2 writes only under its own runs dir.
LEGACY_RUNS_DIRS = ("c5-runs",)
# The post-migration peer state home of DESIGN section 3 (`~/controller-state/<name>/`).
PEERS_DIRNAME = "controller-state"


class ConfigError(Exception):
    """A configuration file or value that cannot be read as declared (usage, exit 2)."""


@dataclass(frozen=True)
class Config:
    """The resolved read inputs, each one attributable."""

    home: Path
    runs_dirs: tuple[Path, ...]
    state_dirs: tuple[Path, ...]
    repo_state_dir: Path | None
    peers_dir: Path
    toml_path: Path
    toml_state: str  # "absent" | "loaded"
    toml_legacy_dirs: tuple[Path, ...]


def expand(value: str, base: Path) -> Path:
    """``~`` expands; a relative value resolves against ``base`` (lib.sh's own rule)."""

    text = value.strip()
    if not text:
        raise ConfigError("a path value names no directory (empty string)")
    path = Path(text).expanduser()
    return path if path.is_absolute() else base / path


def _dedupe(paths: list[Path]) -> tuple[Path, ...]:
    seen: dict[str, None] = {}
    for path in paths:
        seen.setdefault(str(path), None)
    return tuple(Path(text) for text in seen)


def _read_toml(path: Path) -> tuple[Path, ...]:
    """The ``[runs].legacy_dirs`` list of an existing ``agctl.toml``; ``()`` when absent."""

    if not path.is_file():
        return ()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: cannot be read as TOML ({error})") from None
    runs = document.get("runs")
    if runs is None:
        return ()
    if not isinstance(runs, dict):
        raise ConfigError(f"{path}: [runs] must be a table")
    unknown = sorted(set(runs) - {"legacy_dirs"})
    if unknown:
        raise ConfigError(f"{path}: [runs] names unknown key(s): {', '.join(unknown)}")
    declared = runs.get("legacy_dirs", ())
    if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
        raise ConfigError(f"{path}: [runs].legacy_dirs must be a list of path strings")
    return tuple(expand(item, path.parent) for item in declared)


def load(env: Mapping[str, str] | None = None, *, cwd: Path | None = None) -> Config:
    """Resolve the read inputs from ``env`` (default ``os.environ``) and the caller's cwd."""

    environment = os.environ if env is None else env
    base = Path.cwd() if cwd is None else Path(cwd)

    home = expand(environment.get(HOME_VAR) or "~/controller", base)

    runs: list[Path] = []
    configured = environment.get(RUNS_VAR)
    if configured:
        runs.append(expand(configured, base))
    runs.append(home / "runs")
    runs.extend(Path("~").expanduser() / name for name in LEGACY_RUNS_DIRS)

    toml_path = home / TOML_NAME
    explicit = environment.get(TOML_VAR)
    if explicit:
        toml_path = expand(explicit, base)
        if not toml_path.is_file():
            # The caller named a file that has no other source: absence is a configuration
            # error, never silently "no settings".
            raise ConfigError(f"{TOML_VAR}={explicit}: file does not exist")
    legacy = _read_toml(toml_path)
    toml_state = "loaded" if toml_path.is_file() else "absent"

    repo_state: Path | None = None
    if environment.get(REPO_VAR):
        repo_state = expand(environment[REPO_VAR], base) / REPO_STATE_DIR
    peers = home.parent / PEERS_DIRNAME

    return Config(
        home=home,
        runs_dirs=_dedupe([*runs, *legacy]),
        state_dirs=_dedupe([path for path in (repo_state, peers) if path is not None]),
        repo_state_dir=repo_state,
        peers_dir=peers,
        toml_path=toml_path,
        toml_state=toml_state,
        toml_legacy_dirs=legacy,
    )
