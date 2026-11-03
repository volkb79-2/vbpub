#!/usr/bin/env python3
"""Publish the per-interpreter tls-edge bundles under ONE release tag.

`cmru handler tarball-publish` publishes exactly one asset; the variant matrix goes
through cmru's `publish_versioned_variants` keystone (S-REL.6). Reads dist/ (written by
build-artifact.sh): dist/VERSION and dist/tls-edge-v<version>-<variant>.tar.xz.

Fails closed when cmru hands over a retained build-output record
(CMRU_BUILD_OUTPUT_ROOT): that path needs a variants-aware handler in cmru
(see cmru/KNOWN_ISSUES_TODO_BACKLOG.md, "tarball-publish --variants").
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist"


def _env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        print(f"[ERROR] {name} is required", file=sys.stderr)
        raise SystemExit(1)
    return value


def collect(dist: Path) -> tuple[str, list[tuple[str, Path]]]:
    """(version, [(variant, tarball)]) from a dist directory; refuses an empty set."""
    version = (dist / "VERSION").read_text(encoding="utf-8").strip()
    pattern = re.compile(rf"tls-edge-v{re.escape(version)}-([A-Za-z0-9][A-Za-z0-9._-]*)\.tar\.xz")
    found = []
    for path in sorted(dist.glob("tls-edge-v*.tar.xz")):
        match = pattern.fullmatch(path.name)
        if not match:
            raise RuntimeError(f"unexpected artifact in dist/: {path.name}")
        found.append((match.group(1), path))
    if not found:
        raise RuntimeError(f"no tls-edge-v{version}-<variant>.tar.xz in {dist}")
    return version, found


def main() -> int:
    if os.environ.get("CMRU_BUILD_OUTPUT_ROOT"):
        print("[ERROR] retained build-output publication needs a variants-aware cmru handler "
              "(cmru backlog: tarball-publish --variants)", file=sys.stderr)
        return 1
    from cmru.release import GitHubReleases, VariantArtifact, publish_versioned_variants

    token = _env("GITHUB_PUSH_PAT")
    owner = _env("GITHUB_USERNAME")
    repo = _env("GITHUB_REPO")
    version, tarballs = collect(DIST)
    labels = {"py313": "CPython 3.13, x86_64 glibc", "py314": "CPython 3.14, x86_64 glibc"}
    result = publish_versioned_variants(
        GitHubReleases(owner, repo, token),
        prefix="tls-edge",
        version=version,
        variants=[VariantArtifact(name=v, asset_path=p, label=labels.get(v)) for v, p in tarballs],
        asset_suffix=".tar.xz",
        notes=os.environ.get("TLS_EDGE_RELEASE_NOTES") or None,
    )
    print(f"[INFO] Published tls-edge {version}")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
