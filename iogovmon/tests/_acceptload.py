"""Load the acceptance tooling scripts (`tools/accept/*.py`, not a package) by path under a private module name, so a tool that happens to share a
name with another importable module is never picked up instead, and the tests do not depend on sys.path order."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ACCEPT_DIR = Path(__file__).resolve().parents[1] / "tools" / "accept"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "accept"


def load(filename: str):
    name = "accept_tool_" + filename.removesuffix(".py").replace("-", "_")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ACCEPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses / pickling inside a tool look the module up here
    spec.loader.exec_module(module)
    return module
