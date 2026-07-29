"""S7.7 malformed healthcheck authoring boundary."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg import health  # noqa: E402


@pytest.mark.parametrize("mode", ["CMD", "CMD-SHELL"])
def test_healthcheck_command_requires_a_string_not_an_internal_parser_crash(
    tmp_path: Path, mode: str
) -> None:
    """A malformed rendered healthcheck is an S7.7 authoring error, never a false green."""
    compose = tmp_path / "ciu.compose.yml"
    compose.write_text(
        f"""services:
  api:
    image: example/api:1
    healthcheck:
      test: [{mode}, 42]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"\[S7\.7\].*command.*must be a string"):
        health.extract_healthcheck_tools(compose)
