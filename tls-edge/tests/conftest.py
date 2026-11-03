"""Shared helpers for the tls-edge tests (no network, no live start)."""
from __future__ import annotations

import copy
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_renderer():
    spec = importlib.util.spec_from_file_location(
        "tls_edge_render_standalone", ROOT / "scripts" / "render_standalone.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    RENDER = _load_renderer()
except SystemExit:  # jinja2 missing in this interpreter
    RENDER = None

# Under the gate lane (run-gate.toml sets TLS_EDGE_REQUIRE_TOOLS=1) a missing tool is a FAILURE,
# never a green skip: the tests then run and fail on the missing jinja2 / docker.
REQUIRE_TOOLS = os.environ.get("TLS_EDGE_REQUIRE_TOOLS") == "1"

needs_jinja = pytest.mark.skipif(
    RENDER is None and not REQUIRE_TOOLS, reason="jinja2 is not installed"
)
needs_docker = pytest.mark.skipif(
    shutil.which("docker") is None and not REQUIRE_TOOLS, reason="docker CLI is not installed"
)


def defaults_config() -> dict:
    return copy.deepcopy(RENDER.load_config(ROOT / "ciu-stack", defaults_only=True))


def merge(cfg: dict, override: dict) -> dict:
    return RENDER.deep_merge(cfg, override)


def render_to(tmp: Path, cfg: dict) -> Path:
    """Render the standalone artifacts for `cfg` into tmp and return it."""
    cfg = copy.deepcopy(cfg)
    cfg["standalone"] = True
    cfg["base_dir"] = "."
    RENDER.validate(cfg)
    RENDER.render_outputs(ROOT / "ciu-stack", tmp, cfg, certs_only=False)
    return tmp


def compose_config(compose: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        ["docker", "compose", *args, "-f", str(compose), "config", "--format", "json"],
        capture_output=True, text=True, env=full_env, cwd=compose.parent,
    )
