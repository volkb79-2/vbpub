"""Coverage for invalid structured returns through the public hooks API."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.hooks_runner import HookContext, run_hooks  # noqa: E402


def _run_hook(tmp_path: Path, return_value: str) -> ValueError:
    hook = tmp_path / "hook.py"
    hook.write_text(
        textwrap.dedent(
            f"""
            def run(config, ctx):
                return {return_value}
            """
        )
    )
    ctx = HookContext(
        point="pre_compose",
        stack_dir=tmp_path,
        repo_root=tmp_path,
        secret_file=lambda name: tmp_path / ".ciu" / "secrets" / name,
    )
    with pytest.raises(ValueError) as exc_info:
        run_hooks([str(hook)], "pre_compose", {}, ctx, tmp_path / "ciu.toml")
    return exc_info.value


def test_non_dict_hook_return_is_typed_contract_error(tmp_path: Path) -> None:
    error = _run_hook(tmp_path, "['unexpected']")

    assert "[S9.4]" in str(error)
    assert "expected dict or None" in str(error)


def test_hook_entry_missing_value_is_typed_contract_error(tmp_path: Path) -> None:
    error = _run_hook(tmp_path, "{'result': {'apply_to_config': True}}")

    assert "[S9.4]" in str(error)
    assert "missing 'value'" in str(error)
