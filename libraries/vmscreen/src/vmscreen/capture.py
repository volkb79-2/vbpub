"""ocr-pair: a screenshot and its exact text from the same instant (the guest is paused while both are taken)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import sources, textmode
from .errors import NoExactText

SCHEMA = "vmscreen.ocr-pair/1"


def build_sidecar(res: sources.TextResult | None, png: bytes, png_name: str, paused: bool, reason: str | None,
                  tried: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    w, h = textmode.png_size(png)
    side: dict[str, Any] = {
        "schema": SCHEMA, "png": png_name, "sha256": hashlib.sha256(png).hexdigest(), "width": w, "height": h,
        "captured_at": (now or datetime.now(timezone.utc)).isoformat(), "paused_guest": paused,
        "exact": res is not None, "source": res.source if res else None, "tried": tried,
    }
    if res is None:
        side.update(text=None, reason=reason)
        return side
    side.update(text=res.text, lines=res.lines, geometry=res.geometry)
    if res.screen is not None:  # lossless ground truth for glyph-grid work: per-row hex of char bytes and attribute bytes
        buf = res.buffer
        side["char_rows_hex"] = [buf[r * res.screen.cols * 2:(r + 1) * res.screen.cols * 2:2].hex() for r in range(res.screen.rows)]
        side["attr_rows_hex"] = [buf[r * res.screen.cols * 2 + 1:(r + 1) * res.screen.cols * 2:2].hex() for r in range(res.screen.rows)]
        side["cells"] = [[[c.fg, c.bg, c.blink] for c in row] for row in res.screen.cells]
    return side


def ocr_pair(client, out_prefix: Path, *, dump_dir: str | None, blink_mode: bool = True, pause: bool = True,
             allow_inexact: bool = False) -> dict[str, Any]:
    """Write ``<prefix>.png`` and ``<prefix>.json``.  Exact text is the VGA text buffer only (the screen, not a
    serial stream).  Without it exit 4 (NoExactText) unless ``allow_inexact`` (then a sidecar with exact=false)."""
    was_running = pause and client.execute("query-status").get("status") == "running"
    if was_running:
        client.execute("stop")
    try:
        res: sources.TextResult | None = None
        reason, tried = None, []
        try:
            res = sources.read_vga(client, dump_dir, blink_mode)
            png = res.png
            tried = res.tried
        except NoExactText as exc:
            if not allow_inexact:
                raise
            reason, tried = str(exc), exc.tried
            png = sources.screendump_png(client, dump_dir)
    finally:
        if was_running:
            client.execute("cont")
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    png_path = out_prefix.with_suffix(".png")
    json_path = out_prefix.with_suffix(".json")
    png_path.write_bytes(png)
    side = build_sidecar(res, png, png_path.name, was_running, reason, tried)
    json_path.write_text(json.dumps(side, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"png": str(png_path), "json": str(json_path), "exact": side["exact"], "source": side["source"]}
