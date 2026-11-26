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
# The gate result directory override of `remote-gate.sh:225` (`${CONTROLLER_REMOTE_OUT:-...}`).
REMOTE_OUT_VAR = "CONTROLLER_REMOTE_OUT"
TOML_NAME = "agctl.toml"
REPO_STATE_DIR = ".controller-state"
# The gate lock directory of `remote-gate.sh:184` (`$CONTROLLER_HOME/remote-gate-locks`).
GATE_LOCK_DIR = "remote-gate-locks"
# The gate results directory of `remote-gate.sh:225`.
GATE_OUT_DIR = "remote-gates"
# The legacy flat-file runs directories of DESIGN section 4, read-only inputs for the
# one-release window. The registry of slice 2 writes only under its own runs dir.
LEGACY_RUNS_DIRS = ("c5-runs",)
# The post-migration peer state home of DESIGN section 3 (`~/controller-state/<name>/`).
PEERS_DIRNAME = "controller-state"


class ConfigError(Exception):
    """A configuration file or value that cannot be read as declared (usage, exit 2).

    It carries `exit_code = 2` explicitly: cli-extended reads `getattr(exc, "exit_code", 1)`, so
    without this the documented exit code (README "exit 2", DESIGN section 4) would silently be
    1, and a script testing for a configuration refusal would see a runtime failure instead.
    """

    exit_code = 2


@dataclass(frozen=True)
class Config:
    """The resolved read inputs, each one attributable."""

    home: Path
    runs_dirs: tuple[Path, ...]
    state_dirs: tuple[Path, ...]
    repo_state_dir: Path | None
    peers_dir: Path
    gate_lock_dir: Path
    remote_gate_out: Path
    toml_path: Path
    toml_state: str  # "absent" | "loaded"
    toml_legacy_dirs: tuple[Path, ...]
    # The repository the caller DECLARED (`CONTROLLER_REPO`), or None. The git reader takes this
    # first and the caller's cwd second, never the controller home: a home that happens to be a
    # checkout would otherwise silently describe a different repository than the one the peer
    # paths come from.
    repo: str | None = None


def expand(value: str, base: Path) -> Path:
    """``~`` expands; a relative value resolves against ``base`` (lib.sh's own rule)."""

    text = value.strip()
    if not text:
        raise ConfigError("a path value names no directory (empty string)")
    path = Path(text).expanduser()
    resolved = path if path.is_absolute() else base / path
    # A `..` in a path is resolved here, once, so every consumer compares the same spelling
    # (`a/../b` and `b` must not look like two directories in `runs_dirs` or in a report).
    return Path(os.path.normpath(resolved))


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
    unknown_top = sorted(set(document) - {"runs"})
    if unknown_top:
        raise ConfigError(f"{path}: top level names unknown key(s): {', '.join(unknown_top)}")
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
    repo = environment.get(REPO_VAR) or None
    if repo:
        repo_state = expand(repo, base) / REPO_STATE_DIR
    peers = home.parent / PEERS_DIRNAME
    configured_out = environment.get(REMOTE_OUT_VAR)
    remote_gate_out = expand(configured_out, base) if configured_out else home / GATE_OUT_DIR

    return Config(
        home=home,
        runs_dirs=_dedupe([*runs, *legacy]),
        state_dirs=_dedupe([path for path in (repo_state, peers) if path is not None]),
        repo_state_dir=repo_state,
        peers_dir=peers,
        gate_lock_dir=home / GATE_LOCK_DIR,
        remote_gate_out=remote_gate_out,
        toml_path=toml_path,
        toml_state=toml_state,
        toml_legacy_dirs=legacy,
        repo=repo,
    )
