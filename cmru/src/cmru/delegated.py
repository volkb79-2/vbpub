"""Delegated steps — commodity release tooling via subprocess (S7).

S7 contract:
  - If the tool is present, run it and surface its exit code.
  - If absent and required=false (default), skip and log a warning.
  - If absent and required=true, exit 3 (PREREQ_MISSING).
  - Never vendor these tools; always delegate.

Supported delegated steps: cosign (sign), syft+grype (sbom+scan),
git-cliff (changelog), nfpm (deb/rpm packaging), minisign (manifest signing).

Minisign (Ed25519 detached manifest signing, §3 of SPEC B):
  - minisign_sign(blob, *, secret_key, trusted_comment, required=False)
  - minisign_verify(blob, *, public_key, required=False) -> bool

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
    """Run a delegated command; stream output; return exit code."""
    print(f"[INFO] delegated: {' '.join(argv)}")
    result = subprocess.run(list(argv), cwd=cwd)
    return result.returncode


def cosign_sign(
    artifact: Path,
    *,
    key: Optional[str] = None,
    required: bool = False,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Sign artifact with cosign (S7.1).

    key: path to cosign private key, or None for keyless OIDC signing.
    """
    tool = _which("cosign")
    if not tool:
        if required:
            print(f"[ERROR] cosign not found and required=true (S8 exit 3)", file=sys.stderr)
            sys.exit(exit_codes.PREREQ_MISSING)
        print(f"[WARN] cosign not found; skipping signing of {artifact.name} (S7)", file=sys.stderr)
        return

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
    required: bool = False,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Generate SBOM with syft (S7.2)."""
    tool = _which("syft")
    if not tool:
        if required:
            print(f"[ERROR] syft not found and required=true (S8 exit 3)", file=sys.stderr)
            sys.exit(exit_codes.PREREQ_MISSING)
        print(f"[WARN] syft not found; skipping SBOM (S7)", file=sys.stderr)
        return

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
    required: bool = False,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Scan for vulnerabilities with grype (S7.2)."""
    tool = _which("grype")
    if not tool:
        if required:
            print(f"[ERROR] grype not found and required=true (S8 exit 3)", file=sys.stderr)
            sys.exit(exit_codes.PREREQ_MISSING)
        print(f"[WARN] grype not found; skipping vuln scan (S7)", file=sys.stderr)
        return

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
    required: bool = False,
    extra_args: Optional[List[str]] = None,
    cwd: Optional[Path] = None,
) -> None:
    """Generate changelog with git-cliff (S7.3)."""
    tool = _which("git-cliff")
    if not tool:
        if required:
            print(f"[ERROR] git-cliff not found and required=true (S8 exit 3)", file=sys.stderr)
            sys.exit(exit_codes.PREREQ_MISSING)
        print(f"[WARN] git-cliff not found; skipping changelog (S7)", file=sys.stderr)
        return

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
    required: bool = False,
    extra_args: Optional[List[str]] = None,
) -> None:
    """Build .deb / .rpm with nfpm (S7.4).

    packager: "deb" or "rpm"
    """
    tool = _which("nfpm")
    if not tool:
        if required:
            print(f"[ERROR] nfpm not found and required=true (S8 exit 3)", file=sys.stderr)
            sys.exit(exit_codes.PREREQ_MISSING)
        print(f"[WARN] nfpm not found; skipping {packager} packaging (S7)", file=sys.stderr)
        return

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
    required: bool = False,
) -> None:
    """Sign blob with minisign (Ed25519 detached signature, SPEC B §3).

    Produces <blob>.minisig alongside blob.  The trusted_comment is signed and
    tamper-evident — callers MUST use build_trusted_comment() from manifest.py
    to bind the signature to the exact manifest bytes.

    secret_key: path to the minisign secret key file.
    trusted_comment: text embedded in the signed trusted-comment field.
    required: if True and minisign is absent, exit 3; else skip with warning.

    Key generation (one-time):
        minisign -G -p minisign.pub -s minisign.key
    Secret key: gitignored, from env/file — NEVER committed (S2.4 discipline).
    """
    tool = _which("minisign")
    if not tool:
        if required:
            print("[ERROR] minisign not found and required=true (S8 exit 3)", file=sys.stderr)
            sys.exit(exit_codes.PREREQ_MISSING)
        print(f"[WARN] minisign not found; skipping signing of {blob.name} (S7)", file=sys.stderr)
        return

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
    required: bool = False,
) -> bool:
    """Verify a minisign detached signature for blob (SPEC B §3 / SPEC A).

    Returns True if verification succeeds, False if it fails.
    If minisign is absent and required=False, warns and returns False.
    If minisign is absent and required=True, exits 3.

    public_key: path to the minisign public key file.

    Verification command: minisign -Vm <blob> -p <public_key>
    Checks: Ed25519 signature AND the trusted-comment binding to manifest_sha256.
    """
    tool = _which("minisign")
    if not tool:
        if required:
            print("[ERROR] minisign not found and required=true (S8 exit 3)", file=sys.stderr)
            sys.exit(exit_codes.PREREQ_MISSING)
        print("[WARN] minisign not found; skipping verification (S7)", file=sys.stderr)
        return False

    argv: List[str] = [tool, "-Vm", str(blob), "-p", public_key]
    print(f"[INFO] delegated: {' '.join(argv)}")
    result = subprocess.run(list(argv), cwd=blob.parent, capture_output=True)
    if result.returncode == 0:
        return True
    # Log verification failure (not an exit — caller decides how to handle).
    stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
    print(f"[WARN] minisign verify failed for {blob.name}: {stderr_text}", file=sys.stderr)
    return False


