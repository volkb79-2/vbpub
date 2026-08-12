"""External release-tool primitives (not a CMRU configuration feature).

These functions are available only to an explicit project-owned release command.
Every requested tool is mandatory: a missing executable exits with
``PREREQ_MISSING``.  There is no optional-skip mode and no CMRU `[delegated]`
configuration surface; see SPEC S7 and KI-04.

Minisign (Ed25519 detached manifest signing, §3 of SPEC B):
  - minisign_sign(blob, *, secret_key, trusted_comment)
  - minisign_verify(blob, *, public_key) -> bool

Key generation (documented here per spec):
  minisign -G -p minisign.pub -s minisign.key
The secret key is a release-time secret: resolve from env var or a gitignored
file — NEVER commit it, NEVER put it in cmru.toml (same discipline as the
GitHub token, S2.4).  The public key is published and distributed to hosts.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from cmru import exit_codes


def _which(tool: str) -> Optional[str]:
    return shutil.which(tool)


def _run(argv: Sequence[str], cwd: Optional[Path] = None) -> int:
    """Run an external command; stream output; return its exit code."""
    print(f"[INFO] external-tool: {' '.join(argv)}")
    result = subprocess.run(list(argv), cwd=cwd)
    return result.returncode


def cosign_sign(
    artifact: Path,
    *,
    key: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Sign artifact with cosign (S7.1).

    key: path to cosign private key, or None for keyless OIDC signing.
    """
    tool = _which("cosign")
    if not tool:
        print("[ERROR] cosign is required but not found (S8 exit 3)", file=sys.stderr)
        sys.exit(exit_codes.PREREQ_MISSING)

    argv: List[str] = [tool, "sign-blob", "--yes"]
    if key:
        argv += ["--key", key]
    argv += (extra_args or []) + [str(artifact)]
    rc = _run(argv, cwd=artifact.parent)
    if rc != 0:
        print(f"[ERROR] cosign sign-blob exited {rc}", file=sys.stderr)
        sys.exit(exit_codes.FAILURE)


def syft_sbom(
    artifact: Path,
    output: Path,
    *,
    format: str = "spdx-json",
    extra_args: Optional[List[str]] = None,
) -> None:
    """Generate SBOM with syft (S7.2)."""
    tool = _which("syft")
    if not tool:
        print("[ERROR] syft is required but not found (S8 exit 3)", file=sys.stderr)
        sys.exit(exit_codes.PREREQ_MISSING)

    argv: List[str] = [tool, "scan", str(artifact), "--output", f"{format}={output}"]
    argv += extra_args or []
    rc = _run(argv, cwd=artifact.parent)
    if rc != 0:
        print(f"[ERROR] syft scan exited {rc}", file=sys.stderr)
        sys.exit(exit_codes.FAILURE)


def grype_scan(
    sbom_or_artifact: Path,
    *,
    fail_on: str = "high",
    extra_args: Optional[List[str]] = None,
) -> None:
    """Scan for vulnerabilities with grype (S7.2)."""
    tool = _which("grype")
    if not tool:
        print("[ERROR] grype is required but not found (S8 exit 3)", file=sys.stderr)
        sys.exit(exit_codes.PREREQ_MISSING)

    argv: List[str] = [tool, str(sbom_or_artifact), f"--fail-on={fail_on}"]
    argv += extra_args or []
    rc = _run(argv)
    if rc != 0:
        print(f"[ERROR] grype scan found vulnerabilities at level={fail_on} (exit {rc})", file=sys.stderr)
        sys.exit(exit_codes.FAILURE)


def git_cliff_changelog(
    output: Path,
    *,
    tag: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    cwd: Optional[Path] = None,
) -> None:
    """Generate changelog with git-cliff (S7.3)."""
    tool = _which("git-cliff")
    if not tool:
        print("[ERROR] git-cliff is required but not found (S8 exit 3)", file=sys.stderr)
        sys.exit(exit_codes.PREREQ_MISSING)

    argv: List[str] = [tool, "--output", str(output)]
    if tag:
        argv += ["--tag", tag]
    argv += extra_args or []
    rc = _run(argv, cwd=cwd)
    if rc != 0:
        print(f"[ERROR] git-cliff exited {rc}", file=sys.stderr)
        sys.exit(exit_codes.FAILURE)


def nfpm_package(
    config: Path,
    target_dir: Path,
    packager: str = "deb",
    *,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Build .deb / .rpm with nfpm (S7.4).

    packager: "deb" or "rpm"
    """
    tool = _which("nfpm")
    if not tool:
        print("[ERROR] nfpm is required but not found (S8 exit 3)", file=sys.stderr)
        sys.exit(exit_codes.PREREQ_MISSING)

    argv: List[str] = [tool, "package", "--packager", packager,
                       "--config", str(config), "--target", str(target_dir)]
    argv += extra_args or []
    rc = _run(argv, cwd=config.parent)
    if rc != 0:
        print(f"[ERROR] nfpm {packager} exited {rc}", file=sys.stderr)
        sys.exit(exit_codes.FAILURE)


def minisign_sign(
    blob: Path,
    *,
    secret_key: str,
    trusted_comment: str,
) -> None:
    """Sign blob with minisign (Ed25519 detached signature, SPEC B §3).

    Produces <blob>.minisig alongside blob.  The trusted_comment is signed and
    tamper-evident — callers MUST use build_trusted_comment() from manifest.py
    to bind the signature to the exact manifest bytes.

    secret_key: path to the minisign secret key file.
    trusted_comment: text embedded in the signed trusted-comment field.
    Key generation (one-time):
        minisign -G -p minisign.pub -s minisign.key
    Secret key: gitignored, from env/file — NEVER committed (S2.4 discipline).
    """
    tool = _which("minisign")
    if not tool:
        print("[ERROR] minisign is required but not found (S8 exit 3)", file=sys.stderr)
        sys.exit(exit_codes.PREREQ_MISSING)

    argv: List[str] = [
        tool, "-S",
        "-s", secret_key,
        "-m", str(blob),
        "-t", trusted_comment,
    ]
    rc = _run(argv, cwd=blob.parent)
    if rc != 0:
        print(f"[ERROR] minisign sign exited {rc}", file=sys.stderr)
        sys.exit(exit_codes.FAILURE)


def minisign_verify(
    blob: Path,
    *,
    public_key: str,
) -> bool:
    """Verify a minisign detached signature for blob (SPEC B §3 / SPEC A).

    Returns True if verification succeeds, False if it fails.
    If minisign is absent, exits 3.

    public_key: path to the minisign public key file.

    Verification command: minisign -Vm <blob> -p <public_key>
    Checks: Ed25519 signature AND the trusted-comment binding to manifest_sha256.
    """
    tool = _which("minisign")
    if not tool:
        print("[ERROR] minisign is required but not found (S8 exit 3)", file=sys.stderr)
        sys.exit(exit_codes.PREREQ_MISSING)

    argv: List[str] = [tool, "-Vm", str(blob), "-p", public_key]
    print(f"[INFO] external-tool: {' '.join(argv)}")
    result = subprocess.run(list(argv), cwd=blob.parent, capture_output=True)
    if result.returncode == 0:
        return True
    # Log verification failure (not an exit — caller decides how to handle).
    stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
    print(f"[WARN] minisign verify failed for {blob.name}: {stderr_text}", file=sys.stderr)
    return False
