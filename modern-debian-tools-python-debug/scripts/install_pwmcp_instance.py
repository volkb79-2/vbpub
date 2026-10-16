#!/usr/bin/env python3
"""Install the commit-pinned PWMCP CMRU bundle into an MDT image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)+(?:-[A-Za-z0-9.-]+)?")
_MAX_SOURCE_BYTES = 8 * 1024 * 1024


def _download(url: str, expected_sha256: str, *, label: str) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        raise ValueError(f"{label} URL must use raw.githubusercontent.com over HTTPS")
    if not _SHA256.fullmatch(expected_sha256):
        raise ValueError(f"{label} SHA-256 must contain 64 lowercase hexadecimal characters")
    request = urllib.request.Request(url, headers={"User-Agent": "mdt-pwmcp-image-build"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read(_MAX_SOURCE_BYTES + 1)
    if len(content) > _MAX_SOURCE_BYTES:
        raise ValueError(f"{label} exceeds {_MAX_SOURCE_BYTES} bytes")
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--getpy-url", required=True)
    parser.add_argument("--getpy-sha256", required=True)
    parser.add_argument("--contract-url", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    if not _VERSION.fullmatch(args.version):
        parser.error("--version must be the release value from pwmcp.contract.json")

    try:
        get_py = _download(args.getpy_url, args.getpy_sha256, label="PWMCP get.py")
        contract_bytes = _download(
            args.contract_url, args.contract_sha256, label="PWMCP contract"
        )
        contract = json.loads(contract_bytes)
        if not isinstance(contract, dict) or contract.get("release") != args.version:
            raise ValueError("PWMCP contract release does not match the requested version")
        with tempfile.TemporaryDirectory(prefix="mdt-pwmcp-") as temp_dir:
            installer = Path(temp_dir) / "get.py"
            installer.write_bytes(get_py)
            installer.chmod(0o700)
            command = [
                sys.executable,
                str(installer),
                "install",
                "--scope",
                "user",
                "--version",
                f"pwmcp-v{args.version}",
            ]
            import os

            environment = dict(os.environ)
            environment["XDG_DATA_HOME"] = "/srv"
            result = subprocess.run(command, env=environment, check=False)
            if result.returncode:
                return result.returncode
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] cannot install the pinned PWMCP instance: {exc}", file=sys.stderr)
        return 1

    print(f"[INFO] Installed PWMCP {args.version} at /srv/pwmcp-instance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
