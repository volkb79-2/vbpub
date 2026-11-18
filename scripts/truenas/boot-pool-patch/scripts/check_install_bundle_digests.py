#!/usr/bin/env python3
"""Rebuild each registered installer archive and verify its pinned SHA-256."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile


PATCH_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_ROOT = PATCH_ROOT / "acceptance"
sys.path.insert(0, str(ACCEPTANCE_ROOT))

from live_v27_patch import make_bundle as make_v27_bundle
from live_v28_patch import deterministic_bundle as make_v28_bundle


def member_digests(bundle: bytes) -> dict[str, str]:
    """Return the SHA-256 of every file inside a bundle archive, by member name.

    A bundle digest binds every member byte, including readmes and adapter
    metadata. On a mismatch these lines name the member that changed: compare
    them with the same listing built from the commit that registered the digest.
    """
    digests: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
        for member in archive.getmembers():
            handle = archive.extractfile(member)
            if handle is not None:
                digests[member.name] = hashlib.sha256(handle.read()).hexdigest()
    return dict(sorted(digests.items()))


def main() -> int:
    registry = json.loads((PATCH_ROOT / "supported-builds.json").read_text(encoding="utf-8"))
    builds = registry.get("supported_builds")
    if not isinstance(builds, dict) or not builds:
        raise SystemExit("supported-build registry has no builds")

    generated: list[Path] = []
    failures = 0
    try:
        for version, row in sorted(builds.items()):
            expected = row.get("install_bundle_sha256")
            if version.startswith("27."):
                bundle, actual = make_v27_bundle("path-a", version, verify_registry_digest=False)
                generated.append(bundle)
                archive_bytes = bundle.read_bytes()
            elif version.startswith("28."):
                archive_bytes, actual, _ = make_v28_bundle(version=version, verify_registry_digest=False)
            else:
                raise SystemExit(f"no installer archive builder for registered build {version}")
            status = "PASS" if actual == expected else "FAIL"
            print(f"{status} {version} expected={expected} actual={actual}")
            if status != "PASS":
                for name, digest in member_digests(archive_bytes).items():
                    print(f"  member {digest} {name}")
            failures += status != "PASS"
    finally:
        for bundle in generated:
            bundle.unlink(missing_ok=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
