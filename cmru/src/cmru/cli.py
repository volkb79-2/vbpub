#!/usr/bin/env python3
"""Unified release orchestration for vbpub projects."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Mapping, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import tomllib

from cmru.runner import StepConfig, execute_step, parse_step as _runner_parse_step
from cmru import transaction
from cmru import exit_codes
from cmru.config import load_forge_config
from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME
from cmru.dependencies import build_report, render_text as render_dependency_report
from cmru.output import consume_cli_flags


@dataclass(frozen=True)
class Command:
    label: str
    argv: List[str]
    cwd: Path


@dataclass(frozen=True)
class VersionSpec:
    """Per-project versioning rules (S12). Defaults match cmru's historical behaviour."""
    strategy: str = "scm"            # "scm" | "counter" | "file:<PATH>"
    bump: str = "conventional"       # "conventional" | "patch"
    paths: tuple = ()                # extra subtrees to watch for change detection
    base_version: str = "1.0.0"      # counter strategy: <base>-r<N>
    file: str = "VERSION"            # filename for direct file-strategy construction


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    env: Mapping[str, str]
    steps: Mapping[str, List[Command]]
    template_revision: Optional[int] = None
    prefix: Optional[str] = None    # git tag prefix, e.g. "ciu-v"  (S12; required for auto-version)
    scm_dist: Optional[str] = None  # setuptools dist name for SETUPTOOLS_SCM_PRETEND_VERSION_FOR_*
    cwd: Optional[str] = None       # build working dir (relative to repo root); default = name
    version: Optional[VersionSpec] = None
    paths: Optional[List[str]] = None  # change-detection watch paths; default = [cwd]
    # Declared released-output vocabulary. It is descriptive and retention-facing;
    # an artifact name never selects a runner or a publication implementation.
    artifacts: tuple = ()           # e.g. ("wheel",) | ("oci-image",) | ("oci-image", "bundle")
    git_tag: bool = True            # does cmru mint+push <prefix><semver> at HEAD?
    commit_generated: tuple = ()    # project-relative paths cmru commits after build
    # Every managed project gets source-first release history unless it explicitly
    # declines it with ``[project.release] changelog = false``.
    changelog: Optional[str] = "CHANGES.md"
    project_root: Optional[Path] = None  # absolute directory containing this project's cmru.toml
    runner_steps: Mapping[str, StepConfig] = None  # strict project-local runner controls
    build_metadata: Mapping[str, str] = None
    artifact_dirs: tuple[str, ...] = ()  # declared project-relative output directories
    build_step: str = ""                # explicit [project.release].build_step
    github_token: str = ""              # root credential or explicit project-secret override
    # First-party artifacts this project's own tests/tooling consume (S15). Empty ⇒
    # no declared tool dependency (today's behaviour, unchanged).
    tool_dependencies: tuple = ()


@dataclass(frozen=True)
class CleanupConfig:
    release_tag_prefixes: List[str]
    keep_release_tags: List[str]
    ghcr_packages: List[str]
    ghcr_delete_packages: List[str]


@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    token: str
    owner_type: str  # required: "user" | "org"  (V03; replaces the modern-debian-tools probe)


@dataclass(frozen=True)
class ReleaseEnvConfig:
    env: Mapping[str, str]
    registry_url: Optional[str]


