#!/usr/bin/env python3
"""Build the base and cmru-builder bundles and write the P-edge-08 equivalence evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

TLS_EDGE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TLS_EDGE_DIR.parent
EXPECTED_OLD_ONLY = {
    "scripts/build-artifact.sh",
    "scripts/release.sh",
    "scripts/render.sh",
    "scripts/render_standalone.py",
    "scripts/stage-wheels.py",
    "scripts/write-bundle-manifest.py",
}
_WHEEL_RE = re.compile(r"([A-Za-z0-9_.-]+)-([0-9][0-9A-Za-z.+!_]*)-")


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    print(f"[RUN] cwd={cwd} argv={argv}", flush=True)
    result = subprocess.run(argv, cwd=cwd, env=env, check=False)
    if result.returncode:
        raise SystemExit(f"[ERROR] command failed with exit {result.returncode}: {' '.join(argv)}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_data(path: Path) -> tuple[dict, dict[str, str], set[str]]:
    """Return (manifest, archive member hashes, pyc/cache member paths)."""
    hashes: dict[str, str] = {}
    bytecode: set[str] = set()
    manifest: dict | None = None
    with tarfile.open(path, "r:xz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        tops = {Path(member.name).parts[0] for member in members if Path(member.name).parts}
        if len(tops) != 1:
            raise SystemExit(f"[ERROR] {path}: expected one archive top, found {sorted(tops)}")
        top = next(iter(tops))
        for member in members:
            parts = Path(member.name).parts
            if len(parts) < 2:
                continue
            rel = Path(*parts[1:]).as_posix()
            handle = archive.extractfile(member)
            if handle is None:
                raise SystemExit(f"[ERROR] {path}: unreadable member {member.name}")
            data = handle.read()
            hashes[rel] = hashlib.sha256(data).hexdigest()
            if rel.endswith(".pyc") or "__pycache__/" in rel:
                bytecode.add(rel)
            if rel == "manifest.json":
                manifest = json.loads(data)
    if manifest is None:
        raise SystemExit(f"[ERROR] {path}: manifest.json is absent")
    expected = set(manifest.get("files", {}))
    actual = set(hashes) - {"manifest.json"}
    if expected != actual:
        raise SystemExit(
            f"[ERROR] {path}: manifest membership differs (added={sorted(actual - expected)}, "
            f"missing={sorted(expected - actual)})"
        )
    return manifest, hashes, bytecode


def _normal_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _wheel_versions(manifest: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for rel in manifest.get("files", {}):
        if not rel.startswith("wheels/") or not rel.endswith(".whl"):
            continue
        match = _WHEEL_RE.match(Path(rel).name)
        if not match:
            raise SystemExit(f"[ERROR] cannot parse wheel name {rel!r}")
        result[_normal_name(match.group(1))] = match.group(2)
    return result


def compare_manifests(old: dict, new: dict) -> tuple[list[str], list[str], list[str]]:
    old_files = {path: entry for path, entry in old["files"].items() if not path.startswith("wheels/")}
    new_files = {path: entry for path, entry in new["files"].items() if not path.startswith("wheels/")}
    old_only = sorted(set(old_files) - set(new_files))
    new_only = sorted(set(new_files) - set(old_files))
    changed = sorted(
        path
        for path in set(old_files) & set(new_files)
        if old_files[path].get("sha256") != new_files[path].get("sha256")
    )
    if old_only != sorted(EXPECTED_OLD_ONLY):
        raise SystemExit(
            f"[ERROR] old-only operator files are not exactly the maintainer removals: {old_only}"
        )
    if new_only:
        raise SystemExit(f"[ERROR] new bundle adds operator files: {new_only}")
    old_wheels = _wheel_versions(old)
    new_wheels = _wheel_versions(new)
    if old_wheels != new_wheels:
        raise SystemExit(
            f"[ERROR] wheel distribution/version mismatch: old={old_wheels}, new={new_wheels}"
        )
    return old_only, changed, sorted(old_wheels)


def _git_archive(base: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "base.tar"
    _run(
        ["git", "archive", "--format=tar", "--output", str(archive), base, "tls-edge"],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
    )
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")
    shutil.copy2(REPO_ROOT / "cmru.orchestration.toml", destination / "cmru.orchestration.toml")
    return destination / "tls-edge"


def _copy_archives(dist: Path, target: Path, version: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for variant in ("py313", "py314"):
        source = dist / f"tls-edge-v{version}-{variant}.tar.xz"
        if not source.is_file():
            raise SystemExit(f"[ERROR] missing built archive {source}")
        shutil.copy2(source, target / source.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "evidence" / "P-edge-08-equivalence.log",
    )
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    version = (TLS_EDGE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    base = subprocess.run(
        ["git", "rev-parse", args.base],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    epoch = subprocess.run(
        ["git", "show", "-s", "--format=%ct", base],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    env = os.environ.copy()
    env.update({"SOURCE_DATE_EPOCH": epoch, "TLS_EDGE_VERSION": version})
    evidence = [
        "P-edge-08 bundle equivalence evidence",
        f"base={base}",
        f"head={head}",
        f"version={version}",
        f"SOURCE_DATE_EPOCH={epoch}",
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="tls-edge-equivalence-") as temp:
            temp_root = Path(temp)
            _run(
                ["python3", "tools/bundle_wheels.py", "requirements"],
                cwd=TLS_EDGE_DIR,
                env=env,
            )
            requirements = TLS_EDGE_DIR / "requirements-bundle.txt"
            evidence.append(
                f"requirements_lock={requirements.relative_to(TLS_EDGE_DIR).as_posix()} "
                f"sha256={_sha256(requirements)}"
            )
            first = temp_root / "new-first"
            _run(
                [
                    "cmru", "handler", "bundle-build",
                    "--bundle-config", "bundle.toml",
                    "--cmru-config", "../cmru.orchestration.toml",
                    "--name", "tls-edge", "--variant", "all",
                ],
                cwd=TLS_EDGE_DIR,
                env=env,
            )
            _copy_archives(TLS_EDGE_DIR / "dist", first, version)
            _run(
                ["python3", "tools/bundle_wheels.py", "retain", "dist"],
                cwd=TLS_EDGE_DIR,
                env=env,
            )
            manifests = {}
            for variant in ("py313", "py314"):
                manifest, _, _ = _archive_data(
                    first / f"tls-edge-v{version}-{variant}.tar.xz"
                )
                manifests[variant] = manifest
            first_wheels = _wheel_versions(manifests["py313"])
            second_env = env | {
                "TLS_EDGE_CIU_VERSION": first_wheels["ciu"],
                "TLS_EDGE_CLI_EXTENDED_VERSION": first_wheels["cli-extended"],
            }
            second = temp_root / "new-second"
            _run(
                [
                    "cmru", "handler", "bundle-build",
                    "--bundle-config", "bundle.toml",
                    "--cmru-config", "../cmru.orchestration.toml",
                    "--name", "tls-edge", "--variant", "all",
                ],
                cwd=TLS_EDGE_DIR,
                env=second_env,
            )
            _copy_archives(TLS_EDGE_DIR / "dist", second, version)
            _run(
                ["python3", "tools/bundle_wheels.py", "retain", "dist"],
                cwd=TLS_EDGE_DIR,
                env=second_env,
            )
            for variant in ("py313", "py314"):
                name = f"tls-edge-v{version}-{variant}.tar.xz"
                first_sha = _sha256(first / name)
                second_sha = _sha256(second / name)
                evidence.append(f"new[1] {variant} sha256={first_sha} {name}")
                evidence.append(f"new[2] {variant} sha256={second_sha} {name}")
                if first_sha != second_sha:
                    raise SystemExit(f"[ERROR] {variant}: two cmru builds are not byte-identical")

            old_root = _git_archive(base, temp_root / "old")
            old_dist = old_root / "dist"
            old_env = env | {"PIP_CONSTRAINT": str(requirements)}
            _run(
                [
                    sys.executable, "scripts/stage-wheels.py",
                    "--ciu-version", first_wheels["ciu"],
                    "--cli-extended-version", first_wheels["cli-extended"],
                ],
                cwd=old_root,
                env=old_env,
            )
            _run(["bash", "scripts/build-artifact.sh"], cwd=old_root, env=old_env)

            for variant in ("py313", "py314"):
                old_path = old_dist / f"tls-edge-v{version}-{variant}.tar.xz"
                new_path = second / f"tls-edge-v{version}-{variant}.tar.xz"
                old_manifest, _, old_bytecode = _archive_data(old_path)
                new_manifest, _, new_bytecode = _archive_data(new_path)
                dropped, changed, wheels = compare_manifests(old_manifest, new_manifest)
                evidence.append(f"variant={variant} old_sha256={_sha256(old_path)}")
                evidence.append(f"variant={variant} new_sha256={_sha256(new_path)}")
                evidence.append(f"variant={variant} dropped=" + " | ".join(dropped))
                evidence.append(f"variant={variant} operator_file_set_identical={True}")
                evidence.append(
                    f"variant={variant} wheel_versions="
                    + json.dumps(_wheel_versions(new_manifest), sort_keys=True)
                )
                evidence.append(f"variant={variant} content_changed=" + " | ".join(changed))
                if old_bytecode or new_bytecode:
                    raise SystemExit(
                        f"[ERROR] bytecode/cache in archives: old={sorted(old_bytecode)} "
                        f"new={sorted(new_bytecode)}"
                    )
                evidence.append(f"variant={variant} bytecode_or_cache=none")
        evidence.append("RESULT=PASS")
    except BaseException as exc:
        evidence.append(f"RESULT=FAIL {exc}")
        output.write_text("\n".join(evidence) + "\n", encoding="utf-8")
        raise
    output.write_text("\n".join(evidence) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
