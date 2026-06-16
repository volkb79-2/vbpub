"""Compatibility shim: release_manager is now ciu_forge (P1). Remove after P6."""
from __future__ import annotations
import sys
from pathlib import Path

_CIU_FORGE_SRC = Path(__file__).resolve().parents[3] / "ciu-forge" / "src"
if str(_CIU_FORGE_SRC) not in sys.path:
    sys.path.insert(0, str(_CIU_FORGE_SRC))
