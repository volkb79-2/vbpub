#!/usr/bin/env python3
"""Publish the tls-edge artifact to GitHub Releases.

Routes through the shared keystone (release-manager/src/release_manager/github_release.py)
so the release scheme stays uniform across all vbpub projects.

Required credentials (from release.toml [github] or environment):
  GITHUB_PUSH_PAT
  GITHUB_USERNAME
  GITHUB_REPO  (default: vbpub)

Reads VERSION from tls-edge/VERSION (written by scripts/release.sh).
Expects dist/tls-edge-v<VERSION>.tar.xz to exist (built by build-artifact.sh).

Publish strategy (delegated to publish_versioned in the keystone):
  - Immutable release  tls-edge-v<version>  with the versioned tarball + .sha256 sidecar.
  - Thin pointer       tls-edge-latest       containing only latest.json (no asset dup).
  - SHA256 written to release notes for reproducibility verification.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Keystone import ──────────────────────────────────────────────────────────
# parents[2] from tls-edge/scripts/publish-release.py == the vbpub repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "release-manager" / "src"))
from release_manager.github_release import GitHubReleases, publish_versioned

TLS_EDGE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = TLS_EDGE_DIR / "VERSION"
RELEASE_VARS_FILE = TLS_EDGE_DIR / ".release-vars"
DIST_DIR = TLS_EDGE_DIR / "dist"


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_kv_file(path: Path, *, strip_quotes: bool = False) -> None:
    """Load KEY=VALUE lines from a file into os.environ (setdefault — env wins)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if strip_quotes:
            value = value.strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_release_toml_credentials(repo_root: Path) -> None:
    """Populate GITHUB_USERNAME / GITHUB_PUSH_PAT / GITHUB_REPO from release.toml."""
    release_toml = repo_root / "release.toml"
    if not release_toml.exists():
        return
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return  # Best-effort; fallback to env vars / .release-vars
    try:
        with release_toml.open("rb") as fh:
            config = tomllib.load(fh)
        github = config.get("github", {})
        if not os.environ.get("GITHUB_USERNAME") and github.get("username"):
            os.environ["GITHUB_USERNAME"] = str(github["username"])
        if not os.environ.get("GITHUB_PUSH_PAT") and github.get("token"):
            os.environ["GITHUB_PUSH_PAT"] = str(github["token"])
        if not os.environ.get("GITHUB_REPO") and github.get("repo"):
            os.environ["GITHUB_REPO"] = str(github["repo"])
    except Exception as exc:
        print(f"[WARN] Could not parse release.toml: {exc}", file=sys.stderr)


def read_version() -> str:
    if not VERSION_FILE.exists():
        fail(f"VERSION file not found: {VERSION_FILE}")
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        fail("VERSION file is empty.")
    return version


def find_tarball(dist_dir: Path, version: str) -> Path:
    expected = dist_dir / f"tls-edge-v{version}.tar.xz"
    if expected.exists():
        return expected
    candidates = sorted(dist_dir.glob("tls-edge-v*.tar.xz"))
    if not candidates:
        fail(
            f"No artifact found in {dist_dir}.\n"
            "  Run:  bash scripts/build-artifact.sh"
        )
    if len(candidates) > 1:
        fail(
            f"Multiple tls-edge tarballs in {dist_dir}: {[c.name for c in candidates]}.\n"
            "  Clean dist/ and re-run build-artifact.sh."
        )
    found = candidates[0]
    log(f"Expected tls-edge-v{version}.tar.xz; found {found.name} — using it.")
    return found


def main() -> None:
    # ── Credential resolution order: env > .release-vars > release.toml ──────
    load_kv_file(RELEASE_VARS_FILE)
    repo_root = TLS_EDGE_DIR.parent
    for env_path in [repo_root / ".env", TLS_EDGE_DIR / ".env"]:
        load_kv_file(env_path, strip_quotes=True)
    load_release_toml_credentials(repo_root)

    version = read_version()
    log(f"Version: {version}")

    token = os.environ.get("GITHUB_PUSH_PAT", "")
    if not token:
        fail(
            "GITHUB_PUSH_PAT is required.\n"
            "  Set it in the environment, release.toml [github].token, or tls-edge/.release-vars."
        )
    owner = os.environ.get("GITHUB_USERNAME", "")
    if not owner:
        fail(
            "GITHUB_USERNAME is required.\n"
            "  Set it in the environment or release.toml [github].username."
        )
    repo = os.environ.get("GITHUB_REPO", "vbpub")

    tarball = find_tarball(DIST_DIR, version)
    log(f"Artifact: {tarball.name}  ({tarball.stat().st_size:,} bytes)")

    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh,
        prefix="tls-edge",
        version=version,
        asset_path=tarball,
        notes=f"tls-edge {version}",
        latest_pointer=True,
    )

    log(f"TLS_EDGE_ARTIFACT_SHA256={result['sha256']}")
    if result.get("asset_url"):
        log(f"TLS_EDGE_ARTIFACT_URL={result['asset_url']}")
    if result.get("release_tag"):
        log(f"Published: {result['release_tag']}")


if __name__ == "__main__":
    main()
