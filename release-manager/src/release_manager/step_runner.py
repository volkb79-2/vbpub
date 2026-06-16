"""Compatibility shim: implementation moved to ciu_forge.runner (P1). Remove after P6."""
from __future__ import annotations
import sys
from pathlib import Path

_CIU_FORGE_SRC = Path(__file__).resolve().parents[3] / "ciu-forge" / "src"
if str(_CIU_FORGE_SRC) not in sys.path:
    sys.path.insert(0, str(_CIU_FORGE_SRC))

from ciu_forge.runner import *  # noqa: F401, F403, E402
from ciu_forge.runner import (  # noqa: E402
    ReleaseSecrets,
    StepConfig,
    _docker_login,
    apply_env_command,
    apply_release_env,
    apply_reproducible_env,
    compute_build_date,
    ensure_required_env,
    execute_step,
    load_release_secrets,
    load_toml,
    log_error,
    log_info,
    main,
    maybe_login,
    maybe_login_multi,
    parse_step,
    resolve_path,
    run_command,
    run_step,
)
