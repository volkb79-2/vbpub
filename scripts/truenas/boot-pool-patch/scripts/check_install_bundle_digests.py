#!/usr/bin/env python3
"""Rebuild each registered installer archive and verify its pinned SHA-256."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PATCH_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_ROOT = PATCH_ROOT / "acceptance"
sys.path.insert(0, str(ACCEPTANCE_ROOT))

from live_v27_patch import make_bundle as make_v27_bundle
from live_v28_patch import deterministic_bundle as make_v28_bundle


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
            elif version.startswith("28."):
                _, actual, _ = make_v28_bundle(version=version, verify_registry_digest=False)
            else:
                raise SystemExit(f"no installer archive builder for registered build {version}")
            status = "PASS" if actual == expected else "FAIL"
            print(f"{status} {version} expected={expected} actual={actual}")
            failures += status != "PASS"
    finally:
        for bundle in generated:
            bundle.unlink(missing_ok=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
