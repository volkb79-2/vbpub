"""Render and compare the systemd units (``cgpd unit print`` / ``unit check``).

The templates ship inside the wheel (``cgprofile/data/*.in``); the only
inputs are the install root and the wheel venv path, so nothing is read from
a checkout, a user name or a host name (NATIVE-DEPLOYMENT.md section 4).
Stdlib only.
"""

from __future__ import annotations

import importlib.resources
import os
from typing import Dict, List, Mapping, Optional, Sequence

DEFAULT_INSTALL_ROOT = "/opt/cgprofile"
DEFAULT_UNIT_DIR = "/etc/systemd/system"
#: unit file name -> template shipped in the wheel
TEMPLATES: Dict[str, str] = {
    "cgprofile.slice": "cgprofile.slice.in",
    "cgpd.service": "cgpd.service.in",
}


class UnitError(ValueError):
    """An install root or venv path a unit file cannot carry."""


def template_text(name: str) -> str:
    return importlib.resources.files("cgprofile").joinpath("data", TEMPLATES[name]).read_text(
        encoding="utf-8")


def _checked_path(label: str, value: str) -> str:
    if not os.path.isabs(value):
        raise UnitError(f"{label} must be an absolute path, got {value!r}")
    if any(char.isspace() or ord(char) < 32 or char in '@"\\%$' for char in value):
        raise UnitError(f"{label} {value!r} holds a character a unit file cannot carry safely")
    return os.path.normpath(value)


def render(install_root: str = DEFAULT_INSTALL_ROOT, wheel_venv: Optional[str] = None) -> Dict[str, str]:
    """Both units, rendered. ``wheel_venv`` defaults to ``<install_root>/current/venv``."""
    root = _checked_path("install root", install_root)
    venv = _checked_path("wheel venv", wheel_venv) if wheel_venv else f"{root}/current/venv"
    return {
        name: template_text(name).replace("@WHEEL_VENV@", venv)
        for name in TEMPLATES
    }


def first_difference(expected: str, installed: str) -> Optional[str]:
    """``None`` when equal, else a one-line description of the first differing line."""
    if expected == installed:
        return None
    want, have = expected.splitlines(), installed.splitlines()
    for number in range(max(len(want), len(have))):
        left = want[number] if number < len(want) else "<end of file>"
        right = have[number] if number < len(have) else "<end of file>"
        if left != right:
            return f"line {number + 1}: expected {left!r}, installed {right!r}"
    return "trailing newline differs"


def check(installed: Mapping[str, str], rendered: Mapping[str, str]) -> List[str]:
    """One drift message per unit in ``installed`` (name -> path) that differs."""
    problems: List[str] = []
    for name, path in installed.items():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            problems.append(f"{name}: cannot read {path}: {exc.strerror}")
            continue
        difference = first_difference(rendered[name], text)
        if difference is not None:
            problems.append(f"{name}: {path} differs from the rendered unit, {difference}")
    return problems


def installed_map(paths: Sequence[str]) -> Dict[str, str]:
    """Map ``--installed`` paths to unit names by basename; an unknown one raises."""
    mapping: Dict[str, str] = {}
    for path in paths:
        name = os.path.basename(path)
        if name not in TEMPLATES:
            raise UnitError(f"{path}: not a cgprofile unit file (expected {' or '.join(TEMPLATES)})")
        mapping[name] = path
    return mapping