def log_info(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def log_warn(message: str) -> None:
    print(f"[WARN] {message}", flush=True)


def log_error(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr, flush=True)


def _apply_output_options(args: object) -> None:
    """Carry explicit console/logging choices into all child step processes."""
    if getattr(args, "show_run_details", False):
        os.environ["CMRU_SHOW_RUN_DETAILS"] = "1"
    if getattr(args, "log_append", False):
        os.environ["CMRU_LOG_APPEND"] = "1"


def parse_duration(value: str) -> timedelta:
    value = value.strip().lower().replace(" ", "")
    if not value:
        raise ValueError("Duration value is empty")

    units = {
        "s": 1,
        "sec": 1,
        "secs": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "mins": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "w": 604800,
        "week": 604800,
        "weeks": 604800,
    }

    total_seconds = 0
    idx = 0
    while idx < len(value):
        if not value[idx].isdigit():
            raise ValueError(f"Invalid duration syntax: {value}")
        num_start = idx
        while idx < len(value) and value[idx].isdigit():
            idx += 1
        number = int(value[num_start:idx])
        unit_start = idx
        while idx < len(value) and value[idx].isalpha():
            idx += 1
        unit = value[unit_start:idx]
        if unit not in units:
            raise ValueError(f"Unknown duration unit '{unit}' in {value}")
        total_seconds += number * units[unit]

    if total_seconds <= 0:
        raise ValueError(f"Duration must be positive: {value}")
    return timedelta(seconds=total_seconds)


def http_request(method: str, url: str, token: str) -> tuple[int, str, dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    req = Request(url, method=method, headers=headers)
    try:
        with urlopen(req) as response:
            body = response.read().decode("utf-8")
            return response.status, body, dict(response.headers)
    except HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        return exc.code, body, dict(exc.headers or {})


def load_json(url: str, token: str) -> tuple[list, dict]:
    status, body, headers = http_request("GET", url, token)
    if status >= 400:
        raise RuntimeError(f"GitHub API error {status}: {body}")
    if not body.strip():
        return [], headers
    return json.loads(body), headers


def _build_step_config(step_name: str, commands: List[Command]) -> StepConfig:
    """Convert orchestrator Command objects to a StepConfig for the unified runner.

    Every project subprocess uses a durable detailed log and concise outer
    progress. ``--show-run-details`` disables this policy for an interactive
    diagnosis without changing project configuration.
    """
    return StepConfig(
        name=step_name,
        commands=[
            {"label": cmd.label, "argv": cmd.argv, "cwd": str(cmd.cwd)}
            for cmd in commands
        ],
        bake_set_prefix=None,
        bake_set_vars=[],
        no_cache_env=None,
        clean_dirs=[],
        required_env=[],
        login=None,
        step_env={},
        env_command=None,
        quiet=True,
    )


def run_project_step(
    project: "ProjectConfig",
    step_name: str,
    repo_root: Path,
    log_dir: Path,
) -> None:
    """Route a project step through the unified runner contract (S3).

    Every phase is declared in the project's strict ``[steps.<name>]`` contract.
    CMRU never synthesizes an artifact-specific command or silently skips a
    requested phase."""
    # ``cwd`` is derived from the declared project config's location by
    # load_config. Derive the execution root again from the selected repository
    # snapshot so the same contract always targets the child worktree, never the
    # caller checkout.
    if not project.cwd:
        raise RuntimeError(f"{project.name}: derived project working directory is absent")
    project_root = resolve_cwd(repo_root, project.cwd)
    step = (project.runner_steps or {}).get(step_name)
    if step is None:
        raise RuntimeError(f"{project.name}: required declared step {step_name!r} is absent")
    # Detailed records are project-local.  A successful release removes the
    # transaction worktree (and therefore these logs) unless the caller elected
    # to retain them after verified completion.
    stable_log_root = project_root / "logs" / "cmru"
    execute_step(
        step,
        project_root,
        stable_log_root,
        extra_env=dict(project.env) if project.env else None,
        build_metadata=project.build_metadata,
    )


def resolve_repo_root(config_path: Path, raw_value: str) -> Path:
    repo = Path(raw_value)
    if repo.is_absolute():
        return repo
    return (config_path.parent / repo).resolve()


def resolve_cwd(repo_root: Path, raw_cwd: str) -> Path:
    cwd_path = Path(raw_cwd)
    if cwd_path.is_absolute():
        return cwd_path
    return (repo_root / cwd_path).resolve()


def parse_commands(config_path: Path, repo_root: Path, step_name: str, raw_commands: list) -> List[Command]:
    if not raw_commands:
        raise ValueError(f"Step '{step_name}' must define at least one command")
    commands: List[Command] = []
    for idx, command in enumerate(raw_commands, start=1):
        if not isinstance(command, dict):
            raise ValueError(f"Step '{step_name}' command {idx} must be a table")
        label = command.get("label")
        argv = command.get("argv")
        cwd = command.get("cwd")
        if not label or not isinstance(label, str):
            raise ValueError(f"Step '{step_name}' command {idx} missing label")
        if not argv or not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ValueError(f"Step '{step_name}' command {idx} must define argv list")
        if not cwd or not isinstance(cwd, str):
            raise ValueError(f"Step '{step_name}' command {idx} missing cwd")
        commands.append(Command(label=label, argv=argv, cwd=resolve_cwd(repo_root, cwd)))
    return commands


def _parse_version_spec(raw: object, name: str) -> Optional[VersionSpec]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"project.{name}.version must be a table")
    strategy = str(raw.get("strategy") or "scm").strip()
    bump = str(raw.get("bump") or "conventional").strip()
    if bump not in ("conventional", "patch"):
        raise ValueError(f"project.{name}.version.bump must be 'conventional' or 'patch'")
    paths = tuple(str(p) for p in (raw.get("paths") or []))
    base_version = str(raw.get("base_version") or "1.0.0").strip()
    version_file = str(raw.get("file") or "VERSION").strip()
    return VersionSpec(strategy=strategy, bump=bump, paths=paths,
                       base_version=base_version, file=version_file)


def _parse_release_policy(
    project: dict, name: str, version_spec: Optional[VersionSpec]
) -> tuple[tuple, bool, tuple]:
    """Resolve (artifacts, git_tag, commit_generated) for a project (S-REL).

    - artifacts: canonical ``[project].artifacts`` list.
    - git_tag: explicit ``[project.release].git_tag``. Artifact kind never
      silently decides publication semantics.
    - commit_generated: ``[project.release].commit_generated`` (project-relative).
    """
    raw = project.get("artifacts")
    if not isinstance(raw, list):
        raise ValueError(f"project.{name}.artifacts must be a list")
    artifacts: list[str] = []
    for item in raw:
        value = str(item).strip()
        if value:
            artifacts.append(value)
    valid_artifacts = {"wheel", "bundle", "tarball", "oci-image"}
    unknown = [a for a in artifacts if a not in valid_artifacts]
    if unknown:
        raise ValueError(
            f"project.{name}: unknown artifact type {unknown}; "
                f"valid: {sorted(valid_artifacts)}"
        )

    strategy = getattr(version_spec, "strategy", "scm") if version_spec else "scm"

    release_cfg = project.get("release") or {}
    if not isinstance(release_cfg, dict):
        raise ValueError(f"project.{name}.release must be a table")
    if not isinstance(release_cfg.get("git_tag"), bool):
        raise ValueError(f"project.{name}.release.git_tag must be explicitly true or false")
    git_tag = release_cfg["git_tag"]
    commit_generated = release_cfg.get("commit_generated") or []
    if not isinstance(commit_generated, list):
        raise ValueError(f"project.{name}.release.commit_generated must be a list")

    if strategy == "none" and git_tag:
        raise ValueError(
            f"project.{name}: version.strategy='none' requires release.git_tag=false"
        )

    return tuple(artifacts), git_tag, tuple(str(p) for p in commit_generated)


def _bare_prefix(prefix: Optional[str]) -> str:
    """`ciu-v` → `ciu` for release-cleanup API calls."""
    prefix = prefix or ""
    return prefix[:-2] if prefix.endswith("-v") else prefix


def load_config(
    config_path: Path,
    *,
    validate_dependencies: bool = True,
) -> tuple[
    Path,
    dict[str, ProjectConfig],
    list[str],
    list[str],
    list[str],
    str,
    dict[str, list[str]],
    CleanupConfig,
    GitHubConfig,
    ReleaseEnvConfig,
]:
    """Map the strict project documents into the execution model.

    ``load_forge_config`` owns schema validation; orchestration loads additionally
    run the dependency preflight before this execution model is returned. This
    function deliberately only maps that validated grammar; it accepts no
    retired central ``[project.<name>]`` shape and no second build-runner document.
    """
    forge = load_forge_config(config_path)
    if validate_dependencies and config_path.name == ORCHESTRATION_CONFIG_FILENAME:
        report = build_report(
            repo_root=forge.repo_root,
            project_order=forge.orchestration.project_order,
            declared=forge.orchestration.dependencies,
            projects=forge.projects,
        )
        if report.errors:
            for error in report.errors:
                print(f"[ERROR] dependency preflight: {error}", flush=True)
            raise SystemExit(exit_codes.CONFIG_ERROR)
    orchestration = forge.orchestration
    if orchestration is None:  # defensive: both strict loaders always supply one
        raise ValueError("cmru configuration has no project selection")
    repo_root = forge.repo_root
    projects: dict[str, ProjectConfig] = {}
    for name, parsed in forge.projects.items():
        project_config_path = orchestration.project_configs[name]
        with project_config_path.open("rb") as handle:
            document = tomllib.load(handle)
        project_raw = document["project"]
        steps_raw = document["steps"]
        project_root = project_config_path.parent.resolve()
        try:
            project_rel = project_root.relative_to(repo_root)
        except ValueError as exc:  # should already be prohibited by config validation
            raise ValueError(f"{name}: project root is outside orchestration root") from exc
        cwd = project_rel.as_posix() if project_rel.parts else "."
        version_spec = _parse_version_spec(project_raw["version"], name)
        artifacts, git_tag, commit_generated = _parse_release_policy(
            project_raw, name, version_spec
        )
        commands = {
            step_name: parse_commands(project_config_path, project_root, step_name, step_raw["commands"])
            for step_name, step_raw in steps_raw.items()
        }
        runner_steps = {
            step_name: replace(
                _runner_parse_step({"steps": steps_raw}, step_name),
                registries=list(forge.targets.registry),
            )
            for step_name in steps_raw
        }
        extra_paths = list(version_spec.paths) if version_spec else []
        watch_paths = [cwd] + [
            (project_rel / Path(path)).as_posix() if project_rel.parts else path
            for path in extra_paths
        ]
        projects[name] = ProjectConfig(
            name=name, template_revision=parsed.template_revision, env=parsed.env,
            steps=commands, prefix=parsed.prefix, scm_dist=parsed.scm_dist,
            cwd=cwd, version=version_spec, paths=watch_paths,
            artifacts=artifacts, git_tag=git_tag, commit_generated=commit_generated,
            changelog=parsed.changelog, project_root=project_root,
            runner_steps=runner_steps, build_metadata=parsed.build_metadata,
            artifact_dirs=tuple(parsed.artifact_dirs),
            build_step=parsed.build_step,
            github_token=forge.project_tokens.get(name, forge.github.token or ""),
            tool_dependencies=tuple(parsed.tool_dependencies),
        )

    cleanup_raw = forge.cleanup
    cleanup = CleanupConfig(
        release_tag_prefixes=list(cleanup_raw.release_tag_prefixes) if cleanup_raw else [],
        keep_release_tags=list(cleanup_raw.keep_release_tags) if cleanup_raw else [],
        ghcr_packages=list(cleanup_raw.ghcr_packages) if cleanup_raw else [],
        ghcr_delete_packages=list(cleanup_raw.ghcr_delete_packages) if cleanup_raw else [],
    )
    github_config = GitHubConfig(
        owner=forge.github.owner, repo=forge.github.repo,
        token=forge.github.token or "", owner_type=forge.github.owner_type,
    )
    env_config = ReleaseEnvConfig(
        env=forge.env,
        registry_url=forge.targets.registry[0] if forge.targets.registry else None,
    )

    return (
        repo_root,
        projects,
        orchestration.project_order,
        orchestration.default_projects,
        orchestration.default_steps,
        orchestration.execution_mode,
        {},
        cleanup,
        github_config,
        env_config,
    )


def apply_release_env(github: GitHubConfig, env_config: ReleaseEnvConfig) -> None:
    if github.owner:
        os.environ["GITHUB_USERNAME"] = github.owner
    if github.repo:
        os.environ["GITHUB_REPO"] = github.repo
    # Each project operation receives exactly its already-resolved credential.
    # This process can run several selected projects sequentially; merely
    # skipping the assignment for a project with no credential leaves the
    # previous project's token in the child environment.  Clear both accepted
    # input spellings first, then export the one canonical handler spelling.
    # Config loading has already captured an explicitly supplied environment
    # credential, so this cannot discard a source of truth before resolution.
    os.environ.pop("GITHUB_PUSH_PAT", None)
    os.environ.pop("GITHUB_TOKEN", None)
    if github.token:
        os.environ["GITHUB_PUSH_PAT"] = github.token
    os.environ["GITHUB_OWNER_TYPE"] = github.owner_type
    if env_config.registry_url:
        os.environ["REGISTRY"] = env_config.registry_url

    for key, value in env_config.env.items():
        if value is None:
            continue
        # An explicit empty value clears an inherited shell value.  Skipping it
        # would silently turn a declared project environment into ambient
        # fallback state.
        os.environ[key] = str(value)


def github_for_project(github: GitHubConfig, project: ProjectConfig) -> GitHubConfig:
    """Bind the repository credential to one explicitly selected project.

    Metadata is repository-wide.  ``ProjectConfig.github_token`` is already
    resolved from the invocation environment or the strict root-plus-project
    secret overlay, so this function never discovers a credential by fallback.
    """
    return replace(github, token=project.github_token)


def apply_project_release_env(
    github: GitHubConfig, env_config: ReleaseEnvConfig, project: ProjectConfig,
) -> None:
    # The project document is the authority for project-specific process
    # settings.  An orchestration run supplies an explicit estate policy in
    # ``[orchestration.defaults.env]``; preserve the direct-project shape as a
    # merge so both entry points obey one contract.
    project_env = dict(env_config.env)
    project_env.update(project.env)
    apply_release_env(
        github_for_project(github, project),
        replace(env_config, env=project_env),
    )


def require_project_publish_credentials(
    configs: Mapping[str, ProjectConfig], project_names: List[str],
) -> None:
    missing = [name for name in project_names if not configs[name].github_token.strip()]
    if missing:
        raise RuntimeError(
            "Publishing requires GITHUB_PUSH_PAT/GITHUB_TOKEN, repository-root "
            "cmru.secret.toml, or an explicit project cmru.secret.toml override "
            "for project(s): " + ", ".join(missing)
        )


def _git(repo_root: Path, *args: str) -> str:
    """Run ``git <args>`` under *repo_root* and return stripped stdout.

    An empty stdout is a successful and meaningful result for several Git
    queries (for example a clean ``git diff --name-only``).  Preserve it as an
    empty string rather than conflating it with a failed invocation.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable is unavailable") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def resolve_versions_from_git(
    repo_root: Path,
    projects: Optional[Mapping[str, "ProjectConfig"]] = None,
) -> None:
    """Export reproducible-build + git-derived version env for every project (no clock).

    - ``SOURCE_DATE_EPOCH`` = HEAD commit time → reproducible wheel/image timestamps.
    - ``OCI_REVISION`` / ``OCI_CREATED`` = HEAD sha + RFC3339(commit time) for image labels.
    - ``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>`` only when HEAD is exactly on that
      project's ``<prefix>*`` tag and the project has ``scm_dist`` set.

    ``projects``: pass the loaded project config map; projects with both ``prefix`` and
    ``scm_dist`` set get the pretend-version treatment (S12). Without ``projects``,
    only SOURCE_DATE_EPOCH / OCI_* are set.
    """
    epoch = _git(repo_root, "log", "-1", "--format=%ct")
    if epoch:
        os.environ["SOURCE_DATE_EPOCH"] = epoch
        created = datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        os.environ["OCI_CREATED"] = created
    revision = _git(repo_root, "rev-parse", "HEAD")
    if revision:
        os.environ["OCI_REVISION"] = revision

    if not projects:
        return
    for project in projects.values():
        if not project.prefix or not project.scm_dist:
            continue
        prefix_tag = f"{project.prefix}"
        # git describe --exact-match legitimately exits non-zero (128) whenever HEAD
        # isn't exactly on one of THIS project's tags — the normal case for every
        # scm_dist project except whichever one is currently being tagged/built (with
        # multiple projects releasing one after another in the same worktree, S-CLI.5a,
        # that's most of them most of the time). Not a `_git()` failure to raise on.
        probe = subprocess.run(
            ["git", "-C", str(repo_root), "describe", "--tags", "--exact-match", "--match", f"{prefix_tag}*"],
            capture_output=True, text=True,
        )
        if probe.returncode != 0:
            continue
        exact = probe.stdout.strip()
        if not exact:
            continue
        semver = exact[len(prefix_tag):]
        env_name = "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_" + project.scm_dist.upper().replace("-", "_")
        os.environ[env_name] = semver
        log_info(f"{project.scm_dist}: HEAD on {exact} → {env_name}={semver}")


def list_releases(owner: str, repo: str, token: str) -> list[dict]:
    releases: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100&page={page}"
        items, _ = load_json(url, token)
        if not items:
            break
        releases.extend(items)
        if len(items) < 100:
            break
        page += 1
    return releases


def delete_release(owner: str, repo: str, token: str, release_id: int, dry_run: bool) -> None:
    if dry_run:
        log_info(f"[DRY RUN] Would delete release {release_id}")
        return
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/{release_id}"
    status, body, _ = http_request("DELETE", url, token)
    if status >= 400:
        raise RuntimeError(f"Failed to delete release {release_id}: {body}")


def delete_unmanaged_release_tag(
    owner: str,
    repo: str,
    token: str,
    tag: str,
    *,
    dry_run: bool,
) -> bool:
    """Delete exactly one named old GitHub Release, never its Git tag.

    Normal CMRU cleanup manages versioned Releases and their Git tags together.
    This deliberately narrower operation removes an explicitly named unmanaged
    Release left by a predecessor workflow while preserving its tag. An absent
    target is an idempotent no-op; duplicate records or a malformed record are
    unsafe and fail loudly.
    """
    matches = [release for release in list_releases(owner, repo, token)
               if release.get("tag_name") == tag]
    if not matches:
        log_info(f"Cleanup: unmanaged GitHub Release {tag} is absent; no action.")
        return False
    if len(matches) != 1:
        raise RuntimeError(
            f"Cleanup: expected one GitHub Release for {tag!r}, found {len(matches)}."
        )
    release_id = matches[0].get("id")
    if not isinstance(release_id, int):
        raise RuntimeError(f"Cleanup: GitHub Release {tag!r} has no usable numeric ID.")
    if dry_run:
        log_info(
            f"[DRY RUN] Would delete unmanaged GitHub Release {tag} "
            f"(id={release_id}); its Git tag is untouched."
        )
        return True
    log_info(
        f"Cleanup: deleting unmanaged GitHub Release {tag} (id={release_id}); "
        "its Git tag is untouched."
    )
    delete_release(owner, repo, token, release_id, dry_run=False)
    return True


def cleanup_releases(
    owner: str,
    repo: str,
    token: str,
    cutoff: datetime,
    dry_run: bool,
    cleanup: CleanupConfig,
) -> None:
    releases = list_releases(owner, repo, token)
    # Cleanup is destructive. An empty declared selector means "select nothing",
    # never "all releases"; an estate that means all must say "*" explicitly.
    wildcard_prefixes = "*" in cleanup.release_tag_prefixes
    for release in releases:
        tag = release.get("tag_name") or ""
        if tag in cleanup.keep_release_tags:
            continue
        if not wildcard_prefixes and not any(tag.startswith(prefix) for prefix in cleanup.release_tag_prefixes):
            continue
        published_at = release.get("published_at") or release.get("created_at") or release.get("updated_at")
        if not published_at:
            continue
        published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published_dt >= cutoff:
            continue
        release_id = release.get("id")
        if not release_id:
            continue
        log_info(f"Deleting release tag {tag} (published {published_at})")
        delete_release(owner, repo, token, int(release_id), dry_run)


def list_package_versions(owner: str, package: str, token: str, owner_type: str) -> list[dict]:
    versions: list[dict] = []
    page = 1
    while True:
        if owner_type == "org":
            url = f"https://api.github.com/orgs/{owner}/packages/container/{package}/versions?per_page=100&page={page}"
        else:
            url = f"https://api.github.com/users/{owner}/packages/container/{package}/versions?per_page=100&page={page}"
        items, _ = load_json(url, token)
        if not items:
            break
        versions.extend(items)
        if len(items) < 100:
            break
        page += 1
    return versions


def list_container_packages(owner: str, token: str, owner_type: str) -> list[str]:
    packages: list[str] = []
    page = 1
    while True:
        if owner_type == "org":
            url = (
                f"https://api.github.com/orgs/{owner}/packages"
                f"?package_type=container&per_page=100&page={page}"
            )
        else:
            url = (
                f"https://api.github.com/users/{owner}/packages"
                f"?package_type=container&per_page=100&page={page}"
            )
        items, _ = load_json(url, token)
        if not items:
            break
        for item in items:
            name = (item.get("name") or "").strip()
            if name:
                packages.append(name)
        if len(items) < 100:
            break
        page += 1
    return packages


def delete_package_version(owner: str, package: str, token: str, version_id: int, owner_type: str, dry_run: bool) -> None:
    if dry_run:
        log_info(f"[DRY RUN] Would delete {package} version {version_id}")
        return
    if owner_type == "org":
        url = f"https://api.github.com/orgs/{owner}/packages/container/{package}/versions/{version_id}"
    else:
        url = f"https://api.github.com/users/{owner}/packages/container/{package}/versions/{version_id}"
    status, body, _ = http_request("DELETE", url, token)
    if status >= 400:
        if status == 400 and "cannot be deleted" in body:
            log_warn(
                "Skipping GHCR cleanup for "
                f"{package} version {version_id}: {body}"
            )
            return
        if status == 403:
            log_warn(
                "Skipping GHCR cleanup for "
                f"{package} version {version_id}: missing package delete scope."
            )
            return
        raise RuntimeError(f"Failed to delete {package} version {version_id}: {body}")


def delete_package(owner: str, package: str, token: str, owner_type: str, dry_run: bool) -> None:
    if dry_run:
        log_info(f"[DRY RUN] Would delete {package} package")
        return
    if owner_type == "org":
        url = f"https://api.github.com/orgs/{owner}/packages/container/{package}"
    else:
        url = f"https://api.github.com/users/{owner}/packages/container/{package}"
    status, body, _ = http_request("DELETE", url, token)
    if status >= 400:
        if status == 403:
            log_warn(
                "Skipping GHCR package delete for "
                f"{package}: missing package delete scope."
            )
            return
        if status == 404:
            log_warn(f"Skipping GHCR package delete for {package}: not found")
            return
        raise RuntimeError(f"Failed to delete {package} package: {body}")


def cleanup_ghcr(owner: str, token: str, owner_type: str, cutoff: datetime, dry_run: bool, cleanup: CleanupConfig) -> None:

    # Only an explicit "*" is an all-packages selector.
    wildcard_packages = "*" in cleanup.ghcr_packages
    packages = list_container_packages(owner, token, owner_type) if wildcard_packages else cleanup.ghcr_packages
    for package in packages:
        if package in cleanup.ghcr_delete_packages:
            log_info(f"Deleting GHCR package {package} (explicit cleanup list)")
            delete_package(owner, package, token, owner_type, dry_run)
            continue
        versions = list_package_versions(owner, package, token, owner_type)
        for version in versions:
            version_id = version.get("id")
            updated_at = version.get("updated_at") or version.get("created_at")
            if not version_id or not updated_at:
                continue
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated_dt >= cutoff:
                continue
            log_info(f"Deleting GHCR {package} version {version_id} (updated {updated_at})")
            delete_package_version(owner, package, token, int(version_id), owner_type, dry_run)


def remove_assets(
    age: str,
    dry_run: bool,
    cleanup: CleanupConfig,
    github: GitHubConfig,
    env_config: ReleaseEnvConfig,
) -> None:
    duration = parse_duration(age)
    cutoff = datetime.now(timezone.utc) - duration

    apply_release_env(github, env_config)
    owner = github.owner
    repo = github.repo
    token = github.token
    if not token:
        raise RuntimeError("github.token is required for cleanup")

    log_info(f"Removing assets older than {age} (cutoff {cutoff.isoformat()})")
    cleanup_releases(owner, repo, token, cutoff, dry_run, cleanup)
    cleanup_ghcr(owner, token, github.owner_type, cutoff, dry_run, cleanup)


def delete_git_tag_remote(repo_root: Path, tag: str, dry_run: bool) -> None:
    """Delete *tag* on origin; skip gracefully if it does not exist (idempotent)."""
    if dry_run:
        log_info(f"[DRY RUN] Would delete remote tag {tag}")
        return
    rc = subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f":refs/tags/{tag}"],
        capture_output=True, text=True,
    ).returncode
    if rc == 0:
        log_info(f"  Deleted remote tag {tag}")
    else:
        log_info(f"  Remote tag {tag} not found or already deleted — skipping")


def delete_git_tag_local(repo_root: Path, tag: str, dry_run: bool) -> None:
    """Delete *tag* locally; skip gracefully if it does not exist (idempotent)."""
    if dry_run:
        log_info(f"[DRY RUN] Would delete local tag {tag}")
        return
    rc = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-d", tag],
        capture_output=True, text=True,
    ).returncode
    if rc == 0:
        log_info(f"  Deleted local tag {tag}")
    else:
        log_info(f"  Local tag {tag} not found — skipping")


def list_remote_tags_matching(repo_root: Path, pattern: str) -> list[str]:
    """List remote tags matching *pattern* (git ls-remote --tags)."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-remote", "--tags", "origin", f"refs/tags/{pattern}"],
        capture_output=True, text=True, check=False,
    )
    tags = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or "^{}" in line:
            continue
        # format: "<sha>\trefs/tags/<name>"
        parts = line.split("\t", 1)
        if len(parts) == 2:
            ref = parts[1].strip()
            if ref.startswith("refs/tags/"):
                tags.append(ref[len("refs/tags/"):])
    return tags


