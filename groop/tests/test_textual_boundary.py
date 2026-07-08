from __future__ import annotations

import re
from pathlib import Path


def test_textual_imports_live_only_under_ui_package() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "groop"
    pattern = re.compile(r"^\s*(from textual\b|import textual\b)", re.MULTILINE)
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "ui" in path.relative_to(root).parts:
            continue
        if pattern.search(path.read_text()):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
