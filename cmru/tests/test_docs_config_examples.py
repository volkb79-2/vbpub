"""The adopter-facing CMRU config pair remains loadable by the shipped reader."""
from __future__ import annotations

import re
from pathlib import Path

from cmru.config import load_forge_config


def test_consumers_central_config_example_is_complete_and_loadable(tmp_path: Path):
    document = (
        Path(__file__).resolve().parents[1] / "docs" / "CONSUMERS.md"
    ).read_text(encoding="utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", document, flags=re.DOTALL)
    assert len(blocks) >= 2

    (tmp_path / "example-wheel").mkdir()
    (tmp_path / "example-wheel" / "cmru.toml").write_text(blocks[0], encoding="utf-8")
    central = tmp_path / "cmru.orchestration.toml"
    central.write_text(blocks[1], encoding="utf-8")

    config = load_forge_config(central, require_orchestration=True)
    assert config.orchestration is not None
    assert config.orchestration.execution_mode == "project-first"
    assert config.cleanup is not None
    assert config.projects["example-wheel"].name == "example-wheel"
