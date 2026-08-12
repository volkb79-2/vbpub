#!/usr/bin/env python3
"""Generic execution engine for the strict project-local ``cmru.toml`` grammar."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Iterable, Mapping, Optional



@dataclass(frozen=True)
class StepConfig:
    name: str
    commands: list[dict]
    bake_set_prefix: Optional[str]
    bake_set_vars: list[str]
    no_cache_env: Optional[str]
    clean_dirs: list[str]
    required_env: list[str]
    login: Optional[dict]
    step_env: Mapping[str, str]
    env_command: Optional[list[str]]
    registries: list = None  # [targets].registry for multi-push
    quiet: bool = False  # suppress live line-by-line stdout tee; log file only + tail-on-failure


@dataclass(frozen=True)
class CommandResult:
    """Compact, non-invented command outcome for the orchestration console."""
    elapsed_seconds: float
    evidence: Optional[str]


def log_info(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def log_error(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr, flush=True)


def resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _git_out(start: Path, *args: str) -> Optional[str]:
    """Run ``git <args>`` under *start*; return stripped stdout or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(start), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    out = result.stdout.strip()
    return out if (result.returncode == 0 and out) else None


def apply_reproducible_env(project_root: Path) -> None:
    """Derive reproducibility metadata from this worktree's HEAD commit.

    The checkout is the authoritative source. An inherited shell value must
    not make a build describe different source than it actually consumes.
    """
    epoch = _git_out(project_root, "log", "-1", "--format=%ct")
    if epoch:
        os.environ["SOURCE_DATE_EPOCH"] = epoch
        os.environ["OCI_CREATED"] = datetime.fromtimestamp(
            int(epoch), timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    head = _git_out(project_root, "rev-parse", "HEAD")
    if head:
        os.environ["OCI_REVISION"] = head


def compute_build_date(config: dict, project_root: Path) -> None:
    """Seed the reproducible-build env and a commit-derived ``BUILD_DATE``.

    No wall clock and no auto-increment counter: ``BUILD_DATE`` (consumed by docker
    image tags) is derived from the HEAD commit time, so rebuilding the same commit
    yields the same tag. Wheel versions come from setuptools-scm, not from here.
    """
    apply_reproducible_env(project_root)

    metadata = config.get("build_metadata")
    if not metadata:
        return
    if not isinstance(metadata, dict):
        raise ValueError("build_metadata must be a table")

    date_env = (metadata.get("date_env") or "BUILD_DATE").strip()
    date_format = (metadata.get("date_format") or "%Y%m%d").strip()
    if not os.getenv(date_env):
        epoch = os.getenv("SOURCE_DATE_EPOCH")
        if not epoch:
            raise RuntimeError(
                "Cannot derive BUILD_DATE: SOURCE_DATE_EPOCH is absent and the "
                "project is not at a Git commit. Run from a Git worktree or set "
                "SOURCE_DATE_EPOCH explicitly."
            )
        base = datetime.fromtimestamp(int(epoch), timezone.utc)
        os.environ[date_env] = base.strftime(date_format)


def parse_step(config: dict, step_name: str) -> StepConfig:
    steps = config.get("steps")
    if not steps or not isinstance(steps, dict):
        raise ValueError("[steps] section is required in cmru.toml")
    step = steps.get(step_name)
    if not step or not isinstance(step, dict):
        raise ValueError(f"Step '{step_name}' not found in cmru.toml")

    commands = step.get("commands")
    if not commands or not isinstance(commands, list):
        raise ValueError(f"steps.{step_name}.commands must be a list")

    bake_set_prefix = step.get("bake_set_prefix")
    if bake_set_prefix is not None:
        bake_set_prefix = str(bake_set_prefix)

    bake_set_vars = step.get("bake_set_vars") or []
    if not isinstance(bake_set_vars, list):
        raise ValueError(f"steps.{step_name}.bake_set_vars must be a list")

    no_cache_env = step.get("no_cache_env")
    if no_cache_env is not None:
        no_cache_env = str(no_cache_env)

    clean_dirs = step.get("clean_dirs") or []
    if not isinstance(clean_dirs, list):
        raise ValueError(f"steps.{step_name}.clean_dirs must be a list")

    required_env = step.get("required_env") or []
    if not isinstance(required_env, list):
        raise ValueError(f"steps.{step_name}.required_env must be a list")

    login = step.get("login")
    if login is not None and not isinstance(login, dict):
        raise ValueError(f"steps.{step_name}.login must be a table")

    step_env = step.get("env") or {}
    if step_env is None:
        step_env = {}
    if not isinstance(step_env, dict):
        raise ValueError(f"steps.{step_name}.env must be a table")

    env_command = step.get("env_command")
    if env_command is not None and not isinstance(env_command, list):
        raise ValueError(f"steps.{step_name}.env_command must be a list")

    quiet = step.get("quiet")
    if not isinstance(quiet, bool):
        raise ValueError(f"steps.{step_name}.quiet must be explicitly true or false")

    return StepConfig(
        name=step_name,
        commands=commands,
        bake_set_prefix=bake_set_prefix,
        bake_set_vars=bake_set_vars,
        no_cache_env=no_cache_env,
        clean_dirs=clean_dirs,
        required_env=required_env,
        login=login,
        step_env=step_env,
        env_command=[str(item) for item in env_command] if env_command else None,
        quiet=quiet,
    )


def ensure_required_env(required: Iterable[str]) -> None:
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def apply_env_command(env_command: Optional[list[str]], cwd: Path) -> None:
    if not env_command:
        return
    log_info(f"Resolving dynamic environment via: {' '.join(env_command)}")
    result = subprocess.run(
        env_command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=True,
    )
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"env_command output must be KEY=VALUE lines. Got: {line}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"env_command produced empty key in line: {line}")
        os.environ[key] = value


def _docker_login(registry: str, username: str, token: str) -> None:
    log_info(f"Logging into {registry} as {username}")
    subprocess.run(
        ["docker", "login", registry, "-u", username, "--password-stdin"],
        input=f"{token}\n",
        text=True,
        check=True,
    )


def maybe_login(login: Optional[dict]) -> None:
    if not login:
        return
    registry = login["registry"]
    username_env = login["username_env"]
    token_env = login["token_env"]
    required = login["required"]

    username = os.getenv(username_env)
    token = os.getenv(token_env)
    if not token:
        if required:
            raise RuntimeError(f"{token_env} is required for registry login")
        return
    if not username:
        raise RuntimeError(f"{username_env} is required for registry login")
    _docker_login(registry, username, token)


def maybe_login_multi(login: Optional[dict], registries: Optional[list]) -> None:
    """Login to the step's single registry then any additional [targets].registry entries (S11)."""
    maybe_login(login)

    if not registries or len(registries) <= 1:
        return

    # Additional registries beyond the first (which is handled by REGISTRY/login above)
    username = os.getenv("GITHUB_USERNAME")
    token = os.getenv("GITHUB_PUSH_PAT")
    if not username or not token:
        missing = "GITHUB_USERNAME" if not username else "GITHUB_PUSH_PAT"
        raise RuntimeError(
            f"{missing} is required for additional registry login"
        )
    for reg in registries[1:]:
        _docker_login(reg, username, token)


TAIL_LINES_ON_FAILURE = 40
ERROR_LINES_ON_FAILURE = 20
# Matches this codebase's own "[ERROR] ..." convention (wherever it appears in a line,
# e.g. after a buildkit "#63 89.30 " progress prefix) and docker/buildkit's own
# top-level "ERROR: target ... failed to solve" summary line.
_ERROR_LINE_RE = re.compile(r"\[ERROR\]|^ERROR:")
_PYTEST_SUCCESS_RE = re.compile(r"=+ .*?\b\d+ passed(?:, \d+ skipped)? in [^=]+ =+")
_UNITTEST_RUN_RE = re.compile(r"^Ran \d+ tests? in .+$")
_UNITTEST_OK_RE = re.compile(r"^OK(?: \(.+\))?$")


def _success_evidence(lines: Iterable[str]) -> Optional[str]:
    """Return only a known test-framework success fact; never guess from tool noise."""
    recent = list(lines)
    for line in reversed(recent):
        normalized = line.strip()
        if _PYTEST_SUCCESS_RE.search(normalized):
            return normalized.strip("= ")
    for index, line in enumerate(recent):
        if _UNITTEST_RUN_RE.match(line.strip()):
            for candidate in recent[index + 1:index + 4]:
                if _UNITTEST_OK_RE.match(candidate.strip()):
                    return f"{line.strip()}; {candidate.strip()}"
    return None


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _write_line(handle, line: str) -> None:
    handle.write(line)
    handle.flush()


def _open_aggregate_log(local_log: Path, *, quiet: bool):
    """Open the wrapper-owned full run log only while details are console-quiet.

    In ``--show-run-details`` mode the root wrapper's tee already receives the raw
    stream; mirroring again would duplicate every line.  A direct CMRU invocation
    without the wrapper still has its stable per-step file and does not invent a
    repository-wide log path.
    """
    raw_path = (os.getenv("CMRU_RUN_LOG") or "").strip()
    if not quiet or not raw_path:
        return None
    aggregate_path = Path(raw_path).expanduser().resolve()
    if aggregate_path == local_log.resolve():
        return None
    aggregate_path.parent.mkdir(parents=True, exist_ok=True)
    return aggregate_path.open("a", encoding="utf-8", buffering=1)


def run_command(
    argv: list[str],
    cwd: Path,
    log_handle,
    *,
    quiet: bool = False,
    log_path: Optional[Path] = None,
    mirror_handle=None,
) -> CommandResult:
    """Run argv, streaming its combined stdout/stderr into log_handle.

    When ``quiet`` is set (build/push steps whose subprocess is itself very
    noisy, e.g. `docker buildx bake`), individual lines are not echoed live —
    only written to the log file — so the top-level release output stays
    readable. On failure, lines that look like actual errors are surfaced
    immediately; a raw tail is shown as a fallback when nothing matched (a
    failing `docker buildx bake` re-echoes the whole compiled RUN script after
    its real error, so a blind tail often shows that script instead of the
    error that caused it).
    """
    location = f" (full output: {log_path})" if (quiet and log_path) else ""
    log_info(f"Running: {' '.join(argv)}{location}")
    command_env = os.environ.copy()
    # Pipes make Python programs block-buffer by default.  This makes the common
    # case immediate; arbitrary child tools still control their own buffering.
    command_env["PYTHONUNBUFFERED"] = "1"
    start = monotonic()
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=command_env,
    )
    assert process.stdout is not None
    tail: deque[str] = deque(maxlen=TAIL_LINES_ON_FAILURE) if quiet else deque(maxlen=0)
    # First-seen, not last-seen: docker buildx bake always ends with a generic
    # "ERROR: failed to solve: ..." summary line, so a last-N policy would let
    # that crowd out an earlier, actually-informative "[ERROR] ..." line once a
    # failure produces more than ERROR_LINES_ON_FAILURE matches.
    error_lines: list[str] = []
    evidence_lines: deque[str] = deque(maxlen=300)
    for line in process.stdout:
        _write_line(log_handle, line)
        if mirror_handle is not None:
            _write_line(mirror_handle, line)
        evidence_lines.append(line)
        if quiet:
            tail.append(line)
            if len(error_lines) < ERROR_LINES_ON_FAILURE and _ERROR_LINE_RE.search(line):
                error_lines.append(line)
        else:
            print(line, end="", flush=True)
    exit_code = process.wait()
    elapsed_seconds = monotonic() - start
    if exit_code != 0:
        if quiet and error_lines:
            sys.stderr.write(f"[ERROR] {len(error_lines)} error-looking line(s) from the output:\n")
            sys.stderr.write("".join(error_lines))
        if quiet and tail:
            context_note = " (context)" if error_lines else ""
            sys.stderr.write(f"[ERROR] Last {len(tail)} line(s) of output{context_note}:\n")
            sys.stderr.write("".join(tail))
        sys.stderr.flush()
        raise subprocess.CalledProcessError(exit_code, argv)
    return CommandResult(elapsed_seconds=elapsed_seconds, evidence=_success_evidence(evidence_lines))


