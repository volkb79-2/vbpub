"""Resolver — pick highest-semver release for a project prefix (S5).

CLI: ciu-forge resolve --project <name> [--format json|env|url]

The resolver is differentiator #2: monorepo-safe per-project "latest",
replacing GitHub's single repo-global "Latest" badge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def resolve_via_latest_json(
    gh_releases_url: str,
    prefix: str,
) -> Optional[Dict[str, Any]]:
    """Try to fetch <prefix>-latest/latest.json for a fast single-request resolve (S5.3)."""
    from urllib.request import urlopen
    from urllib.error import HTTPError

    latest_tag = f"{prefix}-latest"
    latest_json_url = f"{gh_releases_url}/download/{latest_tag}/latest.json"
    try:
        with urlopen(latest_json_url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("version") and data.get("url"):
            return {
                "version": data["version"],
                "tag": data.get("tag"),
                "asset": data.get("asset"),
                "sha256": data.get("sha256"),
                "url": data["url"],
            }
    except (HTTPError, Exception):
        pass
    return None


def resolve(
    host,  # ReleaseHost
    prefix: str,
    *,
    use_latest_json: bool = True,
    gh_releases_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve the latest release for prefix using the host (S5).

    Returns {version, tag, asset, sha256, url} or None if no release exists.
    Tries latest.json first (S5.3) for speed, falls back to scanning releases (S5.4).
    """
    if use_latest_json and gh_releases_url:
        result = resolve_via_latest_json(gh_releases_url, prefix)
        if result:
            return result
    return host.resolve_latest(prefix)


def format_result(result: Dict[str, Any], fmt: str) -> str:
    """Format a resolve result for CLI output (S5.5).

    fmt: "json" (default) | "env" | "url"
    """
    if fmt == "url":
        return result.get("url") or ""
    if fmt == "env":
        lines = []
        prefix_env = result.get("tag", "").rsplit("-v", 1)[0].upper().replace("-", "_")
        lines.append(f"{prefix_env}_VERSION={result.get('version', '')}")
        lines.append(f"{prefix_env}_TAG={result.get('tag', '')}")
        lines.append(f"{prefix_env}_URL={result.get('url', '')}")
        if result.get("sha256"):
            lines.append(f"{prefix_env}_SHA256={result['sha256']}")
        return "\n".join(lines)
    return json.dumps(result, indent=2)


def resolve_main(argv: Optional[list] = None) -> None:
    """Entry point for ``ciu-forge resolve``."""
    import argparse
    parser = argparse.ArgumentParser(description="Resolve latest release for a project (S5)")
    parser.add_argument("--project", required=True, help="Project name (maps to prefix via config)")
    parser.add_argument("--prefix", help="Tag prefix override (e.g. tls-edge-v)")
    parser.add_argument("--format", choices=["json", "env", "url"], default="json")
    parser.add_argument("--config", help="Path to release.toml or ciu-forge.toml")
    args = parser.parse_args(argv)

    from cmru.hosts.github import github_host_from_env
    host = github_host_from_env()

    prefix = args.prefix
    if not prefix:
        if args.config:
            from cmru.cli import load_config
            from pathlib import Path as P
            result_tuple = load_config(P(args.config).expanduser().resolve())
            configs = result_tuple[1]
            proj = configs.get(args.project)
            prefix = (proj.prefix if proj else None) or f"{args.project}-v"
        else:
            prefix = f"{args.project}-v"

    result = resolve(host, prefix)
    if not result:
        print(f"[ERROR] No releases found for prefix '{prefix}'", file=sys.stderr)
        sys.exit(1)
    print(format_result(result, args.format))
