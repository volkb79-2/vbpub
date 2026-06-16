"""Compatibility shim: implementation moved to ciu_forge.cli (P1). Remove after P6."""
from __future__ import annotations
import sys
from pathlib import Path

_CIU_FORGE_SRC = Path(__file__).resolve().parents[3] / "ciu-forge" / "src"
if str(_CIU_FORGE_SRC) not in sys.path:
    sys.path.insert(0, str(_CIU_FORGE_SRC))

from ciu_forge.cli import *  # noqa: F401, F403, E402
from ciu_forge.cli import (  # noqa: E402
    CleanupConfig,
    Command,
    GitHubConfig,
    ProjectConfig,
    ReleaseEnvConfig,
    apply_release_env,
    build_arg_parser,
    cleanup_ghcr,
    cleanup_releases,
    load_config,
    load_json,
    log_error,
    log_info,
    log_warn,
    main,
    parse_commands,
    parse_duration,
    remove_assets,
    resolve_versions_from_git,
    run_commands,
    run_project_step,
)
