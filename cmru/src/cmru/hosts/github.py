"""GitHub ReleaseHost implementation (S11.2 v1).

Wraps the GitHubReleases REST client from cmru.release and implements
the ReleaseHost ABC. GH Enterprise is nearly free: pass api_base.

Convention: ``prefix`` in ReleaseHost methods is the full tag prefix
(e.g. "ciu-v", "tls-edge-v") — tags are ``<prefix><semver>``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cmru.hosts import ReleaseHost
from cmru.release import (
    GitHubReleases,
    _semver_key,
)


class GitHubReleaseHost(ReleaseHost):
    """ReleaseHost backed by GitHub Releases REST API."""

    def __init__(self, owner: str, repo: str, token: str, api_base: str = "https://api.github.com") -> None:
        self._gh = GitHubReleases(owner=owner, repo=repo, token=token, api_base=api_base)

    def create_release(
        self,
        tag: str,
        name: str,
        body: str,
        commitish: Optional[str] = None,
        draft: bool = False,
        prerelease: bool = False,
    ) -> str:
        rel = self._gh.create_release(tag, name, body, target_commitish=commitish)
        return str(rel.get("id", ""))

    def upload_asset(self, release_id: str, path: Path, content_type: str = "application/octet-stream") -> str:
        import json

        status, body = self._gh._request(
            "GET", self._gh._repo_url(f"/releases/{release_id}"))
        if status >= 400:
            self._gh._fail(f"get release {release_id}", status, body)
        rel = json.loads(body)
        upload_url = rel.get("upload_url", "")
        self._gh.upload_asset(upload_url, path, path.name)
        return self._gh.asset_download_url(rel.get("tag_name", ""), path.name)

    def list_releases(self, prefix: str) -> List[Dict[str, Any]]:
        """List releases whose tag starts with prefix (e.g. "ciu-v")."""
        out = []
        for rel in self._gh.list_releases():
            tag = rel.get("tag_name", "")
            if not tag.startswith(prefix) or rel.get("draft") or rel.get("prerelease"):
                continue
            out.append({
                "tag": tag,
                "id": str(rel.get("id", "")),
                "assets": [
                    {"name": a.get("name"), "url": a.get("browser_download_url")}
                    for a in rel.get("assets", [])
                ],
            })
        return out

    def resolve_latest(
        self, prefix: str, asset_suffix: str = "", variant: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Highest-semver release for prefix (e.g. "ciu-v"); returns {version,tag,asset,sha256,url} (S5).

        ``asset_suffix`` (the project's installer ``asset_suffix``, e.g. ".tar.xz") selects the
        primary asset by type instead of by API order; with several matches the choice is
        deterministic: ``<tag>-<variant><suffix>`` when ``variant`` is given, else
        ``<tag><suffix>``, else the first in sorted order. A checksum sidecar that exists but
        cannot be fetched or parsed raises ``RuntimeError`` (never ``sha256=None``), and so
        does a release WITHOUT a sidecar when ``asset_suffix`` is given (an installer project:
        its get.py refuses a bundle without one)."""
        candidates = []
        for rel in self._gh.list_releases():
            tag = rel.get("tag_name", "")
            if not tag.startswith(prefix) or rel.get("draft") or rel.get("prerelease"):
                continue
            version = tag[len(prefix):]
            assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
            candidates.append((version, tag, assets))
        if not candidates:
            return None
        version, tag, assets = max(candidates, key=lambda c: _semver_key(c[0]))

        # The primary asset: with `asset_suffix` the one asset of that type (never a
        # sidecar), else the first non-sidecar, non-latest.json asset in API order.
        matching = [
            n for n in assets
            if n and not n.endswith(".sha256") and n != "latest.json"
            and (not asset_suffix or n.endswith(asset_suffix))
        ]
        if asset_suffix and (len(matching) > 1 or variant):
            wanted = f"{tag}-{variant}{asset_suffix}" if variant else f"{tag}{asset_suffix}"
            if wanted in matching:
                matching = [wanted]
            else:
                matching = [] if variant else sorted(matching)
        asset_name = matching[0] if matching else None
        if not asset_name:
            return None

        sha256_url = assets.get(f"{asset_name}.sha256")
        sha256_val: Optional[str] = None
        if not sha256_url and asset_suffix:
            raise RuntimeError(
                f"release {tag} has no {asset_name}.sha256 asset; an installer release "
                "must publish its checksum sidecar")
        if sha256_url:
            # A published sidecar that cannot be read, or does not hold a digest, is an
            # error: the caller must never get an unverified "latest" silently (INS-18).
            from urllib.request import urlopen
            try:
                with urlopen(sha256_url, timeout=10) as resp:
                    line = resp.read().decode("utf-8").strip()
            except Exception as exc:
                raise RuntimeError(
                    f"cannot read the checksum sidecar {asset_name}.sha256 of {tag}: {exc}"
                ) from exc
            first = line.split()[0].lower() if line else ""
            if not re.fullmatch(r"[0-9a-f]{64}", first):
                raise RuntimeError(
                    f"checksum sidecar {asset_name}.sha256 of {tag} does not start with a "
                    f"SHA-256 hex digest")
            sha256_val = first

        return {
            "version": version,
            "tag": tag,
            "asset": asset_name,
            "sha256": sha256_val,
            "url": assets.get(asset_name),
        }

    def download_url(self, tag: str, asset_name: str) -> str:
        return self._gh.asset_download_url(tag, asset_name)


def github_host_from_env() -> GitHubReleaseHost:
    """Build a GitHubReleaseHost from GITHUB_USERNAME / GITHUB_REPO / GITHUB_PUSH_PAT env vars."""
    import os
    owner = os.environ.get("GITHUB_USERNAME") or ""
    repo = os.environ.get("GITHUB_REPO") or ""
    token = os.environ.get("GITHUB_PUSH_PAT") or ""
    return GitHubReleaseHost(owner=owner, repo=repo, token=token)