def cleanup_project_releases_and_tags(
    repo_root: Path,
    owner: str,
    repo: str,
    token: str,
    prefix: str,
    keep_tags: list[str],
    dry_run: bool,
) -> list[str]:
    """Delete all GitHub Releases (and their git tags) for *prefix*-v* except kept ones.

    *keep_tags* is a combined list of ``keep_release_tags`` from config PLUS the
    ``<prefix>-latest`` pointer (never deleted by cleanup).  Returns a list of
    tags that were (or would have been) deleted.

    Edge cases:
    - Missing Release for a tag (tag-only) → delete the tag anyway (tag cleanup).
    - Missing tag for a Release → delete the Release.
    - 404 on delete → log and continue (idempotent).
    """
    all_releases = list_releases(owner, repo, token)
    # Collect all versioned releases for this prefix.
    version_marker = f"{prefix}-v"
    latest_tag = f"{prefix}-latest"
    # Build keep set: always keep -latest + explicit keep_release_tags list.
    keep_set = set(keep_tags) | {latest_tag}

    # GitHub Releases to delete.
    to_delete_releases: list[tuple[str, int]] = []  # (tag_name, release_id)
    for rel in all_releases:
        tag = rel.get("tag_name") or ""
        if not tag.startswith(version_marker) and tag != latest_tag:
            continue
        if tag in keep_set:
            log_info(f"  Keeping Release {tag} (in keep list)")
            continue
        release_id = rel.get("id")
        if release_id:
            to_delete_releases.append((tag, int(release_id)))

    # Remote tags to delete (covers tags without a matching Release).
    remote_versioned = list_remote_tags_matching(repo_root, f"{prefix}-v*")
    to_delete_tags: list[str] = []
    for tag in remote_versioned:
        if tag in keep_set:
            continue
        to_delete_tags.append(tag)

    # Union: anything mentioned in either set.
    all_to_delete_tags = set(t for t, _ in to_delete_releases) | set(to_delete_tags)
    deleted: list[str] = []

    for tag, release_id in to_delete_releases:
        log_info(f"Cleanup: deleting GitHub Release {tag}")
        if not dry_run:
            delete_release(owner, repo, token, release_id, dry_run=False)
        else:
            log_info(f"[DRY RUN] Would delete GitHub Release {tag} (id={release_id})")
        deleted.append(tag)

    for tag in sorted(all_to_delete_tags):
        log_info(f"Cleanup: deleting git tag {tag}")
        delete_git_tag_remote(repo_root, tag, dry_run)
        delete_git_tag_local(repo_root, tag, dry_run)
        if tag not in [t for t, _ in to_delete_releases]:
            deleted.append(tag)

    return deleted


def cleanup_project_step(
    repo_root: Path,
    project: "ProjectConfig",
    version: str,
    dry_run: bool,
) -> bool:
    """Invoke ``[steps.clean]`` for the project if defined, passing ``CMRU_VERSION`` in env.

    Returns True if the step ran (caller may then commit any resulting file deletions).
    """
    if "clean" not in project.steps:
        return False
    if dry_run:
        log_info(f"[DRY RUN] Would run steps.clean for {project.name} with CMRU_VERSION={version}")
        return False
    log_info(f"{project.name}: running steps.clean (CMRU_VERSION={version})")
    log_dir = repo_root / "logs"
    step_env = dict(project.env) if project.env else {}
    step_env["CMRU_VERSION"] = version
    step = _build_step_config("clean", project.steps["clean"])
    from cmru.runner import execute_step
    execute_step(step, repo_root, log_dir, extra_env=step_env)
    return True


def cleanup_commit_deletions(
    repo_root: Path,
    project_name: str,
    deleted_tags: list[str],
    dry_run: bool,
) -> None:
    """Commit any file deletions produced by the project's clean step.

    Only commits if there are actually staged changes (no empty commits).
    """
    if dry_run or not deleted_tags:
        return
    dirty = _git(repo_root, "status", "--porcelain")
    if not dirty:
        log_info(f"{project_name}: no file changes to commit after cleanup")
        return
    subprocess.run(["git", "-C", str(repo_root), "add", "-A"], check=False)
    cached = _git(repo_root, "diff", "--cached", "--name-only")
    if not cached:
        log_info(f"{project_name}: nothing staged — skipping cleanup commit")
        return
    tags_summary = ", ".join(deleted_tags[:5])
    if len(deleted_tags) > 5:
        tags_summary += f" (+{len(deleted_tags) - 5} more)"
    rc = subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m",
         f"chore({project_name}): cleanup deleted {tags_summary}"],
    ).returncode
    if rc == 0:
        log_info(f"{project_name}: committed cleanup changes")
    else:
        log_warn(f"{project_name}: cleanup commit failed — check working tree")


def _latest_version_for_prefix(owner: str, repo: str, token: str, bare: str) -> str:
    """Highest-semver surviving ``<bare>-v*`` release version, or ``""`` if none.

    Used to pass ``CMRU_VERSION`` to a project's optional ``[steps.clean]``. Reuses the
    same release listing + semver ordering as ``cmru resolve`` so the value the clean
    step sees matches what consumers resolve as "latest". Drafts/prereleases and the
    thin ``<bare>-latest`` pointer are ignored.
    """
    from cmru.release import _semver_key
    marker = f"{bare}-v"
    versions = [
        (rel.get("tag_name") or "")[len(marker):]
        for rel in list_releases(owner, repo, token)
        if (rel.get("tag_name") or "").startswith(marker)
        and not rel.get("draft") and not rel.get("prerelease")
    ]
    if not versions:
        return ""
    return max(versions, key=_semver_key)


