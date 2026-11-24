"""No-Docker ciu render helpers for behavioural renderer oracles."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tomli_w
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = next(
    parent for parent in (ROOT, *ROOT.parents) if (parent / "ciu/src/ciu").is_dir()
)
CIU_SOURCE = REPO_ROOT / "ciu/src"
ENVIRON = {
    "CONTAINER_UID": "1000",
    "DOCKER_GID": "1000",
    "DOCKER_NETWORK_INTERNAL": "edge-oracle-internal",
    "INSTANCE_ID": "edge-oracle",
}


def _ciu_renderers():
    source = str(CIU_SOURCE)
    if source not in sys.path:
        sys.path.insert(0, source)

    from ciu import composefile, config_model
    from ciu.secrets.directives import discover

    return config_model, composefile, discover


def copy_tracked_stack(destination: Path) -> Path:
    """Copy only tracked ciu-stack files, never the ignored site override."""
    stack = destination / "ciu-stack"
    stack.mkdir(parents=True)
    listing = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "-z",
            "--cached",
            "--",
            "tls-edge/ciu-stack",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    prefix = Path("tls-edge/ciu-stack")
    for name in listing.stdout.split("\0"):
        if not name:
            continue
        relative = Path(name).relative_to(prefix)
        target = stack / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / name, target)
    return stack


def render_ciu_case(
    destination: Path,
    override: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Render a tracked stack copy through ciu's config and compose libraries."""
    config_model, composefile, discover = _ciu_renderers()
    stack = copy_tracked_stack(destination)
    if override is not None:
        (stack / "ciu.toml.j2").write_text(
            tomli_w.dumps({"tls_edge": dict(override)}),
            encoding="utf-8",
        )

    stack_config = config_model.render_stack(
        stack,
        {},
        preserve_state=False,
        ciu_context={"selected_profiles": [], "deployed_stacks": []},
        write_rendered=False,
        environ=ENVIRON,
    )
    root_key = config_model.validate_stack_shape(stack_config)
    guarded = composefile.guard_config(
        stack_config, discover(root_key, stack_config)
    )
    rendered = composefile.render_compose(
        stack / "ciu.compose.yml.j2",
        guarded,
        ciu_context={"selected_profiles": [], "deployed_stacks": []},
        environ=ENVIRON,
    )
    return stack, stack_config, yaml.safe_load(rendered)


def render_ciu_configfiles(
    stack: Path,
    stack_config: Mapping[str, Any],
):
    """Render the stack's declared configfiles through ciu's production library."""
    config_model, composefile, _ = _ciu_renderers()
    root_key = config_model.validate_stack_shape(dict(stack_config))

    def _missing_secret(name: str) -> str:
        raise AssertionError(f"oracle case unexpectedly requested secret {name!r}")

    return composefile.render_configfiles(
        stack,
        root_key,
        dict(stack_config),
        _missing_secret,
        ciu_context={"selected_profiles": [], "deployed_stacks": []},
    )
