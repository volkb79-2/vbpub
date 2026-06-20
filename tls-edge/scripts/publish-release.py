#!/usr/bin/env python3
"""tls-edge publish-release — upload the built artifact to GitHub Releases.

Called by scripts/release.sh after build-artifact.sh.
Routes through cmru.release for the uniform monorepo release scheme.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Resolve cmru from the monorepo root (works both inside and outside a venv)
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]           # …/tls-edge/scripts/.. /.. → vbpub/
_CMRU_SRC  = _REPO_ROOT / "cmru" / "src"
if str(_CMRU_SRC) not in sys.path:
    sys.path.insert(0, str(_CMRU_SRC))

from cmru.release import GitHubReleases, publish_versioned  # noqa: E402

TLS_EDGE_ROOT = _HERE.parents[1]
DIST_DIR      = TLS_EDGE_ROOT / "dist"
VERSION_FILE  = TLS_EDGE_ROOT / "VERSION"
REPO_OWNER    = "volkb79-2"
REPO_NAME     = "vbpub"
PREFIX        = "tls-edge"


def _get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        try:
            import tomllib
            secret = _REPO_ROOT / "cmru.secret.toml"
            if secret.exists():
                with open(secret, "rb") as f:
                    token = tomllib.load(f).get("github", {}).get("token", "")
        except Exception:
            pass
    if not token:
        print(
            "[ERROR] GitHub token not found.\n"
            "  Set GITHUB_TOKEN env var, or add [github] token to cmru.secret.toml.",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def main() -> None:
    if not VERSION_FILE.exists():
        print(f"[ERROR] VERSION file not found: {VERSION_FILE}", file=sys.stderr)
        sys.exit(1)

    version  = VERSION_FILE.read_text().strip()
    tag      = f"tls-edge-v{version}"
    asset    = DIST_DIR / f"{tag}.tar.xz"

    if not asset.exists():
        print(
            f"[ERROR] Artifact not found: {asset}\n"
            "  Run scripts/build-artifact.sh first.",
            file=sys.stderr,
        )
        sys.exit(1)

    token = _get_token()
    gh    = GitHubReleases(REPO_OWNER, REPO_NAME, token)

    print(f"[INFO] Publishing tls-edge v{version} ({asset.name}) …")
    result = publish_versioned(
        gh,
        prefix=PREFIX,
        version=version,
        asset_path=asset,
    )
    print(f"[INFO] Release tag : {result.get('release_tag')}")
    print(f"[INFO] Asset URL   : {result.get('asset_url')}")


if __name__ == "__main__":
    main()
