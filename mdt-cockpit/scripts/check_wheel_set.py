#!/usr/bin/env python3
"""Prove the first-party wheel set of a build is exactly the one it should be.

Used by the Dockerfile's first-party wheel RUN, which installs ONLY what this script
emits. A build whose wheel set is empty, incomplete, or contains anything nobody
reported for THIS build must fail in every build mode:

* ``pip/wheels/`` holds release wheels. The resolver writes ``RESOLVED.json`` naming the
  exact set it resolved for this build; the directory must EQUAL that set (a leftover
  or hand-copied wheel is an error, not a superset to install), and each file must hash
  to the reported sha256.
* ``--build-mode local-dev`` additionally takes the wheels of the ``mdt-local-dev``
  context (``<local-dir>/wheels``), which must equal the wheels recorded in its
  ``provenance.json``, hash to their recorded sha256, and share no distribution with
  the release set. A ``release`` build refuses any wheel in that context.
* The union must cover exactly the distributions ``pip/wheels.list`` declares, compared
  as PEP 503 names (``cli_extended`` satisfies ``cli-extended``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPORT_NAME = "RESOLVED.json"
PROVENANCE_NAME = "provenance.json"
BUILD_MODES = ("release", "local-dev")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def dist_of(filename: str) -> str:
    return normalize(filename.split("-", 1)[0])


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def declared_names(wheels_list: Path) -> list[str]:
    names = []
    for raw in wheels_list.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.append(normalize(line))
    return names


def _wheels_in(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.whl")) if directory.is_dir() else []


def _load_json(path: Path, what: str, problems: list[str]) -> dict | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"cannot read {what} {path}: {exc}")
        return None
    if not isinstance(document, dict):
        problems.append(f"{what} {path} is not a JSON object")
        return None
    return document


def _check_directory(
    directory: Path, expected: dict[str, str], what: str, problems: list[str]
) -> list[tuple[Path, str]]:
    """Directory content must equal ``expected`` (filename -> sha256), hashes included."""
    present = {wheel.name: wheel for wheel in _wheels_in(directory)}
    unreported = sorted(set(present) - set(expected))
    if unreported:
        problems.append(
            f"{what}: wheel(s) present that nobody reported for this build: {', '.join(unreported)} "
            "(a leftover of an earlier build? delete them and re-run the resolver)"
        )
    absent = sorted(set(expected) - set(present))
    if absent:
        problems.append(f"{what}: reported wheel(s) missing from {directory}: {', '.join(absent)}")
    accepted: list[tuple[Path, str]] = []
    for name in sorted(set(present) & set(expected)):
        actual = sha256_of(present[name])
        if actual != expected[name]:
            problems.append(f"{what}: {name} hashes to {actual}, reported {expected[name]}")
        else:
            accepted.append((present[name], actual))
    return accepted


def check(
    wheels_dir: Path,
    wheels_list: Path,
    *,
    build_mode: str = "release",
    local_dir: Path | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Return ``(problems, wheels)``; ``wheels`` are the files to install (empty on error)."""
    problems: list[str] = []
    if build_mode not in BUILD_MODES:
        return [f"unknown build mode {build_mode!r} (expected one of {', '.join(BUILD_MODES)})"], []
    declared = declared_names(wheels_list)
    if not declared:
        return [f"{wheels_list} declares no first-party wheels"], []

    report = _load_json(wheels_dir / REPORT_NAME, "resolver report", problems)
    release: list[tuple[Path, str]] = []
    if report is not None:
        entries = report.get("wheels")
        if not isinstance(entries, list):
            problems.append(f"resolver report {wheels_dir / REPORT_NAME} has no 'wheels' list")
        else:
            expected = {str(item.get("filename")): str(item.get("sha256")) for item in entries}
            release = _check_directory(wheels_dir, expected, str(wheels_dir), problems)

    local: list[tuple[Path, str]] = []
    local_wheels_dir = (local_dir / "wheels") if local_dir is not None else None
    if build_mode == "release":
        stray = _wheels_in(local_wheels_dir) if local_wheels_dir is not None else []
        if stray:
            problems.append(
                f"a release build received local-dev wheel(s): {', '.join(w.name for w in stray)}"
            )
    else:
        if local_dir is None:
            problems.append("--build-mode local-dev needs --local-dir")
        else:
            provenance = _load_json(local_dir / PROVENANCE_NAME, "local-dev provenance", problems)
            if provenance is not None:
                recorded = {
                    Path(str(item["staged"])).name: str(item["sha256"])
                    for item in provenance.get("artifacts", [])
                    if str(item.get("staged", "")).startswith("wheels/")
                }
                local = _check_directory(local_wheels_dir, recorded, "local-dev context", problems)

    covered: dict[str, list[str]] = {}
    for path, _ in release + local:
        covered.setdefault(dist_of(path.name), []).append(path.name)
    for dist, files in sorted(covered.items()):
        if len(files) > 1:
            problems.append(f"distribution {dist} is supplied more than once: {', '.join(files)}")
    if not covered and not problems:
        problems.append(
            f"no first-party wheels at all, but {wheels_list} declares: {', '.join(declared)}. "
            "Run scripts/resolve-devcontainers-release.py (or a --local-dev build) first."
        )
    missing = [name for name in declared if name not in covered]
    if missing:
        problems.append(
            f"first-party wheel(s) declared in {wheels_list} but not supplied: {', '.join(missing)} "
            f"(supplied: {', '.join(sorted(covered)) or 'none'})"
        )
    undeclared = sorted(set(covered) - set(declared))
    if undeclared:
        problems.append(f"wheel(s) supplied that {wheels_list} does not declare: {', '.join(undeclared)}")
    if problems:
        return problems, []
    wheels = [
        {
            "name": dist_of(path.name),
            "filename": path.name,
            "path": str(path),
            "sha256": sha256,
            "source": "release" if (path, sha256) in release else "local-dev",
        }
        for path, sha256 in release + local
    ]
    return [], sorted(wheels, key=lambda item: item["name"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels-dir", type=Path, required=True)
    parser.add_argument("--wheels-list", type=Path, required=True)
    parser.add_argument("--build-mode", required=True, choices=BUILD_MODES)
    parser.add_argument("--local-dir", type=Path)
    parser.add_argument("--install-list", type=Path, required=True, help="write the files to install, one per line")
    parser.add_argument("--record", type=Path, required=True, help="write the installed set (with sha256) as JSON")
    args = parser.parse_args()
    problems, wheels = check(
        args.wheels_dir, args.wheels_list, build_mode=args.build_mode, local_dir=args.local_dir
    )
    for problem in problems:
        print(f"[ERROR] {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"[INFO] wheels.list declares: {' '.join(declared_names(args.wheels_list))}", file=sys.stderr)
    for wheel in wheels:
        print(f"[INFO] first-party wheel ({wheel['source']}): {wheel['filename']} sha256={wheel['sha256']}", file=sys.stderr)
    args.install_list.write_text("".join(w["path"] + "\n" for w in wheels), encoding="utf-8")
    args.record.write_text(
        json.dumps(
            {"schema": 1, "build_mode": args.build_mode,
             "wheels": [{k: v for k, v in w.items() if k != "path"} for w in wheels]},
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
