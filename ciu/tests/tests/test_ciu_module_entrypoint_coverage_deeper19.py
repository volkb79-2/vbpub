"""In-process coverage contract for the ``python -m ciu`` delegation."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli  # noqa: E402


class _Delegated(RuntimeError):
    """Stops the entrypoint after the observable CLI delegation."""


def test_module_entrypoint_delegates_once_in_the_coverage_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executable module imports and invokes CIU's CLI exactly once."""
    calls: list[str] = []

    def sentinel_main() -> None:
        calls.append("main")
        raise _Delegated()

    monkeypatch.setattr(cli, "main", sentinel_main)

    with pytest.raises(_Delegated):
        runpy.run_module("ciu", run_name="__main__")

    assert calls == ["main"]
