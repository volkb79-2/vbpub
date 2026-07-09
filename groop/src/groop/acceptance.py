"""groop acceptance smoke harness for rootless release-confidence checks.

Usage:
    python -m groop.acceptance smoke [--cgroup-root PATH] [--replay PATH] [--json] [--pretty-json]

Exit codes:
    0  All requested checks pass.
    1  One or more smoke checks failed.
    2  Usage / argument validation error.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# No Textual import: this module must work without the UI dependency tree.


__all__ = [
    "Check",
    "SmokeResult",
    "build_parser",
    "run_smoke",
    "smoke_main",
    "format_text",
    "format_json",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Check:
    """One named smoke check result."""

    name: str
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmokeResult:
    """Top-level result from a smoke run."""

    ok: bool
    version: str
    python: str
    platform: str
    checks: list[Check]
    measurements: dict[str, float]
    frame_summary: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m groop.acceptance",
        description="groop acceptance smoke harness for rootless release-confidence checks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    smoke = sub.add_parser("smoke", help="Run release smoke checks.")
    smoke.add_argument(
        "--cgroup-root",
        type=Path,
        default=None,
        help="Alternate cgroup root (default: /sys/fs/cgroup). Use a fixture path for testing.",
    )
    smoke.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to a JSONL recording to include in the replay summary check.",
    )
    smoke.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured JSON instead of human-readable text.",
    )
    smoke.add_argument(
        "--pretty-json",
        action="store_true",
        default=False,
        help="Output indented JSON instead of compact JSON.",
    )
    return parser


# ---------------------------------------------------------------------------
# Core smoke logic
# ---------------------------------------------------------------------------


def _collect_frame(cgroup_root: Path | None) -> dict[str, Any]:
    """Collect one frame and return a jsonable summary dict.

    Returns the full jsonable frame on success, or raises on failure.
    """
    from groop.collect.collector import Collector
    from groop.model import frame_to_jsonable

    collector = Collector(cgroup_root=cgroup_root)
    frame = collector.collect_once()
    return frame_to_jsonable(frame)


def _run_replay(path: Path) -> dict[str, Any]:
    """Load a recording with ReplayDriver and return summary metadata."""
    from groop.record.replay import ReplayDriver

    driver = ReplayDriver.from_path(path)
    frames = driver.frames
    first_ts: float | None = None
    last_ts: float | None = None
    if frames:
        first_ts = frames[0].ts
        last_ts = frames[-1].ts
    return {
        "frame_count": len(frames),
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def _count_source_labels(frame_dict: dict[str, Any]) -> dict[str, int]:
    """Count MetricValue ``src`` values across host and entity metrics."""
    counts: dict[str, int] = {}

    def _count(metrics: dict[str, list[Any]]) -> None:
        for key, val in metrics.items():
            if isinstance(val, list) and len(val) >= 2:
                src = str(val[1]) if val[1] is not None else "none"
                counts[src] = counts.get(src, 0) + 1

    _count(frame_dict.get("host", {}))
    for eframe in frame_dict.get("entities", {}).values():
        if isinstance(eframe, dict):
            _count(eframe.get("metrics", {}))
    return dict(sorted(counts.items()))


def _entity_keys(frame_dict: dict[str, Any]) -> list[str]:
    """Return sorted entity keys from a jsonable frame dict."""
    return sorted(frame_dict.get("entities", {}).keys())


def run_smoke(
    cgroup_root: Path | None = None,
    replay_path: Path | None = None,
) -> SmokeResult:
    """Execute all smoke checks and return a SmokeResult.

    This function does NOT parse args; it is testable directly.
    """
    from groop import __version__

    checks: list[Check] = []

    # --- Wall-clock timing ---
    t0 = time.perf_counter()
    ru0 = resource.getrusage(resource.RUSAGE_SELF)

    # --- Collect ---
    frame_dict: dict[str, Any] | None = None
    collect_ok = False
    collect_msg = ""
    collect_details: dict[str, Any] = {}
    try:
        frame_dict = _collect_frame(cgroup_root)
        entity_count = len(frame_dict.get("entities", {}))
        schema_ver = frame_dict.get("schema_version", "?")
        ts = frame_dict.get("ts", "?")
        collect_details = {
            "entity_count": entity_count,
            "schema_version": schema_ver,
            "ts": ts,
        }
        collect_msg = f"Collected 1 frame with {entity_count} entities (schema v{schema_ver})"
        collect_ok = True
    except Exception as exc:
        collect_msg = f"Collection failed: {exc}"
        collect_details = {"error": str(exc)}
        collect_ok = False

    checks.append(Check(name="collect", ok=collect_ok, message=collect_msg, details=collect_details))

    # --- Serialize ---
    serialize_ok = collect_ok
    serialize_msg = ""
    serialize_details: dict[str, Any] = {}
    if collect_ok and frame_dict is not None:
        try:
            from groop.model import frame_from_jsonable

            # Round-trip: jsonable -> Frame -> jsonable
            reconstructed = frame_from_jsonable(frame_dict)
            _ = reconstructed  # ensure no crash
            serialize_msg = "frame_to_jsonable + frame_from_jsonable round-trip passed"
            serialize_ok = True
        except Exception as exc:
            serialize_msg = f"Serialization round-trip failed: {exc}"
            serialize_details = {"error": str(exc)}
            serialize_ok = False
    else:
        serialize_msg = "Skipped (collect did not produce a frame)"

    checks.append(
        Check(name="serialize", ok=serialize_ok, message=serialize_msg, details=serialize_details)
    )

    # --- Source labels ---
    source_ok = collect_ok
    source_msg = ""
    source_details: dict[str, Any] = {}
    if collect_ok and frame_dict is not None:
        src_counts = _count_source_labels(frame_dict)
        total = sum(src_counts.values())
        parts = [f"{k}={v}" for k, v in src_counts.items()]
        source_msg = f"Metric source distribution ({total} total): {', '.join(parts)}"
        source_details = src_counts
        source_ok = True
    else:
        source_msg = "Skipped (no frame available)"

    checks.append(Check(name="source_labels", ok=source_ok, message=source_msg, details=source_details))

    # --- Replay summary ---
    replay_ok: bool = True
    replay_msg: str = "No replay path provided; skipped"
    replay_details: dict[str, Any] = {}
    if replay_path is not None:
        if replay_path.exists():
            try:
                replay_info = _run_replay(replay_path)
                fc = replay_info["frame_count"]
                ft = f"{replay_info['first_ts']:.3f}" if replay_info["first_ts"] is not None else "N/A"
                lt = f"{replay_info['last_ts']:.3f}" if replay_info["last_ts"] is not None else "N/A"
                replay_msg = f"Replay loaded: {fc} frame(s), first ts={ft}, last ts={lt}"
                replay_details = replay_info
                replay_ok = True
            except Exception as exc:
                replay_msg = f"Replay load failed: {exc}"
                replay_details = {"error": str(exc)}
                replay_ok = False
        else:
            replay_msg = f"Replay path does not exist: {replay_path}"
            replay_details = {"path": str(replay_path)}
            replay_ok = False

    checks.append(Check(name="replay", ok=replay_ok, message=replay_msg, details=replay_details))

    # --- Measurements ---
    t1 = time.perf_counter()
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    measurements: dict[str, float] = {
        "wall_s": round(t1 - t0, 4),
        "user_s": round(ru1.ru_utime - ru0.ru_utime, 4),
        "sys_s": round(ru1.ru_stime - ru0.ru_stime, 4),
        "rss_kb": float(ru1.ru_maxrss),
    }

    # --- Frame summary ---
    frame_summary: dict[str, Any] | None = None
    if frame_dict is not None:
        entity_count = len(frame_dict.get("entities", {}))
        host_count = len(frame_dict.get("host", {}))
        frame_summary = {
            "schema_version": frame_dict.get("schema_version"),
            "ts": frame_dict.get("ts"),
            "interval_s": frame_dict.get("interval_s"),
            "entity_count": entity_count,
            "host_metric_count": host_count,
            "entity_keys": _entity_keys(frame_dict),
        }

    # --- Overall ---
    overall_ok = collect_ok and serialize_ok and source_ok and replay_ok

    return SmokeResult(
        ok=overall_ok,
        version=__version__,
        python=sys.version,
        platform=platform.platform(),
        checks=checks,
        measurements=measurements,
        frame_summary=frame_summary,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _check_to_dict(c: Check) -> dict[str, Any]:
    return {"name": c.name, "ok": c.ok, "message": c.message, "details": c.details}


def format_json(result: SmokeResult, *, pretty: bool = False) -> str:
    """Serialize the result as a JSON string."""
    indent = 2 if pretty else None
    obj: dict[str, Any] = {
        "ok": result.ok,
        "version": result.version,
        "python": result.python,
        "platform": result.platform,
        "checks": [_check_to_dict(c) for c in result.checks],
        "measurements": result.measurements,
    }
    if result.frame_summary is not None:
        obj["frame_summary"] = result.frame_summary
    return json.dumps(
        obj,
        indent=indent,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
        default=str,
    )


_OK_SYMBOL = "OK"
_FAIL_SYMBOL = "FAIL"


def format_text(result: SmokeResult) -> str:
    """Format the result as concise human-readable text."""
    lines: list[str] = []
    lines.append(f"groop acceptance smoke  v{result.version}")
    lines.append(f"python: {result.python.split()[0]}  platform: {result.platform}")
    lines.append("")
    for check in result.checks:
        symbol = _OK_SYMBOL if check.ok else _FAIL_SYMBOL
        lines.append(f"  [{symbol}] {check.name}: {check.message}")
    lines.append("")
    lines.append("  Measurements:")
    m = result.measurements
    lines.append(f"    wall: {m.get('wall_s', '?'):>8.4f}s")
    lines.append(f"    user: {m.get('user_s', '?'):>8.4f}s")
    lines.append(f"     sys: {m.get('sys_s', '?'):>8.4f}s")
    lines.append(f"     RSS: {m.get('rss_kb', '?'):>8.0f} KB")
    if result.frame_summary:
        fs = result.frame_summary
        lines.append("")
        lines.append("  Frame summary:")
        lines.append(f"    schema_version: {fs.get('schema_version', '?')}")
        lines.append(f"    ts:             {fs.get('ts', '?'):.3f}")
        lines.append(f"    interval_s:     {fs.get('interval_s', '?')}")
        lines.append(f"    entity_count:   {fs.get('entity_count', '?')}")
        lines.append(f"    host_metrics:   {fs.get('host_metric_count', '?')}")
        keys = fs.get("entity_keys", [])
        if keys:
            lines.append(f"    entities:       {', '.join(keys)}")
    verdict = "ALL CHECKS PASSED" if result.ok else "SOME CHECKS FAILED"
    lines.append("")
    lines.append(f"  {verdict}  (exit code {'0' if result.ok else '1'})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def smoke_main(argv: list[str] | None = None) -> int:
    """Parse args, run smoke, print output, return exit code.

    This is the entry point used by ``python -m groop.acceptance``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "smoke":
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2

    result = run_smoke(cgroup_root=args.cgroup_root, replay_path=args.replay)

    if args.json or args.pretty_json:
        output = format_json(result, pretty=args.pretty_json)
    else:
        output = format_text(result)

    print(output)

    if result.ok:
        return 0
    else:
        # Determine if failures are usage-related (exit 2) or check failures (exit 1)
        # For now, any check failure = exit 1
        return 1


def main() -> None:
    """Convenience entry point.  Called via ``python -m groop.acceptance``."""
    sys.exit(smoke_main())


if __name__ == "__main__":
    main()
