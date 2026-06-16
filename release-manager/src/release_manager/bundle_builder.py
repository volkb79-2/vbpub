"""Compatibility shim: implementation moved to ciu_forge.bundle (P1). Remove after P6."""
from __future__ import annotations
import sys
from pathlib import Path

_CIU_FORGE_SRC = Path(__file__).resolve().parents[3] / "ciu-forge" / "src"
if str(_CIU_FORGE_SRC) not in sys.path:
    sys.path.insert(0, str(_CIU_FORGE_SRC))

from ciu_forge.bundle import *  # noqa: F401, F403, E402
from ciu_forge.bundle import (  # noqa: E402
    BundleConfig,
    build_arg_parser,
    build_wheel,
    copy_sources,
    create_archive,
    load_toml,
    log_info,
    main,
    parse_config,
    resolve_path,
    run_bundle,
)