def execute_step(
    step: StepConfig,
    project_root: Path,
    log_dir: Path,
    *,
    extra_env: Optional[Mapping[str, str]] = None,
    build_metadata: Optional[Mapping[str, str]] = None,
) -> None:
    """Execute a pre-parsed StepConfig. Called by both run_step() and the orchestrator.

    This is the single execution path every build step flows through (S3 contract).
    ``extra_env`` carries project-level declared environment from the orchestrator.
    Declared values override ambient values for this scoped step, so a stale shell setting
    cannot silently change a project contract. The process environment is restored after
    the step and cannot leak into a later project in the same orchestration run.
    """
    original_environment = os.environ.copy()
    try:
        if build_metadata:
            compute_build_date({"build_metadata": build_metadata}, project_root)
        _execute_step(step, project_root, log_dir, extra_env=extra_env)
    finally:
        os.environ.clear()
        os.environ.update(original_environment)


def _execute_step(
    step: StepConfig,
    project_root: Path,
    log_dir: Path,
    *,
    extra_env: Optional[Mapping[str, str]] = None,
) -> None:
    """Implementation for :func:`execute_step` inside its scoped environment."""
    log_dir.mkdir(parents=True, exist_ok=True)

    if extra_env:
        for key, value in extra_env.items():
            if value is None:
                continue
            # Empty is a meaningful declared value: it masks a stale ambient
            # setting rather than falling through to it.
            os.environ[key] = str(value)

    for key, value in step.step_env.items():
        if value is None:
            continue
        os.environ[key] = str(value)

    apply_env_command(step.env_command, project_root)
    ensure_required_env(step.required_env)
    maybe_login_multi(step.login, step.registries)

    for target in step.clean_dirs:
        clean_path = resolve_path(project_root, str(target))
        if clean_path.exists():
            shutil.rmtree(clean_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_file = log_dir / f"{step.name}.log"
    quiet = step.quiet and not _truthy_env("CMRU_SHOW_RUN_DETAILS")
    append = _truthy_env("CMRU_LOG_APPEND")
    if quiet:
        log_info(f"Details: {log_file} (kept out of the console; use --show-run-details to stream them)")
    else:
        log_info(f"Logging to {log_file}")

    mode = "a" if append else "w"
    aggregate_handle = _open_aggregate_log(log_file, quiet=quiet)
    try:
        with log_file.open(mode, encoding="utf-8", buffering=1) as handle:
            header = f"[cmru] {timestamp} step={step.name} project_root={project_root}\n"
            if append:
                _write_line(handle, "\n---\n")
            _write_line(handle, header)
            if aggregate_handle is not None:
                _write_line(aggregate_handle, f"\n---\n{header}")
            for command in step.commands:
                if not isinstance(command, dict):
                    raise ValueError(f"Command entry must be a table in step '{step.name}'")
                label = command.get("label") or "command"
                argv = command.get("argv")
                cwd_raw = command.get("cwd")
                if not argv or not isinstance(argv, list):
                    raise ValueError(f"Command '{label}' must define argv list")
                if not cwd_raw:
                    raise ValueError(f"Command '{label}' must define cwd")
                cwd = resolve_path(project_root, str(cwd_raw))

                effective_argv = [str(item) for item in argv]
                if step.bake_set_prefix and step.bake_set_vars:
                    for var_name in step.bake_set_vars:
                        value = os.getenv(var_name)
                        if value:
                            effective_argv.extend([
                                "--set",
                                f"{step.bake_set_prefix}{var_name}={value}",
                            ])

                if step.no_cache_env and os.getenv(step.no_cache_env) == "1":
                    effective_argv.append("--no-cache")

                command_header = f"[cmru] command label={label!r} argv={effective_argv!r}\n"
                _write_line(handle, command_header)
                if aggregate_handle is not None:
                    _write_line(aggregate_handle, command_header)
                log_info(label)
                result = run_command(
                    effective_argv,
                    cwd,
                    handle,
                    quiet=quiet,
                    log_path=log_file,
                    mirror_handle=aggregate_handle,
                )
                evidence = f"; {result.evidence}" if result.evidence else ""
                log_info(
                    f"{label}: succeeded in {result.elapsed_seconds:.1f}s{evidence} "
                    f"(details: {log_file})"
                )
    finally:
        if aggregate_handle is not None:
            aggregate_handle.close()


def run_step(project_config_path: Path, step_name: str) -> None:
    """Run one named step from the strict project-local ``cmru.toml``.

    This is intentionally a thin direct entry point over the same parser and
    executor as orchestration.  There is no standalone runner configuration,
    shell-evaluation adapter, or inferred release config to drift from it.
    """
    from cmru.cli import apply_release_env, load_config

    (repo_root, projects, _order, _defaults, _steps, _mode, _step_order,
     _cleanup, github, env) = load_config(project_config_path)
    if len(projects) != 1:
        raise RuntimeError("run-step requires a project-local cmru.toml")
    apply_release_env(github, env)
    project = next(iter(projects.values()))
    step = project.runner_steps.get(step_name) if project.runner_steps else None
    if step is None:
        raise ValueError(f"Step '{step_name}' is not declared in {project_config_path}")
    project_root = project.project_root or repo_root
    execute_step(
        step,
        project_root,
        project_root / "logs" / "cmru",
        extra_env=project.env,
        build_metadata=project.build_metadata,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one named step from a project cmru.toml")
    parser.add_argument("--config", required=True, help="Path to project cmru.toml")
    parser.add_argument("--step", required=True, help="Step name to execute")
    parser.add_argument(
        "--show-run-details", action="store_true",
        help="Stream full subprocess output to this console",
    )
    parser.add_argument(
        "--log-append", action="store_true",
        help="Append a divider and retain the stable step log",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.show_run_details:
        os.environ["CMRU_SHOW_RUN_DETAILS"] = "1"
    if args.log_append:
        os.environ["CMRU_LOG_APPEND"] = "1"
    run_step(
        Path(args.config).expanduser().resolve(),
        args.step,
    )


if __name__ == "__main__":
    main()
