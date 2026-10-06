"""Resolver — pick highest-semver release for a project prefix (S5).

CLI: cmru resolve [all|project[,project...]] [--format json|env|url]

The resolver is differentiator #2: monorepo-safe per-project "latest",
replacing GitHub's single repo-global "Latest" badge.
"""
from __future__ import annotations

import http.client
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME
from cli_extended import (
    CliFailure,
    Conflicts,
    OptionSpec,
    Requires,
    VerbGroup,
    VerbSpec,
)
from cmru.cli_support import cmru_registry, target_argument


def resolve_via_latest_json(
    gh_releases_url: str,
    prefix: str,
) -> Optional[Dict[str, Any]]:
    """Try to fetch <prefix>-latest/latest.json for a fast single-request resolve (S5.3)."""
    from urllib.request import urlopen

    # The thin pointer tag is "<project>-latest" (e.g. "ciu-latest"), while the
    # resolver prefix is the full tag prefix "<project>-v" (e.g. "ciu-v"). Strip a
    # trailing "-v" so the latest.json URL is correct for both conventions.
    base = prefix[:-2] if prefix.endswith("-v") else prefix
    latest_tag = f"{base}-latest"
    latest_json_url = f"{gh_releases_url}/download/{latest_tag}/latest.json"
    try:
        with urlopen(latest_json_url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException):
        # Network failure (HTTPError/URLError/timeout are OSError; a truncated
        # or malformed response is http.client.HTTPException, e.g. IncompleteRead,
        # BadStatusLine) or a body that is not UTF-8 JSON: the fast path is unavailable, scan the releases
        # instead. A programming error is deliberately NOT swallowed here.
        return None
    if isinstance(data, dict) and data.get("version") and data.get("url"):
        return {
            "version": data["version"],
            "tag": data.get("tag"),
            "asset": data.get("asset"),
            "sha256": data.get("sha256"),
            "url": data["url"],
        }
    return None


def resolve(
    host,  # ReleaseHost
    prefix: str,
    *,
    use_latest_json: bool = True,
    gh_releases_url: Optional[str] = None,
    asset_suffix: str = "",
    variant: str = "",
) -> Optional[Dict[str, Any]]:
    """Resolve the latest release for prefix using the host (S5).

    Returns {version, tag, asset, sha256, url} or None if no release exists.
    Tries latest.json first (S5.3) for speed, falls back to scanning releases (S5.4).
    ``asset_suffix`` selects the primary asset by type (INS-18; ``variant`` one of several);
    an unreadable, malformed or (for an installer release) missing checksum sidecar raises
    instead of resolving with ``sha256=None``, and so does a malformed ``sha256`` in the
    latest.json pointer.
    """
    if use_latest_json and gh_releases_url:
        result = resolve_via_latest_json(gh_releases_url, prefix)
        if result:
            digest = result.get("sha256")
            if digest is not None and not (
                    isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest.lower())):
                raise RuntimeError(
                    f"latest.json for {prefix!r} carries a sha256 that is not 64 hex digits")
            if asset_suffix and digest is None:
                result = None  # an installer release needs its digest: scan the release
            else:
                return result
    kwargs: Dict[str, str] = {}
    if asset_suffix:
        kwargs["asset_suffix"] = asset_suffix
    if variant:
        kwargs["variant"] = variant
    return host.resolve_latest(prefix, **kwargs)


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


def resolve_cli():
    """Build the registered grammar for ``cmru resolve``.

    The selected CMRU configuration supplies the project prefix plus GitHub identity.
    Tokens retain S2.4's explicit secret-source precedence; owner/repo are not
    guessed from an incomplete environment.
    """
    registry = cmru_registry(
        "cmru resolve",
        "Resolve the latest published release for configured projects, "
        "or for an explicit --repo/--prefix without any configuration.",
        single_command=True,
        no_args_action=True,
    )
    registry.register(VerbSpec(
        "resolve",
        description="Resolve release version, tag, asset URL and digest.",
        group=VerbGroup.EXPLORATION.value,
        arguments=(target_argument(),),
        options=(
            # Accepted exception to the library --json control: shell consumers
            # need the env and url renderings (redesign B9).
            OptionSpec(
                ("--format",),
                "result format: json (object for one explicit project, map for "
                "'all' or a list), env or url (default: json)",
                metavar="FORMAT",
                parser_kwargs={"choices": ("json", "env", "url"), "default": "json"},
            ),
            OptionSpec(
                ("--config",),
                f"path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}",
                metavar="FILE", parser_kwargs={"default": None},
            ),
            OptionSpec(
                ("--repo",),
                "config-free mode: resolve in this GitHub repository (needs --prefix; "
                "token from $GITHUB_PUSH_PAT or $GITHUB_TOKEN, optional for public repos)",
                metavar="OWNER/REPO", parser_kwargs={"default": None},
            ),
            OptionSpec(
                ("--prefix",),
                "config-free mode: the release tag prefix, for example ciu-v (needs --repo)",
                metavar="PREFIX", parser_kwargs={"default": None},
            ),
        ),
        constraints=(
            Conflicts(("--repo", "--config"),
                      "config-free mode reads no configuration"),
            Requires("--repo", ("--prefix",), "a repository needs the tag prefix to resolve"),
            Requires("--prefix", ("--repo",), "a prefix needs the repository to resolve in"),
        ),
        include_json=False,
        include_progress=False,
        handler=_run_resolve,
    ))
    return registry.build()


_REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+")


def _resolve_config_free(args) -> int | None:
    """``--repo OWNER/REPO --prefix PREFIX``: no configuration is read (CLI-D2)."""
    from cmru.config import _environment_token
    from cmru.hosts.github import GitHubReleaseHost

    if args.target is not None:
        raise CliFailure(
            "--repo/--prefix resolve without configuration and cannot be combined "
            "with a project target",
            exit_code=2, show_help=True,
        )
    if not _REPO_RE.fullmatch(args.repo):
        raise CliFailure(
            f"--repo {args.repo!r} must look like OWNER/REPO", exit_code=2, show_help=True,
        )
    owner, repo = args.repo.split("/", 1)
    host = GitHubReleaseHost(owner=owner, repo=repo, token=_environment_token())
    try:
        result = resolve(
            host, args.prefix,
            gh_releases_url=f"https://github.com/{owner}/{repo}/releases",
        )
    except RuntimeError as exc:
        raise CliFailure(f"cannot resolve prefix {args.prefix!r}: {exc}") from exc
    except OSError as exc:
        # URLError and socket failures: registry metadata is unavailable, a missing
        # prerequisite (exit 3), not a crash. Found by the W2-PKG5 surface review.
        raise CliFailure(
            f"cannot reach {args.repo} to resolve prefix {args.prefix!r}: {exc}",
            exit_code=3,
        ) from exc
    if not result:
        raise CliFailure(f"No releases found in {args.repo} (prefix {args.prefix!r})")
    print(format_result(result, args.format))
    return None


def _run_resolve(args, _runtime) -> int | None:
    """Perform one fully parsed resolve invocation."""
    from cmru.delegate_targets import current_project, resolve_target

    if args.repo is not None:
        return _resolve_config_free(args)

    from cmru.cli import load_config, _resolve_config
    from cmru.config import load_forge_config
    cfg_path = _resolve_config(args.config)
    result_tuple = load_config(cfg_path)
    # The installer section lives on the strict ForgeConfig, the same loader
    # get-py uses for [project.installer]; cli.ProjectConfig has no such field.
    forge_projects = load_forge_config(cfg_path).projects
    configs = result_tuple[1]
    project_order = result_tuple[2]
    github_cfg = result_tuple[8]
    names = resolve_target(args.target, cfg_path, configs, project_order)
    # CLI-13: the JSON shape follows the selector SYNTAX, never the match count.
    # One explicit name (or the omitted "current project") is one object;
    # `all` or a list is a map, even when it happens to hold one project.
    if args.target is None:
        single = current_project(cfg_path, configs) is not None
    else:
        single = isinstance(args.target, tuple) and len(args.target) == 1

    # Owner/repo are source facts in the strict config. ``load_config`` has
    # already resolved the one S2.4 credential contract, including a selected
    # project's local override; do not reopen a second credential precedence
    # path here.
    owner = github_cfg.owner
    repo = github_cfg.repo

    if not owner or not repo:
        raise CliFailure(
            "GitHub owner/repo unknown in the selected CMRU config",
            exit_code=2,
            show_help=True,
        )

    from cmru.hosts.github import GitHubReleaseHost

    # Build the Releases base URL so resolve() can try the fast latest.json path
    # (single request) before falling back to scanning the releases list (S5.3/S5.4).
    gh_releases_url = f"https://github.com/{owner}/{repo}/releases"

    results = {}
    for name in names:
        proj = configs[name]
        token = proj.github_token or github_cfg.token
        host = GitHubReleaseHost(owner=owner, repo=repo, token=token)
        installer = forge_projects[name].installer
        try:
            result = resolve(
                host, proj.prefix, gh_releases_url=gh_releases_url,
                asset_suffix=installer.asset_suffix if installer else "",
            )
        except RuntimeError as exc:
            print(f"[ERROR] cannot resolve project {name!r}: {exc}", file=sys.stderr)
            return 1
        if not result:
            print(f"[ERROR] No releases found for project {name!r} (prefix {proj.prefix!r})", file=sys.stderr)
            return 1
        results[name] = result
    if single and len(results) == 1:
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
    return


def resolve_main(argv: Optional[list] = None) -> int:
    """Entry point for ``cmru resolve`` when called directly."""
    return resolve_cli().run(argv=argv)
