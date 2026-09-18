"""Resolver — pick highest-semver release for a project prefix (S5).

CLI: cmru resolve [all|project[,project...]] [--format json|env|url]

The resolver is differentiator #2: monorepo-safe per-project "latest",
replacing GitHub's single repo-global "Latest" badge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME


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

    The selected CMRU configuration supplies the project prefix plus GitHub identity.
    Tokens retain S2.4's explicit secret-source precedence; owner/repo are not
    guessed from an incomplete environment.
    """
    from cmru.cli_support import CMRUArgumentParser, TargetSelectionError, select_target_names
    parser = CMRUArgumentParser(description="Resolve latest release for registered projects (S5)")
    parser.add_argument(
        "target", nargs="?", metavar="[all|PROJECT[,PROJECT...]]",
        help="Project target; omitted uses the current project or estate default",
    )
    parser.add_argument("--format", choices=["json", "env", "url"], default="json")
    parser.add_argument(
        "--config", help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}"
    )
    args = parser.parse_args(argv)

    from cmru.cli import load_config, _resolve_config
    cfg_path = _resolve_config(args.config)
    result_tuple = load_config(cfg_path)
    configs = result_tuple[1]
    project_order = result_tuple[2]
    github_cfg = result_tuple[8]
    from cmru.config import resolve_invocation_context
    context = resolve_invocation_context(cfg_path)
    try:
        names = select_target_names(
            args.target, configs, project_order,
            context_project=context.project_name,
            estate_scope=context.scope == "estate",
        )
    except TargetSelectionError as exc:
        parser.error(str(exc))

    # Owner/repo are source facts in the strict config. ``load_config`` has
    # already resolved the one S2.4 credential contract, including a selected
    # project's local override; do not reopen a second credential precedence
    # path here.
    owner = github_cfg.owner
    repo = github_cfg.repo

    if not owner or not repo:
        parser.error("GitHub owner/repo unknown in the selected CMRU config")

    from cmru.hosts.github import GitHubReleaseHost

    # Build the Releases base URL so resolve() can try the fast latest.json path
    # (single request) before falling back to scanning the releases list (S5.3/S5.4).
    gh_releases_url = f"https://github.com/{owner}/{repo}/releases"

    results = {}
    for name in names:
        proj = configs[name]
        token = proj.github_token or github_cfg.token
        host = GitHubReleaseHost(owner=owner, repo=repo, token=token)
        result = resolve(host, proj.prefix, gh_releases_url=gh_releases_url)
        if not result:
            print(f"[ERROR] No releases found for project {name!r} (prefix {proj.prefix!r})", file=sys.stderr)
            sys.exit(1)
        results[name] = result
    if len(results) == 1:
        print(format_result(next(iter(results.values())), args.format))
    elif args.format == "json":
        print(json.dumps(results, indent=2, sort_keys=True))
    elif args.format == "env":
        print("\n\n".join(
            f"# Project: {name}\n{format_result(result, 'env')}"
            for name, result in results.items()
        ))
    else:
        print("\n".join(
            f"===== Resolve Project: {name.upper()} =====\n{format_result(result, 'url')}"
            for name, result in results.items()
        ))
