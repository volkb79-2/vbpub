"""Resolver — pick highest-semver release for a project prefix (S5).

CLI: cmru resolve --project <name> [--format json|env|url]

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

    # The thin pointer tag is "<project>-latest" (e.g. "ciu-latest"), while the
    # resolver prefix is the full tag prefix "<project>-v" (e.g. "ciu-v"). Strip a
    # trailing "-v" so the latest.json URL is correct for both conventions.
    base = prefix[:-2] if prefix.endswith("-v") else prefix
    latest_tag = f"{base}-latest"
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
    """Entry point for ``cmru resolve``.

    Identity (owner/repo/token) is resolved the same way as the rest of cmru:
    environment wins (``GITHUB_USERNAME`` / ``GITHUB_REPO`` / ``GITHUB_PUSH_PAT`` |
    ``GITHUB_TOKEN``), otherwise it falls back to ``[github]`` in ``cmru.toml`` (S2.4).
    So a bare ``cmru resolve --project cmru`` works with no env exported.
    """
    import argparse
    import os
    parser = argparse.ArgumentParser(description="Resolve latest release for a project (S5)")
    parser.add_argument("--project", help="Project name (maps to prefix via config)")
    parser.add_argument("--prefix", help="Tag prefix override (e.g. ciu-v, tls-edge-v)")
    parser.add_argument("--format", choices=["json", "env", "url"], default="json")
    parser.add_argument("--config", help="Path to cmru.toml")
    args = parser.parse_args(argv)

    if not args.project and not args.prefix:
        parser.error("one of --project or --prefix is required")

    # Load cmru.toml once: it supplies the prefix (project → tag prefix) AND the
    # owner/repo/token identity fallback. Tolerate a missing/broken config so a
    # full env + --prefix invocation still works without any cmru.toml present.
    configs: dict = {}
    github_cfg = None
    try:
        from cmru.cli import load_config, _resolve_config

        cfg_path = _resolve_config(args.config)
        if cfg_path.exists():
            result_tuple = load_config(cfg_path)
            configs = result_tuple[1]
            github_cfg = result_tuple[8]  # GitHubConfig(username, repo, token, owner_type)
    except Exception as exc:  # config is best-effort here — env can still satisfy us
        print(f"[WARN] could not load cmru.toml ({exc}); relying on env", file=sys.stderr)

    # S2.4 precedence: env first, then cmru.toml's resolved values.
    owner = os.environ.get("GITHUB_USERNAME") or (github_cfg.username if github_cfg else "")
    repo = os.environ.get("GITHUB_REPO") or (github_cfg.repo if github_cfg else "")
    token = (
        os.environ.get("GITHUB_PUSH_PAT")
        or os.environ.get("GITHUB_TOKEN")
        or (github_cfg.token if github_cfg else "")
    )

    prefix = args.prefix
    if not prefix:
        proj = configs.get(args.project) if args.project else None
        prefix = (proj.prefix if proj else None) or f"{args.project}-v"

    if not owner or not repo:
        print(
            "[ERROR] GitHub owner/repo unknown — set [github] owner/repo in cmru.toml "
            "or export GITHUB_USERNAME / GITHUB_REPO",
            file=sys.stderr,
        )
        sys.exit(2)

    from cmru.hosts.github import GitHubReleaseHost

    host = GitHubReleaseHost(owner=owner, repo=repo, token=token)

    # Build the Releases base URL so resolve() can try the fast latest.json path
    # (single request) before falling back to scanning the releases list (S5.3/S5.4).
    gh_releases_url = f"https://github.com/{owner}/{repo}/releases"

    result = resolve(host, prefix, gh_releases_url=gh_releases_url)
    if not result:
        print(f"[ERROR] No releases found for prefix '{prefix}'", file=sys.stderr)
        sys.exit(1)
    print(format_result(result, args.format))
