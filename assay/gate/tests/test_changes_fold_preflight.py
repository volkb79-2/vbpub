from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_PATH = "gate/tests/test_changes_fold_preflight.py"


def test_changes_fold_preflight_is_registered_in_tooling_tests():
    config = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    argv = config["lanes"]["tooling-tests"]["argv"]

    assert PREFLIGHT_PATH in argv
