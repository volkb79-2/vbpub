"""The Python version of a release is a top-level input: the environment overrides the bake default."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_push = _load("mdt_build_push_py", ROOT / "build-push.py")
resolver = _load("mdt_resolver_py", ROOT / "scripts" / "resolve-devcontainers-release.py")


def test_bake_default_is_used_without_override(monkeypatch):
    monkeypatch.delenv("LATEST_KNOWN_PYTHON", raising=False)
    assert build_push._read_bake_default("LATEST_KNOWN_PYTHON") == resolver._bake_default("LATEST_KNOWN_PYTHON")


def test_environment_overrides_python_for_both_readers(monkeypatch):
    monkeypatch.setenv("LATEST_KNOWN_PYTHON", "3.15")
    assert build_push._read_bake_default("LATEST_KNOWN_PYTHON") == "3.15"
    assert resolver._bake_default("LATEST_KNOWN_PYTHON") == "3.15"
    pinned, dev = resolver.base_images_from_bake()
    assert pinned.endswith(":3.15-" + resolver._bake_default("LATEST_KNOWN_DEBIAN"))
    assert dev.startswith("mcr.microsoft.com/devcontainers/python:dev-3.15-")


def test_blank_override_falls_back_to_the_bake_default(monkeypatch):
    monkeypatch.setenv("LATEST_KNOWN_PYTHON", "  ")
    assert build_push._read_bake_default("LATEST_KNOWN_PYTHON") == "3.14"
