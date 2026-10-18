#!/usr/bin/env python3
"""Install the commit-pinned PWMCP CMRU bundle into an MDT image.

Release build (default): download the commit-pinned ``get.py`` installer and the
release contract from raw.githubusercontent.com, verify their SHA-256, check the
contract's release against ``--version`` and run ``get.py install`` (which
downloads and verifies the release bundle itself).

Local-dev build (``--local-dev-dir DIR``, explicit opt-in): ``DIR`` is the staging
directory written by ``build-push.py --build --local-dev``. Its
``provenance.json`` lists the verified local artifacts. ``pwmcp-installer``
replaces the downloaded ``get.py``; ``pwmcp-bundle`` replaces the release-asset
download inside ``get.py`` (everything after the download - SHA-256, manifest
verification against the requested tag, offline wheel install - is the unchanged
``get.py`` code). A name that is absent from the provenance file still comes from
its release, as in a release build. Every local file is re-hashed here against the
provenance record: the build refuses on any mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)+(?:-[A-Za-z0-9.-]+)?")
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
PROVENANCE_NAME = "provenance.json"
LOCAL_INSTALLER = "pwmcp-installer"
LOCAL_BUNDLE = "pwmcp-bundle"


class LocalDevError(ValueError):
    """The local-dev staging directory cannot be trusted."""


def _download(url: str, expected_sha256: str, *, label: str) -> bytes:
    if not url or not expected_sha256:
        raise ValueError(
            f"{label}: no URL/SHA-256 given and no local-dev artifact supplies it"
        )
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_local_artifacts(local_dir: Path) -> dict[str, dict]:
    """Return the verified PWMCP entries of a local-dev staging directory.

    Each returned entry carries the resolved staged ``file`` plus the recorded
    ``sha256`` and ``version``. A missing/unreadable provenance file, a staged
    path escaping the directory, or a hash that differs from the record is an
    error: a local-dev directory that cannot be verified is never ignored.
    """
    provenance = local_dir / PROVENANCE_NAME
    try:
        document = json.loads(provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalDevError(f"cannot read {provenance}: {exc}") from exc
    entries = document.get("artifacts") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("schema") != 1
        or not isinstance(entries, list)
    ):
        raise LocalDevError(f"{provenance} is not a schema-1 local-dev provenance record")
    found: dict[str, dict] = {}
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else None
        if name not in (LOCAL_INSTALLER, LOCAL_BUNDLE):
            continue
        staged = local_dir / str(entry.get("staged", ""))
        try:
            staged.resolve().relative_to(local_dir.resolve())
        except ValueError as exc:
            raise LocalDevError(f"{name}: staged path escapes {local_dir}") from exc
        expected = str(entry.get("sha256", "")).lower()
        if not _SHA256.fullmatch(expected):
            raise LocalDevError(f"{name}: recorded SHA-256 is not 64 lowercase hex digits")
        if not staged.is_file():
            raise LocalDevError(f"{name}: staged file {staged} is missing")
        actual = _sha256_file(staged)
        if actual != expected:
            raise LocalDevError(
                f"{name}: staged file SHA-256 mismatch: recorded {expected}, got {actual}"
            )
        found[name] = {
            "file": staged,
            "sha256": expected,
            "version": str(entry.get("version", "")),
        }
    return found


def _run_get_py_with_local_bundle(
    get_py: Path, bundle: Path, bundle_sha256: str, tag: str
) -> int:
    """Run ``get.py install`` with its release-asset download replaced.

    Only ``_download_asset`` is replaced: it copies the verified local bundle and
    writes a SHA-256 sidecar from the provenance record, so ``get.py`` still
    performs its own checksum, manifest-vs-tag and wheel verification.
    """
    spec = importlib.util.spec_from_file_location("pwmcp_get", get_py)
    if spec is None or spec.loader is None:
        raise LocalDevError(f"cannot load {get_py} as a module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def local_download(_tag: str, asset_name: str, dest: Path, _token) -> None:
        if asset_name.endswith(".sha256"):
            dest.write_text(f"{bundle_sha256}\n", encoding="utf-8")
        else:
            shutil.copyfile(bundle, dest)

    module._download_asset = local_download
    previous_argv = sys.argv
    sys.argv = [str(get_py), "install", "--scope", "user", "--version", tag]
    try:
        module.main()
    except SystemExit as exc:
        return int(exc.code or 0) if not isinstance(exc.code, str) else 1
    finally:
        sys.argv = previous_argv
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--getpy-url", default="")
    parser.add_argument("--getpy-sha256", default="")
    parser.add_argument("--contract-url", default="")
    parser.add_argument("--contract-sha256", default="")
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--local-dev-dir",
        default="",
        help="staging directory of an explicit local-dev build (see module docstring)",
    )
    args = parser.parse_args()

    if not _VERSION.fullmatch(args.version):
        parser.error("--version must be the release value from pwmcp.contract.json")
    tag = f"pwmcp-v{args.version}"

    try:
        local = load_local_artifacts(Path(args.local_dev_dir)) if args.local_dev_dir else {}
        bundle_entry = local.get(LOCAL_BUNDLE)
        if bundle_entry is not None and bundle_entry["version"] != args.version:
            raise LocalDevError(
                f"{LOCAL_BUNDLE} version {bundle_entry['version']!r} does not match the "
                f"pinned PWMCP version {args.version!r}"
            )

        installer_entry = local.get(LOCAL_INSTALLER)
        if installer_entry is not None:
            get_py = installer_entry["file"].read_bytes()
        else:
            get_py = _download(args.getpy_url, args.getpy_sha256, label="PWMCP get.py")

        if bundle_entry is None:
            # Release bundle: the contract pins which release get.py may install.
            contract_bytes = _download(
                args.contract_url, args.contract_sha256, label="PWMCP contract"
            )
            contract = json.loads(contract_bytes)
            if not isinstance(contract, dict) or contract.get("release") != args.version:
                raise ValueError("PWMCP contract release does not match the requested version")

        environment_before = dict(os.environ)
        os.environ["XDG_DATA_HOME"] = "/srv"
        try:
            with tempfile.TemporaryDirectory(prefix="mdt-pwmcp-") as temp_dir:
                installer = Path(temp_dir) / "get.py"
                installer.write_bytes(get_py)
                installer.chmod(0o700)
                if bundle_entry is not None:
                    print(
                        f"[INFO] local-dev: installing the local PWMCP bundle "
                        f"{bundle_entry['file']} (sha256 {bundle_entry['sha256']})",
                        file=sys.stderr,
                    )
                    returncode = _run_get_py_with_local_bundle(
                        installer, bundle_entry["file"], bundle_entry["sha256"], tag
                    )
                else:
                    command = [
                        sys.executable,
                        str(installer),
                        "install",
                        "--scope",
                        "user",
                        "--version",
                        tag,
                    ]
                    returncode = subprocess.run(
                        command, env=dict(os.environ), check=False
                    ).returncode
        finally:
            os.environ.clear()
            os.environ.update(environment_before)
        if returncode:
            return returncode
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] cannot install the pinned PWMCP instance: {exc}", file=sys.stderr)
        return 1

    print(f"[INFO] Installed PWMCP {args.version} at /srv/pwmcp-instance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
