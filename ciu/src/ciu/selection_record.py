"""CIU-205 — the opt-in per-checkout selection record (v7 shape of SPEC-V8 S9.4).

A project that derives a deployment type from its profile selection (S3.12:
``'public' in ciu.selected_profiles``) needs that choice to stay stable across
invocations: re-rendering a ``public`` instance as ``internal`` drops its edge
labels while the data its first boot initialised keeps the public settings.

Opt-in, committed: ``[ciu.selection_record] profiles = ["internal", "public"]``
names the GUARDED profiles. The first deploying invocation records which of
them its resolved selection contains (possibly none) in
``<ciu-root>/.ciu/selection-record.json``; every later deploying invocation
whose selection contains a different subset of the guarded names refuses,
naming both sets and the remedy. Unguarded profiles (tools, mocks) never
conflict. Without the key nothing is read or written.

The record lives under ``.ciu/``, which S1.7 already requires to be
gitignored. It is never written to ``ciu.env``, compose env or the operator's
``ciu.global.instance.toml.j2`` (S3.1b). An unrestricted, successful
``ciu clean`` removes it (it also removes the data the record protects).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

RECORD_RELPATH = Path(".ciu") / "selection-record.json"
RECORD_API = "ciu/selection-record"
RECORD_API_VERSION = 1


def record_path(repo_root: Path) -> Path:
    return Path(repo_root) / RECORD_RELPATH


def guarded_profiles(global_config: dict) -> Optional[list[str]]:
    """The declared guarded profile names, or None when the key is absent.

    Raises ValueError (S10.3 exit 2) for a malformed declaration or a name
    that is not a declared profile: a typo'd guard would guard nothing.
    """
    ciu_cfg = global_config.get("ciu")
    table = ciu_cfg.get("selection_record") if isinstance(ciu_cfg, dict) else None
    if table is None:
        return None
    if not isinstance(table, dict) or set(table) != {"profiles"}:
        raise ValueError(
            "[CIU-205] [ciu.selection_record] must be a table with exactly one key, "
            "profiles = [\"<profile>\", ...]"
        )
    names = table["profiles"]
    if (
        not isinstance(names, list) or not names
        or any(not isinstance(n, str) or not n.strip() or n != n.strip() for n in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError(
            "[CIU-205] ciu.selection_record.profiles must be a non-empty list of "
            "unique profile names"
        )
    declared = (global_config.get("deploy") or {}).get("profiles") or {}
    unknown = [n for n in names if n not in declared]
    if unknown:
        raise ValueError(
            f"[CIU-205] ciu.selection_record.profiles names undeclared profile(s) "
            f"{unknown}; declared: {sorted(declared)}"
        )
    return list(names)


def _read(path: Path) -> list[str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"[CIU-205] cannot read the selection record {path}: {exc}; "
            "remove it with `ciu clean` (it also resets the instance's data)"
        ) from exc
    profiles = document.get("profiles") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("api") != RECORD_API
        or document.get("api_version") != RECORD_API_VERSION
        or not isinstance(profiles, list)
        or any(not isinstance(p, str) for p in profiles)
    ):
        raise ValueError(
            f"[CIU-205] {path} is not a {RECORD_API} v{RECORD_API_VERSION} record; "
            "remove it with `ciu clean`"
        )
    return profiles


def _write_exclusive(path: Path, profiles: list[str]) -> bool:
    """Create the record; False when another invocation created it first.

    The complete payload is written to a temp file first and published with
    ``os.link``, which never replaces an existing record, so a concurrent
    reader sees either no record or a whole one, never a partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"api": RECORD_API, "api_version": RECORD_API_VERSION, "profiles": profiles},
        indent=2,
    ) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-selection-record-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o644)
        try:
            os.link(tmp_name, path)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(tmp_name)


def enforce(
    repo_root: Path,
    global_config: dict,
    selected_profiles: list[str],
    *,
    write: bool,
) -> Optional[list[str]]:
    """Check *selected_profiles* against this checkout's record; record if first.

    Returns the guarded subset of the selection (None when not opted in).
    Raises ValueError naming both sets when the selection conflicts. With
    ``write=False`` (dry run, early preflight) a missing record is not created.
    """
    guarded = guarded_profiles(global_config)
    if guarded is None:
        return None
    chosen = [name for name in guarded if name in selected_profiles]
    path = record_path(repo_root)
    if os.path.lexists(path):
        recorded = _read(path)
    elif write and _write_exclusive(path, chosen):
        print(
            f"[INFO] [CIU-205] recorded this instance's guarded profile selection "
            f"{chosen} in {path}",
            flush=True,
        )
        return chosen
    elif write:
        recorded = _read(path)  # lost the creation race: compare with the winner
    else:
        return chosen
    if sorted(recorded) != sorted(chosen):
        raise ValueError(
            f"[CIU-205] this instance was first deployed with guarded profiles "
            f"{recorded} (record {path}); this invocation selects {chosen} "
            f"(selected profiles {list(selected_profiles)}, guarded {guarded}). "
            "Select the recorded profiles again, or run `ciu clean` to remove the "
            "instance and its record before deploying another selection."
        )
    return chosen


def clear(repo_root: Path) -> bool:
    """Remove the record; True when one was removed."""
    path = record_path(repo_root)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
