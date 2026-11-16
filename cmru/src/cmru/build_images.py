"""Resolve CMRU's configured build and host-probe images."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Mapping


WHEEL_BUILDER_IMAGE_ENV = "CMRU_WHEEL_BUILDER_IMAGE"
CGROUP_PROBE_IMAGE_ENV = "CMRU_TESTER_CGROUP_PROBE_IMAGE"
BUILD_IMAGE_ENV_NAMES = frozenset({
    WHEEL_BUILDER_IMAGE_ENV,
    CGROUP_PROBE_IMAGE_ENV,
})


class EnvironmentResolutionContext(str, Enum):
    RELEASE = "release"
    DIRECT_HANDLER = "direct-handler"


def configured_environment(cwd: Path) -> dict[str, str]:
    """Return the effective configured env for the project enclosing *cwd*."""
    from cmru.config import (
        ORCHESTRATION_CONFIG_FILENAME,
        PROJECT_CONFIG_FILENAME,
        load_forge_config,
    )

    ancestors = (cwd, *cwd.parents)
    orchestration_path = next(
        (directory / ORCHESTRATION_CONFIG_FILENAME for directory in ancestors
         if (directory / ORCHESTRATION_CONFIG_FILENAME).exists()),
        None,
    )
    project_path = next(
        (directory / PROJECT_CONFIG_FILENAME for directory in ancestors
         if (directory / PROJECT_CONFIG_FILENAME).exists()),
        None,
    )
    config_path = orchestration_path or project_path
    if config_path is None:
        return {}

    forge = load_forge_config(config_path)
    values = dict(forge.env)
    if forge.orchestration is None:
        raise RuntimeError(
            f"{config_path}: strict CMRU config loader returned no project registry"
        )
    matches: list[tuple[int, str]] = []
    for project_id, path in forge.orchestration.project_configs.items():
        project_root = path.resolve().parent
        try:
            cwd.relative_to(project_root)
        except ValueError:
            continue
        matches.append((len(project_root.parts), project_id))
    if not matches:
        raise ValueError(
            f"{cwd} is not inside a project registered by {config_path}"
        )
    project_name = max(matches, key=lambda item: item[0])[1]
    values.update(forge.projects[project_name].env)
    return values


def configured_environment_value(cwd: Path, name: str) -> str:
    """Read one value from the effective CMRU config enclosing *cwd*."""
    return str(configured_environment(cwd).get(name, "") or "").strip()


def resolve_environment_value(
    name: str,
    *,
    context: EnvironmentResolutionContext,
    cwd: Path | None = None,
    configured_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
) -> str:
    """Resolve one input using the precedence of its explicit call context."""
    environment = os.environ if process_env is None else process_env
    config = (
        configured_environment(cwd)
        if configured_env is None and cwd is not None
        else configured_env or {}
    )

    if context is EnvironmentResolutionContext.RELEASE:
        # Release steps apply declared env before invoking handlers. A present
        # non-None config value (including "") is therefore the build-time
        # value; the caller snapshot is only the fallback.
        if name in config and config[name] is not None:
            return str(config[name] or "").strip()
        if name in environment:
            return str(environment[name] or "").strip()
        return ""

    if context is EnvironmentResolutionContext.DIRECT_HANDLER:
        # Direct handler invocations do not pass through release env injection.
        if name in environment:
            return str(environment[name] or "").strip()
        if name in config and config[name] is not None:
            return str(config[name] or "").strip()
        return ""

    raise ValueError(f"unsupported environment resolution context: {context!r}")
