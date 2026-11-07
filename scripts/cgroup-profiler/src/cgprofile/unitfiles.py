"""Render and compare the systemd units (``cgpd unit print`` / ``unit check``).

The templates ship inside the wheel (``cgprofile/data/*.in``); the only
inputs are the install root and the wheel venv path, so nothing is read from
a checkout, a user name or a host name (NATIVE-DEPLOYMENT.md section 4).
Stdlib only.
"""

from __future__ import annotations

import importlib.resources
import os
from typing import Any
from typing import Dict, List, Mapping, Optional, Sequence

from . import daemon_config

DEFAULT_INSTALL_ROOT = "/opt/cgprofile"
DEFAULT_UNIT_DIR = "/etc/systemd/system"
DEFAULT_SYSUSERS_DIR = "/etc/sysusers.d"
#: unit file name -> template shipped in the wheel
TEMPLATES: Dict[str, str] = {
    "cgprofile.slice": "cgprofile.slice.in",
    "cgpd.service": "cgpd.service.in",
    "cgprofile-control.socket": "",
    "cgprofile-observer.socket": "",
    "cgprofile.sysusers": "",
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


def _socket_unit(level: str, settings: Mapping[str, str]) -> str:
    path = _checked_path(f"{level} socket path", settings["path"])
    return (
        f"# cgprofile-{level}.socket: systemd-owned {level} access endpoint.\n"
        "[Unit]\n"
        f"Description=cgprofile daemon, {level} access level\n"
        "\n[Socket]\n"
        f"ListenStream={path}\n"
        "SocketUser=root\n"
        f"SocketGroup={settings['group']}\n"
        f"SocketMode={settings['mode']}\n"
        "DirectoryMode=0755\n"
        f"FileDescriptorName={level}\n"
        "RemoveOnStop=no\n"
        "Service=cgpd.service\n"
        "\n[Install]\n"
        "WantedBy=sockets.target\n"
    )


def render(
    install_root: str = DEFAULT_INSTALL_ROOT,
    wheel_venv: Optional[str] = None,
    config_values: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """Render the service resources from the fixed socket config values."""
    root = _checked_path("install root", install_root)
    venv = _checked_path("wheel venv", wheel_venv) if wheel_venv else f"{root}/current/venv"
    values = config_values or {}
    sockets = daemon_config.socket_settings(values)
    rendered = {
        name: template_text(name).replace("@WHEEL_VENV@", venv)
        for name in ("cgprofile.slice", "cgpd.service")
    }
    rendered["cgprofile-control.socket"] = _socket_unit("control", sockets["control"])
    rendered["cgprofile-observer.socket"] = _socket_unit("observer", sockets["observer"])
    groups = []
    if sockets["control"]["group"] == daemon_config.DEFAULT_CONTROL_GROUP:
        groups.append(f"g {daemon_config.DEFAULT_CONTROL_GROUP} - - -\n")
    if sockets["observer"]["group"] == daemon_config.DEFAULT_OBSERVER_GROUP:
        groups.append(f"g {daemon_config.DEFAULT_OBSERVER_GROUP} - - -\n")
    rendered["cgprofile.sysusers"] = "".join(groups)
    return rendered


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
        if name == "cgprofile.conf":
            name = "cgprofile.sysusers"
        if name not in TEMPLATES:
            raise UnitError(f"{path}: not a cgprofile resource (expected {' or '.join(TEMPLATES)})")
        mapping[name] = path
    return mapping


def default_installed_map() -> Dict[str, str]:
    """Return the canonical installation locations for every resource."""
    return {
        "cgprofile.slice": os.path.join(DEFAULT_UNIT_DIR, "cgprofile.slice"),
        "cgpd.service": os.path.join(DEFAULT_UNIT_DIR, "cgpd.service"),
        "cgprofile-control.socket": os.path.join(DEFAULT_UNIT_DIR, "cgprofile-control.socket"),
        "cgprofile-observer.socket": os.path.join(DEFAULT_UNIT_DIR, "cgprofile-observer.socket"),
        "cgprofile.sysusers": os.path.join(DEFAULT_SYSUSERS_DIR, "cgprofile.conf"),
    }
