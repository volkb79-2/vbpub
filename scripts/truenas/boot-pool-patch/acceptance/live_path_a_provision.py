#!/usr/bin/env python3
"""Provision the bounded Path A root on an exact registered v27 installation."""

from __future__ import annotations

import json
import os
import sys

from live_v27_patch import adapter_for_version
from path_a_ui_provisioner import PathARootProvisionError, provision_path_a_ui_root
from patch_drift import get_supported_build
from remote import RemoteError, Target


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for the disposable v27 target")
    if os.environ.get("TRUENAS_STORAGE_PROFILE") != "path-a":
        raise RemoteError("TRUENAS_STORAGE_PROFILE=path-a is required")
    expected = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    build = get_supported_build(expected)
    if not expected.startswith("27.0.0-") or build["train"].startswith("TrueNAS-28-"):
        raise RemoteError(f"Path A root provisioning is registered only for v27 builds, got {expected!r}")
    # Validate the v27 adapter contract before touching the ZFS tree.
    adapter_for_version(expected)
    target = Target.from_environment()
    info = target.call("system.info", timeout=120)
    version = str(info.get("version", ""))
    if version != expected:
        raise RemoteError(f"expected TrueNAS {expected}, got {version!r}")
    record_text = target.shell("cat /root/v27-boot-pool-patch-install.json", timeout=30)
    try:
        record = json.loads(record_text)
    except json.JSONDecodeError as exc:
        raise RemoteError("v27 patch install record is not valid JSON") from exc
    if not isinstance(record, dict) or (
        record.get("version") != expected or record.get("patch") != "v27"
        or record.get("supported") is not True
    ):
        raise RemoteError(f"Path A root provisioning requires the exact supported v27 patch record: {record!r}")
    try:
        result = provision_path_a_ui_root(target)
    except PathARootProvisionError as exc:
        raise RemoteError(f"guarded Path A root provisioning failed: {exc}") from exc
    print(
        "PASS Path A root provisioned: "
        + json.dumps({"version": expected, **result}, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
