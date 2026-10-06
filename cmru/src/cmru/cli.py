#!/usr/bin/env python3
"""Unified release orchestration for vbpub projects."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, List, Mapping, NoReturn, Optional, Sequence
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import tomllib

from cmru.runner import StepConfig, execute_step, parse_step as _runner_parse_step
from cmru import transaction
from cmru import exit_codes
from cmru.git_auth import GitHubGitAuth, run_local_git, run_remote_git
from cmru.config import _RESERVED_CMRU_INTERNAL_ENV, is_reserved_internal_env, load_forge_config
from cmru.config import InvocationContext, resolve_invocation_context
from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME
from cmru.cli_support import (
    TargetSelectionError,
    select_target_names,
    write_config_diagnostic,
)
from cmru.dependencies import build_report, render_text as render_dependency_report
from cmru.errors import (
    CmruError,
    CredentialMissing,
    RefusedBeforeChange,
    StepUnavailable,
    UnsafeRecord,
    UsageRefusal,
)


# The failures a root verb can legitimately hit (bad config, a refused git or
# network operation, a precondition the transaction layer rejects). Verb
# boundaries translate exactly these to a clean ``[ERROR]`` + exit 1; anything
# else is a programming error and must reach the library's unexpected-exception
# handling with its traceback instead of being flattened to ``str(exc)`` (CLI-06).
_DOMAIN_ERRORS = (RuntimeError, OSError, ValueError, subprocess.SubprocessError)

_RELEASE_PREFLIGHT_SNAPSHOT_FD_ENV = "CMRU_INTERNAL_RELEASE_PREFLIGHT_FD"
_ACTIVE_RELEASE_PREFLIGHT_SNAPSHOT: str | None = None


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
    evidence_paths: tuple[str, ...] = ()  # declared project-relative gate evidence paths
    build_step: str = ""                # explicit [project.release].build_step
    github_token: str = ""              # root credential or explicit project-secret override
    runtime_kind: str = "none"           # declared lifecycle owner: none | ciu
    # First-party artifacts this project's own tests/tooling consume (S15). Empty ⇒
    # no declared tool dependency (today's behaviour, unchanged).
    tool_dependencies: tuple = ()


@dataclass(frozen=True)
class CleanupConfig:
    release_tag_prefixes: List[str]
    keep_release_tags: List[str]
    ghcr_packages: List[str]
    ghcr_delete_packages: List[str]


@dataclass
class CleanupPlan:
    """Exact cleanup actions collected for display before confirmation."""

    actions: list[tuple[str, Callable[[], None]]] = field(default_factory=list)

    def add(self, description: str, action: Callable[[], None]) -> None:
        self.actions.append((description, action))

    def apply(self) -> None:
        for description, action in self.actions:
            log_info(f"Applying confirmed cleanup action: {description}")
            action()


@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    token: str
    owner_type: str  # required: "user" | "org"  (V03; replaces the modern-debian-tools probe)


def _git_auth_for_repository(github: GitHubConfig) -> GitHubGitAuth:
    """Bind CMRU Git transport to the root-resolved repository credential."""
    return GitHubGitAuth(owner=github.owner, repo=github.repo, token=github.token)


def _read_origin_tag_refs(
    repo_root: Path, *, git_auth: GitHubGitAuth, context: str,
) -> dict[str, str]:
    result = run_remote_git(
        repo_root, "ls-remote", "--tags", "origin",
        auth=git_auth, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot {context}: " + (result.stderr.strip() or "git ls-remote failed")
        )
    return transaction.parse_ls_remote_refs(
        result.stdout, namespace="refs/tags/", description=context,
    )


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


def _sync_local_main_and_report(
    repo_root: Path, *, git_auth: GitHubGitAuth | None = None,
) -> bool:
    """Attempt caller-main cleanup and expose every false result accurately."""
    result = transaction._sync_local_main_result(repo_root, git_auth=git_auth)
    if result.ok:
        return True
    log_warn(result.reason)
    return False


def _apply_output_options(args: object) -> None:
    """Carry explicit console/logging choices into all child step processes."""
    if getattr(args, "show_run_details", False):
        os.environ["CMRU_INTERNAL_SHOW_RUN_DETAILS"] = "1"
    if getattr(args, "log_append", False):
        os.environ["CMRU_INTERNAL_LOG_APPEND"] = "1"


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
    *,
    protected_env: Optional[Mapping[str, str]] = None,
    project_root_override: Optional[Path] = None,
) -> None:
    """Route a project step through the unified runner contract (S3).

    Every phase is declared in the project's strict ``[steps.<name>]`` contract.
    CMRU never synthesizes an artifact-specific command or silently skips a
    requested phase."""
    # ``cwd`` is derived from the declared project config's location by
    # load_config. Derive the execution root again from the selected repository
    # snapshot so the same contract always targets the child worktree, never the
    # caller checkout.
    project_root = (
        Path(project_root_override).resolve()
        if project_root_override is not None
        else resolve_cwd(repo_root, _project_working_directory(project))
    )
    step = (project.runner_steps or {}).get(step_name)
    if step is None:
        raise StepUnavailable(f"{project.name}: required declared step {step_name!r} is absent")
    # Detailed records are project-local.  A successful release removes the
    # transaction worktree (and therefore these logs) unless the caller elected
    # to retain them after verified completion.
    stable_log_root = project_root / "logs" / "cmru"
    context_keys = (
        "CMRU_WORKSPACE_ID", "CMRU_WORKSPACE_PATH", "CMRU_SOURCE_GIT_ROOT",
        "CMRU_RELEASE_BRANCH", "CMRU_RELEASE_BASE",
    )
    has_transaction_context = transaction.is_transaction_child(repo_root)
    ambient_context = {key: os.environ.get(key) for key in context_keys}
    if not has_transaction_context:
        # A direct caller may have sourced a sibling's shell exports. Those
        # values are not a CMRU context and must not be handed to a project
        # runner as if this invocation owned that workspace.
        for key in context_keys:
            os.environ.pop(key, None)
    try:
        with tempfile.TemporaryDirectory(prefix="cmru-runtime-") as launcher_root:
            launcher_dir = Path(launcher_root)
            launcher = _create_bound_cmru_launcher(launcher_dir)
            internal_env = {
                transaction.INTERNAL_BIN_ENV: str(launcher),
                "CMRU_RUNTIME_KIND": getattr(project, "runtime_kind", "none"),
                **{
                    key: os.environ[key]
                    for key in context_keys
                    if os.environ.get(key)
                },
                **dict(protected_env or {}),
            }
            resolved_token = (getattr(project, "github_token", None) or "").strip()
            if resolved_token:
                # This is applied after project/step env and env commands by
                # runner.execute_step. Keep the publisher credential bound to
                # the token that config resolution selected for this project.
                internal_env["GITHUB_PUSH_PAT"] = resolved_token
            execute_step(
                step,
                project_root,
                stable_log_root,
                extra_env=dict(project.env),
                protected_env=internal_env,
                path_prefixes=(launcher_dir,),
                build_metadata=project.build_metadata,
            )
    finally:
        if not has_transaction_context:
            for key, value in ambient_context.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _create_bound_cmru_launcher(directory: Path) -> Path:
    """Create and verify a `cmru` command bound to this running module/interpreter."""
    from cmru.cli_support import cmru_version

    launcher = directory / "cmru"
    module_root = Path(__file__).resolve().parent.parent
    repo_root = module_root.parent.parent
    import_roots = [module_root]
    # D10: cli-extended is a real wheel dependency now, so a source-tree
    # checkout is never preferred over the installed release. Only the
    # not-yet-published worktree library keeps a checkout import root.
    worktree_root = repo_root / "libraries/worktree/src"
    if worktree_root.is_dir():
        import_roots.append(worktree_root)
    program = (
        "import sys; "
        f"sys.path[:0] = {[str(path) for path in import_roots]!r}; "
        "from cmru.cli import main; "
        "raise SystemExit(main())"
    )
    launcher.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(sys.executable)} -c {shlex.quote(program)} \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o700)

    path_value = os.pathsep.join((str(directory), os.environ.get("PATH", "")))
    resolved = shutil.which("cmru", path=path_value)
    if resolved is None or Path(resolved).resolve() != launcher.resolve():
        raise RuntimeError("CMRU runtime binding failed: project PATH does not resolve to this launcher")

    result = subprocess.run(
        [str(launcher), "version"], capture_output=True, text=True, check=False,
        env={**os.environ, "PATH": path_value},
    )
    expected = f"cmru {cmru_version()}"
    if result.returncode != 0 or result.stdout.strip() != expected or result.stderr:
        raise RuntimeError(
            "CMRU runtime binding failed identity verification before project command: "
            f"expected {expected!r}, got stdout={result.stdout.strip()!r}, "
            f"stderr={result.stderr.strip()!r}, exit={result.returncode}"
        )
    return launcher


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


def _project_working_directory(project: "ProjectConfig") -> str:
    """Return the project-relative directory derived from its config path."""
    cwd = project.cwd
    if not cwd:
        raise StepUnavailable(f"{project.name}: derived project working directory is absent")
    return cwd


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
    resolved_config_path = config_path.expanduser().resolve()
    forge = load_forge_config(resolved_config_path)
    if (
        validate_dependencies
        and resolved_config_path.name == ORCHESTRATION_CONFIG_FILENAME
    ):
        report = build_report(
            repo_root=forge.repo_root,
            project_order=forge.orchestration.project_order,
            declared=forge.orchestration.dependencies,
            projects=forge.projects,
        )
        if report.errors:
            for error in report.errors:
                write_config_diagnostic(f"dependency preflight: {error}")
            raise SystemExit(exit_codes.CONFIG_ERROR)
    orchestration = forge.orchestration
    if orchestration is None:  # defensive: both strict loaders always supply one
        raise ValueError("cmru configuration has no project selection")
    orchestration_root = forge.repo_root
    # A CMRU orchestration root may intentionally sit above the Git family it
    # drives. In a transaction child, keep loading the authoritative central
    # document, but remap each project document into the isolated source
    # worktree before constructing executable commands.
    child_context_valid = False
    if os.environ.get(transaction.CHILD_ENV) == "1":
        child_workspace = os.environ.get("CMRU_WORKSPACE_PATH")
        # The shared validator owns completeness and identity checks. The
        # orchestration root is only a placeholder when workspace is absent;
        # validation refuses incomplete context before comparing the path.
        candidate_root = (
            Path(child_workspace).expanduser().resolve()
            if child_workspace
            else orchestration_root
        )
        child_context_valid = transaction.is_transaction_child(candidate_root)
    if child_context_valid:
        child_workspace = os.environ["CMRU_WORKSPACE_PATH"]
        child_source_root = os.environ["CMRU_SOURCE_GIT_ROOT"]
        execution_root = Path(child_workspace).expanduser().resolve()
        source_git_root = Path(child_source_root).expanduser().resolve()
    else:
        execution_root = orchestration_root
        source_git_root = orchestration_root
    repo_root = execution_root
    transaction_scope = os.environ.get("CMRU_TRANSACTION_PROJECTS")
    if transaction_scope and child_context_valid:
        requested_names = [item for item in transaction_scope.split(",") if item]
        if not requested_names or len(set(requested_names)) != len(requested_names):
            raise ValueError("CMRU_TRANSACTION_PROJECTS must contain unique project names")
        unknown = sorted(set(requested_names) - set(forge.projects))
        if unknown:
            raise ValueError(
                "CMRU_TRANSACTION_PROJECTS names unknown project(s): " + ", ".join(unknown)
            )
        selected_project_names = [
            name for name in orchestration.project_order if name in set(requested_names)
        ]
    else:
        selected_project_names = list(forge.projects)
    projects: dict[str, ProjectConfig] = {}
    for name in selected_project_names:
        parsed = forge.projects[name]
        source_project_config_path = orchestration.project_configs[name]
        try:
            configured_project_root = source_project_config_path.parent.resolve()
            # The central orchestration file may itself have been loaded from
            # the isolated worktree. In that case its project paths already
            # describe the execution snapshot and must not be joined to that
            # snapshot a second time. Configurations intentionally kept outside
            # the checkout still map from the caller's source Git root.
            try:
                project_rel_to_execution = configured_project_root.relative_to(execution_root)
            except ValueError:
                project_rel_to_execution = configured_project_root.relative_to(source_git_root)
        except ValueError as exc:
            raise ValueError(
                f"{name}: project root {source_project_config_path.parent} is outside "
                f"the selected Git root {source_git_root}"
            ) from exc
        project_config_path = (
            execution_root / project_rel_to_execution / PROJECT_CONFIG_FILENAME
        ).resolve()
        if not project_config_path.is_file():
            raise ValueError(
                f"{name}: transaction project config is missing from the isolated "
                f"worktree: {project_config_path}"
            )
        with project_config_path.open("rb") as handle:
            document = tomllib.load(handle)
        project_raw = document["project"]
        steps_raw = document["steps"]
        project_root = project_config_path.parent.resolve()
        try:
            project_rel = project_root.relative_to(execution_root)
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
            evidence_paths=tuple(parsed.evidence_paths),
            build_step=parsed.build_step,
            github_token=forge.project_tokens.get(name, forge.github.token or ""),
            tool_dependencies=tuple(parsed.tool_dependencies),
            runtime_kind=parsed.runtime_kind,
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
        [name for name in orchestration.project_order if name in projects],
        [],  # reserved slot: orchestration.default_projects was never read (CLI-04)
        orchestration.default_steps,
        orchestration.execution_mode,
        {},
        cleanup,
        github_config,
        env_config,
    )


def apply_release_env(github: GitHubConfig, env_config: ReleaseEnvConfig) -> None:
    reserved_credentials = {"GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN"}
    configured_credentials = sorted(reserved_credentials.intersection(env_config.env))
    if configured_credentials:
        raise UsageRefusal(
            "release environment key(s) are reserved for resolved publisher credentials: "
            + ", ".join(configured_credentials)
        )
    configured_internal = sorted(name for name in env_config.env if is_reserved_internal_env(name))
    if configured_internal:
        raise UsageRefusal(
            "release environment key(s) are reserved for CMRU internal launch state: "
            + ", ".join(configured_internal)
        )
    if github.owner:
        os.environ["GITHUB_USERNAME"] = github.owner
    if github.repo:
        os.environ["GITHUB_REPO"] = github.repo
    # Clear inherited credentials before applying ordinary declared settings.
    # Config loading has already captured an explicitly supplied environment
    # credential, so this cannot discard a source of truth before resolution.
    os.environ.pop("GITHUB_PUSH_PAT", None)
    os.environ.pop("GITHUB_TOKEN", None)
    os.environ.pop("CMRU_GIT_AUTH_TOKEN", None)
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

    # Each project operation receives exactly its already-resolved credential.
    # Apply it after declared values so an environment table cannot replace the
    # selected token. The strict config reader rejects credential keys there too.
    if github.token:
        os.environ["GITHUB_PUSH_PAT"] = github.token


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
        raise CredentialMissing(
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
        # setuptools-scm normalises every run of "-", "_" and "." in the dist name.
        env_name = "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_" + re.sub(
            r"[-_.]+", "_", project.scm_dist,
        ).upper()
        # git describe --exact-match legitimately exits non-zero (128) whenever HEAD
        # isn't exactly on one of THIS project's tags — the normal case for every
        # scm_dist project except whichever one is currently being tagged/built (with
        # multiple projects releasing one after another in the same worktree, S-CLI.5a,
        # that's most of them most of the time). Not a `_git()` failure to raise on.
        probe = subprocess.run(
            ["git", "-C", str(repo_root), "describe", "--tags", "--exact-match", "--match", f"{prefix_tag}*"],
            capture_output=True, text=True,
        )
        exact = probe.stdout.strip() if probe.returncode == 0 else ""
        if not exact:
            # BG-09: a stale inherited pretend-version would be forwarded into the
            # wheel builder and let an untagged build claim a release version.
            os.environ.pop(env_name, None)
            continue
        semver = exact[len(prefix_tag):]
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


def _release_asset_inventory(release: dict) -> tuple[tuple[object, ...], ...]:
    """Return stable asset identity fields for a confirmed Release deletion."""
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("GitHub Release response has no usable asset inventory")
    inventory = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise RuntimeError("GitHub Release response has a malformed asset inventory")
        asset_id = asset.get("id")
        name = asset.get("name")
        size = asset.get("size")
        state = asset.get("state")
        updated_at = asset.get("updated_at")
        digest = asset.get("digest")
        if (
            type(asset_id) is not int or not isinstance(name, str)
            or type(size) is not int or not isinstance(state, str)
            or not isinstance(updated_at, str)
            or (digest is not None and not isinstance(digest, str))
        ):
            raise RuntimeError("GitHub Release response has a malformed asset inventory")
        inventory.append((asset_id, name, size, state, updated_at, digest))
    if len({asset[0] for asset in inventory}) != len(inventory):
        raise RuntimeError("GitHub Release response has duplicate asset IDs")
    return tuple(sorted(inventory, key=lambda asset: asset[0]))


def _delete_release_if_tag_still_matches(
    owner: str,
    repo: str,
    token: str,
    release_id: int,
    expected_tag: str,
    *,
    expected_updated_at: object,
    expected_asset_inventory: tuple[tuple[object, ...], ...],
    eligible: Callable[[dict], bool],
) -> bool:
    """Apply a confirmed Release deletion only while its identity and assets match."""
    matches = [
        release for release in list_releases(owner, repo, token)
        if release.get("id") == release_id
    ]
    if len(matches) != 1:
        log_warn(
            f"Cleanup: confirmed GitHub Release id={release_id} disappeared or became ambiguous; skipping."
        )
        return False
    current_tag = matches[0].get("tag_name") or ""
    if (
        current_tag != expected_tag
        or matches[0].get("updated_at") != expected_updated_at
        or _release_asset_inventory(matches[0]) != expected_asset_inventory
        or not eligible(matches[0])
    ):
        log_warn(
            f"Cleanup: GitHub Release id={release_id} changed or no longer qualifies after preview "
            "(tag, update time, or asset inventory changed); "
            f"expected selected tag {expected_tag!r}, found {current_tag!r}; skipping."
        )
        return False
    delete_release(owner, repo, token, release_id, dry_run=False)
    return True


def delete_unmanaged_release_tag(
    owner: str,
    repo: str,
    token: str,
    tag: str,
    *,
    dry_run: bool,
    plan: CleanupPlan | None = None,
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
        expected_updated_at = matches[0].get("updated_at")
        expected_asset_inventory = _release_asset_inventory(matches[0])
        log_info(
            f"[DRY RUN] Would delete unmanaged GitHub Release {tag} "
            f"(id={release_id}); its Git tag is untouched."
        )
        if plan is not None:
            plan.add(
                f"unmanaged GitHub Release {tag} (id={release_id})",
                lambda owner=owner, repo=repo, token=token, release_id=release_id,
                expected_tag=tag, expected_updated_at=expected_updated_at,
                expected_asset_inventory=expected_asset_inventory:
                    _delete_release_if_tag_still_matches(
                        owner, repo, token, release_id, expected_tag,
                        expected_updated_at=expected_updated_at,
                        expected_asset_inventory=expected_asset_inventory,
                        eligible=lambda _release: True,
                    ),
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
    *,
    plan: CleanupPlan | None = None,
) -> None:
    releases = list_releases(owner, repo, token)
    # Cleanup is destructive. An empty declared selector means "select nothing",
    # never "all releases"; an estate that means all must say "*" explicitly.
    selected_prefixes = tuple(cleanup.release_tag_prefixes)
    selected_keep_tags = frozenset(cleanup.keep_release_tags)
    for release in releases:
        tag = release.get("tag_name") or ""
        published_at = release.get("published_at") or release.get("created_at") or release.get("updated_at")
        if not _release_is_selected_for_age_cleanup(
            release, cutoff=cutoff, prefixes=selected_prefixes,
            keep_tags=selected_keep_tags,
        ):
            continue
        release_id = release.get("id")
        if not release_id:
            continue
        log_info(f"Deleting release tag {tag} (published {published_at})")
        release_id = int(release_id)
        if dry_run and plan is not None:
            expected_updated_at = release.get("updated_at")
            expected_asset_inventory = _release_asset_inventory(release)
            plan.add(
                f"GitHub Release {tag} (id={release_id})",
                lambda owner=owner, repo=repo, token=token, release_id=release_id,
                expected_tag=tag, expected_updated_at=expected_updated_at,
                expected_asset_inventory=expected_asset_inventory:
                    _delete_release_if_tag_still_matches(
                        owner, repo, token, release_id, expected_tag,
                        expected_updated_at=expected_updated_at,
                        expected_asset_inventory=expected_asset_inventory,
                        eligible=lambda current, cutoff=cutoff,
                        prefixes=selected_prefixes, keep_tags=selected_keep_tags:
                            _release_is_selected_for_age_cleanup(
                                current, cutoff=cutoff, prefixes=prefixes,
                                keep_tags=keep_tags,
                            ),
                    ),
            )
        else:
            delete_release(owner, repo, token, release_id, dry_run)


def _release_is_selected_for_age_cleanup(
    release: dict,
    *,
    cutoff: datetime,
    prefixes: Sequence[str],
    keep_tags: frozenset[str],
) -> bool:
    """Re-evaluate the captured release policy against the current Release record."""
    tag = release.get("tag_name") or ""
    if tag in keep_tags:
        return False
    if "*" not in prefixes and not any(tag.startswith(prefix) for prefix in prefixes):
        return False
    published_at = release.get("published_at") or release.get("created_at") or release.get("updated_at")
    if not published_at:
        return False
    published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return published_dt < cutoff


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


def get_package_version(
    owner: str, package: str, token: str, version_id: int, owner_type: str,
) -> dict | None:
    """Re-fetch a GHCR version; 404 can mean absent or inaccessible."""
    if owner_type == "org":
        url = f"https://api.github.com/orgs/{owner}/packages/container/{package}/versions/{version_id}"
    else:
        url = f"https://api.github.com/users/{owner}/packages/container/{package}/versions/{version_id}"
    status, body, _ = http_request("GET", url, token)
    if status == 404:
        return None
    if status >= 400:
        raise RuntimeError(f"Failed to recheck {package} version {version_id}: {body}")
    try:
        version = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"GitHub returned invalid JSON while rechecking {package} version {version_id}"
        ) from exc
    if (
        not isinstance(version, dict)
        or type(version.get("id")) is not int
        or version["id"] != version_id
    ):
        raise RuntimeError(
            f"GitHub returned a malformed record while rechecking {package} version {version_id}"
        )
    return version


def _container_version_tags(version: dict) -> tuple[str, ...]:
    metadata = version.get("metadata")
    container = metadata.get("container") if isinstance(metadata, dict) else None
    tags = container.get("tags") if isinstance(container, dict) else None
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise RuntimeError("GitHub package version response has no usable container tag inventory")
    return tuple(sorted(tags))


def _package_version_timestamp(version: dict) -> str | None:
    timestamp = version.get("updated_at") or version.get("created_at")
    return timestamp if isinstance(timestamp, str) else None


def _delete_package_version_if_unchanged(
    owner: str,
    package: str,
    token: str,
    version_id: int,
    owner_type: str,
    cutoff: datetime,
    expected_timestamp: str,
    expected_tags: tuple[str, ...],
) -> None:
    current = get_package_version(owner, package, token, version_id, owner_type)
    if current is None:
        log_warn(
            f"Cleanup: GitHub returned 404 for GHCR {package} version {version_id} "
            "after preview; it may be absent or inaccessible, so cleanup cannot be "
            "verified and deletion is skipped."
        )
        return
    current_timestamp = _package_version_timestamp(current)
    if (
        current_timestamp != expected_timestamp
        or _container_version_tags(current) != expected_tags
        or datetime.fromisoformat(current_timestamp.replace("Z", "+00:00")) >= cutoff
    ):
        log_warn(
            f"Cleanup: GHCR {package} version {version_id} changed or no longer qualifies "
            "after preview; skipping."
        )
        return
    delete_package_version(owner, package, token, version_id, owner_type, dry_run=False)


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


def get_container_package(
    owner: str, package: str, token: str, owner_type: str,
) -> dict | None:
    """Return a package record; a 404 can mean absent or inaccessible."""
    if owner_type == "org":
        url = f"https://api.github.com/orgs/{owner}/packages/container/{package}"
    else:
        url = f"https://api.github.com/users/{owner}/packages/container/{package}"
    status, body, _ = http_request("GET", url, token)
    if status == 404:
        return None
    if status >= 400:
        raise RuntimeError(f"Failed to inspect GHCR package {package}: {body}")
    try:
        record = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitHub returned invalid JSON while inspecting GHCR package {package}") from exc
    if (
        not isinstance(record, dict)
        or record.get("package_type") != "container"
        or type(record.get("id")) is not int
        or record["id"] <= 0
        or not isinstance(record.get("name"), str)
        or record["name"].casefold() != package.casefold()
    ):
        raise RuntimeError(f"GitHub returned a malformed record while inspecting GHCR package {package}")
    return record


def _delete_container_package_if_unchanged(
    owner: str,
    package: str,
    token: str,
    owner_type: str,
    expected_id: int,
) -> None:
    """Delete a package only while its previewed identity still exists."""
    current = get_container_package(owner, package, token, owner_type)
    if current is None:
        log_warn(
            f"Cleanup: GitHub returned 404 for GHCR package {package} after preview; "
            "it may be absent or inaccessible, so cleanup cannot be verified and "
            "deletion is skipped."
        )
        return
    if current["id"] != expected_id:
        log_warn(f"Cleanup: GHCR package {package} was replaced after preview; skipping.")
        return
    delete_package(owner, package, token, owner_type, dry_run=False)


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
            log_warn(
                f"Skipping GHCR package delete for {package}: GitHub returned 404; "
                "the package may be absent or inaccessible, so deletion was not confirmed."
            )
            return
        raise RuntimeError(f"Failed to delete {package} package: {body}")


def cleanup_ghcr(
    owner: str, token: str, owner_type: str, cutoff: datetime, dry_run: bool,
    cleanup: CleanupConfig, *, plan: CleanupPlan | None = None,
) -> None:

    # Only an explicit "*" is an all-packages selector.
    wildcard_packages = "*" in cleanup.ghcr_packages
    packages = list_container_packages(owner, token, owner_type) if wildcard_packages else cleanup.ghcr_packages
    for package in packages:
        if package in cleanup.ghcr_delete_packages:
            preview = get_container_package(owner, package, token, owner_type)
            if preview is None:
                log_warn(
                    f"GitHub returned 404 for GHCR package {package}; it may be absent "
                    "or inaccessible, so package cleanup is skipped."
                )
                continue
            package_id = preview["id"]
            log_info(f"Deleting GHCR package {package} (id={package_id}; explicit cleanup list)")
            if dry_run and plan is not None:
                plan.add(
                    f"GHCR package {package} (id={package_id})",
                    lambda owner=owner, package=package, token=token, owner_type=owner_type,
                    package_id=package_id:
                        _delete_container_package_if_unchanged(
                            owner, package, token, owner_type, package_id,
                        ),
                )
            elif dry_run:
                log_info(f"[DRY RUN] Would delete GHCR package {package} (id={package_id})")
            else:
                _delete_container_package_if_unchanged(
                    owner, package, token, owner_type, package_id,
                )
            continue
        versions = list_package_versions(owner, package, token, owner_type)
        for version in versions:
            version_id = version.get("id")
            updated_at = _package_version_timestamp(version)
            if not version_id or not updated_at:
                continue
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated_dt >= cutoff:
                continue
            log_info(f"Deleting GHCR {package} version {version_id} (updated {updated_at})")
            version_id = int(version_id)
            if dry_run and plan is not None:
                expected_tags = _container_version_tags(version)
                plan.add(
                    f"GHCR {package} version {version_id}",
                    lambda owner=owner, package=package, token=token, version_id=version_id,
                    owner_type=owner_type, cutoff=cutoff, updated_at=updated_at,
                    expected_tags=expected_tags:
                        _delete_package_version_if_unchanged(
                            owner, package, token, version_id, owner_type, cutoff,
                            updated_at, expected_tags,
                        ),
                )
            else:
                delete_package_version(owner, package, token, version_id, owner_type, dry_run)


def remove_assets(
    age: str,
    dry_run: bool,
    cleanup: CleanupConfig,
    github: GitHubConfig,
    env_config: ReleaseEnvConfig,
    *,
    plan: CleanupPlan | None = None,
) -> CleanupPlan | None:
    duration = parse_duration(age)
    cutoff = datetime.now(timezone.utc) - duration

    apply_release_env(github, env_config)
    owner = github.owner
    repo = github.repo
    token = github.token
    if not token:
        raise CredentialMissing("github.token is required for cleanup")

    log_info(f"Removing assets older than {age} (cutoff {cutoff.isoformat()})")
    cleanup_releases(owner, repo, token, cutoff, dry_run, cleanup, plan=plan)
    cleanup_ghcr(owner, token, github.owner_type, cutoff, dry_run, cleanup, plan=plan)
    return plan


def delete_git_tag_remote(
    repo_root: Path, tag: str, dry_run: bool, *,
    git_auth: GitHubGitAuth | None = None,
    expected_present: bool | None = None,
    expected_oid: str | None = None,
) -> None:
    """Delete *tag* on origin; skip gracefully if it does not exist (idempotent)."""
    if dry_run:
        log_info(f"[DRY RUN] Would delete remote tag {tag}")
        return
    if expected_present is False:
        log_info(f"  Remote tag {tag} was absent from the confirmed cleanup preview — skipping")
        return
    if expected_oid is not None and not transaction.COMMIT_ID_RE.fullmatch(expected_oid):
        raise RuntimeError(f"Confirmed remote tag {tag} has an invalid captured object ID")
    remote_oid = list_remote_tag_refs_matching(repo_root, tag, git_auth=git_auth).get(tag)
    if expected_present is True and expected_oid is None:
        raise RuntimeError(f"Confirmed remote tag {tag} has no captured object ID")
    target_oid = expected_oid if expected_oid is not None else remote_oid
    if remote_oid is None:
        log_info(f"  Remote tag {tag} not found — skipping")
        return
    if remote_oid != target_oid:
        log_info(f"  Remote tag {tag} changed after the confirmed cleanup preview — skipping")
        return
    result = run_remote_git(
        repo_root, "push", f"--force-with-lease=refs/tags/{tag}:{target_oid}",
        "origin", f":refs/tags/{tag}", auth=git_auth,
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        log_info(f"  Deleted remote tag {tag}")
        return
    remaining_oid = list_remote_tag_refs_matching(repo_root, tag, git_auth=git_auth).get(tag)
    if remaining_oid != target_oid:
        log_info(f"  Remote tag {tag} disappeared or changed during deletion — skipping")
        return
    detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
    raise RuntimeError(
        f"Failed to delete remote tag {tag} ({result.returncode}): {detail}"
    )


def _require_local_tag_inspection_support(repo_root: Path) -> None:
    """Refuse before a release cycle when Git cannot distinguish a missing tag."""
    probe = run_local_git(
        repo_root, "show-ref", "--exists", "refs/tags/__cmru_tag_inspection_probe__",
        capture_output=True, text=True, check=False,
    )
    if probe.returncode in (0, 2):
        return
    detail = probe.stderr.strip() or probe.stdout.strip() or "no diagnostic output"
    if probe.returncode == 129:
        raise RuntimeError(
            "local tag inspection requires Git 2.43 or newer: "
            f"`git show-ref --exists` is unsupported ({detail})"
        )
    raise RuntimeError(
        "Failed to verify Git local tag inspection support "
        f"({probe.returncode}): {detail}"
    )


def local_git_tag_exists(repo_root: Path, tag: str, *, action: str = "inspect") -> bool:
    """Check one local tag ref, distinguishing absence from Git failure."""
    return local_git_tag_oid(repo_root, tag, action=action) is not None


def local_git_tag_oid(
    repo_root: Path, tag: str, *, action: str = "inspect",
) -> str | None:
    """Return a local tag's exact ref object ID, or None when the ref is absent."""
    ref = f"refs/tags/{tag}"
    presence = run_local_git(
        repo_root, "show-ref", "--exists", ref,
        capture_output=True, text=True, check=False,
    )
    if presence.returncode == 2:
        return None
    if presence.returncode != 0:
        detail = presence.stderr.strip() or presence.stdout.strip() or "no diagnostic output"
        if presence.returncode == 129:
            raise RuntimeError(
                "local tag inspection requires Git 2.43 or newer: "
                f"`git show-ref --exists` is unsupported ({detail})"
            )
        verb = "inspect" if action == "inspect" else "recheck"
        suffix = "" if action == "inspect" else " after deletion failed"
        raise RuntimeError(
            f"Failed to {verb} local tag {tag}{suffix} "
            f"({presence.returncode}): {detail}"
        )

    result = run_local_git(
        repo_root, "show-ref", "--hash", "--verify", ref,
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        if action == "inspect":
            context = f"Failed to resolve local tag {tag} after its presence was confirmed"
        else:
            context = (
                f"Failed to recheck local tag {tag} after deletion failed; "
                "its presence had been confirmed"
            )
        raise RuntimeError(
            f"{context} ({result.returncode}): {detail}"
        )
    oid = result.stdout.strip()
    if not transaction.COMMIT_ID_RE.fullmatch(oid):
        raise RuntimeError(f"Git returned an invalid object ID while inspecting local tag {tag}")
    return oid


def delete_git_tag_local(
    repo_root: Path, tag: str, dry_run: bool, *,
    expected_present: bool | None = None,
    expected_oid: str | None = None,
) -> None:
    """Delete *tag* locally; skip gracefully if it does not exist (idempotent)."""
    if dry_run:
        log_info(f"[DRY RUN] Would delete local tag {tag}")
        return
    if expected_present is False:
        log_info(f"  Local tag {tag} was absent from the confirmed cleanup preview — skipping")
        return
    if expected_oid is not None and not transaction.COMMIT_ID_RE.fullmatch(expected_oid):
        raise RuntimeError(f"Confirmed local tag {tag} has an invalid captured object ID")
    local_oid = local_git_tag_oid(repo_root, tag)
    if expected_present is True and expected_oid is None:
        raise RuntimeError(f"Confirmed local tag {tag} has no captured object ID")
    target_oid = expected_oid if expected_oid is not None else local_oid
    if local_oid is None:
        log_info(f"  Local tag {tag} not found — skipping")
        return
    if local_oid != target_oid:
        log_info(f"  Local tag {tag} changed after the confirmed cleanup preview — skipping")
        return
    result = run_local_git(
        repo_root, "update-ref", "-d", f"refs/tags/{tag}", target_oid,
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        log_info(f"  Deleted local tag {tag}")
        return
    remaining_oid = local_git_tag_oid(repo_root, tag, action="recheck")
    if remaining_oid != target_oid:
        log_info(f"  Local tag {tag} disappeared or changed during deletion — skipping")
        return
    detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
    raise RuntimeError(
        f"Failed to delete local tag {tag} ({result.returncode}): {detail}"
    )


def list_remote_tag_refs_matching(
    repo_root: Path, pattern: str, *, git_auth: GitHubGitAuth | None = None,
) -> dict[str, str]:
    """Map remote tag names to exact ref object IDs for *pattern*."""
    result = run_remote_git(
        repo_root, "ls-remote", "--tags", "origin", f"refs/tags/{pattern}",
        auth=git_auth, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(
            f"Failed to list remote tags matching {pattern!r} "
            f"({result.returncode}): {detail}"
        )
    tags: dict[str, str] = {}
    peeled_tags: set[str] = set()
    oid_pattern = transaction.COMMIT_ID_RE
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        parts = line.split("\t")
        if len(parts) != 2:
            raise RuntimeError(
                f"Malformed remote tag listing for {pattern!r} at line {line_number}: "
                "expected an object ID and one tag ref"
            )
        oid, ref = parts
        if not oid_pattern.fullmatch(oid) or not ref.startswith("refs/tags/"):
            raise RuntimeError(
                f"Malformed remote tag listing for {pattern!r} at line {line_number}: "
                "invalid object ID or tag ref"
            )

        suffix = ref[len("refs/tags/"):]
        is_peeled = suffix.endswith("^{}")
        tag_name = suffix[:-3] if is_peeled else suffix
        if not tag_name or any(char.isspace() for char in tag_name):
            raise RuntimeError(
                f"Malformed remote tag listing for {pattern!r} at line {line_number}: "
                "empty or invalid tag name"
            )
        tag_ref = f"refs/tags/{tag_name}"
        if not fnmatch.fnmatchcase(tag_name, pattern):
            raise RuntimeError(
                f"Malformed remote tag listing for {pattern!r} at line {line_number}: "
                f"tag ref {tag_ref!r} does not match the requested pattern"
            )
        ref_check = run_local_git(
            repo_root, "check-ref-format", tag_ref,
            capture_output=True, text=True, check=False,
        )
        if ref_check.returncode != 0:
            raise RuntimeError(
                f"Malformed remote tag listing for {pattern!r} at line {line_number}: "
                f"invalid Git tag ref {tag_ref!r}"
            )
        if is_peeled:
            # Annotated tags have a second, peeled record. Validate its shape
            # before discarding it; malformed successful output must not
            # silently shrink a destructive cleanup plan.
            if tag_name in peeled_tags:
                raise RuntimeError(
                    f"Malformed remote tag listing for {pattern!r} at line {line_number}: "
                    f"duplicate peeled tag ref {tag_name!r}"
                )
            peeled_tags.add(tag_name)
            continue
        if tag_name in tags:
            raise RuntimeError(
                f"Malformed remote tag listing for {pattern!r} at line {line_number}: "
                f"duplicate tag ref {tag_name!r}"
            )
        tags[tag_name] = oid
    if not peeled_tags.issubset(tags):
        raise RuntimeError(
            f"Malformed remote tag listing for {pattern!r}: peeled refs had no matching tag ref"
        )
    return tags


def list_remote_tags_matching(
    repo_root: Path, pattern: str, *, git_auth: GitHubGitAuth | None = None,
) -> list[str]:
    """List remote tags matching *pattern* (git ls-remote --tags)."""
    return list(list_remote_tag_refs_matching(repo_root, pattern, git_auth=git_auth))


def cleanup_project_releases_and_tags(
    repo_root: Path,
    owner: str,
    repo: str,
    token: str,
    prefix: str,
    keep_tags: list[str],
    dry_run: bool,
    *,
    git_auth: GitHubGitAuth | None = None,
    plan: CleanupPlan | None = None,
    outcomes: dict[str, bool] | None = None,
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
    to_delete_releases: list[tuple[str, int, dict]] = []  # (tag_name, release_id, preview record)
    for rel in all_releases:
        tag = rel.get("tag_name") or ""
        if not tag.startswith(version_marker) and tag != latest_tag:
            continue
        if tag in keep_set:
            log_info(f"  Keeping Release {tag} (in keep list)")
            continue
        release_id = rel.get("id")
        if release_id:
            to_delete_releases.append((tag, int(release_id), rel))

    # Remote tags to delete (covers tags without a matching Release).
    remote_versioned_refs = list_remote_tag_refs_matching(
        repo_root, f"{prefix}-v*", git_auth=git_auth,
    )
    remote_versioned = list(remote_versioned_refs)
    to_delete_tags: list[str] = []
    for tag in remote_versioned:
        if tag in keep_set:
            continue
        to_delete_tags.append(tag)

    # Union: anything mentioned in either set.
    all_to_delete_tags = set(t for t, _, _ in to_delete_releases) | set(to_delete_tags)
    deleted: list[str] = []
    release_tags = {tag for tag, _, _ in to_delete_releases}
    cleanup_outcomes = outcomes if outcomes is not None else {}
    release_deletion_results: dict[str, bool] = {}

    for tag, release_id, preview_release in to_delete_releases:
        log_info(f"Cleanup: deleting GitHub Release {tag}")
        if not dry_run:
            delete_release(owner, repo, token, release_id, dry_run=False)
        else:
            log_info(f"[DRY RUN] Would delete GitHub Release {tag} (id={release_id})")
            if plan is not None:
                expected_updated_at = preview_release.get("updated_at")
                expected_asset_inventory = _release_asset_inventory(preview_release)
                plan.add(
                    f"GitHub Release {tag} (id={release_id})",
                    lambda owner=owner, repo=repo, token=token, release_id=release_id,
                    expected_tag=tag, selected_prefix=version_marker,
                    selected_keep_set=frozenset(keep_set),
                    expected_updated_at=expected_updated_at,
                    expected_asset_inventory=expected_asset_inventory,
                    cleanup_outcomes=cleanup_outcomes,
                    release_outcomes=release_deletion_results:
                        _record_release_cleanup_result(
                            expected_tag,
                            _delete_release_if_tag_still_matches(
                                owner, repo, token, release_id, expected_tag,
                                expected_updated_at=expected_updated_at,
                                expected_asset_inventory=expected_asset_inventory,
                                eligible=lambda current, selected_prefix=selected_prefix,
                                selected_keep_set=selected_keep_set: (
                                    (current.get("tag_name") or "").startswith(selected_prefix)
                                    and (current.get("tag_name") or "") not in selected_keep_set
                                ),
                            ),
                            cleanup_outcomes=cleanup_outcomes,
                            release_outcomes=release_outcomes,
                        ),
                )
        deleted.append(tag)

    for tag in sorted(all_to_delete_tags):
        log_info(f"Cleanup: deleting git tag {tag}")
        if dry_run and plan is not None:
            remote_oid = remote_versioned_refs.get(tag)
            local_oid = local_git_tag_oid(repo_root, tag)
            remote_present = remote_oid is not None
            local_present = local_oid is not None
            selected_refs = [
                name for name, present in (
                    ("remote", remote_present), ("local", local_present),
                ) if present
            ]
            if selected_refs:
                selected_label = " and ".join(selected_refs)
                plan.add(
                    f"{selected_label} Git tag {tag}",
                    lambda repo_root=repo_root, tag=tag, git_auth=git_auth,
                    remote_present=remote_present, local_present=local_present,
                    remote_oid=remote_oid, local_oid=local_oid,
                    release_tags=release_tags,
                    release_outcomes=release_deletion_results,
                    cleanup_outcomes=cleanup_outcomes,
                    release_tag=tag, owner=owner, repo=repo, token=token:
                        _delete_project_tag_after_release_check(
                            repo_root, owner, repo, token, release_tag,
                            expected_remote_present=remote_present,
                            expected_local_present=local_present,
                            expected_remote_oid=remote_oid,
                            expected_local_oid=local_oid,
                            git_auth=git_auth,
                            release_was_planned=release_tag in release_tags,
                            release_outcomes=release_outcomes,
                            cleanup_outcomes=cleanup_outcomes,
                        ),
                )
                log_info(f"[DRY RUN] Would delete {selected_label} Git tag {tag}")
            else:
                log_info(
                    f"[DRY RUN] No remote or local Git tag {tag} was present; "
                    "a later tag with this name is outside the confirmed cleanup plan"
                )
        else:
            delete_git_tag_remote(repo_root, tag, dry_run, git_auth=git_auth)
            delete_git_tag_local(repo_root, tag, dry_run)
        if tag not in [t for t, _, _ in to_delete_releases]:
            deleted.append(tag)

    return deleted


def _delete_project_tag_after_release_check(
    repo_root: Path,
    owner: str,
    repo: str,
    token: str,
    tag: str,
    *,
    expected_remote_present: bool,
    expected_local_present: bool,
    expected_remote_oid: str | None,
    expected_local_oid: str | None,
    git_auth: GitHubGitAuth | None,
    release_was_planned: bool,
    release_outcomes: Mapping[str, bool],
    cleanup_outcomes: dict[str, bool],
) -> None:
    """Delete a confirmed tag only after its associated Release was deleted."""
    release_deleted = release_outcomes.get(tag) if release_was_planned else None
    if release_was_planned and release_deleted is not True:
        log_warn(
            f"Cleanup: GitHub Release {tag} was not deleted after preview; "
            "skipping its Git tag."
        )
        cleanup_outcomes[tag] = False
        return
    current_releases = list_releases(owner, repo, token)
    if any((release.get("tag_name") or "") == tag for release in current_releases):
        log_warn(
            f"Cleanup: GitHub Release {tag} still exists after preview; skipping its Git tag."
        )
        cleanup_outcomes[tag] = False
        return
    # An absent ref is deliberately not rediscovered into this confirmed plan.
    # The helpers preserve refs that appear after preview; the final reads below
    # make the recorded outcome reflect both refs that actually remain.
    delete_git_tag_remote(
        repo_root, tag, False, git_auth=git_auth,
        expected_present=expected_remote_present,
        expected_oid=expected_remote_oid,
    )
    delete_git_tag_local(
        repo_root, tag, False, expected_present=expected_local_present,
        expected_oid=expected_local_oid,
    )
    remaining_remote = list_remote_tag_refs_matching(
        repo_root, tag, git_auth=git_auth,
    ).get(tag)
    remaining_local = local_git_tag_oid(repo_root, tag, action="recheck")
    cleanup_outcomes[tag] = (
        remaining_remote is None and remaining_local is None
    )


def _record_release_cleanup_result(
    tag: str,
    deleted: bool,
    *,
    cleanup_outcomes: dict[str, bool],
    release_outcomes: dict[str, bool],
) -> None:
    release_outcomes[tag] = deleted
    cleanup_outcomes[tag] = deleted


def cleanup_project_step(
    repo_root: Path,
    project: "ProjectConfig",
    version: str,
    dry_run: bool,
    *,
    publisher_token: str | None = None,
) -> bool:
    """Invoke ``[steps.clean]`` for the project if defined, passing ``CMRU_VERSION`` in env.

    Returns True if the step ran (caller may then commit its generated paths).
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
    if publisher_token is not None:
        # A captured dry-run plan can execute after later projects and GHCR
        # cleanup have changed the ambient token. The selected project's
        # resolved credential is the authority for this clean step.
        step_env["GITHUB_PUSH_PAT"] = publisher_token
    step = _build_step_config("clean", project.steps["clean"])
    from cmru.runner import execute_step
    protected_env = {"GITHUB_PUSH_PAT": publisher_token} if publisher_token else None
    execute_step(
        step, repo_root, log_dir, extra_env=step_env, protected_env=protected_env,
    )
    return True


def _cleanup_worktree_paths(repo_root: Path) -> set[str]:
    """Return dirty tracked and untracked paths without newline ambiguity."""
    result = subprocess.run(
        [
            "git", "-C", str(repo_root), "status", "--porcelain=v1", "-z",
            "--untracked-files=all", "--no-renames",
        ],
        capture_output=True,
        check=True,
    )
    output = result.stdout
    if isinstance(output, str):
        output = os.fsencode(output)
    return {
        os.fsdecode(record[3:])
        for record in output.split(b"\0")
        if record
    }


def cleanup_commit_deletions(
    repo_root: Path,
    project_name: str,
    deleted_tags: list[str],
    dry_run: bool,
    *,
    before_paths: set[str],
) -> None:
    """Commit only paths made dirty by the project's clean step.

    Only commits if there are actually staged changes (no empty commits).
    """
    if dry_run:
        return
    after_paths = _cleanup_worktree_paths(repo_root)
    preexisting_dirty = sorted(after_paths & before_paths)
    if preexisting_dirty:
        log_warn(
            f"{project_name}: paths already dirty before steps.clean are excluded "
            "from the cleanup commit"
        )
    generated_paths = sorted(after_paths - before_paths)
    if not generated_paths:
        log_info(f"{project_name}: no new clean-step paths to commit after cleanup")
        return
    literal_pathspecs = [f":(literal){path}" for path in generated_paths]
    staged = subprocess.run(
        ["git", "-C", str(repo_root), "add", "-A", "--", *literal_pathspecs],
        check=False,
        capture_output=True,
        text=True,
    )
    if staged.returncode:
        detail = staged.stderr.strip() or staged.stdout.strip() or "git add failed"
        raise RuntimeError(
            f"{project_name}: failed to stage cleanup-generated paths "
            f"({staged.returncode}): {detail}"
        )
    cached = _git(
        repo_root, "diff", "--cached", "--name-only", "--", *literal_pathspecs,
    )
    if not cached:
        log_info(f"{project_name}: nothing staged — skipping cleanup commit")
        return
    if deleted_tags:
        tags_summary = ", ".join(deleted_tags[:5])
        if len(deleted_tags) > 5:
            tags_summary += f" (+{len(deleted_tags) - 5} more)"
        subject = f"chore({project_name}): cleanup deleted {tags_summary}"
    else:
        subject = f"chore({project_name}): commit cleanup-generated files"
    rc = run_local_git(
        repo_root, "commit", "--only", "-m", subject, "--", *literal_pathspecs,
    ).returncode
    if rc == 0:
        log_info(f"{project_name}: committed cleanup changes")
    else:
        log_warn(f"{project_name}: cleanup commit failed — check working tree")


def _run_cleanup_step_and_commit(
    repo_root: Path,
    project_name: str,
    project: ProjectConfig,
    version: str,
    deleted_tags: list[str],
    publisher_token: str,
) -> None:
    """Run the confirmed clean step and commit only its newly dirty paths."""
    before_paths = _cleanup_worktree_paths(repo_root)
    step_ran = cleanup_project_step(
        repo_root, project, version, False, publisher_token=publisher_token,
    )
    if step_ran:
        cleanup_commit_deletions(
            repo_root, project_name, deleted_tags, False,
            before_paths=before_paths,
        )


def _run_cleanup_step_after_plan(
    repo_root: Path,
    project_name: str,
    project: ProjectConfig,
    owner: str,
    repo: str,
    token: str,
    bare: str,
    preview_deleted_tags: list[str],
    cleanup_outcomes: Mapping[str, bool],
) -> None:
    """Run clean against the remote state left by the confirmed actions."""
    version = _latest_version_for_prefix(owner, repo, token, bare)
    deleted_tags = [
        tag for tag in preview_deleted_tags if cleanup_outcomes.get(tag) is True
    ]
    _run_cleanup_step_and_commit(
        repo_root, project_name, project, version, deleted_tags, token,
    )


def _latest_version_for_prefix(
    owner: str, repo: str, token: str, bare: str, *, exclude_tags: Sequence[str] = (),
) -> str:
    """Highest-semver surviving ``<bare>-v*`` release version, or ``""`` if none.

    Used to pass ``CMRU_VERSION`` to a project's optional ``[steps.clean]``. Reuses the
    same release listing + semver ordering as ``cmru resolve`` so the value the clean
    step sees matches what consumers resolve as "latest". Drafts/prereleases and the
    thin ``<bare>-latest`` pointer are ignored.
    """
    from cmru.release import _semver_key
    marker = f"{bare}-v"
    excluded = set(exclude_tags)
    versions = [
        (rel.get("tag_name") or "")[len(marker):]
        for rel in list_releases(owner, repo, token)
        if (rel.get("tag_name") or "").startswith(marker)
        and (rel.get("tag_name") or "") not in excluded
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
    project_filter: Optional[str | list[str]],
    dry_run: bool,
    *,
    plan: CleanupPlan | None = None,
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

    names = list(project_filter) if isinstance(project_filter, list) else (
        [project_filter] if project_filter else list(project_order)
    )
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
            raise CredentialMissing(
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
        cleanup_outcomes: dict[str, bool] = {}
        deleted = cleanup_project_releases_and_tags(
            repo_root, owner, repo, token,
            bare, keep_tags, dry_run,
            git_auth=_git_auth_for_repository(github_config),
            plan=plan,
            outcomes=cleanup_outcomes,
        )
        any_deleted.extend(deleted)

        # 2. Optional per-project clean step (e.g. delete referenced manifests).
        #    CMRU_VERSION = highest-semver surviving <prefix>-v* release (post-cleanup),
        #    or "" when none survive. (In --dry-run nothing was deleted, so this is the
        #    current latest.)
        version = _latest_version_for_prefix(
            owner, repo, token, bare, exclude_tags=deleted,
        )
        clean_planned = dry_run and plan is not None and "clean" in project.steps
        if clean_planned:
            log_info(
                f"[DRY RUN] Would run {name} steps.clean and commit only paths it makes dirty; "
                f"CMRU_VERSION={version} is a preview estimate and will be re-resolved "
                "from Releases remaining after confirmation"
            )
            cleanup_project_step(
                repo_root, project, version, True, publisher_token=token,
            )
            plan.add(
                f"{name} steps.clean with preview CMRU_VERSION={version} (re-resolved from "
                "surviving Releases after confirmation) and its generated paths",
                lambda repo_root=repo_root, name=name, project=project,
                deleted=deleted, publisher_token=token, owner=owner, repo=repo,
                bare=bare, cleanup_outcomes=cleanup_outcomes:
                    _run_cleanup_step_after_plan(
                        repo_root, name, project, owner, repo, publisher_token,
                        bare, deleted, cleanup_outcomes,
                    ),
            )
        elif not dry_run:
            before_paths = _cleanup_worktree_paths(repo_root) if "clean" in project.steps else set()
            step_ran = cleanup_project_step(
                repo_root, project, version, False, publisher_token=token,
            )
            if step_ran:
                cleanup_commit_deletions(
                    repo_root, name, deleted, False,
                    before_paths=before_paths,
                )
        else:
            cleanup_project_step(
                repo_root, project, version, True, publisher_token=token,
            )

    # 4. Prune old ghcr package versions (whole-repo, not per-project).
    # ghcr pruning is age-based; use ``cmru cleanup --remove-assets AGE`` for that path.
    # Here we only prune packages declared in ghcr_delete_packages (explicit wipe list).
    if cleanup.ghcr_delete_packages:
        # GHCR package deletion is repository-wide, so it deliberately uses the
        # repository credential rather than choosing one project's override.
        apply_release_env(github_config, env_config)
        if not github_config.token:
            raise CredentialMissing(
                "Repository-wide GHCR cleanup requires GITHUB_PUSH_PAT/GITHUB_TOKEN "
                "or repository-root cmru.secret.toml"
            )
        if dry_run:
            log_info(
                f"[DRY RUN] Would delete GHCR packages: {', '.join(cleanup.ghcr_delete_packages)}"
            )
            if plan is not None:
                for pkg in cleanup.ghcr_delete_packages:
                    preview = get_container_package(
                        github_config.owner, pkg, github_config.token,
                        github_config.owner_type,
                    )
                    if preview is None:
                        log_warn(
                            f"[DRY RUN] GitHub returned 404 for GHCR package {pkg}; it may "
                            "be absent or inaccessible, so no deletion is planned."
                        )
                        continue
                    package_id = preview["id"]
                    plan.add(
                        f"GHCR package {pkg} (id={package_id})",
                        lambda pkg=pkg, package_id=package_id:
                            _delete_container_package_if_unchanged(
                                github_config.owner, pkg, github_config.token,
                                github_config.owner_type, package_id,
                            ),
                    )
        else:
            for pkg in cleanup.ghcr_delete_packages:
                preview = get_container_package(
                    github_config.owner, pkg, github_config.token,
                    github_config.owner_type,
                )
                if preview is None:
                    log_warn(
                        f"Cleanup: GitHub returned 404 for GHCR package {pkg}; it may be "
                        "absent or inaccessible, so package cleanup is skipped."
                    )
                    continue
                log_info(
                    f"Cleanup: deleting GHCR package {pkg} (id={preview['id']}; "
                    "ghcr_delete_packages list)"
                )
                _delete_container_package_if_unchanged(
                    github_config.owner, pkg, github_config.token,
                    github_config.owner_type, preview["id"],
                )

    if any_deleted:
        label = "Would delete" if dry_run else "Deleted"
        log_info(f"Cleanup complete. {label}: {', '.join(any_deleted)}")
    else:
        log_info("Cleanup complete. Nothing deleted.")


def build_arg_parser():
    """Compatibility accessor for the parser generated from the public grammar."""
    return _build_cli().command_parsers["run"]


def _orchestrate(args=None) -> None:
    if args is None:
        args = build_arg_parser().parse_args()
    _apply_output_options(args)

    config_path = _resolve_config(args.config)

    (
        repo_root,
        configs,
        project_order,
        _default_projects,
        default_steps,
        execution_mode,
        _step_project_order,
        cleanup,
        github_config,
        env_config,
    ) = load_config(config_path)

    selected_names = _select_projects(
        config_path, getattr(args, "target", None), configs, project_order,
    )

    selected = [configs[name] for name in selected_names]

    steps = list(args.step or default_steps)

    # CLI-09: a step no selected project declares is a usage error naming what
    # IS declared, never a RuntimeError traceback halfway through a run.
    for project in selected:
        declared = project.runner_steps or {}
        absent = [step_name for step_name in steps if step_name not in declared]
        if absent:
            _usage_error(
                f"{project.name}: step(s) not declared: {', '.join(absent)}; "
                f"declared steps: {', '.join(sorted(declared)) or '(none)'}"
            )

    if getattr(args, "dry_run", False):
        log_info(
            "[DRY RUN] Run plan: "
            + (", ".join(steps) if steps else "no project steps")
            + f"; projects: {', '.join(selected_names) if selected_names else '(none)'}"
        )
        plan = []
        if execution_mode == "project-first":
            plan = [(project, step_name) for project in selected for step_name in steps]
        else:
            plan = [(project, step_name) for step_name in steps for project in selected]
        from cmru.runner import render_step_plan
        for project, step_name in plan:
            step = project.runner_steps[step_name]  # declared: checked above
            project_root = resolve_cwd(repo_root, _project_working_directory(project))
            for line in render_step_plan(step, project_root):
                log_info(f"[DRY RUN] {project.name}:{step_name}: {line}")
        log_info("[DRY RUN] No project command was started.")
        return

    if not steps:
        log_info("Release manager complete")
        return

    if "push" in steps:
        require_project_publish_credentials(configs, selected_names)

    log_dir = repo_root / "logs"
    resolve_versions_from_git(repo_root, configs)

    if execution_mode == "project-first":
        for project in selected:
            apply_project_release_env(github_config, env_config, project)
            for step in steps:
                run_project_step(project, step, repo_root, log_dir)
    else:
        for step in steps:
            for project in selected:
                apply_project_release_env(github_config, env_config, project)
                run_project_step(project, step, repo_root, log_dir)

    log_info("Release manager complete")


def _default_config_path() -> Path:
    """Return the nearest discovered project or orchestration config."""
    return resolve_invocation_context().config_path


def _resolve_config(config_opt: Optional[str]) -> Path:
    context = resolve_invocation_context(
        Path(config_opt).expanduser() if config_opt else None,
    )
    return getattr(context, "config_reference_path", None) or context.config_path


def _invocation_context(config_opt: Optional[str]) -> InvocationContext:
    return resolve_invocation_context(
        Path(config_opt).expanduser() if config_opt else None,
    )


def _select_projects(
    config_path: Path, raw_target: Optional[str], configs: Mapping[str, "ProjectConfig"],
    project_order: List[str],
) -> List[str]:
    # An explicit target is already authoritative and does not need a second
    # filesystem discovery pass.  A standalone project config also establishes
    # project context by definition; this keeps child dispatches deterministic
    # when they are given the snapshot's project-local config path.
    context_project: str | None = None
    if raw_target is None:
        if config_path.name == PROJECT_CONFIG_FILENAME and len(configs) == 1:
            context_project = next(iter(configs))
        else:
            context = resolve_invocation_context(config_path)
            context_project = context.project_name
    try:
        return select_target_names(
            raw_target,
            configs,
            project_order,
            context_project=context_project,
        )
    except TargetSelectionError as exc:
        from cmru.cli_support import write_config_diagnostic

        write_config_diagnostic(str(exc))
        raise SystemExit(exit_codes.CONFIG_ERROR)


def _release_resume_target(
    config_path: Path,
    raw_target: str | None,
    recorded_scope: list[str] | None,
    configs: Mapping[str, "ProjectConfig"],
    project_order: List[str],
) -> str:
    """Keep a resumed transaction on its recorded project scope.

    A targetless release normally means every configured project. A retained
    candidate already has a narrower, authoritative scope, so resume derives
    that scope from its sidecar. An explicit target is accepted only when it
    names the same projects. Legacy candidates without scope metadata need an
    explicit target.
    """
    if recorded_scope is None:
        if raw_target is None:
            raise UsageRefusal(
                "retained release has no recorded project scope; inspect the candidate "
                "and pass its explicit project target to `cmru release TARGET --resume`"
            )
        return raw_target
    if (
        not isinstance(recorded_scope, list)
        or not recorded_scope
        or any(not isinstance(name, str) or not name for name in recorded_scope)
        or len(recorded_scope) != len(set(recorded_scope))
    ):
        raise UnsafeRecord("retained release has malformed project-scope metadata")
    unknown = [name for name in recorded_scope if name not in configs]
    if unknown:
        raise UsageRefusal(
            "retained release scope contains project(s) absent from the selected config: "
            + ", ".join(unknown)
        )
    ordered_scope = [name for name in project_order if name in set(recorded_scope)]
    if raw_target is not None:
        requested = _select_projects(config_path, raw_target, configs, project_order)
        if set(requested) != set(ordered_scope):
            raise UsageRefusal(
                "release resume target does not match the retained transaction scope; "
                f"saved scope is {','.join(ordered_scope)!r}, requested scope is "
                f"{','.join(requested)!r}"
            )
    return ",".join(ordered_scope)


def _configure_native_release_logging(repo_root: Path, *, append: bool) -> None:
    """Provide the former wrapper's aggregate log and live tee for release.

    The real CLI uses an OS pipe so child processes inherit the same tee. Tests
    that call ``main(argv=...)`` stay in-process and therefore do not mutate
    their capture descriptors.
    """
    if os.environ.get("CMRU_NATIVE_RELEASE_LOGGING") == "0":
        return
    log_path = _prepare_native_release_log(repo_root, append=append)
    tee = subprocess.Popen(["tee", "-a", str(log_path)], stdin=subprocess.PIPE)
    assert tee.stdin is not None
    os.dup2(tee.stdin.fileno(), 1)
    os.dup2(1, 2)


def _prepare_native_release_log(repo_root: Path, *, append: bool) -> Path:
    """Prepare the aggregate log and child-process environment."""
    log_path = Path(
        os.environ.get("CMRU_RELEASE_LOG") or (repo_root / "cmru.release.log")
    ).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n---\n")
        os.environ["CMRU_INTERNAL_LOG_APPEND"] = "1"
    else:
        log_path.write_text("", encoding="utf-8")
        os.environ.pop("CMRU_INTERNAL_LOG_APPEND", None)
    os.environ["CMRU_INTERNAL_RUN_LOG"] = str(log_path)
    os.environ["PYTHONUNBUFFERED"] = "1"
    return log_path


def _ordered_configs(
    configs: Mapping[str, "ProjectConfig"],
    project_order: List[str],
) -> "dict[str, ProjectConfig]":
    """Project configs limited to ``project_order`` (the orchestrated set), in order.

    ``status``/``release`` use this so they never auto-tag projects that still own a
    bespoke pipeline (tls-edge, empyrion) and are not yet migrated into the orchestrator.
    """
    return {name: configs[name] for name in project_order if name in configs}


def _push_tags(
    repo_root: Path,
    tags: List[str],
    *,
    git_auth: GitHubGitAuth | None = None,
    workspace: transaction.ReleaseWorkspace | None = None,
) -> None:
    """Push release tags to origin before any external publisher can run."""
    if not tags:
        return
    local_oids: dict[str, str] = {}
    for tag in tags:
        oid = local_git_tag_oid(repo_root, tag)
        if oid is None:
            raise RuntimeError(f"local release tag {tag!r} disappeared before it could be pushed")
        local_oids[tag] = oid
    if workspace is not None:
        transaction.write_release_tag_attempts(
            repo_root, workspace,
            {f"refs/tags/{tag}": oid for tag, oid in local_oids.items()},
        )
    log_info(f"Pushing tags to origin: {', '.join(tags)}")
    rc = run_remote_git(repo_root, "push", "origin", *tags, auth=git_auth).returncode
    if rc != 0:
        try:
            advertised_refs = _read_origin_tag_refs(
                repo_root, git_auth=git_auth,
                context="verify release tags after a failed push",
            )
            origin_refs = {
                tag: _tag_origin_ref_subset(advertised_refs, tag)
                for tag in tags
            }
        except _DOMAIN_ERRORS as exc:
            raise RuntimeError(
                "release tag push failed and CMRU could not determine origin state; "
                "the local tag and candidate were retained for inspection"
            ) from exc
        pushed = {
            tag: origin_refs[tag].get(f"refs/tags/{tag}") == local_oids[tag]
            for tag in tags
        }
        if all(pushed.values()):
            log_warn(
                "git push reported failure, but origin now has every exact release tag; "
                "continuing with the verified candidate"
            )
            return
        conflicting = {
            tag: oid for tag, refs in origin_refs.items()
            if (oid := refs.get(f"refs/tags/{tag}")) is not None
            and oid != local_oids[tag]
        }
        cleanup_errors = []
        confirmed_absent: dict[str, str] = {}
        for tag, refs in origin_refs.items():
            if f"refs/tags/{tag}" in refs:
                continue
            try:
                delete_git_tag_local(
                    repo_root, tag, False, expected_present=True,
                    expected_oid=local_oids[tag],
                )
                remaining_oid = local_git_tag_oid(repo_root, tag, action="recheck")
                if remaining_oid is not None:
                    cleanup_errors.append(
                        f"{tag}: local ref remains at {remaining_oid} after its guarded cleanup"
                    )
                else:
                    confirmed_absent[f"refs/tags/{tag}"] = local_oids[tag]
            except _DOMAIN_ERRORS as exc:
                cleanup_errors.append(f"{tag}: {exc}")
        if confirmed_absent and workspace is not None:
            transaction.write_confirmed_absent_release_tag_attempts(
                repo_root, workspace, confirmed_absent,
            )
        if cleanup_errors:
            raise RuntimeError(
                "release tag push failed; could not remove every origin-absent local tag, "
                "so the candidate was retained for inspection: " + "; ".join(cleanup_errors)
            )
        if any(pushed.values()):
            raise RuntimeError(
                "only some release tags were confirmed on origin after a failed push; "
                "origin-absent local tags were removed and the candidate was retained "
                "for inspection"
            )
        if conflicting:
            names = ", ".join(sorted(conflicting))
            raise RuntimeError(
                "release tag push failed because origin already has conflicting release "
                f"tag(s) at those names ({names}); the local tags and candidate were "
                "retained for inspection"
            )
        raise RuntimeError(
            "could not push release tag(s) to origin; CMRU removed the unpushed local tag(s), "
            "stopped before build or publish, and retained an untagged candidate that can be resumed"
        )


def _release_tag_origin_refs(
    repo_root: Path, tag: str, *, git_auth: GitHubGitAuth | None,
) -> dict[str, str]:
    """Return origin's direct and peeled refs for one release tag."""
    ref = f"refs/tags/{tag}"
    result = run_remote_git(
        repo_root, "ls-remote", "--tags", "origin", ref, ref + "^{}",
        auth=git_auth, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"git ls-remote could not verify {tag!r}: {detail}")
    refs = transaction.parse_ls_remote_refs(
        result.stdout, namespace="refs/tags/", description=f"origin tag lookup for {tag!r}",
    )
    return _tag_origin_ref_subset(refs, tag)


def _tag_origin_ref_subset(refs: Mapping[str, str], tag: str) -> dict[str, str]:
    """Select one exact direct/peeled tag pair from a complete ref advertisement."""
    ref = f"refs/tags/{tag}"
    if ref + "^{}" in refs and ref not in refs:
        raise RuntimeError(
            f"origin advertised a peeled release tag {tag!r} without its direct ref"
        )
    return {
        candidate: refs[candidate]
        for candidate in (ref, ref + "^{}")
        if candidate in refs
    }


def _release_tag_origin_commit(
    repo_root: Path, tag: str, *, git_auth: GitHubGitAuth | None,
) -> str | None:
    """Return origin's peeled release-tag commit, or None if the tag is absent."""
    ref = f"refs/tags/{tag}"
    refs = _release_tag_origin_refs(repo_root, tag, git_auth=git_auth)
    return refs.get(ref + "^{}") or refs.get(ref)


def _release_tag_matches_origin(
    repo_root: Path, tag: str, *, git_auth: GitHubGitAuth | None,
) -> bool:
    """Determine whether origin has this tag name pointing at the local commit."""
    remote_commit = _release_tag_origin_commit(repo_root, tag, git_auth=git_auth)
    if remote_commit is None:
        return False
    local_commit = _git(repo_root, "rev-parse", f"{tag}^{{commit}}")
    return remote_commit == local_commit


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
                raise StepUnavailable(
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
            raise StepUnavailable(f"{name}: project.release.build_step is absent")
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
    # ``prepare``. It has already been committed and gated before cmru creates
    # any tags for this transaction; rebuilding here would both waste work and
    # risk producing artifacts from a post-tag HEAD.
    artifact_step = project.build_step
    if not artifact_step:
        raise StepUnavailable(f"{name}: project.release.build_step is absent")
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
        raise StepUnavailable(f"{name}: required push step is absent")


def _version_strategy(proj: "ProjectConfig") -> str:
    return proj.version.strategy if getattr(proj, "version", None) else "scm"


def _assert_release_candidate_unchanged(
    repo_root: Path, project_name: str, expected_sha: str,
) -> None:
    """Require publication to have used the exact candidate commit.

    Release steps may create ignored logs and artifact output, but they must not
    move ``HEAD`` or leave a non-ignored worktree mutation behind. A later
    promotion must therefore integrate the same commit that was gated and built.
    """
    actual_sha = _git(repo_root, "rev-parse", "HEAD")
    if actual_sha != expected_sha:
        raise RuntimeError(
            f"{project_name}: release step moved HEAD from {expected_sha} to "
            f"{actual_sha}; refusing to promote a different commit"
        )
    changed = _worktree_changed_paths(repo_root)
    if changed:
        raise RuntimeError(
            f"{project_name}: release step left non-ignored changes before promotion: "
            f"{', '.join(changed)}"
        )


def _project_release_paths(project: "ProjectConfig", name: str) -> list[str]:
    """Repo-relative paths whose change would alter what a release gated and built."""
    return list(
        getattr(project, "paths", None) or [getattr(project, "cwd", None) or name]
    )


def _release_tag_recovery_commands(tag: str, oid: str | None, branch: str) -> str:
    pinned = f":{oid}" if oid else ""
    return (
        f"  git push --force-with-lease=refs/tags/{tag}{pinned} origin :refs/tags/{tag}\n"
        f"  git tag -d {tag}\n"
        f"  cmru abandon {branch} --yes\n"
        "then start a fresh release"
    )


def _rollback_unpublished_release_tag(
    repo_root: Path,
    workspace: transaction.ReleaseWorkspace,
    tag: str,
    *,
    git_auth: GitHubGitAuth | None,
    cause: BaseException,
) -> bool:
    """Undo a release tag after a NON-publishing step failed (REL-05).

    Both deletions are pinned to the exact object CMRU pushed, and an absence
    proof is recorded, so the candidate is resumable afterwards. Returns whether
    the rollback completed; on failure it prints the manual recovery commands.
    """
    ref = f"refs/tags/{tag}"
    attempts = transaction.read_release_tag_attempts(repo_root, workspace) or {}
    oid = attempts.get(ref)
    log_error(
        f"{tag}: a build step failed before any publication ({cause}); rolling the "
        "release tag back so the candidate stays resumable"
    )
    try:
        if oid is None:
            raise RuntimeError(f"no recorded push attempt for {tag}")
        delete_git_tag_remote(
            repo_root, tag, False, git_auth=git_auth,
            expected_present=True, expected_oid=oid,
        )
        remote_oid = list_remote_tag_refs_matching(
            repo_root, tag, git_auth=git_auth,
        ).get(tag)
        if remote_oid is not None:
            raise RuntimeError(f"origin still has {tag} at {remote_oid}")
        delete_git_tag_local(
            repo_root, tag, False, expected_present=True, expected_oid=oid,
        )
        local_oid = local_git_tag_oid(repo_root, tag, action="recheck")
        if local_oid is not None:
            raise RuntimeError(f"the local tag remains at {local_oid}")
        transaction.write_confirmed_absent_release_tag_attempts(
            repo_root, workspace, {ref: oid},
        )
    except _DOMAIN_ERRORS as exc:
        log_error(
            f"Could not roll back release tag {tag}: {exc}. The tag was kept; nothing was "
            "published. Recover by hand:\n"
            + _release_tag_recovery_commands(tag, oid, workspace.branch)
        )
        return False
    log_info(
        f"Rolled back release tag {tag} (local and origin); nothing was published. "
        f"Fix the build, then resume: cmru release --resume {workspace.path}"
    )
    return True


def _report_publication_started(
    repo_root: Path, workspace: transaction.ReleaseWorkspace, tag: str,
) -> None:
    """Print the tag, its object id and exact recovery once publishing has begun."""
    oid = (transaction.read_release_tag_attempts(repo_root, workspace) or {}).get(
        f"refs/tags/{tag}"
    )
    log_error(
        f"Publishing of {tag} had started when this release failed, so CMRU does NOT "
        f"roll the tag back (public artifacts may already exist). Tag: {tag}, object "
        f"id: {oid or 'unknown (see `git rev-parse refs/tags/' + tag + '`)'}. "
        "`--resume` refuses this candidate. Recovery, from the source checkout:\n"
        + _release_tag_recovery_commands(tag, oid, workspace.branch)
        + "\nAssets already published under the tag are removed with `cmru cleanup --policy` or `cmru cleanup --remove-assets AGE`."
    )


def _run_tagged_build_and_publish(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    name: str,
    workspace: transaction.ReleaseWorkspace,
    tag: str,
    artifact_phases: List[str],
    *,
    candidate_sha: str,
    git_auth: GitHubGitAuth | None,
    github_config: GitHubConfig,
    env_config: ReleaseEnvConfig,
) -> None:
    """Build (non-publishing), then publish, with the right failure handling.

    A failed build rolls the freshly pushed tag back (the candidate stays
    resumable). Once the publishing step has begun, the tag is never touched.
    """
    if artifact_phases:
        try:
            _run_project_steps(
                repo_root, configs, [name], artifact_phases,
                github_config=github_config, env_config=env_config,
            )
        except Exception as exc:  # deliberately broad: ANY build failure must roll the tag back; re-raised
            _rollback_unpublished_release_tag(
                repo_root, workspace, tag, git_auth=git_auth, cause=exc,
            )
            raise
    try:
        _run_project_steps(
            repo_root, configs, [name], ["push"],
            github_config=github_config, env_config=env_config,
        )
        _assert_release_candidate_unchanged(repo_root, name, candidate_sha)
    except Exception:  # deliberately broad: ANY failure after publish began must report it; re-raised
        _report_publication_started(repo_root, workspace, tag)
        raise


def _release_projects_sequentially(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    workspace: transaction.ReleaseWorkspace,
    release_names: List[str],
    *,
    github_config: GitHubConfig,
    env_config: ReleaseEnvConfig,
    git_auth: GitHubGitAuth | None = None,
    no_build: bool = False,
    minor: bool = False,
    major: bool = False,
    set_version: Optional[str] = None,
) -> List[str]:
    """Release every named project one after another.

    Each project's own prepare → gate → tag → build → publish → promote cycle
    completes in full before the next project starts. Promotion is intentionally
    last: ``origin/main`` receives the exact candidate commit only after its
    public artifact has succeeded. This is what lets a later project (e.g. an OCI
    image) resolve an earlier project's (e.g. a wheel) brand-new release within
    this SAME run, instead of always trailing one ``cmru release`` behind.

    Progress is checkpointed after each project's full success
    (:func:`transaction.write_release_progress`). A failed candidate remains on
    its durable transaction branch; no source-tree revert is needed because the
    failed project's candidate was never promoted.

    Returns the "{name} (...)" labels actually built/published (empty entries for
    projects released with ``no_build=True`` are omitted).
    """
    from cmru.version import release_cmd

    # Seed the checkpoint at this run's own starting point. Without this, a
    # --resume reusing the same branch token would read a previous attempt's
    # stale checkpoint and misreport the last completed source candidate.
    # Returning "the run's base" is exactly equivalent to "nothing has fully
    # succeeded yet in this run".
    transaction.write_release_progress(repo_root, workspace, workspace.base)

    released: List[str] = []
    for name in release_names:
        project = configs[name]
        apply_project_release_env(github_config, env_config, project)
        log_info(f"=== {name}: releasing ===")

        _prepare_release_projects(
            repo_root, configs, [name], minor=minor, major=major, set_version=set_version,
        )
        # Keep the remote candidate branch current before the gate too. A gate
        # failure must leave the generated candidate available for inspection.
        transaction.push_backup_branch(workspace, git_auth=git_auth)
        _run_release_gates(repo_root, configs, [name])
        gated_sha = _git(repo_root, "rev-parse", "HEAD")

        strategy = _version_strategy(project)
        if not getattr(project, "git_tag", True):
            if not no_build:
                log_info(f"Building + publishing {name} (no git tag)")
                candidate_sha = _git(repo_root, "rev-parse", "HEAD")
                _run_untagged_project(
                    repo_root, configs, name,
                    github_config=github_config, env_config=env_config,
                )
                _assert_release_candidate_unchanged(repo_root, name, candidate_sha)
                released.append(f"{name} (no git tag)")
                transaction.write_release_result(
                    repo_root, workspace, name, f"source-{_git(repo_root, 'rev-parse', 'HEAD')[:12]}"
                )
            else:
                log_info(f"{name}: --no-build — skipped build/push")
            transaction.promote_workspace(
                workspace, git_auth=git_auth,
                project_paths=_project_release_paths(project, name),
                release_label=name,
            )
            log_info(f"{name}: promoted release candidate to origin/main")
        else:
            release_cmd(repo_root, {name: project}, minor=minor, major=major, set_version=set_version)
            # A file:-strategy tag commits a version bump here. Refresh the
            # durable candidate before tags or public publication are touched.
            transaction.push_backup_branch(workspace, git_auth=git_auth)
            candidate_sha = _git(repo_root, "rev-parse", "HEAD")
            if candidate_sha != gated_sha:
                # The file strategy adds a mechanical version commit after the
                # initial pre-tag gate. Re-run the real gate on that exact
                # candidate so the commit that produces the artifact has passed
                # the same acceptance contract before publication begins.
                log_info(f"{name}: versioning changed the candidate; re-running release gate")
                _run_release_gates(repo_root, configs, [name])
            tag = _tag_on_head(repo_root, project.prefix or f"{name}-v")
            if tag:
                _push_tags(repo_root, [tag], git_auth=git_auth, workspace=workspace)
                if not no_build:
                    log_info(f"Building + publishing {name} ({tag})")
                    candidate_sha = _git(repo_root, "rev-parse", "HEAD")
                    artifact_phases = [] if project.build_step == "prepare" else [project.build_step]
                    _run_tagged_build_and_publish(
                        repo_root, configs, name, workspace, tag, artifact_phases,
                        candidate_sha=candidate_sha, git_auth=git_auth,
                        github_config=github_config, env_config=env_config,
                    )
                    released.append(f"{name} ({tag})")
                    transaction.write_release_result(repo_root, workspace, name, tag)
                else:
                    log_info(f"{name}: --no-build — tagged {tag}, skipped build/publish")
                    transaction.write_release_result(repo_root, workspace, name, tag)
                transaction.promote_workspace(
                    workspace, git_auth=git_auth,
                    project_paths=_project_release_paths(project, name),
                    release_label=tag,
                )
                log_info(f"{name}: promoted release candidate to origin/main")
            elif not no_build:
                raise RuntimeError(
                    f"{name}: gate passed and it was in this run's changed-project scope, "
                    "but no tag ended up on HEAD (release.git_tag produced nothing to "
                    "build/publish) — this should not happen; investigate before retrying"
                )
            else:
                transaction.promote_workspace(
                    workspace, git_auth=git_auth,
                    project_paths=_project_release_paths(project, name),
                    release_label=name,
                )
                log_info(f"{name}: promoted release candidate to origin/main")

        # This project's whole cycle succeeded — checkpoint it so a LATER
        # project's retained candidate can be compared with the last complete
        # source state.
        transaction.write_release_progress(repo_root, workspace, _git(repo_root, "rev-parse", "HEAD"))

    return released


def _transaction_workspace_from_env(repo_root: Path) -> transaction.ReleaseWorkspace:
    """Recover transaction provenance in the re-execed child process."""
    shared_path = os.environ.get("CMRU_WORKSPACE_PATH")
    if shared_path:
        try:
            shared = transaction._shared_worktree()
            path = Path(shared_path).resolve()
            _top, common, _branch, _head = shared.discover_git_context(path)
            record = shared.find_workspace(common, path)
            if record is not None:
                context = shared.ensure_workspace(record)
                return transaction.ReleaseWorkspace(
                    repo_root=context.source_git_root,
                    path=path,
                    branch=context.branch,
                    base=context.base_commit,
                    context=context,
                )
        except _DOMAIN_ERRORS as exc:
            raise RuntimeError(f"invalid shared workspace context: {exc}") from exc
    workspace = transaction.ReleaseWorkspace(
        repo_root=repo_root,
        path=repo_root,
        branch=os.environ.get(transaction.BRANCH_ENV, ""),
        base=os.environ.get(transaction.BASE_ENV, ""),
    )
    if not workspace.branch or not workspace.base:
        raise RuntimeError("release child is missing transaction provenance")
    return workspace


def _value_taking_flags(verb: str) -> frozenset[str]:
    """Option spellings of ``verb`` that consume the following argv token."""
    parser = _build_cli().command_parsers.get(verb)
    if parser is None:
        return frozenset()
    return frozenset(
        flag for flag, action in parser._option_string_actions.items()
        if action.nargs != 0
    )


def _forwarded_global_args(parsed: object | None, rest: Sequence[str]) -> List[str]:
    """The library-global flags the PARENT parsed, as child argv (CLI-19).

    ``runtime.command_argv`` is only the argv AFTER the verb, so a global given
    before it (``cmru --dry-run release``) is not in ``rest``. A child that did
    not receive ``--dry-run`` would run a REAL release while the parent believed
    it was previewing. Every global the parent parsed is therefore re-serialised
    from the namespace; a flag already present in ``rest`` is never doubled, and
    the mutually exclusive verbosity/colour families are skipped as a whole when
    ``rest`` already chose one.
    """
    if parsed is None:
        return []
    present = {token.partition("=")[0] for token in rest if token.startswith("-")}
    forwarded: List[str] = []
    if getattr(parsed, "dry_run", False) and "--dry-run" not in present:
        forwarded.append("--dry-run")
    if not present & {"--log-level", "--quiet", "--debug", "--verbose"}:
        level = getattr(parsed, "log_level", None)
        if level:
            forwarded.extend(["--log-level", str(level)])
        elif getattr(parsed, "quiet", False):
            forwarded.append("--quiet")
        elif getattr(parsed, "debug", False):
            forwarded.append("--debug")
    if getattr(parsed, "debug_raw", False) and "--debug-raw" not in present:
        forwarded.append("--debug-raw")
    if not present & {"--color", "--no-color"}:
        color = getattr(parsed, "color", None)
        if color is True:
            forwarded.append("--color")
        elif color is False:
            forwarded.append("--no-color")
    if (
        getattr(parsed, "log_prefix_time_short", False)
        and "--log-prefix-time-short" not in present
    ):
        forwarded.append("--log-prefix-time-short")
    return forwarded


def _child_release_args(
    rest: List[str], config_path: Path, repo_root: Path, *, source_git_root: Path | None = None,
    target_override: str | None = None, original_target: object | None = None,
    verb: str = "release", forward_from: object | None,
) -> List[str]:
    """Point a transaction child at its snapshot or central CMRU config.

    The child argv is the caller's argv with ``--config``/``--resume`` dropped,
    the target replaced by ``target_override``, and the deprecated ``--ref``
    spelling rewritten. The target is the first POSITIONAL token, found by
    walking the argv with the verb's own value-taking options (CLI-19): a value
    that merely equals the target text, like ``--set-version a``, is never
    mistaken for it.
    """
    value_flags = _value_taking_flags(verb)
    result: List[str] = []
    removed_target = original_target is None
    drop_next = keep_next = False
    for value in rest:
        if drop_next:
            drop_next = False
            continue
        if keep_next:
            keep_next = False
            result.append(value)
            continue
        flag, equals, remainder = value.partition("=")
        if flag in ("--config", "--resume"):
            drop_next = not equals
            continue
        if flag == "--ref" and verb == "release":
            result.append(f"--ahead-check-ref{equals}{remainder}")
            keep_next = not equals
            continue
        if value.startswith("-") and value != "-":
            result.append(value)
            keep_next = not equals and flag in value_flags
            continue
        if not removed_target:
            removed_target = True
            continue
        result.append(value)
    config_reference = config_path.expanduser()
    if not config_reference.is_absolute():
        config_reference = Path.cwd() / config_reference
    try:
        relative = _config_reference_relative_to_git_root(
            config_path, source_git_root or repo_root,
        )
    except ValueError:
        # The CMRU root is allowed to be a central directory above/around the
        # selected Git family. Such a root is still the authoritative config
        # source for the child; load_config remaps registered project documents
        # into the isolated worktree.
        config_arg = str(config_reference)
    else:
        config_arg = str(relative)
    if target_override is not None:
        result.insert(0, target_override)
    result.extend(_forwarded_global_args(forward_from, rest))
    result.extend(["--config", config_arg])
    return result


def _dispatch_independent_git_families(
    verb: str,
    rest: List[str],
    config_path: Path,
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: Sequence[str],
    *,
    original_target: str | None,
    origin_main_snapshots: Mapping[Path, str] | None = None,
    forward_from: object | None,
) -> int | None:
    """Run one normal transaction per independent selected Git family.

    Git cannot make commits in independent repositories atomic.  Keeping the
    existing single-family transaction as the child protocol, while dispatching
    each family as its own explicit invocation, preserves that limitation in
    observable state: every family has its own branch, workspace record, lock,
    promotion result, and recovery path.
    """
    if len(project_names) <= 1:
        return None
    groups = transaction.project_git_family_groups(
        repo_root, [configs[name] for name in project_names]
    )
    if len(groups) <= 1:
        return None
    if origin_main_snapshots is not None and (
        verb != "release" or set(origin_main_snapshots) != set(groups)
    ):
        raise RuntimeError(
            "preflighted origin/main snapshots do not match the selected release families"
        )
    if any(flag in rest or any(item.startswith(flag + "=") for item in rest)
           for flag in ("--resume",)):
        raise UsageRefusal(
            "resume must target one retained CMRU family workspace at a time; "
            "select one Git-family project set"
        )
    try:
        import shutil
        launcher = transaction.internal_launcher(repo_root) or shutil.which("cmru")
        if launcher:
            command_prefix = [launcher]
        else:
            command_prefix = [sys.executable, "-m", "cmru"]
        for family_root, members in groups.items():
            names = [getattr(project, "name") for project in members]
            child_args = _child_release_args(
                rest,
                config_path,
                repo_root,
                source_git_root=family_root,
                target_override=",".join(names),
                original_target=original_target,
                verb=verb,
                forward_from=forward_from,
            )
            child_env = os.environ.copy()
            child_env.pop("CMRU_RELEASE_PREFLIGHT_SNAPSHOT", None)
            child_env.pop(_RELEASE_PREFLIGHT_SNAPSHOT_FD_ENV, None)
            snapshot_fd = None
            try:
                if origin_main_snapshots is not None:
                    snapshot_fd, write_fd = os.pipe()
                    try:
                        payload = (
                            f"{family_root.resolve()}:{origin_main_snapshots[family_root]}"
                        ).encode("utf-8")
                        if os.write(write_fd, payload) != len(payload):
                            raise RuntimeError("short write while handing off release snapshot")
                    finally:
                        os.close(write_fd)
                    child_env[_RELEASE_PREFLIGHT_SNAPSHOT_FD_ENV] = str(snapshot_fd)
                completed = subprocess.run(
                    [*command_prefix, verb, *child_args],
                    cwd=Path.cwd(),
                    env=child_env,
                    **({"pass_fds": (snapshot_fd,)} if snapshot_fd is not None else {}),
                )
            finally:
                if snapshot_fd is not None:
                    os.close(snapshot_fd)
            if completed.returncode:
                return completed.returncode
    except OSError as exc:
        raise RuntimeError(f"could not dispatch per-Git-family {verb} transaction: {exc}") from exc
    return 0


def _preflight_multi_family_release_tag_support(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: Sequence[str],
    *,
    config_path: Path,
    git_auth: GitHubGitAuth | None,
) -> dict[Path, str] | None:
    """Check every tagged family before a release launcher dispatches any child.

    A later family's Git refusal must not come after an earlier independent
    family has already started its release cycle. Read ``git_tag`` from each
    family's fetched origin/main snapshot, which is the configuration the child
    will release. Each child repeats the capability check under its own release
    lock before doing family-local work.
    """
    groups = transaction.project_git_family_groups(
        repo_root, [configs[name] for name in project_names]
    )
    if len(groups) <= 1:
        return None
    snapshots: dict[Path, str] = {}
    for family_root in groups:
        snapshots[family_root] = transaction.fetch_origin_main(
            family_root, git_auth=git_auth,
        )

    tagged_families: dict[Path, bool] = {}
    for family_root, members in groups.items():
        base = snapshots[family_root]
        project_config_paths = _project_config_paths_at_snapshot(
            family_root, base, config_path, configs,
            [getattr(project, "name") for project in members],
        )
        has_tagged_project = False
        for project in members:
            has_tagged_project = (
                _project_git_tag_policy_at_snapshot(
                    family_root, base, project,
                    project_config_rel=project_config_paths[getattr(project, "name")],
                ) or has_tagged_project
            )
        tagged_families[family_root] = has_tagged_project
    for family_root, is_tagged in tagged_families.items():
        if is_tagged:
            _require_local_tag_inspection_support(family_root)
    return snapshots


def _project_git_tag_policy_at_snapshot(
    repo_root: Path,
    base: str,
    project: "ProjectConfig",
    *,
    project_config_rel: Path | None = None,
) -> bool:
    """Read one selected project's explicit release tag policy from a commit."""
    project_name = getattr(project, "name", "selected project")
    if project_config_rel is None:
        project_config_rel = _project_config_paths_from_loaded(
            repo_root, {project_name: project}, [project_name],
        )[project_name]
    if (
        project_config_rel.is_absolute()
        or ".." in project_config_rel.parts
        or project_config_rel.name != PROJECT_CONFIG_FILENAME
    ):
        raise RuntimeError(f"{project_name}: invalid project config path in Git family")
    config_rel = project_config_rel.as_posix()
    content = _read_git_path_at_commit(
        repo_root, base, project_config_rel,
        source_label=f"origin/main ({base}:{config_rel})",
    )
    return _parse_project_git_tag_policy(
        content, project_name,
        f"origin/main ({base}:{config_rel})",
    )


def _resolve_git_file_at_commit(
    repo_root: Path, revision: str, relative_path: Path, *, source_label: str,
) -> tuple[Path, str]:
    """Resolve and read a regular file, following only in-tree Git symlinks."""
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or not relative_path.parts
    ):
        raise RuntimeError(f"Invalid tracked path at {source_label}: {relative_path}")
    pending = PurePosixPath(relative_path.as_posix())
    for _ in range(40):
        parts = pending.parts
        treeish = revision
        parent_parts: list[str] = []
        for index, component in enumerate(parts):
            result = run_local_git(
                repo_root,
                "ls-tree", "-z", treeish, "--", f":(literal){component}",
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
                raise RuntimeError(f"Failed to inspect {source_label}: {detail}")
            entry = None
            tree_output = result.stdout
            if tree_output and not tree_output.endswith("\0"):
                raise RuntimeError(
                    f"Malformed Git tree response at {source_label}: missing NUL terminator"
                )
            raw_entries = tree_output[:-1].split("\0") if tree_output else []
            if any(not raw_entry for raw_entry in raw_entries):
                raise RuntimeError(
                    f"Malformed Git tree response at {source_label}: empty entry"
                )
            if len(raw_entries) > 1:
                raise RuntimeError(
                    f"Malformed Git tree response at {source_label}: expected a single entry"
                )
            if raw_entries:
                raw_entry = raw_entries[0]
                metadata, separator, entry_path = raw_entry.partition("\t")
                if not separator:
                    raise RuntimeError(
                        f"Malformed Git tree response at {source_label}: missing path separator"
                    )
                fields = metadata.split()
                if len(fields) != 3:
                    raise RuntimeError(
                        f"Malformed Git tree metadata at {source_label}: "
                        "expected mode, type and object ID"
                    )
                if entry_path != component:
                    raise RuntimeError(
                        f"Git returned an unexpected path at {source_label}: {entry_path!r}"
                    )
                entry = (fields[0], fields[1], fields[2])
            if entry is None:
                tracked_path = PurePosixPath(*parent_parts, component)
                raise RuntimeError(
                    f"Tracked path is missing from {source_label}: {tracked_path}"
                )
            mode, object_type, object_id = entry
            tracked_path = PurePosixPath(*parent_parts, component)
            if mode == "120000" and object_type == "blob":
                target_result = run_local_git(
                    repo_root, "cat-file", "blob", object_id,
                    capture_output=True, text=True, check=False,
                )
                if target_result.returncode != 0:
                    detail = (
                        target_result.stderr.strip() or target_result.stdout.strip()
                        or "no diagnostic output"
                    )
                    raise RuntimeError(
                        f"Failed to read symlink at {source_label} ({tracked_path}): {detail}"
                    )
                target_value = target_result.stdout
                target = PurePosixPath(target_value)
                if target.is_absolute():
                    raise RuntimeError(
                        f"Symlink escapes the Git family at {source_label}: {tracked_path}"
                    )
                resolved = list(parts[:index])
                for component in target_value.split("/"):
                    if component in {"", "."}:
                        continue
                    if component == "..":
                        if not resolved:
                            raise RuntimeError(
                                f"Symlink escapes the Git family at {source_label}: "
                                f"{tracked_path}"
                            )
                        resolved.pop()
                    else:
                        resolved.append(component)
                resolved.extend(parts[index + 1:])
                if not resolved:
                    raise RuntimeError(
                        f"Symlink does not resolve to a file at {source_label}: {tracked_path}"
                    )
                pending = PurePosixPath(*resolved)
                break
            if index < len(parts) - 1:
                if mode != "040000" or object_type != "tree":
                    raise RuntimeError(
                        f"Tracked path component is not a directory at {source_label}: "
                        f"{tracked_path}"
                    )
                treeish = object_id
                parent_parts.append(component)
                continue
            if mode not in {"100644", "100755"} or object_type != "blob":
                raise RuntimeError(
                    f"Tracked path is not a regular file at {source_label}: {tracked_path}"
                )
            content = run_local_git(
                repo_root, "cat-file", "blob", object_id,
                capture_output=True, text=True, check=False,
            )
            if content.returncode != 0:
                detail = content.stderr.strip() or content.stdout.strip() or "no diagnostic output"
                raise RuntimeError(f"Failed to read {source_label}: {detail}")
            return Path(tracked_path.as_posix()), content.stdout
    raise RuntimeError(f"Symlink resolution exceeded its limit at {source_label}: {relative_path}")


def _read_git_path_at_commit(
    repo_root: Path, revision: str, relative_path: Path, *, source_label: str,
) -> str:
    """Read a regular file from a commit, resolving only in-tree symlinks."""
    return _resolve_git_file_at_commit(
        repo_root, revision, relative_path, source_label=source_label,
    )[1]


def _resolve_project_config_paths_at_commit(
    repo_root: Path,
    revision: str,
    project_paths: Mapping[str, Path],
    *,
    source_label: str,
) -> dict[str, Path]:
    """Resolve selected configs and retain the loader's required basename."""
    result: dict[str, Path] = {}
    for name, path in project_paths.items():
        resolved_path, _content = _resolve_git_file_at_commit(
            repo_root, revision, path,
            source_label=f"{source_label}:{path.as_posix()}",
        )
        if resolved_path.name != PROJECT_CONFIG_FILENAME:
            raise RuntimeError(
                f"{name}: project config symlink target must be named "
                f"{PROJECT_CONFIG_FILENAME}, matching the shipped config loader "
                f"({source_label}:{resolved_path.as_posix()})"
            )
        result[name] = resolved_path
    return result


def _project_config_paths_from_loaded(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: Sequence[str],
) -> dict[str, Path]:
    """Resolve selected project config paths from an external/current config."""
    result: dict[str, Path] = {}
    for name in project_names:
        project_root_value = getattr(configs[name], "project_root", None)
        if project_root_value is None:
            raise RuntimeError(
                f"{name}: project_root is required to read release policy"
            )
        project_root = Path(project_root_value)
        if not project_root.is_absolute():
            project_root = repo_root / project_root
        try:
            project_rel = project_root.resolve().relative_to(repo_root.resolve())
        except ValueError as exc:
            raise RuntimeError(
                f"{name}: project config is outside Git family {repo_root}"
            ) from exc
        result[name] = project_rel / PROJECT_CONFIG_FILENAME
    return result


def _config_reference_relative_to_git_root(
    config_path: Path, git_root: Path,
) -> Path:
    """Map a selected config link to its Git path without resolving the link.

    The caller may reach the checkout through a symlinked repository alias.
    Find the lexical ancestor that resolves to the physical Git root, then keep
    every path component below that ancestor intact for Git-tree resolution.
    """
    reference = config_path.expanduser()
    if not reference.is_absolute():
        reference = Path.cwd() / reference
    physical_root = git_root.resolve()
    try:
        return reference.relative_to(physical_root)
    except ValueError:
        pass

    ancestor = reference.parent
    while True:
        if ancestor.resolve() == physical_root:
            return reference.relative_to(ancestor)
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    raise ValueError(f"{reference} is outside Git family {physical_root}")


def _parse_project_config_paths_from_orchestration(
    orchestration_rel: Path,
    content: str,
    project_names: Sequence[str],
    *,
    source_label: str,
) -> dict[str, Path]:
    """Read selected cmru.toml paths from one orchestration document."""
    try:
        orchestration = tomllib.loads(content)["orchestration"]
        entries = orchestration["project"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Invalid orchestration config at {source_label}: "
            "selected project config paths are unavailable"
        ) from exc

    result: dict[str, Path] = {}
    for name in project_names:
        try:
            entry = entries[name]
            config_value = entry["config"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Invalid orchestration config at {source_label}: "
                f"project {name!r} has no config path"
            ) from exc
        if not isinstance(config_value, str) or not config_value.strip():
            raise RuntimeError(
                f"Invalid orchestration config at {source_label}: "
                f"project {name!r} config must be a non-empty relative path"
            )
        config_rel = Path(config_value)
        if (
            config_rel.is_absolute()
            or ".." in config_rel.parts
            or config_rel.name != PROJECT_CONFIG_FILENAME
        ):
            raise RuntimeError(
                f"Invalid orchestration config at {source_label}: "
                f"project {name!r} config must stay inside the family and end in "
                f"{PROJECT_CONFIG_FILENAME}"
            )
        result[name] = orchestration_rel.parent / config_rel
    return result


def _project_config_paths_at_snapshot(
    repo_root: Path,
    base: str,
    config_path: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: Sequence[str],
) -> dict[str, Path]:
    """Resolve selected config paths from the exact release source snapshot."""
    try:
        config_rel = _config_reference_relative_to_git_root(config_path, repo_root)
    except ValueError:
        return _project_config_paths_from_loaded(repo_root, configs, project_names)

    resolved_config_rel, content = _resolve_git_file_at_commit(
        repo_root, base, config_rel,
        source_label=f"origin/main ({base}:{config_rel.as_posix()})",
    )
    if resolved_config_rel.name == PROJECT_CONFIG_FILENAME:
        if len(project_names) != 1:
            raise RuntimeError(
                f"{config_path}: a project config can select only one project"
            )
        return _resolve_project_config_paths_at_commit(
            repo_root, base, {project_names[0]: config_rel},
            source_label=f"origin/main ({base})",
        )
    if resolved_config_rel.name != ORCHESTRATION_CONFIG_FILENAME:
        raise RuntimeError(f"Unsupported CMRU config path in Git family: {config_path}")

    parsed_paths = _parse_project_config_paths_from_orchestration(
        resolved_config_rel, content, project_names,
        source_label=(
            f"origin/main ({base}:{resolved_config_rel.as_posix()})"
        ),
    )
    return _resolve_project_config_paths_at_commit(
        repo_root, base, parsed_paths, source_label=f"origin/main ({base})",
    )


def _project_config_paths_in_candidate(
    source_git_root: Path,
    candidate_root: Path,
    config_path: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: Sequence[str],
) -> dict[str, Path]:
    """Resolve selected config paths from a committed retained candidate."""
    try:
        config_rel = _config_reference_relative_to_git_root(config_path, source_git_root)
    except ValueError:
        return _project_config_paths_from_loaded(source_git_root, configs, project_names)

    candidate_revision = run_local_git(
        candidate_root, "rev-parse", "--verify", "HEAD",
        capture_output=True, text=True, check=False,
    )
    if candidate_revision.returncode != 0:
        detail = (
            candidate_revision.stderr.strip() or candidate_revision.stdout.strip()
            or "no diagnostic output"
        )
        raise RuntimeError(f"Could not identify committed retained candidate: {detail}")
    candidate_sha = candidate_revision.stdout.strip()

    resolved_config_rel, content = _resolve_git_file_at_commit(
        candidate_root, candidate_sha, config_rel,
        source_label=f"retained candidate ({candidate_sha}:{config_rel.as_posix()})",
    )
    if resolved_config_rel.name == PROJECT_CONFIG_FILENAME:
        if len(project_names) != 1:
            raise RuntimeError(
                f"{config_path}: a project config can select only one project"
            )
        return _resolve_project_config_paths_at_commit(
            candidate_root, candidate_sha, {project_names[0]: config_rel},
            source_label=f"retained candidate ({candidate_sha})",
        )
    if resolved_config_rel.name != ORCHESTRATION_CONFIG_FILENAME:
        raise RuntimeError(f"Unsupported CMRU config path in Git family: {config_path}")
    parsed_paths = _parse_project_config_paths_from_orchestration(
        resolved_config_rel, content, project_names,
        source_label=(
            f"retained candidate ({candidate_sha}:{resolved_config_rel.as_posix()})"
        ),
    )
    return _resolve_project_config_paths_at_commit(
        candidate_root, candidate_sha, parsed_paths,
        source_label=f"retained candidate ({candidate_sha})",
    )


def _assert_resume_candidate_is_safe_to_replay(
    repo_root: Path,
    workspace: transaction.ReleaseWorkspace,
    project_names: Sequence[str],
    configs: Mapping[str, "ProjectConfig"],
    *,
    git_auth: GitHubGitAuth,
    release_policies: Mapping[str, tuple[str, bool]] | None = None,
) -> None:
    """Allow only pre-tag retries and results already promoted to origin/main."""
    scope = set(project_names)
    unknown_projects = sorted(scope - set(configs))
    if unknown_projects:
        raise UnsafeRecord(
            "retained release scope names unknown project(s): "
            + ", ".join(unknown_projects)
        )
    policies = {
        name: (
            getattr(configs[name], "prefix", None) or f"{name}-v",
            getattr(configs[name], "git_tag", True),
        )
        for name in project_names
    }
    if release_policies is not None:
        unknown_policies = sorted(set(release_policies) - scope)
        if unknown_policies:
            raise UnsafeRecord(
                "retained candidate release policy names project(s) outside the recorded "
                "scope: " + ", ".join(unknown_policies)
            )
        policies.update(release_policies)
    results = transaction.read_release_results(repo_root, workspace)
    unexpected_results = sorted(set(results) - scope)
    if unexpected_results:
        raise UnsafeRecord(
            "retained release result metadata names project(s) outside the recorded "
            "scope: " + ", ".join(unexpected_results)
        )

    pending_tagged = [
        name for name in project_names
        if name not in results and policies[name][1]
    ]
    has_tagged_results = any(
        name in configs and policies[name][1]
        for name in results
    )
    origin_tags: dict[str, str] | None = None
    if pending_tagged or has_tagged_results:
        origin_tags = _read_origin_tag_refs(
            repo_root, git_auth=git_auth,
            context="verify release tags before resuming a retained candidate",
        )

    if results:
        origin_main = transaction.fetch_origin_main(repo_root, git_auth=git_auth)
        tag_records = _tag_records_by_name(origin_tags or {})
        attempts = transaction.read_release_tag_attempts(repo_root, workspace) or {}
        for name, result_id in results.items():
            if policies[name][1]:
                prefix = policies[name][0]
                tag_name = result_id
                if not tag_name.startswith(prefix) or tag_name.endswith("-latest"):
                    raise UnsafeRecord(
                        f"retained release result for {name} is not a valid release tag: "
                        f"{result_id!r}"
                    )
                tag_record = tag_records.get(tag_name, {})
                result_commit = (
                    tag_record.get(f"refs/tags/{tag_name}^{{}}")
                    or tag_record.get(f"refs/tags/{tag_name}")
                )
                attempted_oid = attempts.get(f"refs/tags/{tag_name}")
                remote_oid = (origin_tags or {}).get(f"refs/tags/{tag_name}")
                if result_commit is None or remote_oid is None:
                    raise UnsafeRecord(
                        f"retained release result for {name} names {tag_name}, but origin "
                        "does not advertise that release tag; inspect the candidate and "
                        "published artifact before retrying"
                    )
                if attempted_oid is not None and remote_oid != attempted_oid:
                    raise UnsafeRecord(
                        f"origin release tag {tag_name} no longer matches CMRU's recorded "
                        "push attempt; inspect the candidate before retrying"
                    )
            else:
                if not result_id.startswith("source-"):
                    raise UnsafeRecord(
                        f"retained untagged release result for {name} is malformed"
                    )
                source_prefix = result_id.removeprefix("source-")
                if not re.fullmatch(r"[0-9a-f]{12,40}", source_prefix):
                    raise UnsafeRecord(
                        f"retained untagged release result for {name} is malformed"
                    )
                resolved = run_local_git(
                    repo_root, "rev-parse", "--verify", f"{source_prefix}^{{commit}}",
                    capture_output=True, text=True, check=False,
                )
                if resolved.returncode != 0:
                    raise RuntimeError(
                        f"cannot resolve retained source result for {name}: "
                        f"{resolved.stderr.strip() or resolved.stdout.strip() or 'unknown Git error'}"
                    )
                result_commit = resolved.stdout.strip()

            promoted = run_local_git(
                repo_root, "merge-base", "--is-ancestor", result_commit, origin_main,
                capture_output=True, text=True, check=False,
            )
            if promoted.returncode == 1:
                raise UnsafeRecord(
                    f"retained result for {name} is recorded, but its source commit "
                    "is not in origin/main; publication or promotion is incomplete, "
                    "so the candidate was kept for inspection"
                )
            if promoted.returncode != 0:
                raise RuntimeError(
                    f"cannot verify retained result for {name} against origin/main: "
                    f"{promoted.stderr.strip() or promoted.stdout.strip() or 'unknown Git error'}"
                )

    if not pending_tagged:
        return

    assert origin_tags is not None
    initial_tags = transaction.read_release_tag_snapshot(repo_root, workspace)
    tag_attempts = transaction.read_release_tag_attempts(repo_root, workspace) or {}
    absence_proofs = transaction.read_confirmed_absent_release_tag_attempts(
        repo_root, workspace,
    )
    local_tags = transaction.list_local_tag_refs(repo_root)
    completed_tag_names = {
        result_id for name, result_id in results.items()
        if policies[name][1]
    }

    for name in pending_tagged:
        prefix = policies[name][0]
        current_project_tags = {
            ref: oid for ref, oid in origin_tags.items()
            if _tag_ref_name(ref).startswith(prefix)
            and not _tag_ref_name(ref).endswith("-latest")
            and _tag_ref_name(ref) not in completed_tag_names
        }
        if initial_tags is not None:
            initial_project_tags = {
                ref: oid for ref, oid in initial_tags.items()
                if _tag_ref_name(ref).startswith(prefix)
                and not _tag_ref_name(ref).endswith("-latest")
                and _tag_ref_name(ref) not in completed_tag_names
            }
            if current_project_tags != initial_project_tags:
                raise UnsafeRecord(
                    f"origin release tags for {name} changed after this candidate was "
                    "created; refuse to replay it and inspect the candidate first"
                )

        attempted_refs = [
            ref for ref in tag_attempts
            if _tag_ref_name(ref).startswith(prefix)
            and not _tag_ref_name(ref).endswith("-latest")
            and _tag_ref_name(ref) not in completed_tag_names
        ]
        unresolved_attempts = [
            ref for ref in attempted_refs
            if (
                ref in local_tags
                or ref in origin_tags
                or absence_proofs.get(ref) != tag_attempts.get(ref)
            )
        ]
        if unresolved_attempts:
            names = ", ".join(sorted(_tag_ref_name(ref) for ref in unresolved_attempts))
            raise UnsafeRecord(
                f"CMRU attempted to push release tag(s) for {name} ({names}), but a tag "
                "is still present or CMRU has no exact record proving origin confirmed "
                "its absence after local removal. Refusing to replay this candidate; "
                "inspect the retained candidate and published artifact before manual "
                "recovery"
            )

        if initial_tags is None:
            head_result = run_local_git(
                workspace.path, "rev-parse", "--verify", "HEAD",
                capture_output=True, text=True, check=False,
            )
            if head_result.returncode != 0:
                raise RuntimeError(
                    "cannot identify retained candidate HEAD while checking legacy "
                    "release tags"
                )
            candidate_head = head_result.stdout.strip()
            tag_records = _tag_records_by_name(current_project_tags)
            for tag_name, records in tag_records.items():
                target = (
                    records.get(f"refs/tags/{tag_name}^{{}}")
                    or records.get(f"refs/tags/{tag_name}")
                )
                if target == candidate_head:
                    raise UnsafeRecord(
                        f"legacy retained candidate HEAD is tagged as {tag_name}, but no "
                        "pre-attempt tag snapshot proves whether this transaction pushed "
                        "it; inspect the candidate before retrying"
                    )


def _parse_project_git_tag_policy(
    content: str, project_name: str, source_label: str,
) -> bool:
    return _parse_project_release_policy(content, project_name, source_label)[1]


def _parse_project_release_policy(
    content: str, project_name: str, source_label: str,
) -> tuple[str, bool]:
    """Read the project tag prefix and tag policy from one project document."""
    try:
        project_document = tomllib.loads(content)["project"]
        project_id = project_document["id"]
        prefix = project_document["prefix"]
        git_tag = project_document["release"]["git_tag"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Invalid project config at {source_label}: "
            "project.id, project.prefix, and project.release.git_tag are required"
        ) from exc
    if project_id != project_name:
        raise RuntimeError(
            f"Invalid project config at {source_label}: project.id is {project_id!r}, "
            f"expected {project_name!r}"
        )
    if not isinstance(prefix, str) or not prefix.strip():
        raise RuntimeError(f"Invalid project config at {source_label}: project.prefix is required")
    if not isinstance(git_tag, bool):
        raise RuntimeError(
            f"Invalid project config at {source_label}: "
            "project.release.git_tag must be explicitly true or false"
        )
    return prefix.strip(), git_tag


def _project_release_policy_in_candidate(
    candidate_root: Path, project_name: str, project_config_rel: Path,
) -> tuple[str, bool]:
    """Read prefix and release.git_tag from one retained candidate config."""
    candidate_revision = run_local_git(
        candidate_root, "rev-parse", "--verify", "HEAD",
        capture_output=True, text=True, check=False,
    )
    if candidate_revision.returncode != 0:
        detail = (
            candidate_revision.stderr.strip() or candidate_revision.stdout.strip()
            or "no diagnostic output"
        )
        raise RuntimeError(f"Could not identify committed retained candidate: {detail}")
    candidate_sha = candidate_revision.stdout.strip()
    content = _read_git_path_at_commit(
        candidate_root, candidate_sha, project_config_rel,
        source_label=(
            f"retained candidate ({candidate_sha}:{project_config_rel.as_posix()})"
        ),
    )
    return _parse_project_release_policy(
        content, project_name,
        f"retained candidate ({candidate_sha}:{project_config_rel.as_posix()})",
    )


def _project_git_tag_policy_in_candidate(
    candidate_root: Path, project_name: str, project_config_rel: Path,
) -> bool:
    """Read release.git_tag from one project config in a retained worktree."""
    return _project_release_policy_in_candidate(
        candidate_root, project_name, project_config_rel,
    )[1]


def _consume_release_snapshot_handoff(
    repo_root: Path, handoff: str | None,
) -> str | None:
    """Validate the private family-launcher handoff for an exact commit."""
    if handoff is None:
        return None
    source_root, separator, base = handoff.rpartition(":")
    if (
        not separator
        or Path(source_root).resolve() != repo_root.resolve()
        or not transaction.COMMIT_ID_RE.fullmatch(base)
    ):
        raise RuntimeError("invalid CMRU preflighted origin/main snapshot handoff")
    result = run_local_git(
        repo_root, "cat-file", "-e", f"{base}^{{commit}}",
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"preflighted origin/main commit is unavailable: {detail}")
    return base


def _read_release_snapshot_handoff_from_pipe() -> str | None:
    """Read the private snapshot payload passed by a family-dispatch parent."""
    raw_fd = os.environ.pop(_RELEASE_PREFLIGHT_SNAPSHOT_FD_ENV, None)
    if raw_fd is None:
        return None
    fd = None
    try:
        fd = int(raw_fd)
        if fd <= 2 or not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise ValueError("expected an inherited pipe descriptor")
        os.set_blocking(fd, False)
        payload = bytearray()
        while len(payload) <= 8192:
            chunk = os.read(fd, 8193 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > 8192:
            raise ValueError("snapshot handoff payload is too large")
        return payload.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid internal release snapshot pipe: {exc}") from exc
    finally:
        if fd is not None and fd > 2:
            try:
                os.close(fd)
            except OSError:
                pass


def _configs_for_git_family(
    configs: Mapping[str, "ProjectConfig"],
    project_names: Sequence[str],
    git_root: Path,
) -> dict[str, "ProjectConfig"]:
    """Rebase the execution model for read-only/direct project verbs.

    ``load_config`` normally derives paths from the CMRU root.  When that root
    is above a repository, Git-facing helpers need the same projects expressed
    relative to the selected repository instead; command paths are rebased with
    them so direct verbs cannot accidentally run from the orchestration root.
    """
    result: dict[str, ProjectConfig] = {}
    selected_root = git_root.resolve()
    for name in project_names:
        project = configs[name]
        project_root = Path(getattr(project, "project_root", None) or "")
        if not project_root.is_absolute():
            project_root = selected_root / project_root
        project_root = project_root.resolve()
        try:
            relative = project_root.relative_to(selected_root)
        except ValueError as exc:
            raise UsageRefusal(
                f"{name}: project root {project_root} is outside selected Git root {selected_root}"
            ) from exc
        child_root = selected_root / relative
        rebased_steps: dict[str, list[Command]] = {}
        for step_name, commands in project.steps.items():
            rebased: list[Command] = []
            for command in commands:
                try:
                    command_relative = command.cwd.relative_to(project_root)
                except ValueError as exc:
                    raise UsageRefusal(
                        f"{name}: declared command cwd escapes project root: {command.cwd}"
                    ) from exc
                rebased.append(replace(command, cwd=child_root / command_relative))
            rebased_steps[step_name] = rebased
        cwd = relative.as_posix() if relative.parts else "."
        result[name] = replace(
            project,
            cwd=cwd,
            paths=[cwd],
            project_root=child_root,
            steps=rebased_steps,
        )
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
            raise RefusedBeforeChange(
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
    before any project's prepare/gate cycle starts, never a mid-release
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
        project_root = getattr(project, "project_root", None)
        if project_root is None:
            continue
        project_root = Path(project_root)
        if not project_root.is_absolute():
            project_root = repo_root / project_root
        try:
            relative_root = project_root.resolve().relative_to(repo_root.resolve())
        except ValueError as exc:
            raise UsageRefusal(
                f"{name}: project root {project_root} is outside selected Git root {repo_root}"
            ) from exc
        paths = [relative_root.as_posix() if relative_root.parts else "."]
        changed = _worktree_changed_paths(repo_root, paths=paths)
        if changed:
            dirty[name] = changed
    return dirty


def _is_declared_generated(path: str, declared: List[str]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in declared)


RELEASE_CANDIDATE_TRAILER = "Cmru-Release-Candidate"


def _commit_prepared_generated(
    repo_root: Path, project: "ProjectConfig", candidate_label: str | None = None,
) -> bool:
    """Commit only a prepare step's declared generated outputs, or fail closed.

    Generated source is part of the release input, never a side effect to sweep
    into a post-publish commit.  This deliberately checks the entire worktree so
    a prepare script cannot hide an unrelated mutation behind one allowlisted file.
    The commit carries a ``Cmru-Release-Candidate: <tag>`` trailer (REL-08) so the
    release-inputs commit is recognisably candidate-only history, never something
    to merge into main by hand.
    """
    cwd = _project_working_directory(project)
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
        raise RefusedBeforeChange(
            f"{project.name}: prepare changed undeclared paths: {', '.join(unexpected)}; "
            "declare mechanical outputs in project.<name>.release.commit_generated"
        )
    subprocess.run(["git", "add", "-A", "--", *changed], cwd=repo_root, check=True)
    message = f"chore({project.name}): prepare release inputs"
    if candidate_label:
        message += f"\n\n{RELEASE_CANDIDATE_TRAILER}: {candidate_label}"
    run_local_git(repo_root, "commit", "-m", message, check=True)
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
    from cmru.changelog import generate_release_changelog, pending_release_tag

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
            candidate_label = project.name
            if getattr(project, "git_tag", True):
                try:
                    candidate_label = pending_release_tag(
                        repo_root, project, minor=minor, major=major,
                        set_version=set_version,
                    ) or project.name
                except RuntimeError:
                    pass  # the trailer is provenance only; fall back to the name
            _commit_prepared_generated(repo_root, project, candidate_label)


def _prepare_dry_run_external_versions(
    repo_root: Path,
    configs: Mapping[str, "ProjectConfig"],
    project_names: List[str],
    *,
    github_config: "GitHubConfig",
    env_config: "ReleaseEnvConfig",
) -> None:
    """Populate disposable external-version inputs before a dry-run plan.

    ``external:VAR`` is intentionally derived by the project's declared
    ``prepare`` step. A dry run must therefore execute that narrow preparation
    in its isolated transaction before asking the version planner what the
    release would do; otherwise PWMCP's resolver cannot expose the version it
    just discovered. No gate, tag, build, push, or promotion is performed.
    """
    log_dir = repo_root / "logs"
    for name in project_names:
        project = configs[name]
        if not _version_strategy(project).startswith("external:"):
            continue
        if "prepare" not in (project.steps or {}):
            continue
        apply_project_release_env(github_config, env_config, project)
        log_info(f"{name}: preparing external version inputs for dry-run")
        run_project_step(project, "prepare", repo_root, log_dir)
        _commit_prepared_generated(repo_root, project)


def _config_hint(repo_root: Path) -> str:
    """Return an explicit config argument for commands printed to operators."""
    for filename in (PROJECT_CONFIG_FILENAME, ORCHESTRATION_CONFIG_FILENAME):
        candidate = repo_root / filename
        if candidate.exists():
            return f" --config {shlex.quote(str(candidate))}"
    return ""


def _current_git_root() -> Path:
    """Resolve the current repository root through the shared Git API."""

    return transaction._shared_worktree().discover_git_root(Path.cwd())[0]


def _dispatch(args, runtime):
    """Run a root CMRU command from cli-extended's parsed grammar."""
    import sys as _sys

    verb = args.verb
    rest = list(runtime.command_argv)
    # The library's common controls are absent or None unless given; every
    # verb body below reads plain booleans.
    args.dry_run = bool(getattr(args, "dry_run", False))
    args.yes = bool(getattr(args, "yes", False))
    if verb == "run":
        _orchestrate(args)

    elif verb == "worktrees":
        vargs = args
        shared = transaction._shared_worktree()
        try:
            repo_root = _current_git_root()
        except shared.WorkspaceError as exc:
            _usage_error(
                "cmru worktrees must run inside the repository whose worktrees "
                f"you want to inspect: {exc}"
            )
        workspaces = transaction.list_cmru_workspaces(repo_root)
        release_scopes = {}
        for workspace in workspaces:
            if transaction.workspace_purpose(workspace.branch) != "release":
                continue
            try:
                scope = transaction.read_release_scope_for_workspace(repo_root, workspace)
            except RuntimeError:
                release_scopes[workspace.branch] = (None, "unreadable")
            else:
                release_scopes[workspace.branch] = (
                    scope, "recorded" if scope is not None else "missing",
                )
        if vargs.json:
            records = []
            for workspace in workspaces:
                record = {
                    "purpose": transaction.workspace_purpose(workspace.branch),
                    "branch": workspace.branch,
                    "path": str(workspace.path),
                    "source_commit": workspace.base or None,
                    "prunable": workspace.is_prunable,
                }
                if record["purpose"] == "release":
                    scope, state = release_scopes[workspace.branch]
                    record["project_scope"] = scope
                    record["project_scope_state"] = state
                records.append(record)
            runtime.output.primary(records)
        elif not workspaces:
            log_info("No retained CMRU build or release worktrees.")
        else:
            config_hint = _config_hint(repo_root)
            for workspace in workspaces:
                purpose = transaction.workspace_purpose(workspace.branch)
                source = workspace.base[:12] if workspace.base else "unknown"
                print(f"{purpose}: {workspace.branch}\n  path: {workspace.path}\n  source: {source}")
                if purpose == "release":
                    scope, state = release_scopes[workspace.branch]
                    if state == "recorded":
                        print(f"  project scope: {', '.join(scope)}")
                    else:
                        print(f"  project scope: {state}")
                    if workspace.is_prunable:
                        print(
                            "  action: withheld; Git marks this worktree registration prunable. "
                            "Inspect the checkout and its Git metadata before acting."
                        )
                    elif state == "recorded":
                        scope_arg = shlex.quote(",".join(scope))
                        print(
                            f"  resume: cmru release {scope_arg} --resume "
                            f"{shlex.quote(str(workspace.path))}"
                        )
                        print(
                            "  config: repeat the same --config PATH used by the original "
                            "release if it used an external config; its path is not recorded"
                        )
                    elif state == "missing":
                        print(
                            "  resume: inspect the candidate and pass its explicit target; "
                            "repeat the original external --config PATH if one was used"
                        )
                    else:
                        print("  resume: withheld; cannot verify recorded project scope")
                elif purpose == "build" and not workspace.is_prunable:
                    print(
                        "  discard: cmru abandon"
                        f"{config_hint} {shlex.quote(str(workspace.path))} --yes"
                    )
                elif workspace.is_prunable:
                    print(
                        "  action: withheld; Git marks this worktree registration prunable. "
                        "Inspect the checkout and its Git metadata before acting."
                    )


    elif verb == "dependencies":
        vargs = args
        cfg_path = _resolve_config(vargs.config)
        resolved_cfg_path = cfg_path.expanduser().resolve()
        forge = load_forge_config(resolved_cfg_path)
        if (
            forge.orchestration is None
            or resolved_cfg_path.name != ORCHESTRATION_CONFIG_FILENAME
        ):
            _usage_error(f"dependencies requires {ORCHESTRATION_CONFIG_FILENAME}")
        report = build_report(
            repo_root=forge.repo_root,
            project_order=forge.orchestration.project_order,
            declared=forge.orchestration.dependencies,
            projects=forge.projects,
        )
        if vargs.write:
            from cmru.dependencies import write_comment_block
            if vargs.dry_run:
                if not write_comment_block(resolved_cfg_path, report, dry_run=True):
                    print(f"[INFO] Dependency graph already matches {cfg_path}")
            else:
                write_comment_block(resolved_cfg_path, report)
                print(f"[INFO] Wrote generated dependency graph to {cfg_path}")
        # (--dry-run without --write is refused by the verb's Requires constraint)
        if vargs.json:
            runtime.output.primary(report.as_dict())
        else:
            print(render_dependency_report(report))
        if report.errors:
            _sys.exit(exit_codes.CONFIG_ERROR)



    elif verb in ("build", "publish"):
        vargs = args
        _apply_output_options(vargs)
        cfg_path = _resolve_config(vargs.config)
        (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
        github_config, env_config = _rest[-2], _rest[-1]
        git_auth = _git_auth_for_repository(github_config)
        apply_release_env(github_config, env_config)
        ordered = _ordered_configs(configs, project_order)
        names = _select_projects(cfg_path, vargs.target, configs, project_order)
        build_output_id = getattr(vargs, "build_output", None) if verb == "publish" else None
        if verb == "publish" and not getattr(vargs, "from_checkout", False) and build_output_id is None:
            _usage_error("publish needs a source: --build-output ID or --from-checkout")
        # ``is not None``: an empty ID must fail validation, never become an
        # unverified checkout publish.
        if build_output_id is not None and not transaction.is_build_output_id(build_output_id):
            _usage_error(
                "publish --build-output must use the exact ID printed by cmru build "
                "(<UTC timestamp>_<40-character commit SHA>)"
            )
        if build_output_id is not None and len(names) != 1:
            _usage_error("publish --build-output requires exactly one selected project")
        if verb == "publish" and not vargs.dry_run and build_output_id is None:
            require_project_publish_credentials(configs, names)
        step = "build" if verb == "build" else "push"
        transaction_child = transaction.is_transaction_child(repo_root)

        if vargs.dry_run:
            log_info(f"[DRY RUN] Would run {step} for: {', '.join(names)}")
            build_records = {}
            if build_output_id:
                for name in names:
                    project = configs[name]
                    build_records[name] = transaction.validate_retained_build_output(
                        project, name, build_output_id,
                    )
                    manifest = build_records[name]["manifest"]
                    log_info(
                        f"[DRY RUN] {name}: publish build {build_output_id} "
                        f"from {manifest['source_commit']} via "
                        f"{build_records[name]['artifact_root']}"
                    )
                    log_info(
                        f"[DRY RUN] {name}: push receives protected "
                        "CMRU_BUILD_OUTPUT_ROOT, CMRU_BUILD_OUTPUT_ID, "
                        "CMRU_BUILD_OUTPUT_PROJECT, CMRU_BUILD_SOURCE_COMMIT, "
                        "and CMRU_BUILD_SOURCE_DATE"
                    )
                    for artifact in manifest["artifacts"]:
                        for item in artifact["files"]:
                            log_info(
                                f"[DRY RUN] {name}: verified "
                                f"{artifact['directory']}/{item['path']} "
                                f"sha256={item['sha256']} bytes={item['bytes']}"
                            )
            from cmru.runner import render_step_plan
            for name in names:
                project = configs[name]
                step_config = (project.runner_steps or {}).get(step)
                if step_config is None:
                    raise StepUnavailable(f"{name}: required declared step {step!r} is absent")
                project_root = resolve_cwd(repo_root, _project_working_directory(project))
                for line in render_step_plan(step_config, project_root):
                    log_info(f"[DRY RUN] {name}:{step}: {line}")
            return

        if verb == "build" and not transaction_child:
            dispatched = _dispatch_independent_git_families(
                verb,
                rest,
                cfg_path,
                repo_root,
                configs,
                names,
                original_target=vargs.target,
                forward_from=vargs,
            )
            if dispatched is not None:
                _sys.exit(dispatched)
            # A normal build uses the exact same isolated source boundary as a
            # release, but intentionally stops before every release action.  A
            # successful build copies its local-only evidence to the caller tree
            # and removes the worktree; a failure retains the worktree exactly
            # where the diagnostic source/log/artifact state exists.
            try:
                transaction_root = transaction.source_git_root_for_projects(
                    repo_root, [configs[name] for name in names]
                )
                child_args = _child_release_args(
                    rest, cfg_path, repo_root, source_git_root=transaction_root,
                    target_override=",".join(names), original_target=vargs.target,
                    verb="build", forward_from=vargs,
                )
                with transaction.release_lock(transaction_root):
                    dirty = _uncommitted_release_paths(transaction_root, configs, names)
                    if dirty:
                        for project_name, files in dirty.items():
                            log_error(f"{project_name}: uncommitted changes — {', '.join(files)}")
                        raise transaction.RefusedBeforeChange(
                            "cmru build snapshots origin/main; commit and push the selected project "
                            "changes first so the isolated build cannot silently omit them."
                        )
                    base = transaction.fetch_origin_main(transaction_root, git_auth=git_auth)
                    snapshot_config_paths = _project_config_paths_at_snapshot(
                        transaction_root, base, cfg_path, configs, names,
                    )
                    behind = transaction.assert_local_main_not_ahead(transaction_root)
                    if behind:
                        log_warn(
                            f"Local main is {behind} commit(s) behind origin/main; "
                            f"build uses fetched origin/main {base[:12]}."
                        )
                    workspace = transaction.create_workspace(
                        repo_root, base=base, purpose="build", scope=','.join(names),
                        source_git_root=transaction_root,
                    )
                    transaction.copy_secret_overlays(
                        repo_root,
                        workspace,
                        [Path(configs[name].project_root) / PROJECT_CONFIG_FILENAME for name in names
                         if configs[name].project_root is not None],
                        candidate_config_paths=[snapshot_config_paths[name] for name in names
                                                if configs[name].project_root is not None],
                    )
                    log_info(
                        f"Build transaction {workspace.branch}: snapshot {workspace.base[:12]} "
                        f"at {workspace.path}"
                    )
                    rc = transaction.run_child(
                        workspace, child_args, verb="build", project_names=names,
                    )
                    if rc:
                        log_error(
                            f"Build transaction failed; worktree retained for debugging: {workspace.path}"
                        )
                        _sys.exit(rc)
                    try:
                        retained = transaction.retain_successful_build_outputs(
                            repo_root, workspace, configs, names,
                        )
                    except _DOMAIN_ERRORS as exc:
                        log_error(
                            "Build completed but local-output retention failed; worktree retained for "
                            f"debugging: {workspace.path}\n{exc}"
                        )
                        _sys.exit(1)
                    try:
                        transaction.remove_workspace(workspace)
                    except _DOMAIN_ERRORS as exc:
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
                        try:
                            transaction.validate_retained_build_output(
                                configs[name], name, output_id,
                            )
                        except (OSError, RuntimeError, ValueError) as exc:
                            log_warn(
                                f"{name}: retained build output is not eligible for publication: {exc}"
                            )
                        else:
                            log_info(
                                f"{name}: publish these exact retained bytes with: "
                                f"cmru publish {shlex.quote(name)}{config_hint} "
                                f"--build-output {output_id}"
                            )
                        log_info(
                            f"{name}: remove this local record only when no longer needed:\n"
                            f"  cmru cleanup{config_hint} {name} "
                            f"--delete-build-output {output_id} --yes"
                        )
                    _sys.exit(0)
            except CmruError as exc:
                # A deliberate domain refusal/failure (refused before the build
                # changed anything, a missing prerequisite, a failed step): its
                # taxonomy exit code, never "failed after start" by default.
                _exit_for_domain_error(exc)
            except _DOMAIN_ERRORS as exc:
                # The build worktree is only removed after full success above, so a
                # failure here has already retained it; a programming error (any
                # other type) keeps its traceback instead of becoming str(exc).
                log_error(str(exc))
                _sys.exit(1)
        if verb == "build":
            _run_isolated_build_projects(repo_root, configs, names)
        elif build_output_id:
            build_records = {
                name: transaction.validate_retained_build_output(
                    configs[name], name, build_output_id,
                )
                for name in names
            }
            missing_push = [
                name for name in names
                if "push" not in (configs[name].runner_steps or {})
            ]
            if missing_push:
                _usage_error(
                    "build-output publication requires each selected project's declared push step; "
                    f"missing for: {', '.join(missing_push)}"
                )
            require_project_publish_credentials(configs, names)
            groups = transaction.project_git_family_groups(
                repo_root, [configs[name] for name in names]
            )
            for family_root, members in groups.items():
                family_names = [getattr(project, "name") for project in members]
                family_configs = _configs_for_git_family(configs, family_names, family_root)
                for name in family_names:
                    project = family_configs[name]
                    record = build_records[name]
                    manifest = record["manifest"]
                    apply_project_release_env(github_config, env_config, project)
                    run_project_step(
                        project, "push", family_root, family_root / "logs",
                        protected_env={
                            "CMRU_BUILD_OUTPUT_ROOT": str(record["artifact_root"]),
                            "CMRU_BUILD_OUTPUT_ID": build_output_id,
                            "CMRU_BUILD_OUTPUT_PROJECT": name,
                            "CMRU_BUILD_SOURCE_COMMIT": manifest["source_commit"],
                            "CMRU_BUILD_SOURCE_DATE": manifest["source_commit_date"],
                        },
                    )
            log_info(f"cmru publish complete from retained build output {build_output_id}")
            return
        else:
            groups = transaction.project_git_family_groups(
                repo_root, [configs[name] for name in names]
            )
            for family_root, members in groups.items():
                family_names = [getattr(project, "name") for project in members]
                family_configs = _configs_for_git_family(configs, family_names, family_root)
                _run_project_steps(
                    family_root, family_configs, family_names, [step],
                    github_config=github_config, env_config=env_config,
                )
        log_info(f"cmru {verb} complete")



    elif verb == "changelog":
        from cmru.changelog import backfill_release_changelog

        vargs = args
        cfg_path = _resolve_config(vargs.config)
        repo_root, configs, project_order, *_ = load_config(cfg_path)
        selected_names = _select_projects(cfg_path, vargs.target, configs, project_order)
        assignments: dict[str, str] = {}
        for tag in vargs.backfill_tag:
            matches = [
                name for name in selected_names
                if tag.startswith(configs[name].prefix)
            ]
            if len(matches) != 1:
                _usage_error(
                    f"backfill tag {tag!r} must match exactly one selected project prefix; "
                    f"matched {matches or 'none'}"
                )
            if matches[0] in assignments:
                _usage_error(f"more than one backfill tag was supplied for {matches[0]}")
            assignments[matches[0]] = tag
        missing_tags = [name for name in selected_names if name not in assignments]
        if missing_tags:
            _usage_error("missing --backfill-tag for project(s): " + ", ".join(missing_tags))
        for name in selected_names:
            project = configs[name]
            if not project.changelog:
                _usage_error(
                    f"{name}: release history is explicitly disabled; "
                    "remove release.changelog = false before backfilling"
                )
            tag = assignments[name]
            print(f"===== Changelog Project: {name.upper()} " + "=" * 25)
            changed = backfill_release_changelog(
                repo_root, project, tag, dry_run=vargs.dry_run,
            )
            if changed:
                if vargs.dry_run:
                    log_info(f"[DRY RUN] Would backfill {project.changelog} for {tag}")
                else:
                    log_info(
                        f"{name}: backfilled {project.changelog} for {tag}; "
                        "review and commit this post-release migration explicitly"
                    )
            else:
                log_info(f"{name}: {project.changelog} already records {tag}")



    elif verb in ("release", "status"):
        _release_or_status(verb, args, rest, runtime)

    elif verb == "cleanup":
        vargs = args

        cfg_path = _resolve_config(vargs.config)

        (repo_root, configs, project_order, _default_projects, _default_steps,
         _execution_mode, _step_project_order, cleanup, github_config, env_config) = load_config(cfg_path)
        selected_names = _select_projects(cfg_path, vargs.target, configs, project_order)
        plan = CleanupPlan()

        # Every mode is selected by ``is not None`` (never truthiness): an empty
        # value such as ``--remove-assets "$UNSET"`` must not fall through to the
        # destructive policy cleanup. The parser also rejects empty strings.
        if vargs.delete_unmanaged_release_tag is not None:
            if len(selected_names) != 1:
                _usage_error("--delete-unmanaged-release-tag requires exactly one project target")
            selected_name = selected_names[0]
            project = configs.get(selected_name)
            if project is None:
                _usage_error(f"unknown project: {selected_name}")
            prefix = project.prefix or ""
            bare = _bare_prefix(prefix)
            tag = vargs.delete_unmanaged_release_tag
            if not bare or not tag.startswith(f"{bare}-"):
                _usage_error(
                    f"{tag!r} is outside project {selected_name!r}'s release namespace {bare!r}"
                )
            if tag.startswith(prefix) or tag == f"{bare}-latest":
                _usage_error(
                    f"{tag!r} is CMRU-managed; use ordinary cleanup policy, not "
                    "--delete-unmanaged-release-tag"
                )
            project_github = github_for_project(github_config, project)
            if not project_github.token:
                _usage_error("an explicit CMRU publish credential is required to delete a GitHub Release")
            action = lambda dry_run: delete_unmanaged_release_tag(
                project_github.owner, project_github.repo, project_github.token, tag,
                dry_run=dry_run, plan=plan,
            )
        elif vargs.delete_build_output is not None:
            if len(selected_names) != 1:
                _usage_error("--delete-build-output requires exactly one project target")
            selected_name = selected_names[0]
            project = configs.get(selected_name)
            if project is None:
                _usage_error(f"unknown project: {selected_name}")
            def action(dry_run):
                expected_identity = transaction.retained_build_output_identity(
                    repo_root, project, selected_name, vargs.delete_build_output,
                )
                targets = transaction.delete_retained_build_output(
                    repo_root, project, selected_name, vargs.delete_build_output,
                    dry_run=dry_run, expected_identity=expected_identity,
                )
                label = "Would delete" if dry_run else "Deleted"
                for target in targets:
                    log_info(f"{label} retained local build output: {target}")
                plan.add(
                    f"retained local build output {vargs.delete_build_output}",
                    lambda target_project=project, target_name=selected_name,
                    target_id=vargs.delete_build_output,
                    expected_identity=expected_identity:
                        transaction.delete_retained_build_output(
                            repo_root, target_project, target_name, target_id,
                            dry_run=False, expected_identity=expected_identity,
                        ),
                )
        elif vargs.remove_assets is not None:
            # Explicit age-based cleanup mode. It applies the estate-wide [cleanup]
            # policy, so a project target would be silently ignored (CLI-05).
            if vargs.target is not None:
                _usage_error("--remove-assets applies estate-wide [cleanup] policy; omit the target")
            action = lambda dry_run: remove_assets(
                vargs.remove_assets, dry_run, cleanup, github_config, env_config,
                plan=plan,
            )
        elif vargs.policy:
            action = lambda dry_run: run_cleanup_verb(
                repo_root, configs, project_order, cleanup,
                github_config, env_config,
                project_filter=selected_names,
                dry_run=dry_run,
                plan=plan,
            )
        else:
            # Unreachable through the required mode group; fail closed rather
            # than default to the destructive policy cleanup.
            _usage_error(
                "cleanup needs a mode: --policy, --remove-assets AGE, "
                "--delete-unmanaged-release-tag TAG or --delete-build-output ID"
            )

        # Enumerate and show an immutable action set before confirmation. Applying
        # that captured plan cannot widen to assets that appeared while the prompt
        # was open or crossed an age cutoff after the preview.
        action(True)
        if vargs.dry_run:
            return
        if not (vargs.yes or runtime.confirm(
            "Apply the cleanup actions listed above?"
        )):
            return
        plan.apply()

    else:
        write_config_diagnostic(f"Unknown verb '{verb}'. Run 'cmru --help' for usage.")
        _sys.exit(2)


def _resolve_ahead_check_ref(vargs) -> None:
    """Fold the deprecated ``release --ref`` spelling into ``--ahead-check-ref``."""
    legacy = getattr(vargs, "ref", None)
    if legacy is None:
        return
    if vargs.ahead_check_ref is not None:
        _usage_error("--ref is a deprecated alias of --ahead-check-ref; pass only one of them")
    log_warn(
        "release --ref is deprecated; use --ahead-check-ref "
        "(the --ref spelling is removed in the next release)"
    )
    vargs.ahead_check_ref = legacy


def _release_or_status(verb: str, args, rest: List[str], runtime=None) -> None:
    """Shared prefix of ``release`` and ``status``, then route to the right role.

    ``status`` is read-only (:func:`_status`). ``release`` is either the
    launcher (:func:`_release_launcher`, never publishes from the caller's
    checkout) or, inside the isolated transaction worktree, the child
    (:func:`_release_child`).
    """
    vargs = args
    if verb == "release":
        _resolve_ahead_check_ref(vargs)
    _apply_output_options(vargs)
    cfg_path = _resolve_config(vargs.config)
    (repo_root, configs, project_order, *_rest) = load_config(cfg_path)
    github_config, env_config = _rest[-2], _rest[-1]
    git_auth = _git_auth_for_repository(github_config)
    transaction_child = transaction.is_transaction_child(repo_root)
    # CLI-01: only a release owns the aggregate log. ``status`` is read-only and
    # must not truncate cmru.release.log or redirect the caller's stderr.
    if verb == "release" and not transaction_child:
        _configure_native_release_logging(repo_root, append=vargs.log_append)
    apply_release_env(github_config, env_config)
    # Restrict versioning verbs to the orchestrated set so un-migrated projects
    # with their own pipelines (tls-edge, empyrion) are never auto-tagged.
    ordered = _ordered_configs(configs, project_order)
    selection_target = vargs.target
    resume_scope = None
    if verb == "release" and vargs.resume:
        resume_scope = transaction.read_release_scope_for_path(
            Path(vargs.resume).expanduser()
        )
        selection_target = _release_resume_target(
            cfg_path, selection_target, resume_scope, ordered, project_order,
        )
        if vargs.target is None:
            log_info(f"Resuming recorded project scope: {selection_target}")
    selected_names = _select_projects(cfg_path, selection_target, ordered, project_order)

    if vargs.set_version and len(selected_names) != 1:
        # CLI-11: one explicit version cannot meaningfully name several projects.
        _usage_error(
            "--set-version needs exactly one selected project; "
            f"selected {len(selected_names)}: {', '.join(selected_names) or '(none)'}"
        )

    if vargs.minor or vargs.major or vargs.set_version:
        ignored_overrides = [
            name for name in selected_names
            if not configs[name].git_tag
            or _version_strategy(configs[name]).startswith("external:")
        ]
        if ignored_overrides:
            _usage_error(
                "version overrides (--minor, --major, --set-version) do not apply "
                "to external-version or no-tag project(s): "
                + ", ".join(ignored_overrides)
            )

    if verb == "status":
        _status(vargs, runtime, repo_root, configs, selected_names)
        return

    release_scope = selected_names
    if not vargs.dry_run:
        require_project_publish_credentials(configs, release_scope)

    preflight_snapshot_handoff = _ACTIVE_RELEASE_PREFLIGHT_SNAPSHOT
    if preflight_snapshot_handoff is not None:
        if transaction_child or vargs.dry_run or vargs.resume:
            log_error(
                "the internal origin/main snapshot handoff is valid only for a "
                "new family release launcher"
            )
            sys.exit(exit_codes.FAILURE)
        if len(transaction.project_git_family_groups(
            repo_root, [configs[name] for name in release_scope],
        )) != 1:
            log_error(
                "the internal origin/main snapshot handoff cannot span Git families"
            )
            sys.exit(exit_codes.FAILURE)

    if not transaction_child:
        # Never returns: the launcher always ends in ``sys.exit``.
        _release_launcher(
            rest, vargs, cfg_path, repo_root, configs, ordered, project_order,
            selected_names, resume_scope, git_auth, preflight_snapshot_handoff,
        )
    else:
        _release_child(
            vargs, repo_root, configs, ordered, project_order, selected_names,
            github_config, env_config, git_auth,
        )


def _status(vargs, runtime, repo_root: Path, configs, selected_names: List[str]) -> None:
    """Read-only version/plan preview for each selected Git family.

    ``--json`` emits ONE list holding a record per SELECTED project across every
    family, each with a ``changed`` field (see :func:`cmru.version.status_records`).
    The text table lists only changed projects.
    """
    from cmru.version import status_cmd, status_records

    groups = transaction.project_git_family_groups(
        repo_root, [configs[name] for name in selected_names]
    )
    records: list = []
    for family_root, members in groups.items():
        family_names = [getattr(project, "name") for project in members]
        family_configs = _configs_for_git_family(configs, family_names, family_root)
        family = {name: family_configs[name] for name in family_names}
        options = dict(
            minor=vargs.minor, major=vargs.major, set_version=vargs.set_version,
            ref=vargs.ref or "HEAD",
        )
        if vargs.json:
            records.extend(status_records(family_root, family, **options))
        else:
            status_cmd(family_root, family, **options)
    if vargs.json:
        runtime.output.primary(records)


def _exit_for_domain_error(exc: CmruError) -> NoReturn:
    """Process-boundary rendering of a domain error for the legacy exit paths.

    The library renders a ``CmruError`` itself where it propagates; the release
    and build launchers instead catch everything in ``_DOMAIN_ERRORS`` to print
    a plain line and ``sys.exit``, so they route the family here to keep its
    taxonomy exit code (and hint) instead of flattening it to exit 1.
    """
    log_error(str(exc))
    if exc.hint:
        log_info(exc.hint)
    sys.exit(exc.exit_code)


def _release_launcher(
    rest: List[str], vargs, cfg_path: Path, repo_root: Path, configs, ordered,
    project_order, selected_names: List[str], resume_scope, git_auth,
    preflight_snapshot_handoff,
) -> None:
    """The caller-side half of ``release``: set up and drive the transaction.

    Always exits the process (``sys.exit``); it never returns normally.
    """
    release_scope = selected_names
    origin_main_snapshots = None
    if not vargs.dry_run and not vargs.resume:
        try:
            origin_main_snapshots = _preflight_multi_family_release_tag_support(
                repo_root, configs, release_scope,
                config_path=cfg_path, git_auth=git_auth,
            )
        except CmruError as exc:
            _exit_for_domain_error(exc)
        except _DOMAIN_ERRORS as exc:
            log_error(str(exc))
            sys.exit(1)

    dispatched = _dispatch_independent_git_families(
        "release",
        rest,
        cfg_path,
        repo_root,
        configs,
        release_scope,
        original_target=vargs.target,
        origin_main_snapshots=origin_main_snapshots,
        forward_from=vargs,
    )
    if dispatched is not None:
        sys.exit(dispatched)

    # The normal command is a launcher, never a publisher from the caller's
    # checkout: origin/main is the only release source, built in an isolated
    # worktree. Local-only *commits* are a fail-closed condition (they are
    # likely intended release inputs — see assert_local_main_not_ahead below).
    # Uncommitted work is fail-closed too, but only when it touches a released
    # project's own path (--allow-uncommitted overrides): otherwise it would be
    # silently left out with no warning, since the build never looks at it.
    try:
        candidate_project_config_paths: list[Path] | None = None
        transaction_root = transaction.source_git_root_for_projects(
            repo_root, [configs[name] for name in release_scope]
        )
        preflighted_base = _consume_release_snapshot_handoff(
            transaction_root, preflight_snapshot_handoff,
        )
        child_args = _child_release_args(
            rest, cfg_path, repo_root, source_git_root=transaction_root,
            target_override=",".join(release_scope), original_target=vargs.target,
            forward_from=vargs,
        )
        with transaction.release_lock(transaction_root):
            if preflighted_base is not None:
                verified_base = transaction.fetch_origin_main(
                    transaction_root, git_auth=git_auth,
                )
                if verified_base != preflighted_base:
                    raise RefusedBeforeChange(
                        "origin/main changed after the multi-family release preflight "
                        f"(checked {preflighted_base}, now {verified_base}); "
                        "rerun the release so every family is checked against "
                        "one consistent snapshot"
                    )
            # Not --dry-run: a preview has no publish step to protect, and "I have
            # local edits I haven't committed yet" is exactly when you'd run one.
            if not vargs.dry_run and not vargs.allow_uncommitted:
                # release actually iterates `ordered` (== project_order), which is
                # independently configurable from the default target — check
                # what will really run, not a possibly different default.
                dirty = _uncommitted_release_paths(transaction_root, ordered, release_scope)
                if dirty:
                    for name, files in dirty.items():
                        log_error(f"{name}: uncommitted changes — {', '.join(files)}")
                    from cli_extended import CliFailure

                    raise CliFailure(
                        "Uncommitted local changes touch the project path(s) above. "
                        "origin/main is the only release source, so this run would "
                        "silently leave them out. Commit (and push) them first, or "
                        "pass --allow-uncommitted to release without them.",
                        exit_code=exit_codes.REFUSED,
                    )

            if getattr(vargs, "resume", None):
                workspace = transaction.resume_workspace(
                    transaction_root, Path(vargs.resume), git_auth=git_auth,
                )
                current_scope = transaction.read_release_scope_for_path(workspace.path)
                if current_scope != resume_scope:
                    raise RefusedBeforeChange(
                        "retained release scope changed while acquiring its lock; "
                        "inspect the candidate and retry"
                    )
                transaction.assert_resume_workspace_committed(workspace.path)
                candidate_config_paths = _project_config_paths_in_candidate(
                    transaction_root, workspace.path, cfg_path, configs,
                    release_scope,
                )
                candidate_release_policies = {
                    name: _project_release_policy_in_candidate(
                        workspace.path, name, candidate_config_paths[name],
                    )
                    for name in release_scope
                }
                _assert_resume_candidate_is_safe_to_replay(
                    transaction_root, workspace, release_scope, configs,
                    git_auth=git_auth,
                    release_policies=candidate_release_policies,
                )
                candidate_project_config_paths = [
                    candidate_config_paths[name] for name in release_scope
                    if configs[name].project_root is not None
                ]
                if not vargs.dry_run:
                    if any(policy[1] for policy in candidate_release_policies.values()):
                        _require_local_tag_inspection_support(transaction_root)
            else:
                base = preflighted_base or transaction.fetch_origin_main(
                    transaction_root, git_auth=git_auth,
                )
                snapshot_config_paths = _project_config_paths_at_snapshot(
                    transaction_root, base, cfg_path, configs, release_scope,
                )
                candidate_project_config_paths = [
                    snapshot_config_paths[name] for name in release_scope
                    if configs[name].project_root is not None
                ]
                if not vargs.dry_run:
                    if any(
                        _project_git_tag_policy_at_snapshot(
                            transaction_root, base, configs[name],
                            project_config_rel=snapshot_config_paths[name],
                        )
                        for name in release_scope
                    ):
                        _require_local_tag_inspection_support(transaction_root)
                initial_tag_refs = None
                if not vargs.dry_run:
                    initial_tag_refs = _read_origin_tag_refs(
                        transaction_root,
                        git_auth=git_auth,
                        context="capture origin release tags before the transaction",
                    )
                behind = transaction.assert_local_main_not_ahead(
                    transaction_root, ref=vargs.ahead_check_ref or "main",
                )
                if behind:
                    log_warn(
                        f"Local main is {behind} commit(s) behind origin/main; "
                        f"release uses fetched origin/main {base[:12]}."
                    )
                workspace = transaction.create_workspace(
                    repo_root, base=base, scope=','.join(release_scope),
                    source_git_root=transaction_root,
                )
                if initial_tag_refs is not None:
                    transaction.write_release_tag_snapshot(
                        transaction_root, workspace, initial_tag_refs,
                    )
            transaction.copy_secret_overlays(
                repo_root,
                workspace,
                [Path(configs[name].project_root) / PROJECT_CONFIG_FILENAME for name in release_scope
                 if configs[name].project_root is not None],
                candidate_config_paths=candidate_project_config_paths,
            )
            log_info(
                f"Release transaction {workspace.branch}: "
                f"snapshot {workspace.base[:12]} at {workspace.path}"
            )
            transaction.clear_plan_refused(transaction_root, workspace)
            rc = transaction.run_child(
                workspace, child_args, project_names=release_scope,
            )
            if rc == 0:
                retained: list[Path] = []
                discarded = set(vargs.discard or ())
                retain_logs = "logs" not in discarded
                retain_artifacts = "artifacts" not in discarded
                retain_evidence = "evidence" not in discarded
                has_declared_evidence = any(
                    bool(getattr(configs.get(name), "evidence_paths", ()) or ())
                    for name in configs
                )
                if retain_logs or retain_artifacts or (retain_evidence and has_declared_evidence):
                    release_results = transaction.read_release_results(transaction_root, workspace)
                    retained = transaction.retain_success_outputs(
                        repo_root,
                        workspace,
                        configs,
                        release_results,
                        retain_logs=retain_logs,
                        retain_artifacts=retain_artifacts,
                        retain_evidence=retain_evidence,
                    )
                for path in retained:
                    log_info(f"Retained release output: {path}")
                transaction.remove_backup_branch(workspace, git_auth=git_auth)
                transaction.remove_workspace(workspace)
                transaction.forget_release_scope(transaction_root, workspace)
                if _sync_local_main_and_report(transaction_root, git_auth=git_auth):
                    log_info("Local main synced with origin/main.")
                log_info("Release transaction complete; isolated worktree removed.")
            elif transaction.plan_was_refused(transaction_root, workspace):
                if vargs.resume:
                    log_error(
                        "Release plan refused before any project started; the "
                        f"existing candidate {workspace.path} on branch "
                        f"{workspace.branch} was retained for inspection. "
                        "Resolve the refusal before retrying or abandoning it."
                    )
                else:
                    # A new workspace has no prior work or remote backup to
                    # preserve when its first release-plan check refuses.
                    transaction.remove_workspace(workspace)
                    transaction.forget_release_scope(transaction_root, workspace)
                    _sync_local_main_and_report(transaction_root, git_auth=git_auth)
                    log_error(
                        "Release plan refused before any project started; no changes "
                        "were made (see the error above). Worktree discarded."
                    )
            else:
                # Promotion is now the final step of every project's
                # candidate cycle. A failed project therefore leaves its
                # source commit on the retained candidate branch while
                # origin/main contains only earlier, fully completed
                # projects. Never manufacture a revert commit for a
                # candidate whose public artifact may already exist.
                log_error(
                    "Release candidate was not promoted; origin/main was left "
                    "at the last fully completed project. The durable candidate "
                    f"branch {workspace.branch} was retained for inspection."
                )
                # REL-06: say where the candidate is BEFORE the best-effort caller
                # sync, so the path is never lost behind a sync problem.
                log_error(
                    f"Release transaction failed; retained {workspace.path} "
                    f"on branch {workspace.branch} for inspection. Follow the recovery "
                    "steps printed above: `cmru release --resume` works only when no "
                    "release tag was left behind (a failed build rolls its tag back "
                    "automatically); once publishing has started the tag is kept and "
                    "the candidate must be abandoned and re-released."
                )
                _sync_local_main_and_report(transaction_root, git_auth=git_auth)
            sys.exit(rc)
    except CmruError as exc:
        _exit_for_domain_error(exc)
    except _DOMAIN_ERRORS as exc:
        # Retention of the candidate worktree already happened above (a failed
        # child leaves it in place); any other exception type is a bug and
        # propagates with its traceback.
        log_error(str(exc))
        sys.exit(1)


def _release_child(
    vargs, repo_root: Path, configs, ordered, project_order,
    selected_names: List[str], github_config, env_config, git_auth,
) -> None:
    """The in-worktree half of ``release``: detect, tag, push, build, publish."""
    from cmru.version import ReleasePlanRefused, release_cmd, detect_changed_projects

    selected_ordered = {name: ordered[name] for name in selected_names}

    # Scope to what this run will actually touch *before* computing the plan
    # (not by filtering the result afterward): an unrelated orchestrated
    # project's degenerate tag state must not abort a target-scoped run
    # that never touches it.
    release_scope = selected_names
    scoped_for_plan = {name: ordered[name] for name in release_scope}
    if vargs.dry_run:
        # External version strategies read a fact emitted by prepare. Run
        # only that declared input-discovery step in this disposable
        # transaction before the single release-plan comparison below.
        _prepare_dry_run_external_versions(
            repo_root, configs, release_scope,
            github_config=github_config, env_config=env_config,
        )
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
            git_auth=git_auth,
        )
    except ReleasePlanRefused as exc:
        log_error(str(exc))
        transaction.mark_plan_refused(repo_root, _transaction_workspace_from_env(repo_root))
        sys.exit(exit_codes.REFUSED)
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

    # S15: declared tool dependencies for external/copy consumers
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
        sys.exit(exit_codes.REFUSED)

    if vargs.dry_run:
        # Preview only: show what would be tagged for every changed project, no
        # commits/gates/promotion/tags — nothing here has side effects.
        release_cmd(
            repo_root, selected_ordered,
            minor=vargs.minor, major=vargs.major, set_version=vargs.set_version,
            dry_run=True,
        )
        log_info("[DRY RUN] No tags pushed, nothing built/published.")
        return

    if not release_names:
        log_info("Nothing to release (no changed projects).")
        return

    # Build all projects after another (S-REL): each project's own
    # prepare → gate → tag → build → publish → promote cycle runs to completion
    # before the next project starts. This is what lets a later project (e.g. an
    # OCI image) resolve an earlier project's (e.g. a wheel) brand-new release
    # within this SAME `cmru release` run, instead of always trailing one run
    # behind. If project N fails, its exact candidate remains on the retained
    # transaction branch and already-published projects before it are left alone.
    workspace = _transaction_workspace_from_env(repo_root)
    transaction.write_release_scope(repo_root, workspace, release_names)
    transaction.push_backup_branch(workspace, git_auth=git_auth)
    log_info(f"Pushed release candidate {workspace.branch} to origin (durability).")

    released = _release_projects_sequentially(
        repo_root, configs, workspace, release_names,
        github_config=github_config, env_config=env_config,
        git_auth=git_auth,
        no_build=vargs.no_build, minor=vargs.minor, major=vargs.major,
        set_version=vargs.set_version,
    )

    if released:
        log_info(f"Released: {', '.join(released)}")
    elif vargs.no_build:
        log_info("Tagged only (--no-build); nothing built or published.")
    else:
        log_info("Nothing built or published (see per-project log above for why).")


def _non_empty(value: str) -> str:
    """argparse ``type`` for mode values: an empty string (``--remove-assets "$UNSET"``) is a usage error."""
    import argparse

    if not value.strip():
        raise argparse.ArgumentTypeError("the value must not be empty")
    return value


def _usage_error(message: str) -> None:
    from cli_extended import CliFailure

    raise CliFailure(message, exit_code=2, show_help=True)


def _abandon(args, runtime) -> int:
    """Serialize abandonment with releases, then inspect and discard candidates."""
    from cli_extended import CliFailure

    try:
        repo_root = _current_git_root()
    except _DOMAIN_ERRORS as exc:
        raise CliFailure(f"cannot inspect CMRU release worktrees: {exc}", exit_code=2) from exc
    try:
        # Keep the same lock from the initial state inspection through the
        # confirmation and cleanup. A local release cannot resume this candidate
        # while the operator is deciding whether to abandon it.
        with transaction.release_lock(repo_root):
            return _abandon_locked(args, runtime, repo_root)
    except CliFailure:
        # ``ReleaseLockHeld`` is a ``CmruError`` (hence a ``CliFailure``) with exit 4.
        raise


def _release_tag_prefixes_for_scope(
    scope: Sequence[str], configs: Mapping[str, "ProjectConfig"],
) -> tuple[str, ...]:
    prefixes = {
        getattr(configs[name], "prefix", None) or f"{name}-v"
        for name in scope
        if name in configs and getattr(configs[name], "git_tag", False)
    }
    return tuple(sorted(prefix for prefix in prefixes if isinstance(prefix, str) and prefix))


def _tag_ref_name(tag_ref: str) -> str:
    name = tag_ref.removeprefix("refs/tags/")
    return name[:-3] if name.endswith("^{}") else name


def _release_tag_ref_records(
    tag_refs: Mapping[str, str], prefixes: Sequence[str],
) -> dict[str, str]:
    return {
        ref: oid for ref, oid in tag_refs.items()
        if any(_tag_ref_name(ref).startswith(prefix) for prefix in prefixes)
    }


def _tag_records_by_name(tag_refs: Mapping[str, str]) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for ref, oid in tag_refs.items():
        name = _tag_ref_name(ref)
        records.setdefault(name, {})[ref] = oid
    return records


def _abandon_build_worktree(args, runtime, repo_root: Path, workspace) -> int:
    """Discard one retained failed BUILD worktree (formerly ``cleanup --discard-build-worktree``)."""
    path = Path(workspace.path)
    inspected = transaction.discard_build_workspace(repo_root, path, dry_run=True)
    log_info(f"Would discard retained build worktree: {inspected.path} ({inspected.branch})")
    if args.dry_run:
        log_info("[DRY RUN] No branch, worktree, metadata, or remote state was changed.")
        return 0
    if not (getattr(args, "yes", False) or runtime.confirm("Discard the retained build worktree listed above?")):
        return 0
    transaction.discard_build_workspace(
        repo_root, path, dry_run=False, expected_workspace=inspected,
    )
    log_info(f"Discarded retained build worktree: {inspected.path} ({inspected.branch})")
    return 0


def _abandon_locked(args, runtime, repo_root: Path) -> int:
    """Inspect and, after exact confirmation, discard candidates under release lock."""
    from cli_extended import CliFailure

    try:
        all_workspaces = transaction.list_cmru_workspaces(repo_root)
    except _DOMAIN_ERRORS as exc:
        raise CliFailure(f"cannot inspect CMRU release worktrees: {exc}", exit_code=2) from exc

    releases = [item for item in all_workspaces if transaction.workspace_purpose(item.branch) == "release"]
    if args.branch is None:
        selected = releases
    else:
        requested_path = Path(args.branch).expanduser()
        selected = [
            item for item in all_workspaces
            if item.branch == args.branch
            or (requested_path.is_absolute() and Path(item.path) == requested_path)
        ]
        if not selected:
            raise CliFailure(
                f"no exact managed CMRU build or release branch or worktree path "
                f"named {args.branch!r}"
                + (
                    "; pass the absolute path shown by `cmru worktrees`"
                    if not requested_path.is_absolute() and "/" in args.branch else ""
                ), exit_code=2,
            )
        if transaction.workspace_purpose(selected[0].branch) == "build":
            return _abandon_build_worktree(args, runtime, repo_root, selected[0])

    if not selected:
        log_info("No retained release transactions to abandon.")
        return 0

    try:
        (
            _loaded_root, scoped_configs, _loaded_order, _default_projects,
            _default_steps, _execution_mode, _step_project_order, _cleanup,
            github_config, _env_config,
        ) = load_config(_resolve_config(getattr(args, "config", None)))
        git_auth = _git_auth_for_repository(github_config)
    except _DOMAIN_ERRORS as exc:
        raise CliFailure(
            f"cannot load project release policy to inspect retained transactions: {exc}",
            exit_code=2,
        ) from exc

    blockers: list[tuple[transaction.ReleaseWorkspace, str, list[str], list[str]]] = []
    plans: list[tuple[
        transaction.ReleaseWorkspace,
        list[str],
        list[str],
        str | None,
        dict[str, str],
        dict[str, str],
        tuple[str, ...],
        dict[str, str],
        dict[str, str],
    ]] = []
    for workspace in selected:
        scope: list[str] = []
        remote_assets: list[str] = []
        try:
            if workspace.is_prunable:
                raise RuntimeError("Git marks this worktree registration prunable")
            scope = transaction.read_release_scope(repo_root, workspace)
            if not scope or any(not isinstance(name, str) or not name for name in scope) or len(scope) != len(set(scope)):
                raise RuntimeError("release scope metadata is missing, malformed, or ambiguous")
            results = transaction.read_release_results(repo_root, workspace)
            if results:
                raise RuntimeError(
                    "release result metadata records a published project: "
                    + ", ".join(sorted(results))
                )
            progress = transaction.read_release_progress(repo_root, workspace)
            if progress is None:
                raise RuntimeError("release progress metadata is missing")
            if not transaction.COMMIT_ID_RE.fullmatch(progress):
                raise RuntimeError("release progress metadata is malformed")
            base_commit = getattr(workspace.context, "base_commit", None)
            if not base_commit or not transaction.COMMIT_ID_RE.fullmatch(str(base_commit)):
                raise RuntimeError("original snapshot commit is unavailable; promotion state is ambiguous")
            remote = run_remote_git(
                repo_root, "ls-remote", "--heads", "origin",
                "refs/heads/" + workspace.branch, "refs/heads/main",
                auth=git_auth, capture_output=True, text=True, check=False,
            )
            if remote.returncode != 0:
                raise RuntimeError("could not determine origin branch state: " + (remote.stderr.strip() or "git ls-remote failed"))
            requested_refs = {
                "refs/heads/" + workspace.branch,
                "refs/heads/main",
            }
            remote_refs = transaction.parse_ls_remote_refs(
                remote.stdout, namespace="refs/heads/", description="origin branch lookup",
            )
            unexpected_refs = set(remote_refs) - requested_refs
            if unexpected_refs:
                raise RuntimeError(
                    "origin branch lookup returned unexpected ref(s): "
                    + ", ".join(sorted(unexpected_refs))
                )
            remote_candidate = remote_refs.get("refs/heads/" + workspace.branch)
            remote_main = remote_refs.get("refs/heads/main")
            if remote_main is None:
                raise RuntimeError(
                    "origin/main ref is missing; cannot determine whether release progress was promoted"
                )
            if remote_candidate is not None:
                remote_assets.append("origin/refs/heads/" + workspace.branch)
            pushed = transaction.backup_was_pushed(repo_root, workspace)
            removed = transaction.backup_was_removed(repo_root, workspace)
            expected_remote_candidate = pushed and not removed
            if expected_remote_candidate != (remote_candidate is not None):
                raise RuntimeError("backup-branch marker and origin ref disagree")
            if remote_candidate is not None:
                candidate = run_local_git(
                    repo_root, "merge-base", "--is-ancestor", remote_candidate,
                    "refs/heads/" + workspace.branch,
                    capture_output=True, text=True, check=False,
                )
                if candidate.returncode != 0:
                    raise RuntimeError("origin candidate ref is not a known ancestor of the retained worktree")
            promoted = run_local_git(
                repo_root, "merge-base", "--is-ancestor", progress, remote_main,
                capture_output=True, text=True, check=False,
            )
            if progress != base_commit and promoted.returncode == 0:
                remote_assets.append("origin/refs/heads/main")
                raise RuntimeError("origin/main contains the recorded release progress; transaction was promoted")
            if promoted.returncode not in (0, 1):
                raise RuntimeError("could not determine whether release progress reached origin/main")
            # Only this transaction's projects can make a tag relevant to its
            # release or abandonment. A new tag in a different project's
            # namespace must not turn an ordinary retained candidate into a
            # false publication refusal.
            tag_ref_records = _read_origin_tag_refs(
                repo_root, git_auth=git_auth, context="inspect origin release tags",
            )
            tag_prefixes = _release_tag_prefixes_for_scope(scope, scoped_configs)
            if not tag_prefixes:
                raise RuntimeError("release scope has no usable Git tag prefix")
            scoped_remote_tags = _release_tag_ref_records(tag_ref_records, tag_prefixes)
            initial_tag_refs = transaction.read_release_tag_snapshot(repo_root, workspace)
            new_tags: list[str] = []
            if initial_tag_refs is None:
                # Legacy candidates have no baseline. Keep their original
                # conservative ancestry test, but restrict it to this scope.
                for tag_name, tag_record in _tag_records_by_name(scoped_remote_tags).items():
                    tag_sha = tag_record.get(f"refs/tags/{tag_name}^{{}}") or tag_record.get(
                        f"refs/tags/{tag_name}"
                    )
                    tag_on_candidate = run_local_git(
                        repo_root, "merge-base", "--is-ancestor", tag_sha,
                        "refs/heads/" + workspace.branch,
                        capture_output=True, text=True, check=False,
                    )
                    if tag_on_candidate.returncode == 0:
                        # REL-03: every earlier release tag is reachable from the
                        # candidate. Only a tag that is NOT a strict ancestor of the
                        # transaction base can have been created by this transaction;
                        # a tag exactly at the base stays suspicious.
                        if tag_sha != base_commit:
                            below_base = run_local_git(
                                repo_root, "merge-base", "--is-ancestor", tag_sha,
                                str(base_commit),
                                capture_output=True, text=True, check=False,
                            )
                            if below_base.returncode == 0:
                                continue
                            if below_base.returncode != 1:
                                raise RuntimeError(f"could not inspect remote tag {tag_name}")
                        new_tags.append(tag_name)
                    elif tag_on_candidate.returncode != 1:
                        raise RuntimeError(f"could not inspect remote tag {tag_name}")
            else:
                initial_scoped_tags = _release_tag_ref_records(initial_tag_refs, tag_prefixes)
                current_records = _tag_records_by_name(scoped_remote_tags)
                initial_records = _tag_records_by_name(initial_scoped_tags)
                new_tags = sorted(
                    tag_name
                    for tag_name in set(current_records) | set(initial_records)
                    if current_records.get(tag_name, {}) != initial_records.get(tag_name, {})
                )
            if new_tags:
                remote_assets.extend(
                    "origin/refs/tags/" + tag
                    for tag in new_tags
                )
                if initial_tag_refs is None:
                    raise RuntimeError(
                        "the transaction has no pre-attempt origin tag snapshot, so selected-scope "
                        "release tag(s) cannot be classified safely: "
                        + ", ".join(sorted(new_tags))
                    )
                raise RuntimeError(
                    "origin contains selected-scope release tag refs that changed during this transaction: "
                    + ", ".join(sorted(new_tags))
                )
            tag_attempts = transaction.read_release_tag_attempts(repo_root, workspace)
            if tag_attempts is not None:
                outside_scope = [
                    ref for ref in tag_attempts
                    if not any(_tag_ref_name(ref).startswith(prefix) for prefix in tag_prefixes)
                ]
                if outside_scope:
                    raise RuntimeError(
                        "release tag attempt metadata names refs outside the recorded project scope: "
                        + ", ".join(sorted(outside_scope))
                    )
            current_local_tags = transaction.list_local_tag_refs(repo_root)
            scoped_local_tags = _release_tag_ref_records(current_local_tags, tag_prefixes)
            current_origin_names = {
                _tag_ref_name(ref) for ref in tag_ref_records
            }
            local_tags_to_remove: dict[str, str] = {}
            unclassified_local_tags: list[str] = []
            for ref, oid in scoped_local_tags.items():
                tag_name = _tag_ref_name(ref)
                if tag_name.endswith("-latest") or tag_name in current_origin_names:
                    continue
                if tag_attempts is None or ref not in tag_attempts:
                    unclassified_local_tags.append(tag_name)
                    continue
                if tag_attempts[ref] != oid:
                    raise RuntimeError(
                        f"local release tag {tag_name} changed after its CMRU push attempt"
                    )
                # The selected-scope snapshot comparison above already refused
                # any tag ref that existed before the attempt but disappeared.
                if initial_tag_refs is None:
                    raise RuntimeError(
                        f"local release tag {tag_name} has no origin tag baseline; "
                        "inspect it before abandoning the candidate"
                    )
                local_tags_to_remove[ref] = oid
            if unclassified_local_tags:
                remote_assets.extend(
                    "local/refs/tags/" + tag_name
                    for tag_name in sorted(set(unclassified_local_tags))
                )
                raise RuntimeError(
                    "local-only release tag(s) in the selected project scope have no recorded "
                    "CMRU push attempt: " + ", ".join(sorted(set(unclassified_local_tags)))
                    + ". Inspect each tag; if it is an unneeded local ref and is absent from origin, "
                    "remove it with `git tag -d TAG` before abandoning this candidate."
                )
            untagged = [name for name in scope if name not in scoped_configs or not scoped_configs[name].git_tag]
            if untagged:
                remote_assets.extend(f"external publication state unknown for {name}" for name in untagged)
                raise RuntimeError(
                    "transaction scope contains project(s) that can publish without a Git tag, "
                    "and local result metadata cannot prove whether publication completed: "
                    + ", ".join(untagged)
                )
            plans.append((
                workspace, scope, remote_assets, remote_candidate, remote_refs,
                scoped_remote_tags, tag_prefixes, scoped_local_tags, local_tags_to_remove,
            ))
        except _DOMAIN_ERRORS as exc:
            # Nothing has been mutated at this point, so a programming error
            # (any other type) may crash; only domain failures are classified
            # as a "Withheld" blocker.
            try:
                if not scope:
                    recorded_scope = transaction.read_release_scope(repo_root, workspace)
                    if recorded_scope:
                        scope = recorded_scope
            except _DOMAIN_ERRORS:
                pass  # best-effort decoration of the blocker row
            try:
                if transaction.backup_was_pushed(repo_root, workspace):
                    branch_ref = "origin/refs/heads/" + workspace.branch
                    if branch_ref not in remote_assets:
                        remote_assets.append(branch_ref)
            except _DOMAIN_ERRORS:
                pass  # best-effort decoration of the blocker row
            try:
                recorded_results = transaction.read_release_results(repo_root, workspace)
                remote_assets.extend(
                    f"published release {name}:{tag}"
                    for name, tag in sorted(recorded_results.items())
                )
            except _DOMAIN_ERRORS:
                pass  # best-effort decoration of the blocker row
            blockers.append((workspace, str(exc), scope, remote_assets))

    for (
        workspace, scope, assets, _candidate_oid, _branch_refs, _remote_tags,
        _tag_prefixes, _local_tags, local_tags_to_remove,
    ) in plans:
        print(
            f"Candidate: {workspace.branch}\n"
            f"  worktree: {workspace.path}\n"
            f"  transaction scope: {', '.join(scope)}\n"
            f"  remote refs/assets removed: {', '.join(assets) if assets else 'none'}\n"
            f"  local release tags removed: "
            f"{', '.join(ref.removeprefix('refs/tags/') for ref in sorted(local_tags_to_remove)) if local_tags_to_remove else 'none'}\n"
            "  after abandonment: candidate worktree (including its logs/artifacts), branch, "
            "local attempt tags, and transaction sidecars are removed; origin/main is unchanged"
        )
    for workspace, reason, scope, assets in blockers:
        print(
            f"Withheld: {workspace.branch}\n"
            f"  worktree: {workspace.path}\n"
            f"  transaction scope: {', '.join(scope) if scope else 'unavailable'}\n"
            f"  refs/assets involved: {', '.join(assets) if assets else 'unknown'}\n"
            f"  reason: {reason}"
        )
    if args.dry_run:
        log_info("[DRY RUN] No branch, worktree, metadata, or remote state was changed.")
        return exit_codes.REFUSED if blockers else exit_codes.OK
    if blockers:
        raise CliFailure(
            "one or more selected release transactions are ambiguous or published; "
            "nothing was abandoned",
            exit_code=exit_codes.REFUSED,
        )
    prompt = (
        "Abandon exactly the listed retained release transaction(s), deleting their "
        "CMRU backup refs, listed local release tags, worktrees, and local scope/progress sidecars?"
    )
    if not getattr(args, "yes", False) and not runtime.confirm(prompt):
        return 0

    # Confirmation may take arbitrarily long. Recheck every captured remote
    # fact before the first mutation so one stale candidate cannot make a
    # multi-candidate confirmation partially apply.
    for (
        workspace, _scope, _assets, _candidate_oid, expected_refs, expected_tags,
        tag_prefixes, expected_local_tags, _local_tags_to_remove,
    ) in plans:
        try:
            remote = run_remote_git(
                repo_root, "ls-remote", "--heads", "origin",
                "refs/heads/" + workspace.branch, "refs/heads/main",
                auth=git_auth, capture_output=True, text=True, check=False,
            )
            if remote.returncode != 0:
                raise RuntimeError("could not recheck origin branch state")
            current_refs = transaction.parse_ls_remote_refs(
                remote.stdout, namespace="refs/heads/", description="origin branch recheck",
            )
            if current_refs != expected_refs:
                raise RuntimeError("origin branch refs changed after confirmation")
            current_remote_tags = _release_tag_ref_records(
                _read_origin_tag_refs(
                    repo_root, git_auth=git_auth, context="recheck origin release tags",
                ),
                tag_prefixes,
            )
            if current_remote_tags != expected_tags:
                raise RuntimeError("origin release tags changed after confirmation")
            if _release_tag_ref_records(
                transaction.list_local_tag_refs(repo_root), tag_prefixes,
            ) != expected_local_tags:
                raise RuntimeError("local release tags changed after confirmation")
        except _DOMAIN_ERRORS as exc:
            raise CliFailure(
                "origin state changed or could not be rechecked after confirmation; "
                f"nothing was abandoned: {exc}",
                exit_code=exit_codes.REFUSED,
            ) from exc

    for (
        workspace, _scope, _assets, candidate_oid, branch_refs, tag_refs,
        tag_prefixes, local_tag_refs, local_tags_to_remove,
    ) in plans:
        transaction.abandon_workspace(
            repo_root, workspace, git_auth=git_auth,
            expected_remote_candidate_oid=candidate_oid,
            expected_remote_tag_refs=tag_refs,
            release_tag_prefixes=tag_prefixes,
            expected_local_tag_refs=local_tag_refs,
            local_tags_to_remove=local_tags_to_remove,
        )
        log_info(f"Abandoned release transaction {workspace.branch}")
    return 0


def _build_cli():
    from cli_extended import ArgumentSpec, OptionSpec, Requires, VerbGroup, VerbSpec
    from cmru.cli_support import cmru_registry, target_argument

    registry = cmru_registry(
        "cmru",
        "Independent releases for products that share one repository.",
        getting_started=("cmru status", "cmru release", "cmru cleanup --policy --dry-run"),
    )
    direct = lambda: (lambda args, runtime: _dispatch(args, runtime))
    target = target_argument()
    config_opt = OptionSpec(("--config",), f"path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}", metavar="PATH", parser_kwargs={"default": None})
    detail_opts = (
        OptionSpec(("--show-run-details",), "stream full project subprocess output", parser_kwargs={"action": "store_true", "default": False}),
        OptionSpec(("--log-append",), "append a divider and retain existing stable per-step logs", parser_kwargs={"action": "store_true", "default": False}),
    )
    delegated = []
    from cmru.scaffold import init_cli
    from cmru.versions import versions_cli
    from cmru.handlers import handlers_cli
    from cmru.tester_gate import tester_gate_cli
    from cmru.resolve import resolve_cli
    from cmru.getpy import getpy_cli
    from cmru.standards import standards_cli
    from cmru.tool_deps import tool_deps_cli
    delegated.extend((
        VerbSpec("init", description="Adopt a project with the guided scaffolding wizard.", group=VerbGroup.MODIFICATION.value, mutating=True, delegate=init_cli(), include_json=False, include_progress=False),
        VerbSpec("versions", description="Derive, resolve, and check declared dependency versions.", group=VerbGroup.MODIFICATION.value, mutating=True, delegate=versions_cli(), include_json=False, include_progress=False),
        VerbSpec("handler", description="Run a concrete project-step handler.", group=VerbGroup.MODIFICATION.value, mutating=True, delegate=handlers_cli(), include_json=False, include_progress=False),
        VerbSpec("tester-gate", description="Run one command inside tester-unified.", group=VerbGroup.MODIFICATION.value, mutating=True, delegate=tester_gate_cli(), include_json=False, include_progress=False),
        VerbSpec("resolve", description="Resolve a project's latest published artifact.", group=VerbGroup.EXPLORATION.value, delegate=resolve_cli(), include_json=False, include_progress=False),
        VerbSpec("get-py", description="Emit the standalone Python installer.", group=VerbGroup.MODIFICATION.value, mutating=True, delegate=getpy_cli(), include_json=False, include_progress=False),
        VerbSpec("standards", description="Check or update CMRU framework markers.", group=VerbGroup.MIXED.value, mutating=True, delegate=standards_cli(), include_json=False, include_progress=False),
        VerbSpec("tool-deps", description="Verify declared tool dependency integrity, authenticity, and freshness.", group=VerbGroup.MIXED.value, mutating=True, delegate=tool_deps_cli(), include_json=False, include_progress=False),
    ))
    # Every conditionally or fully mutating root verb takes the library's
    # ``--dry-run`` (VerbSpec(dry_run=True)); no verb declares its own.
    registry.register(VerbSpec(
        "run",
        description=(
            "Run declared project steps in the CALLER'S checkout (not an isolated "
            "worktree); configured default_steps when no --step is given."
        ),
        group=VerbGroup.MODIFICATION.value, arguments=(target,),
        options=(
            config_opt,
            OptionSpec(("--step",), "declared step to run; repeat for several, in the order given (default: the configured default_steps)", metavar="NAME", parser_kwargs={"action": "append", "default": None}),
            *detail_opts,
        ),
        mutating=True, dry_run=True, include_confirmation=False, include_json=False,
        include_progress=False, handler=direct(),
    ))
    registry.register(VerbSpec("worktrees", description="Discover retained CMRU build and release worktrees (read-only).", group=VerbGroup.EXPLORATION.value, include_json=True, include_progress=False, handler=direct()))
    registry.register(VerbSpec(
        "dependencies",
        description="Show and preflight the project dependency graph; --write updates its generated block (read-only without it).",
        group=VerbGroup.MIXED.value,
        options=(
            config_opt,
            OptionSpec(("--write",), "write the generated graph into the orchestration document", parser_kwargs={"action": "store_true", "default": False}),
        ),
        mutating=True, dry_run=True, include_confirmation=False, include_json=True,
        include_progress=False, handler=direct(),
        constraints=(Requires("--dry-run", ("--write",), "a dependency preview is only meaningful for the --write block"),),
    ))
    common_target = (target,)
    build_options = (config_opt, *detail_opts)
    registry.register(VerbSpec("build", description="Run the isolated build step.", group=VerbGroup.MODIFICATION.value, arguments=common_target, options=build_options, mutating=True, dry_run=True, include_confirmation=False, include_json=False, include_progress=False, handler=direct()))
    publish_options = (
        OptionSpec(("--build-output",), "publish the verified retained build ID printed by cmru build", metavar="ID", parser_kwargs={"default": None, "type": _non_empty}, mutually_exclusive_group="publish-source", mutually_exclusive_required=True),
        OptionSpec(("--from-checkout",), "publish whatever the CALLER'S checkout currently holds (not isolated, not verified)", parser_kwargs={"action": "store_true", "default": False}, mutually_exclusive_group="publish-source", mutually_exclusive_required=True),
        *build_options,
    )
    registry.register(VerbSpec("publish", description="Run the project publish step from a verified retained build (--build-output) or, explicitly, from the caller's checkout (--from-checkout).", group=VerbGroup.MODIFICATION.value, arguments=common_target, options=publish_options, mutating=True, dry_run=True, include_confirmation=False, include_json=False, include_progress=False, handler=direct()))
    registry.register(VerbSpec("changelog", description="Backfill history for an already-published tagged release.", group=VerbGroup.MODIFICATION.value, arguments=common_target, options=(OptionSpec(("--backfill-tag",), "tag to backfill; repeat once per selected project", metavar="TAG", parser_kwargs={"action": "append", "required": True}), config_opt), mutating=True, dry_run=True, include_confirmation=False, include_json=False, include_progress=False, handler=direct()))
    release_options = (
        OptionSpec(("--minor",), "bump minor versions", parser_kwargs={"action": "store_true", "default": False}, mutually_exclusive_group="version-override"),
        OptionSpec(("--major",), "bump major versions", parser_kwargs={"action": "store_true", "default": False}, mutually_exclusive_group="version-override"),
        OptionSpec(("--set-version",), "set an explicit version (exactly one project must be selected)", metavar="VER", parser_kwargs={"default": None}, mutually_exclusive_group="version-override"),
        OptionSpec(("--no-build",), "tag and push only; skip build and publish", parser_kwargs={"action": "store_true", "default": False}),
        config_opt,
        OptionSpec(("--resume",), "resume a retained failed release worktree", metavar="WORKTREE", parser_kwargs={"default": None}),
        OptionSpec(("--allow-uncommitted",), "allow local edits to be omitted from the origin/main snapshot", parser_kwargs={"action": "store_true", "default": False}),
        OptionSpec(("--allow-tag-ahead-of-head",), "allow a tag strictly ahead of the snapshot", parser_kwargs={"action": "store_true", "default": False}),
        OptionSpec(("--allow-stale-tool-deps",), "allow declared tool dependencies behind their latest release", parser_kwargs={"action": "store_true", "default": False}),
        *detail_opts,
        OptionSpec(("--discard",), "do not retain this kind of output after a successful release; repeat for several", metavar="{logs,artifacts,evidence}", parser_kwargs={"action": "append", "choices": ("logs", "artifacts", "evidence"), "default": None}),
        OptionSpec(("--ahead-check-ref",), "git ref the local-ahead guard compares against (the snapshot is always origin/main)", metavar="REF", parser_kwargs={"default": None}),
        OptionSpec(("--ref",), "DEPRECATED alias of --ahead-check-ref; removed in the next release", metavar="REF", parser_kwargs={"default": None}),
    )
    status_options = (
        OptionSpec(("--minor",), "bump minor versions", parser_kwargs={"action": "store_true", "default": False}, mutually_exclusive_group="version-override"),
        OptionSpec(("--major",), "bump major versions", parser_kwargs={"action": "store_true", "default": False}, mutually_exclusive_group="version-override"),
        OptionSpec(("--set-version",), "set an explicit version (exactly one project must be selected)", metavar="VER", parser_kwargs={"default": None}, mutually_exclusive_group="version-override"),
        config_opt,
        OptionSpec(("--ref",), "git ref used for status comparison", metavar="REF", parser_kwargs={"default": None}),
    )
    registry.register(VerbSpec("release", description="Release selected projects from an isolated origin/main snapshot.", group=VerbGroup.MIXED.value, arguments=common_target, options=release_options, mutating=True, dry_run=True, include_confirmation=False, include_json=False, include_progress=False, handler=direct()))
    registry.register(VerbSpec("status", description="Preview changed projects and their next versions (read-only).", group=VerbGroup.EXPLORATION.value, arguments=common_target, options=status_options, include_json=True, include_progress=False, handler=direct()))
    cleanup_options = (
        OptionSpec(("--policy",), "apply the configured [cleanup] policy: delete Releases, tags and GHCR versions per project, run steps.clean, and commit", parser_kwargs={"action": "store_true", "default": False}, mutually_exclusive_group="cleanup-mode", mutually_exclusive_required=True),
        OptionSpec(("--remove-assets",), "age-based remote Releases and GHCR cleanup (estate-wide; takes no project target)", metavar="AGE", parser_kwargs={"default": None, "type": _non_empty}, mutually_exclusive_group="cleanup-mode", mutually_exclusive_required=True),
        OptionSpec(("--delete-unmanaged-release-tag",), "delete one exact non-CMRU GitHub Release, never its Git tag", metavar="TAG", parser_kwargs={"default": None, "type": _non_empty}, mutually_exclusive_group="cleanup-mode", mutually_exclusive_required=True),
        OptionSpec(("--delete-build-output",), "delete one exact local build record", metavar="ID", parser_kwargs={"default": None, "type": _non_empty}, mutually_exclusive_group="cleanup-mode", mutually_exclusive_required=True),
        config_opt,
    )
    registry.register(VerbSpec("cleanup", description="Remove remote release assets (--policy, --remove-assets, --delete-unmanaged-release-tag) or one retained local build record (--delete-build-output); one mode is required. It does not abandon release transactions.", group=VerbGroup.MAINTENANCE.value, arguments=common_target, options=cleanup_options, mutating=True, dry_run=True, include_confirmation=True, include_json=False, include_progress=False, handler=direct()))
    registry.register(VerbSpec(
        "abandon",
        description=("Inspect and discard retained local release transactions (no argument) or one retained build or release worktree (BRANCH or PATH). Removes the exact CMRU backup branch, worktree, in-worktree logs/artifacts, and transaction sidecars; it refuses release transactions with publication or promotion evidence."),
        group=VerbGroup.MAINTENANCE.value,
        arguments=(ArgumentSpec("branch", "exact managed build or release branch, or the path of its worktree; omit to select all retained release transactions", metavar="BRANCH|PATH", parser_kwargs={"nargs": "?", "default": None}),),
        options=(config_opt,),
        mutating=True,
        dry_run=True,
        include_json=False,
        include_progress=False,
        handler=_abandon,
    ))
    for spec in delegated:
        registry.register(spec)
    # The packaged agent skill (AC-19) and the environment doctor (AC-20); the
    # library adds the automatic ``skills`` doctor check once both are present.
    from cli_extended import register_doctor, register_skills_verbs
    from cmru.doctor import doctor_checks

    register_skills_verbs(registry, package="cmru")
    register_doctor(registry, doctor_checks())
    return registry.build()


def usage() -> str:
    """Return usage rendered from the registered grammar."""
    return _build_cli().parser.format_help()


def main(argv: Optional[List[str]] = None) -> int:
    global _ACTIVE_RELEASE_PREFLIGHT_SNAPSHOT
    from cmru.output import configure_from_environment

    # Discard the previous environment-based handoff protocol. A release
    # snapshot comes only from the private descriptor inherited by a family
    # launcher; configured environment values are applied later in dispatch.
    os.environ.pop("CMRU_RELEASE_PREFLIGHT_SNAPSHOT", None)
    previous_handoff = _ACTIVE_RELEASE_PREFLIGHT_SNAPSHOT
    try:
        _ACTIVE_RELEASE_PREFLIGHT_SNAPSHOT = _read_release_snapshot_handoff_from_pipe()
    except RuntimeError as exc:
        log_error(str(exc))
        return 2
    arguments = list(argv) if argv is not None else sys.argv[1:]
    from cli_extended.identity import VersionLookupError
    from cmru.cli_support import INTERACTIVE_EXTRA, report_not_installed

    try:
        configure_from_environment()
        try:
            # Built here, never at import: the identity is read from the
            # installed distribution and a missing one must be a clean exit 3.
            cli = _build_cli()
        except VersionLookupError:
            return report_not_installed()
        return cli.run(argv=arguments, interactive_extra=INTERACTIVE_EXTRA)
    finally:
        _ACTIVE_RELEASE_PREFLIGHT_SNAPSHOT = previous_handoff


if __name__ == "__main__":
    raise SystemExit("Use the installed 'cmru' command; python -m cmru.cli is not supported.")