def run_cleanup_verb(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_order: list[str],
    cleanup: "CleanupConfig",
    github_config: "GitHubConfig",
    env_config: "ReleaseEnvConfig",
    project_filter: Optional[str],
    dry_run: bool,
) -> None:
    """Generic ``cmru cleanup``: per project, delete old Releases, prune ghcr, delete
    stale tags, optionally invoke ``[steps.clean]``, and commit the result.

    Keeps ``<prefix>-latest`` and any tag in ``cleanup.keep_release_tags``.
    Everything is idempotent: missing targets are skipped, not errors.
    """
    # Export reproducible-build env (SOURCE_DATE_EPOCH / SETUPTOOLS_SCM_* / OCI_*) so any
    # [steps.clean] that rebuilds an artifact gets the same provenance as a release build.
    # NOTE: the per-project CMRU_VERSION is resolved separately below from the surviving
    # <prefix>-v* releases — this call does NOT set it.
    resolve_versions_from_git(repo_root, configs)

    names = [project_filter] if project_filter else list(project_order)
    missing = [n for n in names if n not in configs]
    if missing:
        raise ValueError(f"Unknown project(s): {', '.join(missing)}")

    keep_tags = list(cleanup.keep_release_tags)

    any_deleted: list[str] = []
    for name in names:
        project = configs[name]
        project_github = github_for_project(github_config, project)
        apply_project_release_env(github_config, env_config, project)
        if not project_github.token:
            raise RuntimeError(
                f"Cleanup for {name!r} requires GITHUB_PUSH_PAT/GITHUB_TOKEN, "
                "repository-root cmru.secret.toml, or its explicit project override"
            )
        owner = project_github.owner
        repo = project_github.repo
        token = project_github.token
        prefix = project.prefix
        if not prefix:
            log_info(f"{name}: no prefix configured — skipping Release/tag cleanup")
            continue
        # Strip trailing "-v" to get the bare prefix for -latest.
        bare = _bare_prefix(prefix)

        log_info(f"Cleanup: {name} (prefix={prefix})")

        # 1. Delete old Releases + their git tags; keep -latest + keep_release_tags.
        deleted = cleanup_project_releases_and_tags(
            repo_root, owner, repo, token,
            bare, keep_tags, dry_run,
        )
        any_deleted.extend(deleted)

        # 2. Optional per-project clean step (e.g. delete referenced manifests).
        #    CMRU_VERSION = highest-semver surviving <prefix>-v* release (post-cleanup),
        #    or "" when none survive. (In --dry-run nothing was deleted, so this is the
        #    current latest.)
        version = _latest_version_for_prefix(owner, repo, token, bare)
        step_ran = cleanup_project_step(repo_root, project, version, dry_run)

        # 3. Commit any file deletions the clean step produced.
        if step_ran or deleted:
            cleanup_commit_deletions(repo_root, name, deleted, dry_run)

    # 4. Prune old ghcr package versions (whole-repo, not per-project).
    # ghcr pruning is age-based; use ``cmru cleanup --remove-assets AGE`` for that path.
    # Here we only prune packages declared in ghcr_delete_packages (explicit wipe list).
    if cleanup.ghcr_delete_packages:
        # GHCR package deletion is repository-wide, so it deliberately uses the
        # repository credential rather than choosing one project's override.
        apply_release_env(github_config, env_config)
        if not github_config.token:
            raise RuntimeError(
                "Repository-wide GHCR cleanup requires GITHUB_PUSH_PAT/GITHUB_TOKEN "
                "or repository-root cmru.secret.toml"
            )
        if dry_run:
            log_info(
                f"[DRY RUN] Would delete GHCR packages: {', '.join(cleanup.ghcr_delete_packages)}"
            )
        else:
            for pkg in cleanup.ghcr_delete_packages:
                log_info(f"Cleanup: deleting GHCR package {pkg} (ghcr_delete_packages list)")
                delete_package(
                    github_config.owner,
                    pkg,
                    github_config.token,
                    github_config.owner_type,
                    dry_run=False,
                )

    if any_deleted:
        log_info(f"Cleanup complete. Deleted: {', '.join(any_deleted)}")
    else:
        log_info("Cleanup complete. Nothing deleted.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="cmru explicit step orchestration")
    parser.add_argument(
        "--config",
        help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=None,
        help="Project to operate on (default: all)",
    )
    parser.add_argument("--run-tests", action="store_true", help="Run tests")
    parser.add_argument("--build", action="store_true", help="Build artifacts")
    parser.add_argument("--push", action="store_true", help="Push artifacts")
    parser.add_argument("--validate", action="store_true", help="Validate releases")
    parser.add_argument("--remove-assets", metavar="AGE", help="Remove assets/images older than AGE (e.g., 1h, 2d)")
    parser.add_argument("--dry-run", action="store_true", help="Show cleanup actions without deleting")
    parser.add_argument(
        "--show-run-details", action="store_true",
        help="Stream full project subprocess output to this console",
    )
    parser.add_argument(
        "--log-append", action="store_true",
        help="Append a divider and retain existing stable per-step logs",
    )
    return parser


def _orchestrate() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    _apply_output_options(args)

    config_path = _resolve_config(args.config)

    (
        repo_root,
        configs,
        project_order,
        default_projects,
        default_steps,
        execution_mode,
        step_project_order,
        cleanup,
        github_config,
        env_config,
    ) = load_config(config_path)

    projects = args.project or default_projects
    if "all" in projects:
        selected_names = project_order
    else:
        selected_names = projects

    missing = [name for name in selected_names if name not in configs]
    if missing:
        raise ValueError(f"Unknown project(s) in selection: {', '.join(missing)}")

    selected = [configs[name] for name in selected_names]

    steps = []
    if args.run_tests:
        steps.append("run-tests")
    if args.build:
        steps.append("build")
    if args.push:
        steps.append("push")
    if args.validate:
        steps.append("validate")

    if not steps and not args.remove_assets:
        steps = default_steps

    if "push" in steps:
        require_project_publish_credentials(configs, selected_names)

    log_dir = repo_root / "logs"

    if steps:
        resolve_versions_from_git(repo_root, configs)

    if execution_mode == "project-first":
        for project in selected:
            apply_project_release_env(github_config, env_config, project)
            for step in steps:
                run_project_step(project, step, repo_root, log_dir)
    else:
        for step in steps:
            ordered_names = step_project_order.get(step) or selected_names
            for project_name in ordered_names:
                if project_name not in configs:
                    raise ValueError(f"Unknown project in step_project_order: {project_name}")
                if project_name not in selected_names:
                    continue
                project = configs[project_name]
                apply_project_release_env(github_config, env_config, project)
                run_project_step(project, step, repo_root, log_dir)

    if args.remove_assets:
        remove_assets(args.remove_assets, args.dry_run, cleanup, github_config, env_config)

    log_info("Release manager complete")


def _source_tree_version() -> Optional[str]:
    """Return CMRU's source version when this import is from its source tree.

    A repository shim puts ``cmru/src`` ahead of an editable installation. In
    that situation package metadata can describe an older installed wheel even
    though the source tree being executed is tagged differently. Git is the
    authoritative source for a checkout; installed wheels simply have no adjacent
    ``pyproject.toml`` and use distribution metadata below.  For a checkout
    beyond an exact CMRU tag, derive the same next-patch dev shape used by
    setuptools-scm rather than reporting unrelated, stale editable metadata.
    """
    project_root = Path(__file__).resolve().parents[2]
    if not (project_root / "pyproject.toml").is_file():
        return None
    result = subprocess.run(
        ["git", "-C", str(project_root), "describe", "--exact-match", "--tags", "--match", "cmru-v*"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        tag = result.stdout.strip()
        match = re.fullmatch(r"cmru-v([0-9][0-9A-Za-z.+-]*)", tag)
        if match:
            return match.group(1)

    result = subprocess.run(
        ["git", "-C", str(project_root), "describe", "--tags", "--long", "--match", "cmru-v*"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        return None
    return _dev_version_from_describe(result.stdout.strip())


def _dev_version_from_describe(description: str) -> Optional[str]:
    """Render a simple SemVer ``git describe --long`` result as a dev version.

    CMRU's released tags are plain ``X.Y.Z`` SemVer.  Do not guess a version for
    another grammar: falling back to installed distribution metadata is more
    honest than inventing a release candidate shape this command cannot prove.
    """
    match = re.fullmatch(r"cmru-v(\d+)\.(\d+)\.(\d+)-(\d+)-g([0-9a-f]+)", description)
    if not match:
        return None
    major, minor, patch, distance, revision = match.groups()
    if distance == "0":
        return f"{major}.{minor}.{patch}"
    return f"{major}.{minor}.{int(patch) + 1}.dev{distance}+g{revision}"


def _cmru_version() -> str:
    source_version = _source_tree_version()
    if source_version:
        return source_version
    try:
        from importlib.metadata import version as _mv
        return _mv("cmru")
    except Exception:
        return "dev"


def _default_config_path() -> Path:
    """Select a config declared directly by the current working directory.

    A portable project owns ``cmru.toml``.  A repository root may instead own
    ``cmru.orchestration.toml``.  Looking only in the current directory keeps
    the installed CLI independent of parent checkouts while allowing both the
    portable and estate entry points to be used without a wrapper-specific
    config injection.
    """
    cwd = Path.cwd()
    project = cwd / PROJECT_CONFIG_FILENAME
    if project.exists():
        return project
    orchestration = cwd / ORCHESTRATION_CONFIG_FILENAME
    if orchestration.exists():
        return orchestration
    # Preserve the project-config error when neither source exists.  The
    # resulting message remains actionable and stable for standalone callers.
    return project


def _resolve_config(config_opt: Optional[str]) -> Path:
    raw = config_opt or str(_default_config_path())
    return Path(raw).expanduser().resolve()


def _ordered_configs(
    configs: Mapping[str, "ProjectConfig"],
    project_order: List[str],
) -> "dict[str, ProjectConfig]":
    """Project configs limited to ``project_order`` (the orchestrated set), in order.

    ``status``/``release`` use this so they never auto-tag projects that still own a
    bespoke pipeline (tls-edge, empyrion) and are not yet migrated into the orchestrator.
    """
    return {name: configs[name] for name in project_order if name in configs}


def _push_tags(repo_root: Path, tags: List[str]) -> None:
    """Push annotated release tags to origin. A failure is non-fatal: the GitHub
    Release API recreates the tag at publish time, so we warn rather than abort."""
    if not tags:
        return
    log_info(f"Pushing tags to origin: {', '.join(tags)}")
    rc = subprocess.run(["git", "-C", str(repo_root), "push", "origin", *tags]).returncode
    if rc != 0:
        log_warn("git push of tags failed — continuing; publish will create the tag via the API.")


def _tag_on_head(repo_root: Path, prefix: str) -> Optional[str]:
    """Return the project's ``<prefix>*`` tag pointing at HEAD (highest semver), else None."""
    out = _git(repo_root, "tag", "--points-at", "HEAD", "--list", f"{prefix}*")
    if not out:
        return None
    tags = [t for t in out.splitlines() if t.strip() and not t.endswith("-latest")]
    if not tags:
        return None
    from cmru.release import _semver_key
    return max(tags, key=lambda t: _semver_key(t[len(prefix):]))


def _run_project_steps(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: List[str],
    steps: List[str],
    *,
    github_config: Optional[GitHubConfig] = None,
    env_config: Optional[ReleaseEnvConfig] = None,
) -> None:
    """Run ``steps`` (in order) for each named project through the unified runner (S3).

    Seeds reproducible-build + SETUPTOOLS_SCM pretend-version env first so a wheel
    built here matches the tag on HEAD. A requested missing step is a configuration
    error; it is never treated as a successful no-op."""
    resolve_versions_from_git(repo_root, dict(configs))
    log_dir = repo_root / "logs"
    for name in project_names:
        project = configs[name]
        if github_config is not None and env_config is not None:
            apply_project_release_env(github_config, env_config, project)
        for step in steps:
            if step not in (project.runner_steps or {}):
                raise RuntimeError(
                    f"{name}: requested step {step!r} is not declared in "
                    f"{PROJECT_CONFIG_FILENAME}"
                )
            log_info(f"{name}: running step '{step}'")
            run_project_step(project, step, repo_root, log_dir)


def _run_isolated_build_projects(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: List[str],
) -> None:
    """Run the non-publishing half of each project's declared release contract.

    A retained ``cmru build`` performs source preparation (when declared), the
    required gate, and the project-selected artifact-producing step.  It never
    tags, publishes, or copies outputs back to the caller checkout.
    """
    for name in project_names:
        project = configs[name]
        artifact_step = project.build_step
        if not artifact_step:
            raise RuntimeError(f"{name}: project.release.build_step is absent")
        phases: list[str] = []
        if "prepare" in (project.runner_steps or {}):
            phases.append("prepare")
        phases.append("run-tests")
        if artifact_step not in phases:
            phases.append(artifact_step)
        _run_project_steps(repo_root, configs, [name], phases)


def _run_untagged_project(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    name: str,
    *,
    github_config: GitHubConfig,
    env_config: ReleaseEnvConfig,
) -> None:
    """Run the build/push half of a declared no-Git-tag release contract.

    The project owns what it publishes (registry image, external package, or another
    declared artifact). CMRU only enforces its selected build step and required push step.
    """
    resolve_versions_from_git(repo_root, dict(configs))
    log_dir = repo_root / "logs"
    project = configs[name]
    apply_project_release_env(github_config, env_config, project)
    # Projects that extract tracked provenance must do their private build in
    # ``prepare``. It has already been committed, gated and promoted before cmru
    # creates any tags for this transaction; rebuilding here would both waste work
    # and risk producing artifacts from a post-tag HEAD.
    artifact_step = project.build_step
    if not artifact_step:
        raise RuntimeError(f"{name}: project.release.build_step is absent")
    prepared_build = artifact_step == "prepare"
    if prepared_build:
        log_info(f"{name}: using artifact output deliberately produced by steps.prepare")
    else:
        log_info(f"{name}: running artifact step {artifact_step!r}")
        run_project_step(project, artifact_step, repo_root, log_dir)

    if not prepared_build and _worktree_changed_paths(repo_root):
        raise RuntimeError(
            f"{name}: build changed tracked source after tags were created; move it to steps.prepare "
            "and declare its outputs in release.commit_generated"
        )

    if "push" in (project.runner_steps or {}):
        log_info(f"{name}: running step 'push'")
        run_project_step(project, "push", repo_root, log_dir)
    else:  # config validation requires it; keep the runtime guard for direct callers.
        raise RuntimeError(f"{name}: required push step is absent")


def _version_strategy(proj: "ProjectConfig") -> str:
    return proj.version.strategy if getattr(proj, "version", None) else "scm"


def _release_projects_sequentially(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    workspace: transaction.ReleaseWorkspace,
    release_names: List[str],
    *,
    github_config: GitHubConfig,
    env_config: ReleaseEnvConfig,
    no_build: bool = False,
    minor: bool = False,
    major: bool = False,
    set_version: Optional[str] = None,
) -> List[str]:
    """Release every named project one after another (build all projects after
    another): each project's own prepare → gate → promote → tag → build → publish
    cycle completes in full before the next project starts. This is what lets a
    later project (e.g. an OCI image) resolve an earlier project's (e.g. a wheel)
    brand-new release within this SAME run, instead of always trailing one
    `cmru release` behind.

    Progress is checkpointed after each project's full success
    (:func:`transaction.write_release_progress`) so that if a LATER project fails,
    the caller's revert only undoes that project's promoted changes — an earlier,
    already-succeeded, already-published project is left alone.

    Returns the "{name} (...)" labels actually built/published (empty entries for
    projects released with ``no_build=True`` are omitted).
    """
    from cmru.version import release_cmd

    # Seed the checkpoint at this run's own starting point. Without this, a
    # --resume reusing the same branch token would read a *previous* attempt's
    # (older, now-invalid) checkpoint and could revert past the operator's own
    # fix commit on --resume. read_release_progress() returning "the run's base"
    # is exactly equivalent to "nothing has fully succeeded yet in this run".
    transaction.write_release_progress(repo_root, workspace, workspace.base)

    released: List[str] = []
    for name in release_names:
        project = configs[name]
        apply_project_release_env(github_config, env_config, project)
        log_info(f"=== {name}: releasing ===")

        _prepare_release_projects(
            repo_root, configs, [name], minor=minor, major=major, set_version=set_version,
        )
        _run_release_gates(repo_root, configs, [name])

        transaction.promote_workspace(workspace)
        log_info(f"{name}: promoted to origin/main")
        # Keep the durability backup current as the run progresses — otherwise
        # it forever holds only the pre-run base and a crash mid-run has nothing
        # of this run's work to recover from.
        transaction.push_backup_branch(workspace)

        strategy = _version_strategy(project)
        if not getattr(project, "git_tag", True):
            if not no_build:
                log_info(f"Building + publishing {name} (no git tag)")
                _run_untagged_project(
                    repo_root, configs, name,
                    github_config=github_config, env_config=env_config,
                )
                released.append(f"{name} (no git tag)")
                transaction.write_release_result(
                    repo_root, workspace, name, f"source-{_git(repo_root, 'rev-parse', 'HEAD')[:12]}"
                )
            else:
                log_info(f"{name}: --no-build — skipped build/push")
        else:
            release_cmd(repo_root, {name: project}, minor=minor, major=major, set_version=set_version)
            # A file:-strategy tag commits a version bump *after* the promote
            # above — push it now so it lands on origin/main this cycle, not
            # deferred to whichever project (if any) happens to promote next.
            # A no-op (nothing new to push) for scm/counter strategies.
            transaction.promote_workspace(workspace)
            tag = _tag_on_head(repo_root, project.prefix or f"{name}-v")
            if tag:
                _push_tags(repo_root, [tag])
                if not no_build:
                    log_info(f"Building + publishing {name} ({tag})")
                    artifact_phases = [] if project.build_step == "prepare" else [project.build_step]
                    _run_project_steps(
                        repo_root, configs, [name], [*artifact_phases, "push"],
                        github_config=github_config, env_config=env_config,
                    )
                    released.append(f"{name} ({tag})")
                    transaction.write_release_result(repo_root, workspace, name, tag)
                else:
                    log_info(f"{name}: --no-build — tagged {tag}, skipped build/publish")
                    transaction.write_release_result(repo_root, workspace, name, tag)
            elif not no_build:
                raise RuntimeError(
                    f"{name}: gate passed and it was in this run's changed-project scope, "
                    "but no tag ended up on HEAD (release.git_tag produced nothing to "
                    "build/publish) — this should not happen; investigate before retrying"
                )

        # This project's whole cycle succeeded — checkpoint it so a LATER
        # project's failure can only revert what comes after this point.
        transaction.write_release_progress(repo_root, workspace, _git(repo_root, "rev-parse", "HEAD"))

    return released


def _transaction_workspace_from_env(repo_root: Path) -> transaction.ReleaseWorkspace:
    """Recover transaction provenance in the re-execed child process."""
    workspace = transaction.ReleaseWorkspace(
        repo_root=repo_root,
        path=repo_root,
        branch=os.environ.get(transaction.BRANCH_ENV, ""),
        base=os.environ.get(transaction.BASE_ENV, ""),
    )
    if not workspace.branch or not workspace.base:
        raise RuntimeError("release child is missing transaction provenance")
    return workspace


def _child_release_args(rest: List[str], config_path: Path, repo_root: Path) -> List[str]:
    """Point a transaction child at the matching config *inside* its snapshot."""
    result: List[str] = []
    skip_next = False
    for index, value in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if value == "--config":
            skip_next = True
            continue
        if value.startswith("--config="):
            continue
        if value == "--resume":
            skip_next = True
            continue
        if value.startswith("--resume="):
            continue
        if value == "--abandon":
            skip_next = True
            continue
        if value.startswith("--abandon="):
            continue
        result.append(value)
    try:
        relative = config_path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(
            "isolated releases require a config tracked inside the repository"
        ) from exc
    result.extend(["--config", str(relative)])
    return result


def _run_release_gates(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: List[str],
) -> None:
    """Run every selected project's declared release gate before source promotion."""
    log_dir = repo_root / "logs"
    for name in project_names:
        project = configs[name]
        if not project.steps.get("run-tests"):
            raise RuntimeError(
                f"{name}: no release gate is declared ([project.{name}.steps.run-tests]); "
                "cmru refuses to tag or publish without a meaningful tester-unified gate"
            )
        log_info(f"{name}: running required release gate")
        run_project_step(project, "run-tests", repo_root, log_dir)


def _check_release_tool_dependencies(
    scoped: Mapping[str, "ProjectConfig"],
    configs: Mapping[str, "ProjectConfig"],
    *,
    github_config: "GitHubConfig",
    allow_stale: bool,
) -> None:
    """Release-preflight tool-dependency verification (S15) -- the release refuses
    to ship a product whose vendored tool is stale or not what it claims to be.
    ``scoped`` is what THIS run will actually release; ``configs`` is the whole
    loaded estate, used only to resolve each dependency's PROVIDER project's tag
    ``prefix``. Raises :class:`cmru.version.ReleasePlanRefused` -- exactly like the
    tag-preflight beside it -- so a blocking finding is a typed, clean refusal
    before any project's prepare/gate/promote cycle starts, never a mid-release
    failure. A project that declares nothing is a silent no-op: zero network calls."""
    from cmru.tool_deps import is_blocking, render_status, verify_project
    from cmru.version import ReleasePlanRefused

    blocking: List[str] = []
    for name, project in scoped.items():
        for status in verify_project(
            owner=github_config.owner, repo=github_config.repo, project=project, projects=configs,
        ):
            log_info(f"tool-deps: {render_status(status)}")
            if is_blocking(status, allow_stale=allow_stale):
                blocking.append(render_status(status))
    if blocking:
        raise ReleasePlanRefused(
            "tool dependency preflight refused release (S15) -- "
            + " | ".join(line.replace("\n", "; ") for line in blocking)
            + ". Pass --allow-stale-tool-deps to proceed despite a stale (behind-latest) pin "
              "only; there is no override for an integrity or authenticity failure. Run "
              "`cmru tool-deps --refresh <provider-project>` to re-vendor deliberately."
        )


def _worktree_changed_paths(repo_root: Path, *, paths: Optional[List[str]] = None) -> List[str]:
    """Return every non-ignored changed path (tracked diff + staged + untracked),
    optionally scoped to ``paths`` — otherwise repo-wide."""
    scope = list(paths) if paths else []
    commands = (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    )
    changed: list[str] = []
    for command in commands:
        args = (*command, "--", *scope) if scope else command
        out = _git(repo_root, *args)
        changed.extend(line for line in out.splitlines() if line)
    return list(dict.fromkeys(changed))


def _uncommitted_release_paths(
    repo_root: Path, ordered: Mapping[str, "ProjectConfig"], names: Iterable[str],
) -> dict[str, List[str]]:
    """Map project name -> its uncommitted (tracked/staged/untracked) file paths,
    for every named project that currently has any. Empty when the scope is clean.

    This runs in the CALLER's own checkout, before an isolated worktree is even
    created — origin/main is the only release source, so local uncommitted work
    would otherwise be silently left out with no signal (see the note in
    ``main()``'s release verb handler)."""
    dirty: dict[str, List[str]] = {}
    for name in names:
        project = ordered.get(name)
        if project is None:
            continue
        paths = getattr(project, "paths", None) or [getattr(project, "cwd", None) or name]
        changed = _worktree_changed_paths(repo_root, paths=paths)
        if changed:
            dirty[name] = changed
    return dirty


def _is_declared_generated(path: str, declared: List[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in declared)


def _commit_prepared_generated(repo_root: Path, project: "ProjectConfig") -> bool:
    """Commit only a prepare step's declared generated outputs, or fail closed.

    Generated source is part of the release input, never a side effect to sweep
    into a post-publish commit.  This deliberately checks the entire worktree so
    a prepare script cannot hide an unrelated mutation behind one allowlisted file.
    """
    cwd = project.cwd or project.name
    declared_outputs = [*project.commit_generated]
    changelog = getattr(project, "changelog", None)
    if changelog:
        declared_outputs.append(changelog)
    declared = [f"{cwd}/{path}" for path in declared_outputs]
    changed = _worktree_changed_paths(repo_root)
    if not changed:
        return False
    unexpected = [path for path in changed if not _is_declared_generated(path, declared)]
    if unexpected:
        raise RuntimeError(
            f"{project.name}: prepare changed undeclared paths: {', '.join(unexpected)}; "
            "declare mechanical outputs in project.<name>.release.commit_generated"
        )
    subprocess.run(["git", "add", "-A", "--", *changed], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore({project.name}): prepare release inputs"],
        cwd=repo_root, check=True,
    )
    log_info(f"{project.name}: committed prepared release inputs")
    return True


def _prepare_release_projects(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: List[str],
    *,
    minor: bool = False,
    major: bool = False,
    set_version: Optional[str] = None,
) -> None:
    """Prepare declared source inputs, including an optional generated changelog."""
    from cmru.changelog import generate_release_changelog

    log_dir = repo_root / "logs"
    for name in project_names:
        project = configs[name]
        if "prepare" in project.steps:
            log_info(f"{name}: preparing release inputs")
            run_project_step(project, "prepare", repo_root, log_dir)
        changelog = getattr(project, "changelog", None)
        if changelog:
            log_info(f"{name}: generating release history")
            changed = generate_release_changelog(
                repo_root, project, minor=minor, major=major, set_version=set_version,
            )
            if changed:
                log_info(f"{name}: updated {changelog}")
        if "prepare" in project.steps or changelog:
            _commit_prepared_generated(repo_root, project)


def usage() -> str:
    """Return the public CLI overview, including every user-facing option.

    The verb implementations use focused parsers because their options have
    different semantics.  Keep this overview as the single discoverability
    surface, and include the complete option set for each of those parsers.
    Internal transaction flags and the project-step implementation commands are
    intentionally excluded; they are not supported operator interfaces.
    """
    return (
        f"CMRU {_cmru_version()} — Configurable Multi Release Utility\n"
        f"Config: project {PROJECT_CONFIG_FILENAME}, or explicit "
        f"{ORCHESTRATION_CONFIG_FILENAME} — select with --config\n"
        "\n"
        "TYPICAL WORKFLOW  (run from a project or repository root):\n"
        "  1. status                  preview what changed + the next version (no writes)\n"
        "  2. release                 isolated: prepare → gate → integrate → tag → build → publish\n"
        "       build alone retains local non-release outputs; only a failed build keeps its worktree\n"
        "  3. cleanup [--project P] [--dry-run]  prune old releases/images (keeps -latest)\n"
        "     cleanup --remove-assets AGE         age-based prune (e.g. 30d)\n"
        "\n"
        "PLANNING (read-only)\n"
        "    status   [--config C] [--project P] [--minor|--major] [--set-version V] [--dry-run]\n"
        "    worktrees [--json]                                      discover retained worktrees\n"
        "    dependencies [--config C] [--json] [--write]              graph + preflight\n"
        "\n"
        "RELEASE / HISTORY (writes)\n"
        "    release  [--config C] [--project P] [--minor|--major|--set-version V] [--dry-run]\n"
        "             [--no-build] [--resume WORKTREE|--abandon WORKTREE|all-previous]\n"
        "             [--allow-uncommitted] [--show-run-details] [--log-append]\n"
        "             [--retain-logs-on-release] [--retain-artifacts-on-release]\n"
        "                                                  isolated source-first transaction\n"
        "    changelog --config C --project P --backfill-tag TAG\n"
        "                                                  catalog an already-published tagged release\n"
        "    init [--layout single|monorepo] [--project ID] [--owner O] [--repo R]\n"
        "                                                  guided scaffolding: writes cmru.toml (and, for a\n"
        "                                                  monorepo, cmru.orchestration.toml) after validating\n"
        "                                                  them with the real loaders; never overwrites\n"
        "    standards [--config C] [--project P ...] [--update]\n"
        "                                                  check/update CMRU framework markers\n"
        "    tool-deps [--config C] [--project P ...] [--json] [--timeout S]\n"
        "              [--allow-stale-tool-deps] [--refresh PROVIDER_PROJECT]\n"
        "                                                  verify declared tool dependencies (S15):\n"
        "                                                  integrity + authenticity + freshness\n"
        "                                                  (network; never run during tests)\n"
        "    build    [--config C] [--project P] [--show-run-details] [--log-append]\n"
        "                                                  isolated local build; retains outputs on success\n"
        "    publish  [--config C] [--project P] [--show-run-details] [--log-append]\n"
        "                                                  run the project 'push' step\n"
        "    run      [--config C] [--project P ...] [--run-tests] [--build] [--push] [--validate]\n"
        "             [--remove-assets AGE] [--dry-run] [--show-run-details] [--log-append]\n"
        "                                                  low-level: explicit steps × projects\n"
        "\n"
        "CONSUMPTION (read-only)\n"
        "    resolve  [--config C] [--project P|--prefix PREFIX] [--format env|json|url]\n"
        "    get|get-py --config C --project P [--output FILE]\n"
        "                                                  emit standalone get.py installer\n"
        "\n"
        "MAINTENANCE\n"
        "    cleanup  [--config C] [--remove-assets AGE] [--project P] [--dry-run]\n"
        "             [--delete-unmanaged-release-tag TAG] [--delete-build-output ID]\n"
        "             [--discard-build-worktree PATH] [--yes]\n"
        "    run-step --config C --step S [--show-run-details] [--log-append]\n"
        "                                                  execute one declared project step\n"
        "    version                                     print this CMRU version\n"
        "\n"
        "OUTPUT\n"
        "    -h, --help                                  show this complete command overview\n"
        "    --log-prefix-time-short                       emit HH:MM:SS before severity prefixes\n"
        "                                                  (interactive severities are colour-coded; pipes stay plain)\n"
    )


def _config_hint(repo_root: Path) -> str:
    """Return an explicit config argument for commands printed to operators."""
    for filename in (PROJECT_CONFIG_FILENAME, ORCHESTRATION_CONFIG_FILENAME):
        candidate = repo_root / filename
        if candidate.exists():
            return f" --config {shlex.quote(str(candidate))}"
    return ""


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point for the ``cmru`` CLI.

    Verb dispatch. Normal release path:  ``status`` → ``release`` (→ ``cleanup``).
    ``release`` is the one-shot (tag → push → build → publish). ``build`` is an
    isolated, retained diagnostic build; ``publish`` remains an explicit project
    push step. ``run`` is the explicit-steps escape hatch; ``run-step`` is the raw
    single-step runner.
    """
    import sys as _sys

    av = consume_cli_flags(list(argv) if argv is not None else _sys.argv[1:])
    if not av or av[0] in ("-h", "--help"):
        print(usage())
        return

    if av[0] == "version":
        print(f"cmru {_cmru_version()}")
        return

    verb = av[0]
    rest = av[1:]

    if verb == "init":
        from cmru.scaffold import init_main
        return init_main(rest)

    if verb == "run":
        _sys.argv = ["cmru"] + rest
        _orchestrate()

    elif verb == "worktrees":
        parser = argparse.ArgumentParser(
            description=(
                "List retained CMRU build/release worktrees.  This is read-only and derives "
                "the repository from the current Git checkout; it does not require a CMRU config."
            )
        )
        parser.add_argument("--json", action="store_true", help="Emit machine-readable records")
        vargs = parser.parse_args(rest)
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False,
        )
        if result.returncode or not result.stdout.strip():
            parser.error("cmru worktrees must run inside the repository whose worktrees you want to inspect")
        repo_root = Path(result.stdout.strip()).resolve()
        workspaces = transaction.list_cmru_workspaces(repo_root)
        if vargs.json:
            print(json.dumps([
                {
                    "purpose": workspace.branch.split("/", 2)[1],
                    "branch": workspace.branch,
                    "path": str(workspace.path),
                    "source_commit": workspace.base or None,
                    "visible": workspace.path.is_dir(),
                }
                for workspace in workspaces
            ], indent=2, sort_keys=True))
        elif not workspaces:
            log_info("No retained CMRU build or release worktrees.")
        else:
            config_hint = _config_hint(repo_root)
            for workspace in workspaces:
                purpose = workspace.branch.split("/", 2)[1]
                source = workspace.base[:12] if workspace.base else "unavailable from this filesystem view"
                print(f"{purpose}: {workspace.branch}\n  path: {workspace.path}\n  source: {source}")
                if purpose == "release" and workspace.path.is_dir():
                    print(f"  resume: cmru release{config_hint} --resume {shlex.quote(str(workspace.path))}")
                elif purpose == "build" and workspace.path.is_dir():
                    print(
                        "  discard: cmru cleanup"
                        f"{config_hint} --discard-build-worktree {shlex.quote(str(workspace.path))} --yes"
                    )
                elif not workspace.path.is_dir():
                    print("  action: unavailable here; inspect or clean it from the filesystem view that created it")

    elif verb == "run-step":
        # Raw single-step runner for the one project cmru.toml grammar.
        from cmru.runner import main as runner_main
        runner_main(rest)

    elif verb in ("dependencies", "dependency-graph", "graph"):
        parser = argparse.ArgumentParser(
            description=(
                "Show and preflight the project dependency graph. Artifact inputs are "
                "derived from first-party pip/wheels.list manifests; --write refreshes "
                "the marked comment block in the orchestration document."
            )
        )
        parser.add_argument(
            "--config", help=f"Path to {ORCHESTRATION_CONFIG_FILENAME}"
        )
        parser.add_argument("--json", action="store_true", help="Emit machine-readable graph data")
        parser.add_argument(
            "--write", action="store_true",
            help="Write the generated graph into the orchestration TOML comment block",
        )
        vargs = parser.parse_args(rest)
        cfg_path = _resolve_config(vargs.config)
        forge = load_forge_config(cfg_path)
        if forge.orchestration is None or cfg_path.name != ORCHESTRATION_CONFIG_FILENAME:
            parser.error(f"dependencies requires {ORCHESTRATION_CONFIG_FILENAME}")
        report = build_report(
            repo_root=forge.repo_root,
            project_order=forge.orchestration.project_order,
            declared=forge.orchestration.dependencies,
            projects=forge.projects,
        )
        if vargs.write:
            from cmru.dependencies import write_comment_block
            write_comment_block(cfg_path, report)
            print(f"[INFO] Wrote generated dependency graph to {cfg_path}")
        if vargs.json:
            print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        else:
            print(render_dependency_report(report))
        if report.errors:
            _sys.exit(exit_codes.CONFIG_ERROR)

    elif verb == "handler":
        # Stable CLI access to the explicit project-step command library.
        from cmru.handlers import main as handlers_main
        handlers_main(rest)

    elif verb == "tester-gate":
        from cmru.tester_gate import main as tester_gate_main
        tester_gate_main(rest)

    elif verb in ("build", "publish"):
        import argparse as _ap
        parser = _ap.ArgumentParser(description=f"cmru {verb}")
        parser.add_argument("--project", help="Limit to one project (default: all orchestrated)")
        parser.add_argument(
            "--config", help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}"
        )
        parser.add_argument("--_transaction-child", action="store_true", help=_ap.SUPPRESS)
        parser.add_argument("--show-run-details", action="store_true")
        parser.add_argument("--log-append", action="store_true")
        vargs = parser.parse_args(rest)
        _apply_output_options(vargs)
        cfg_path = _resolve_config(vargs.config)
        (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
        github_config, env_config = _rest[-2], _rest[-1]
        apply_release_env(github_config, env_config)
        ordered = _ordered_configs(configs, project_order)
        names = [vargs.project] if vargs.project else list(ordered.keys())
        missing = [n for n in names if n not in configs]
        if missing:
            log_error(f"Unknown project(s): {', '.join(missing)}")
            _sys.exit(2)
        if verb == "publish":
            require_project_publish_credentials(configs, names)
        step = "build" if verb == "build" else "push"

        if verb == "build" and not vargs._transaction_child:
            # A normal build uses the exact same isolated source boundary as a
            # release, but intentionally stops before every release action.  A
            # successful build copies its local-only evidence to the caller tree
            # and removes the worktree; a failure retains the worktree exactly
            # where the diagnostic source/log/artifact state exists.
            try:
                child_args = _child_release_args(rest, cfg_path, repo_root)
                with transaction.release_lock(repo_root):
                    dirty = _uncommitted_release_paths(repo_root, configs, names)
                    if dirty:
                        for project_name, files in dirty.items():
                            log_error(f"{project_name}: uncommitted changes — {', '.join(files)}")
                        raise RuntimeError(
                            "cmru build snapshots origin/main; commit and push the selected project "
                            "changes first so the isolated build cannot silently omit them."
                        )
                    base = transaction.fetch_origin_main(repo_root)
                    behind = transaction.assert_local_main_not_ahead(repo_root)
                    if behind:
                        log_warn(
                            f"Local main is {behind} commit(s) behind origin/main; "
                            f"build uses fetched origin/main {base[:12]}."
                        )
                    workspace = transaction.create_workspace(
                        repo_root, base=base, purpose="build", scope=vargs.project,
                    )
                    transaction.copy_secret_overlays(
                        repo_root,
                        workspace,
                        [Path(project.project_root) / PROJECT_CONFIG_FILENAME for project in configs.values()
                         if project.project_root is not None],
                    )
                    log_info(
                        f"Build transaction {workspace.branch}: snapshot {workspace.base[:12]} "
                        f"at {workspace.path}"
                    )
                    rc = transaction.run_child(workspace, child_args, verb="build")
                    if rc:
                        log_error(
                            f"Build transaction failed; worktree retained for debugging: {workspace.path}"
                        )
                        _sys.exit(rc)
                    try:
                        retained = transaction.retain_successful_build_outputs(
                            repo_root, workspace, configs, names,
                        )
                    except Exception as exc:
                        log_error(
                            "Build completed but local-output retention failed; worktree retained for "
                            f"debugging: {workspace.path}\n{exc}"
                        )
                        _sys.exit(1)
                    try:
                        transaction.remove_workspace(workspace)
                    except Exception as exc:
                        log_error(
                            "Build outputs were retained but worktree cleanup failed; remove the exact "
                            f"worktree after inspection: {workspace.path}\n{exc}"
                        )
                        _sys.exit(1)
                    log_info(
                        "Build transaction complete; retained local non-release outputs:\n"
                        + "\n".join(f"  {path}" for path in retained)
                    )
                    output_id = retained[0].name
                    config_hint = f" --config {shlex.quote(str(cfg_path))}"
                    for name in names:
                        log_info(
                            f"{name}: remove this local record only when no longer needed:\n"
                            f"  cmru cleanup{config_hint} --project {name} "
                            f"--delete-build-output {output_id} --yes"
                        )
                    _sys.exit(0)
            except Exception as exc:
                log_error(str(exc))
                _sys.exit(1)
        if verb == "build":
            _run_isolated_build_projects(repo_root, configs, names)
        else:
            _run_project_steps(
                repo_root, configs, names, [step],
                github_config=github_config, env_config=env_config,
            )
        log_info(f"cmru {verb} complete")

    elif verb == "resolve":
        from cmru.resolve import resolve_main
        resolve_main(rest)

    elif verb in ("get", "get-py"):
        from cmru.getpy import getpy_main
        getpy_main(rest)

    elif verb == "changelog":
        import argparse as _ap
        from cmru.changelog import backfill_release_changelog

        parser = _ap.ArgumentParser(
            description=(
                "Backfill source-derived history for an already-published CMRU-tagged release. "
                "Normal cmru release writes history automatically before its gate."
            )
        )
        parser.add_argument("--project", required=True)
        parser.add_argument("--backfill-tag", required=True, metavar="TAG")
        parser.add_argument(
            "--config", help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}"
        )
        vargs = parser.parse_args(rest)
        cfg_path = _resolve_config(vargs.config)
        repo_root, configs, *_ = load_config(cfg_path)
        project = configs.get(vargs.project)
        if project is None:
            log_error(f"Unknown project: {vargs.project}")
            _sys.exit(2)
        if not project.changelog:
            log_error(
                f"{vargs.project}: release history is explicitly disabled; "
                "remove release.changelog = false before backfilling"
            )
            _sys.exit(2)
        changed = backfill_release_changelog(repo_root, project, vargs.backfill_tag)
        if changed:
            log_info(
                f"{vargs.project}: backfilled {project.changelog} for {vargs.backfill_tag}; "
                "review and commit this post-release migration explicitly"
            )
        else:
            log_info(f"{vargs.project}: {project.changelog} already records {vargs.backfill_tag}")

    elif verb == "standards":
        from cmru.standards import standards_main
        standards_main(rest)

    elif verb == "tool-deps":
        from cmru.tool_deps import tool_deps_main
        tool_deps_main(rest)

    elif verb in ("release", "status"):
        import argparse as _ap
        parser = _ap.ArgumentParser(description=f"cmru {verb}")
        parser.add_argument("--project", help="Limit to one project")
        parser.add_argument("--minor", action="store_true")
        parser.add_argument("--major", action="store_true")
        parser.add_argument("--set-version", metavar="VER")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-build", action="store_true",
                            help="release: tag + push only; skip build/publish")
        parser.add_argument(
            "--config", help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}"
        )
        parser.add_argument("--resume", metavar="WORKTREE",
                            help="Resume a retained failed release worktree")
        parser.add_argument("--abandon", metavar="WORKTREE|all-previous",
                            help="Discard a retained failed release worktree (or all "
                                 "previous ones whose scope overlaps this run's "
                                 "projects), then proceed with a fresh release")
        parser.add_argument("--allow-uncommitted", action="store_true",
                            help="release: proceed even though a released project's path has "
                                 "local uncommitted changes (which origin/main won't include)")
        parser.add_argument(
            "--allow-tag-ahead-of-head", "--allow-tag-at-head",
            dest="allow_tag_ahead_of_head", action="store_true",
            help="release: downgrade to a skip, instead of aborting, when a project's latest "
                 "tag is pushed but strictly AHEAD of the snapshot commit -- a half-completed "
                 "prior release (S12.2b). --allow-tag-at-head is a deprecated alias: a tag "
                 "exactly AT the snapshot commit is never an error and needs no override.",
        )
        parser.add_argument(
            "--allow-stale-tool-deps", action="store_true",
            help="release: proceed even though a declared tool dependency (S15) is behind the "
                 "highest published release for its project. Never overrides an integrity or "
                 "authenticity failure -- there is no override for either of those.",
        )
        parser.add_argument("--_transaction-child", action="store_true", help=_ap.SUPPRESS)
        parser.add_argument(
            "--show-run-details", action="store_true",
            help="Stream full project subprocess output to this console",
        )
        parser.add_argument(
            "--log-append", action="store_true",
            help="Append a divider and retain existing stable per-step logs",
        )
        parser.add_argument(
            "--retain-logs-on-release", action="store_true",
            help="After a successful release, move project logs into <project>/logs/cmru-release/<tag>",
        )
        parser.add_argument(
            "--retain-artifacts-on-release", action="store_true",
            help="After a successful release, move declared artifacts into <project>/artifacts/<tag>",
        )
        vargs = parser.parse_args(rest)
        _apply_output_options(vargs)
        if getattr(vargs, "resume", None) and getattr(vargs, "abandon", None):
            log_error("--resume and --abandon are mutually exclusive.")
            _sys.exit(2)
        if "--allow-tag-at-head" in rest:
            log_warn("--allow-tag-at-head is a deprecated alias; use --allow-tag-ahead-of-head.")

        cfg_path = _resolve_config(vargs.config)
        (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
        default_projects = _rest[0]
        github_config, env_config = _rest[-2], _rest[-1]
        apply_release_env(github_config, env_config)
        # Restrict versioning verbs to the orchestrated set so un-migrated projects
        # with their own pipelines (tls-edge, empyrion) are never auto-tagged.
        ordered = _ordered_configs(configs, project_order)

        from cmru.version import status_cmd, release_cmd
        if verb == "status":
            status_projects = ordered
            if vargs.project:
                if vargs.project not in ordered:
                    log_error(f"Unknown or non-orchestrated project: {vargs.project}")
                    _sys.exit(2)
                status_projects = {vargs.project: ordered[vargs.project]}
            status_cmd(
                repo_root, status_projects,
                minor=vargs.minor, major=vargs.major, set_version=vargs.set_version,
            )
            return

        if vargs.project and vargs.project not in ordered:
            log_error(f"Unknown or non-orchestrated project: {vargs.project}")
            _sys.exit(2)

        release_scope = [vargs.project] if vargs.project else list(ordered.keys())
        if not vargs.dry_run:
            require_project_publish_credentials(configs, release_scope)

        # The normal command is a launcher, never a publisher from the caller's
        # checkout: origin/main is the only release source, built in an isolated
        # worktree. Local-only *commits* are a fail-closed condition (they are
        # likely intended release inputs — see assert_local_main_not_ahead below).
        # Uncommitted work is fail-closed too, but only when it touches a released
        # project's own path (--allow-uncommitted overrides): otherwise it would be
        # silently left out with no warning, since the build never looks at it.
        if not vargs._transaction_child:
            try:
                child_args = _child_release_args(rest, cfg_path, repo_root)
                with transaction.release_lock(repo_root):
                    scope = [vargs.project] if vargs.project else default_projects
                    if getattr(vargs, "abandon", None):
                        if vargs.abandon == "all-previous":
                            abandoned = transaction.abandon_previous(repo_root, scope)
                            if abandoned:
                                for branch in abandoned:
                                    log_info(f"Abandoned previous release attempt: {branch}")
                            else:
                                log_info("No previous release attempts to abandon.")
                        else:
                            target = transaction.resume_workspace(repo_root, Path(vargs.abandon))
                            transaction.abandon_workspace(repo_root, target)
                            log_info(f"Abandoned release attempt: {target.branch}")

                    # Not --dry-run: a preview has no publish step to protect, and "I have
                    # local edits I haven't committed yet" is exactly when you'd run one.
                    if not vargs.dry_run and not vargs.allow_uncommitted:
                        # release actually iterates `ordered` (== project_order), which is
                        # independently configurable from default_projects (used for the
                        # --abandon scope above) — check what will really run, not a
                        # possibly-narrower-or-wider default.
                        release_scope = [vargs.project] if vargs.project else list(ordered.keys())
                        dirty = _uncommitted_release_paths(repo_root, ordered, release_scope)
                        if dirty:
                            for name, files in dirty.items():
                                log_error(f"{name}: uncommitted changes — {', '.join(files)}")
                            log_error(
                                "Uncommitted local changes touch the project path(s) above. "
                                "origin/main is the only release source, so this run would "
                                "silently leave them out. Commit (and push) them first, or "
                                "pass --allow-uncommitted to release without them."
                            )
                            _sys.exit(2)

                    if getattr(vargs, "resume", None):
                        workspace = transaction.resume_workspace(repo_root, Path(vargs.resume))
                    else:
                        base = transaction.fetch_origin_main(repo_root)
                        behind = transaction.assert_local_main_not_ahead(repo_root)
                        if behind:
                            log_warn(
                                f"Local main is {behind} commit(s) behind origin/main; "
                                f"release uses fetched origin/main {base[:12]}."
                            )
                        workspace = transaction.create_workspace(repo_root, base=base, scope=vargs.project)
                    transaction.copy_secret_overlays(
                        repo_root,
                        workspace,
                        [Path(project.project_root) / PROJECT_CONFIG_FILENAME for project in configs.values()
                         if project.project_root is not None],
                    )
                    log_info(
                        f"Release transaction {workspace.branch}: "
                        f"snapshot {workspace.base[:12]} at {workspace.path}"
                    )
                    rc = transaction.run_child(workspace, child_args)
                    if rc == 0:
                        retained: list[Path] = []
                        if vargs.retain_logs_on_release or vargs.retain_artifacts_on_release:
                            retained = transaction.retain_success_outputs(
                                repo_root,
                                workspace,
                                configs,
                                transaction.read_release_results(repo_root, workspace),
                                retain_logs=vargs.retain_logs_on_release,
                                retain_artifacts=vargs.retain_artifacts_on_release,
                            )
                        for path in retained:
                            log_info(f"Retained release output: {path}")
                        transaction.remove_backup_branch(workspace)
                        transaction.remove_workspace(workspace)
                        transaction.forget_release_scope(repo_root, workspace)
                        if transaction.sync_local_main(repo_root):
                            log_info("Local main synced with origin/main.")
                        else:
                            log_warn(
                                "Could not sync local main automatically (a rebase "
                                "conflict); resolve manually — `git rebase origin/main`."
                            )
                        log_info("Release transaction complete; isolated worktree removed.")
                    elif transaction.plan_was_refused(repo_root, workspace):
                        # The release plan itself refused (S12.2a/S12.2b) before any
                        # project's cycle started: no gate ran, nothing was promoted or
                        # tagged, and `push_backup_branch` never ran either -- there is
                        # nothing here worth retaining a worktree for, unlike a genuine
                        # mid-release failure. Discard it exactly like a success would
                        # (the detailed refusal was already printed by the child above).
                        transaction.remove_workspace(workspace)
                        transaction.forget_release_scope(repo_root, workspace)
                        transaction.sync_local_main(repo_root)
                        log_error(
                            "Release plan refused before any project started; no changes "
                            "were made (see the error above). Worktree discarded."
                        )
                    else:
                        if transaction.promotion_landed(repo_root, workspace):
                            # Projects release one after another (each project's own
                            # prepare/gate/promote/tag/build/publish finishes before the next
                            # starts — S-REL). checkpoint is the source commit as of the last
                            # project to fully finish (may equal workspace.base, e.g. when
                            # every earlier project in this run had no prepare-step commit of
                            # its own — their tags/artifacts are unaffected either way, only
                            # source-tree commits are ever reverted). Reverting from checkpoint
                            # rather than workspace.base leaves any earlier project's own
                            # promoted commit alone.
                            checkpoint = transaction.read_release_progress(repo_root, workspace)
                            log_error(
                                "Release failed after origin/main was already promoted; "
                                "attempting automatic revert of the in-flight project's "
                                "changes..."
                            )
                            revert = transaction.revert_promotion(workspace, from_sha=checkpoint)
                            if revert.ok and revert.reverted:
                                log_info("origin/main reverted to its last-known-good state.")
                            elif revert.ok:
                                log_info(
                                    "Nothing to revert on origin/main — the in-flight project "
                                    "never got as far as its own promotion."
                                )
                            else:
                                log_error(
                                    "Automatic revert did not apply cleanly (origin/main may "
                                    "have advanced, or the revert conflicted) — manual cleanup "
                                    f"required: inspect branch {workspace.branch}."
                                )
                        transaction.sync_local_main(repo_root)
                        log_error(
                            f"Release transaction failed; retained {workspace.path} "
                            f"on branch {workspace.branch} for inspection/resume."
                        )
                    _sys.exit(rc)
            except Exception as exc:
                log_error(str(exc))
                _sys.exit(1)

        # --- release: detect → tag → push → build → publish -------------------
        from cmru.version import ReleasePlanRefused, release_cmd, detect_changed_projects

        # Scope to what this run will actually touch *before* computing the plan
        # (not by filtering the result afterward): an unrelated orchestrated
        # project's degenerate tag state must not abort a `--project`-scoped run
        # that never touches it.
        release_scope = [vargs.project] if vargs.project else list(ordered.keys())
        scoped_for_plan = {name: ordered[name] for name in release_scope}
        # S12.2a/S12.2b (KI-12): the release plan — unlike a read-only `status`
        # preview or a `changelog` migration — MUST be a function of the pushed
        # repository, and a tag pushed but strictly ahead of the snapshot commit
        # MUST abort loudly rather than look identical to a genuinely unchanged
        # project (S-CLI.5: continuing here would silently produce an empty
        # release). `--allow-tag-ahead-of-head` downgrades only that one check;
        # a tag exactly AT the snapshot commit is always reported and skipped,
        # never an error. A refusal here means no project's cycle ever started
        # -- nothing was gated, promoted, or tagged -- so the parent discards
        # this worktree exactly like a success, never retaining it for
        # inspection (there would be nothing there to inspect).
        try:
            changed = detect_changed_projects(
                repo_root, scoped_for_plan,
                require_pushed_baseline=True,
                check_tag_at_head=True,
                allow_tag_ahead_of_head=vargs.allow_tag_ahead_of_head,
            )
        except ReleasePlanRefused as exc:
            log_error(str(exc))
            transaction.mark_plan_refused(repo_root, _transaction_workspace_from_env(repo_root))
            _sys.exit(exit_codes.FAILURE)
        changed_names = {c[0] for c in changed}

        release_names = [name for name in project_order if name in changed_names]
        if release_names:
            log_info(
                f"Release plan: {len(release_names)}/{len(project_order)} project(s) changed "
                f"— releasing in order: {', '.join(release_names)}"
            )
        else:
            log_info("Release plan: no changed projects detected; nothing to release.")
        # KI-13/S12.2e: every unchanged project already printed its own specific
        # "[INFO] Unchanged, skipping: <name> (<baseline tag> @ <reason>)" line
        # above, from inside detect_changed_projects (this isolated transaction
        # always passes check_tag_at_head=True) -- one line per project naming
        # its exact baseline and reason, not a second, bare name-only list here.
        # This also runs identically whether or not --dry-run is set (KI-14):
        # the plan above is computed once, before the dry-run/real branch below.

        # S15: declared tool dependencies (e.g. cmru's own vendored assay zipapp)
        # are verified here, alongside the tag-preflight above -- same phase (no
        # project's cycle has started), same network-touching plan-computation
        # step, same typed refusal. This is deliberately scoped to `release_names`
        # (what THIS run will actually ship), so an unrelated orchestrated
        # project's stale/unreachable tool dependency never blocks a run that
        # never touches it -- and NEVER runs when nothing changed (no network
        # call for a no-op run). It runs identically for --dry-run too, for the
        # same S-CLI.5c reason the tag preflight above does.
        try:
            _check_release_tool_dependencies(
                {name: configs[name] for name in release_names},
                configs,
                github_config=github_config,
                allow_stale=vargs.allow_stale_tool_deps,
            )
        except ReleasePlanRefused as exc:
            log_error(str(exc))
            transaction.mark_plan_refused(repo_root, _transaction_workspace_from_env(repo_root))
            _sys.exit(exit_codes.FAILURE)

        if vargs.dry_run:
            # Preview only: show what would be tagged for every changed project, no
            # commits/gates/promotion/tags — nothing here has side effects.
            release_cmd(
                repo_root, ordered,
                project_filter=vargs.project,
                minor=vargs.minor, major=vargs.major, set_version=vargs.set_version,
                dry_run=True,
            )
            log_info("[DRY RUN] No tags pushed, nothing built/published.")
            return

        if not release_names:
            log_info("Nothing to release (no changed projects).")
            return

        # Build all projects after another (S-REL): each project's own
        # prepare → gate → promote → tag → build → publish cycle runs to completion
        # before the next project starts. This is what lets a later project (e.g. an
        # OCI image) resolve an earlier project's (e.g. a wheel) brand-new release
        # within this SAME `cmru release` run, instead of always trailing one run
        # behind. Each project's promotion is independent: if project N fails, the
        # already-published projects before it are left alone (see
        # transaction.write_release_progress / the parent's scoped-revert handling).
        workspace = _transaction_workspace_from_env(repo_root)
        transaction.write_release_scope(repo_root, workspace, release_names)
        transaction.push_backup_branch(workspace)
        log_info(f"Backed up release branch {workspace.branch} to origin (durability).")

        released = _release_projects_sequentially(
            repo_root, configs, workspace, release_names,
            github_config=github_config, env_config=env_config,
            no_build=vargs.no_build, minor=vargs.minor, major=vargs.major,
            set_version=vargs.set_version,
        )

        if released:
            log_info(f"Released: {', '.join(released)}")
        elif vargs.no_build:
            log_info("Tagged only (--no-build); nothing built or published.")
        else:
            log_info("Nothing built or published (see per-project log above for why).")

    elif verb == "cleanup":
        import argparse as _ap
        parser = _ap.ArgumentParser(
            description=(
                "cmru cleanup — delete old Releases, stale tags, and prune ghcr.\n\n"
                "Without --remove-assets: project-aware cleanup driven by [cleanup] config\n"
                "  (keeps <prefix>-latest + keep_release_tags; deletes the rest).\n"
                "With --remove-assets AGE: age-based cleanup.\n"
                "With --delete-unmanaged-release-tag TAG: delete that exact old GitHub Release only; "
                "requires --project and --yes (or --dry-run).\n"
                "With --delete-build-output ID: delete one verified local non-release build record; "
                "requires --project and --yes (or --dry-run).\n"
                "With --discard-build-worktree PATH: delete one inspected failed build worktree; "
                "requires --yes (or --dry-run)."
            ),
            formatter_class=_ap.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "--remove-assets", metavar="AGE",
            help="Age-based cleanup: remove Releases/ghcr versions older than AGE (e.g. 30d, 2w)",
        )
        parser.add_argument("--project", help="Limit or scope a project-aware cleanup operation")
        parser.add_argument("--dry-run", action="store_true",
                            help="List what would be deleted without deleting")
        parser.add_argument(
            "--delete-unmanaged-release-tag", metavar="TAG",
            help="Delete one explicitly named non-CMRU GitHub Release, never its Git tag",
        )
        parser.add_argument(
            "--delete-build-output", metavar="ID",
            help="Delete one exact local build record (<commit-date>_<40-hex-commit>)",
        )
        parser.add_argument(
            "--discard-build-worktree", metavar="PATH",
            help="Discard one exact inspected failed cmru-build-* worktree",
        )
        parser.add_argument(
            "--yes", action="store_true",
            help="Confirm an exact destructive cleanup target (not required with --dry-run)",
        )
        parser.add_argument(
            "--config", help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}"
        )
        vargs = parser.parse_args(rest)

        cfg_path = _resolve_config(vargs.config)

        (repo_root, configs, project_order, _default_projects, _default_steps,
         _execution_mode, _step_project_order, cleanup, github_config, env_config) = load_config(cfg_path)

        exclusive_modes = [
            ("--remove-assets", vargs.remove_assets),
            ("--delete-unmanaged-release-tag", vargs.delete_unmanaged_release_tag),
            ("--delete-build-output", vargs.delete_build_output),
            ("--discard-build-worktree", vargs.discard_build_worktree),
        ]
        selected_modes = [name for name, value in exclusive_modes if value]
        if len(selected_modes) > 1:
            parser.error(f"{' and '.join(selected_modes)} are mutually exclusive")

        if vargs.delete_unmanaged_release_tag:
            if not vargs.project:
                parser.error("--delete-unmanaged-release-tag requires --project to scope the operation")
            if not (vargs.yes or vargs.dry_run):
                parser.error("--delete-unmanaged-release-tag requires --yes (or --dry-run)")
            project = configs.get(vargs.project)
            if project is None:
                parser.error(f"unknown project: {vargs.project}")
            prefix = project.prefix or ""
            bare = _bare_prefix(prefix)
            tag = vargs.delete_unmanaged_release_tag
            if not bare or not tag.startswith(f"{bare}-"):
                parser.error(
                    f"{tag!r} is outside project {vargs.project!r}'s release namespace {bare!r}"
                )
            if tag.startswith(prefix) or tag == f"{bare}-latest":
                parser.error(
                    f"{tag!r} is CMRU-managed; use ordinary cleanup policy, not "
                    "--delete-unmanaged-release-tag"
                )
            project_github = github_for_project(github_config, project)
            if not project_github.token:
                parser.error("an explicit CMRU publish credential is required to delete a GitHub Release")
            delete_unmanaged_release_tag(
                project_github.owner, project_github.repo, project_github.token, tag,
                dry_run=vargs.dry_run,
            )
        elif vargs.delete_build_output:
            if not vargs.project:
                parser.error("--delete-build-output requires --project to scope the operation")
            if not (vargs.yes or vargs.dry_run):
                parser.error("--delete-build-output requires --yes (or --dry-run)")
            project = configs.get(vargs.project)
            if project is None:
                parser.error(f"unknown project: {vargs.project}")
            targets = transaction.delete_retained_build_output(
                repo_root, project, vargs.project, vargs.delete_build_output,
                dry_run=vargs.dry_run,
            )
            action = "Would delete" if vargs.dry_run else "Deleted"
            for target in targets:
                log_info(f"{action} retained local build output: {target}")
        elif vargs.discard_build_worktree:
            if vargs.project:
                parser.error("--discard-build-worktree is already exactly scoped; do not pass --project")
            if not (vargs.yes or vargs.dry_run):
                parser.error("--discard-build-worktree requires --yes (or --dry-run)")
            workspace = transaction.discard_build_workspace(
                repo_root, Path(vargs.discard_build_worktree), dry_run=vargs.dry_run,
            )
            action = "Would discard" if vargs.dry_run else "Discarded"
            log_info(f"{action} retained build worktree: {workspace.path} ({workspace.branch})")
        elif vargs.remove_assets:
            # Explicit age-based cleanup mode.
            remove_assets(vargs.remove_assets, vargs.dry_run, cleanup, github_config, env_config)
        else:
            # New project-aware cleanup: keep -latest + keep_release_tags, delete the rest.
            run_cleanup_verb(
                repo_root, configs, project_order, cleanup,
                github_config, env_config,
                project_filter=vargs.project,
                dry_run=vargs.dry_run,
            )

    else:
        log_error(f"Unknown verb '{verb}'. Run 'cmru --help' for usage.")
        _sys.exit(2)


if __name__ == "__main__":
    main()
