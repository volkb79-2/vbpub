#!/usr/bin/env python3
"""Generate the stored event/1 fixture corpus in ``fixtures/event/``.

The event frames are stored records, not socket frames, so this generator has
its own output directory. The fixture descriptions and bytes come from the
independent reference codec and the event cases authored in ``make_fixtures``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "fixtures" / "event"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_fixtures  # noqa: E402


def generate(out: Path) -> list[tuple[str, int, str | None]]:
    """Write every event fixture into ``out`` and return its case summary."""
    out.mkdir(parents=True, exist_ok=True)
    return make_fixtures.write_event_fixtures(out)


def main() -> int:
    written = generate(OUT)
    for name, size, refusal in written:
        detail = "refusal" if refusal else "valid"
        print(f"{name}.bin: {size} bytes ({detail})")
    print(f"wrote {len(written)} event fixtures into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
