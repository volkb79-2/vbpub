from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def fixture_root() -> Path:
    return ROOT / "tests" / "fixtures"


def fixture_frame():
    import json

    from groop.model import frame_from_jsonable

    payload = json.loads((fixture_root() / "frames" / "gstammtisch-once.jsonl").read_text())
    payload.pop("type", None)
    return frame_from_jsonable(payload)


def systemctl_fixture_runner(name: str):
    from groop.drift.origin import ShowResult

    base = fixture_root() / "systemctl" / name

    def _runner(unit: str, _properties: tuple[str, ...]) -> ShowResult:
        path = base / f"{unit}.show"
        if not path.exists():
            return ShowResult("", stderr=f"Unit {unit} not found", returncode=1)
        return ShowResult(path.read_text(), returncode=0)

    return _runner
