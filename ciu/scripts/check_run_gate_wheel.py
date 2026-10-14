#!/usr/bin/env python3
"""Refuse CIU's registered lane when tester-unified has a stale Run-Gate wheel."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
import tomllib
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


def verify_console_scripts(installed_distribution, source_scripts: dict[str, str]) -> None:
    """Require installed Run-Gate entry points to match its source metadata."""
    installed_scripts = {
        entry_point.name: entry_point.value
        for entry_point in installed_distribution.entry_points
        if entry_point.group == "console_scripts"
    }
    if installed_scripts != source_scripts:
        raise SystemExit(
            "tester-unified's installed Run-Gate console entry points do not "
            "match run-gate-project/pyproject.toml: "
            f"installed={installed_scripts!r}, checkout={source_scripts!r}"
        )


def _source_console_scripts(pyproject_path: Path) -> dict[str, str]:
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"cannot read Run-Gate checkout metadata {pyproject_path}: {exc}") from exc
    project = data.get("project")
    scripts = project.get("scripts") if isinstance(project, dict) else None
    if not isinstance(scripts, dict) or not all(
        isinstance(name, str) and isinstance(target, str)
        for name, target in scripts.items()
    ):
        raise SystemExit(
            f"Run-Gate checkout metadata has no valid [project.scripts] table: {pyproject_path}"
        )
    return scripts
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

    try:
        distribution = importlib.metadata.distribution("run-gate")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SystemExit("tester-unified has no installed Run-Gate distribution") from exc
    verify_console_scripts(
        distribution,
        _source_console_scripts(source_root / "pyproject.toml"),
    )

    print("tester-unified Run-Gate modules and console entry points match the selected checkout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