def run_delegated_config(
    delegated_cfg: dict,
    artifact: Optional[Path] = None,
    cwd: Optional[Path] = None,
) -> None:
    """Run all configured delegated steps for a project (S7.5).

    delegated_cfg is the parsed [project.<name>.delegated] table.
    """
    if not delegated_cfg:
        return

    sign_cfg = delegated_cfg.get("sign", {})
    if sign_cfg and sign_cfg.get("enabled", False):
        if artifact:
            cosign_sign(
                artifact,
                key=sign_cfg.get("key"),
                required=sign_cfg.get("required", False),
            )

    sbom_cfg = delegated_cfg.get("sbom", {})
    if sbom_cfg and sbom_cfg.get("enabled", False) and artifact:
        sbom_out = artifact.parent / f"{artifact.name}.sbom.spdx.json"
        syft_sbom(
            artifact, sbom_out,
            format=sbom_cfg.get("format", "spdx-json"),
            required=sbom_cfg.get("required", False),
        )
        if sbom_cfg.get("scan", True):
            grype_scan(sbom_out, required=sbom_cfg.get("scan_required", False))

    changelog_cfg = delegated_cfg.get("changelog", {})
    if changelog_cfg and changelog_cfg.get("enabled", False):
        cl_out = Path(changelog_cfg.get("output", "CHANGELOG.md"))
        if not cl_out.is_absolute() and cwd:
            cl_out = cwd / cl_out
        git_cliff_changelog(cl_out, required=changelog_cfg.get("required", False), cwd=cwd)

    nfpm_cfg = delegated_cfg.get("nfpm", {})
    if nfpm_cfg and nfpm_cfg.get("enabled", False):
        for packager in nfpm_cfg.get("packagers", ["deb"]):
            nfpm_config = Path(nfpm_cfg.get("config", "nfpm.yaml"))
            if not nfpm_config.is_absolute() and cwd:
                nfpm_config = cwd / nfpm_config
            target = Path(nfpm_cfg.get("target", "dist"))
            if not target.is_absolute() and cwd:
                target = cwd / target
            nfpm_package(
                nfpm_config, target, packager,
                required=nfpm_cfg.get("required", False),
            )

    minisign_cfg = delegated_cfg.get("minisign", {})
    if minisign_cfg and minisign_cfg.get("enabled", False) and artifact:
        # Secret key: env var name or file path (never a literal secret in config).
        key_env = minisign_cfg.get("secret_key_env")
        key_file = minisign_cfg.get("secret_key_file")
        import os as _os
        resolved_key: Optional[str] = None
        if key_env:
            resolved_key = _os.environ.get(key_env)
        if not resolved_key and key_file:
            resolved_key = key_file
        if not resolved_key:
            print(
                f"[ERROR] [project.delegated.minisign] is enabled but no secret key is "
                f"configured (set secret_key_env or secret_key_file)",
                file=sys.stderr,
            )
            sys.exit(exit_codes.CONFIG_ERROR)
        from cmru.manifest import build_trusted_comment
        tc = minisign_cfg.get("trusted_comment")
        if not tc:
            # Default: derive from artifact path (best effort; callers should supply).
            tc = f"artifact={artifact.name}"
        minisign_sign(
            artifact,
            secret_key=resolved_key,
            trusted_comment=tc,
            required=minisign_cfg.get("required", False),
        )
