"""One project-target resolver for the delegate verbs (redesign section B13).

``resolve``, ``get-py``, ``standards``, ``tool-deps`` and ``run-step`` each
carried a copied block that derived the "current project" for an omitted target
and rendered selector errors. This module is that block, once, so the verbs
cannot drift apart again.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from cli_extended import CliFailure

from cmru.cli_support import TargetSelectionError, select_target_names
from cmru.config_names import PROJECT_CONFIG_FILENAME


def current_project(
    config_path: Path | None, configs: Mapping[str, object],
) -> str | None:
    """The project an omitted target means, or ``None`` at the estate root.

    A standalone project-local ``cmru.toml`` is its own context; otherwise the
    invocation directory decides.
    """
    if (
        config_path is not None
        and config_path.name == PROJECT_CONFIG_FILENAME
        and len(configs) == 1
    ):
        return next(iter(configs))
    from cmru.config import resolve_invocation_context

    return resolve_invocation_context(config_path).project_name


def resolve_target(
    target,
    config_path: Path | None,
    configs: Mapping[str, object],
    project_order: Iterable[str],
) -> list[str]:
    """Resolve a parsed ``target_argument()`` value to project names in order.

    An omitted target means the current project, or every orchestrated project
    at the estate root. Selector problems become ``CliFailure`` exit 2 with the
    verb's help.
    """
    context_project = (
        current_project(config_path, configs) if target is None else None
    )
    try:
        return select_target_names(
            target, configs, project_order, context_project=context_project,
        )
    except TargetSelectionError as exc:
        raise CliFailure(str(exc), exit_code=2, show_help=True) from exc
