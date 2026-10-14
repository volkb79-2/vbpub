#!/usr/bin/env python3
"""Refuse CIU's registered lane when tester-unified has a stale Run-Gate wheel."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def verify_module_file(
    module_name: str,
    installed_path: Path,
    source_path: Path,
    venv_root: Path,
) -> None:
    """Require one installed wheel module to match the selected source bytes."""
    if not installed_path.is_relative_to(venv_root):
        raise SystemExit(
            f"Run-Gate {module_name} resolved outside tester-unified's installed "
            f"venv: {installed_path}; use the installed Run-Gate wheel"
        )
    if not source_path.is_file():
        raise SystemExit(f"Run-Gate checkout source is missing: {source_path}")
    if installed_path.read_bytes() != source_path.read_bytes():
        raise SystemExit(
            "tester-unified's installed Run-Gate wheel does not match the "
            f"selected checkout ({module_name}); rebuild it from the selected "
            "checkout using the supported procedure in "
            "`tester-unified/README.md` (build the image from the exact Run-Gate "
            "wheel for that checkout), then rerun the registered CIU gate"
        )


def main() -> int:
    checkout_root = Path(__file__).resolve().parents[2]
    source_root = checkout_root / "run-gate-project"
    venv_root = Path(sys.prefix).resolve()

    for module_name in ("run_gate", "run_gate_admission"):
        module = importlib.import_module(module_name)
        installed_path = Path(module.__file__).resolve()
        source_path = (source_root / f"{module_name}.py").resolve()
        verify_module_file(module_name, installed_path, source_path, venv_root)

    print("tester-unified Run-Gate wheel matches the selected checkout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
