"""Render the tracked Mattermost templates through ciu without live state."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

STACK = Path(__file__).resolve().parents[1]
DEFAULTS = STACK / "ciu.defaults.toml.j2"
COMPOSE = STACK / "ciu.compose.yml.j2"

# A synthetic identity keeps the render independent of a live ciu.toml.j2.
ENVIRON = {"PUBLIC_FQDN": "example.test"}


def repo_root() -> Path:
    """Find the vbpub root that owns the source-backed ciu package."""
    for parent in STACK.parents:
        if (parent / "ciu/src/ciu").is_dir():
            return parent
    raise RuntimeError("cannot find repo root containing ciu/src/ciu")


def tls_edge_root() -> Path | None:
    """Find tls-edge by walking parents, or None when this is a sparse checkout."""
    for parent in STACK.parents:
        candidate = parent / "tls-edge"
        if candidate.is_dir():
            return candidate
    return None


def _ciu_renderers():
    source = str(repo_root() / "ciu/src")
    if source not in sys.path:
        sys.path.insert(0, source)

    from ciu.composefile import render_compose
    from ciu.config_model import render_stack, render_toml_template

    return render_stack, render_compose, render_toml_template


def render_mattermost(
    tmp_path: Path,
    *,
    calls_enabled: bool | None = None,
    expose_public: bool | None = None,
) -> dict[str, Any]:
    """Render only the two tracked stack templates into a caller-provided temp dir."""
    render_stack, render_compose, _ = _ciu_renderers()
    stack_dir = tmp_path / "mattermost-server"
    stack_dir.mkdir()
    shutil.copyfile(DEFAULTS, stack_dir / DEFAULTS.name)
    shutil.copyfile(COMPOSE, stack_dir / COMPOSE.name)

    config = render_stack(
        stack_dir,
        {},
        preserve_state=False,
        write_rendered=False,
        environ=ENVIRON,
    )
    # These are CIU hostdir declarations until the deploy pipeline resolves
    # them to physical paths. Stub the resolved form in the temp tree so the
    # tracked compose template can render without touching live volumes.
    config["mattermost"]["postgres"]["hostdir"]["data"] = str(
        tmp_path / "vol-postgres-data"
    )
    config["mattermost"]["app"]["hostdir"]["config"] = str(
        tmp_path / "vol-mattermost-config"
    )
    config["mattermost"]["app"]["hostdir"]["logs"] = str(
        tmp_path / "vol-mattermost-logs"
    )
    if calls_enabled is not None:
        config["mattermost"]["calls"]["enabled"] = calls_enabled
    if expose_public is not None:
        config["mattermost"]["expose_public"] = expose_public

    import yaml

    rendered = render_compose(
        stack_dir / COMPOSE.name,
        config,
        environ=ENVIRON,
    )
    return yaml.safe_load(rendered)


def service_environment(service: Mapping[str, Any]) -> dict[str, str]:
    """Normalize Compose mapping/list environment syntax to string key/value pairs."""
    raw = service.get("environment", {})
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, list):
        result: dict[str, str] = {}
        for item in raw:
            key, separator, value = str(item).partition("=")
            if separator:
                result[key] = value
        return result
    raise AssertionError(f"unsupported compose environment shape: {raw!r}")


def service_label_keys(service: Mapping[str, Any]) -> set[str]:
    """Normalize Compose mapping/list labels to their key set."""
    raw = service.get("labels", {})
    if isinstance(raw, Mapping):
        return {str(key) for key in raw}
    if isinstance(raw, list):
        return {str(item).partition("=")[0] for item in raw}
    raise AssertionError(f"unsupported compose labels shape: {raw!r}")
