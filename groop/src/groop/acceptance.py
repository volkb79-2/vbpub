"""groop acceptance smoke harness for rootless release-confidence checks.

Usage:
    python -m groop.acceptance smoke [--cgroup-root PATH] [--replay PATH] [--json] [--pretty-json]
    python -m groop.acceptance steady [--cgroup-root PATH] [--samples N] [--interval-s SECONDS]
                         [--max-cpu-pct FLOAT] [--max-rss-kb INT] [--json] [--pretty-json]
    python -m groop.acceptance tui-smoke [--replay PATH] [--config PATH] [--profile NAME]
                               [--timeout-s FLOAT] [--json] [--pretty-json]
    python -m groop.acceptance mcp-smoke [--socket PATH] [--timeout-s FLOAT]
                                [--json] [--pretty-json]

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
import subprocess
import sys
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path
from typing import Any

# No Textual import: this module must work without the UI dependency tree.


__all__ = [
    "Check",
    "SmokeResult",
    "SteadySample",
    "SteadyResult",
    "TuiSmokeResult",
    "build_parser",
    "run_smoke",
    "run_steady",
    "run_tui_smoke",
    "smoke_main",
    "acceptance_main",
    "format_text",
    "format_json",
    "format_steady_text",
    "format_steady_json",
    "format_tui_smoke_text",
    "format_tui_smoke_json",
    "McpSmokeResult",
    "run_mcp_smoke",
    "format_mcp_smoke_text",
    "format_mcp_smoke_json",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Check:
    """One named check result."""

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


@dataclass
class SteadySample:
    """Result of one sample in a steady-state run."""

    index: int
    wall_s: float
    entity_count: int


@dataclass
class SteadyResult:
    """Top-level result from a steady-state collector run."""

    ok: bool
    version: str
    python: str
    platform: str
    samples_requested: int
    samples_completed: int
    measurements: dict[str, float]
    entity_counts: dict[str, int]
    collection_errors: list[str]
    threshold_errors: list[str]


@dataclass
class TuiSmokeResult:
    """Top-level result from a TUI smoke run."""

    ok: bool
    exit_code: int
    version: str
    python: str
    platform: str
    smoke_line: str | None
    stdout_snippet: str
    stderr_snippet: str
    frames: int | None
    view: str | None
    profile: str | None
    measurements: dict[str, float]


# Default replay path for TUI smoke (repository checkout relative)
_DEFAULT_REPLAY = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "frames" / "gstammtisch-once.jsonl"

# MCP smoke defaults
_MCP_SMOKE_TIMEOUT_S = 30.0
_MCP_SMOKE_TOOLS = {"groop_health", "groop_overview", "groop_entity", "groop_history"}
# Call order is load-bearing, not cosmetic: groop_entity and groop_history need
# the entity key that groop_overview discovers, so iterating the set above (whose
# order varies with PYTHONHASHSEED) would call them with an empty selector in
# most interpreters.
_MCP_SMOKE_CALL_ORDER = ("groop_health", "groop_overview", "groop_entity", "groop_history")


@dataclass
class McpSmokeResult:
    """Top-level result from an MCP smoke run."""

    ok: bool
    version: str
    python: str
    platform: str
    extra_installed: bool
    checks: list[Check]
    max_response_bytes: int | None
    measurements: dict[str, float]


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

    steady = sub.add_parser("steady", help="Run steady-state collector loop for release confidence.")
    steady.add_argument(
        "--cgroup-root",
        type=Path,
        default=None,
        help="Alternate cgroup root (default: /sys/fs/cgroup). Use a fixture path for testing.",
    )
    steady.add_argument(
        "--samples",
        type=int,
        default=60,
        help="Number of frames to collect (default: 60).",
    )
    steady.add_argument(
        "--interval-s",
        type=float,
        default=5.0,
        help="Seconds between samples (default: 5.0).",
    )
    steady.add_argument(
        "--max-cpu-pct",
        type=float,
        default=None,
        help="Fail if measured CPU percent exceeds this threshold.",
    )
    steady.add_argument(
        "--max-rss-kb",
        type=int,
        default=None,
        help="Fail if measured max RSS (KB) exceeds this threshold.",
    )
    steady.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured JSON instead of human-readable text.",
    )
    steady.add_argument(
        "--pretty-json",
        action="store_true",
        default=False,
        help="Output indented JSON instead of compact JSON.",
    )

    tui = sub.add_parser("tui-smoke", help="Run TUI smoke evidence via subprocess (--ui-smoke path).")
    tui.add_argument(
        "--replay",
        type=Path,
        default=_DEFAULT_REPLAY,
        metavar="PATH",
        help=f"Replay path for UI smoke (default: {_DEFAULT_REPLAY}).",
    )
    tui.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Pass --config PATH through to the UI smoke child process.",
    )
    tui.add_argument(
        "--profile",
        type=str,
        default=None,
        metavar="NAME",
        help="Pass --profile NAME through to the UI smoke child process.",
    )
    tui.add_argument(
        "--timeout-s",
        type=float,
        default=30.0,
        help="Seconds before child process is killed (default: 30.0).",
    )
    tui.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured JSON instead of human-readable text.",
    )
    tui.add_argument(
        "--pretty-json",
        action="store_true",
        default=False,
        help="Output indented JSON instead of compact JSON.",
    )

    mcp = sub.add_parser("mcp-smoke", help="Run MCP live-daemon acceptance checks.")
    mcp.add_argument(
        "--socket",
        type=Path,
        default=None,
        metavar="PATH",
        help="Daemon socket path for MCP smoke (default: temp socket).",
    )
    mcp.add_argument(
        "--timeout-s",
        type=float,
        default=_MCP_SMOKE_TIMEOUT_S,
        help=f"Seconds before daemon start times out (default: {_MCP_SMOKE_TIMEOUT_S}).",
    )
    mcp.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured JSON instead of human-readable text.",
    )
    mcp.add_argument(
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
# Core steady logic
# ---------------------------------------------------------------------------


def run_steady(
    cgroup_root: Path | None = None,
    samples: int = 60,
    interval_s: float = 5.0,
    max_cpu_pct: float | None = None,
    max_rss_kb: int | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _perf_counter: Callable[[], float] = time.perf_counter,
    _collect: Callable[[Path | None], dict[str, Any]] = _collect_frame,
) -> SteadyResult:
    """Run a steady-state collector loop and return a SteadyResult.

    Parameters
    ----------
    cgroup_root : Path or None
        Alternate cgroup root for testing.
    samples : int
        Number of frames to collect.
    interval_s : float
        Seconds to sleep between samples.
    max_cpu_pct : float or None
        Optional CPU percent threshold.
    max_rss_kb : int or None
        Optional RSS threshold (KB).
    _sleep : callable, optional
        Sleep function (injectable for tests).  Default ``time.sleep``.
    _perf_counter : callable, optional
        High-resolution timer (injectable for tests). Default ``time.perf_counter``.
    """
    from groop import __version__

    threshold_errors: list[str] = []
    collection_errors: list[str] = []
    sample_records: list[SteadySample] = []
    t0 = _perf_counter()
    ru0 = resource.getrusage(resource.RUSAGE_SELF)

    for i in range(samples):
        sample_t0 = _perf_counter()
        try:
            frame_dict = _collect(cgroup_root)
            entity_count = len(frame_dict.get("entities", {}))
        except Exception as exc:
            entity_count = 0
            collection_errors.append(f"sample {i + 1}: {exc}")
        sample_wall = _perf_counter() - sample_t0
        sample_records.append(SteadySample(index=i, wall_s=sample_wall, entity_count=entity_count))

        if i < samples - 1 and interval_s > 0:
            _sleep(interval_s)

    t1 = _perf_counter()
    ru1 = resource.getrusage(resource.RUSAGE_SELF)

    wall_s = t1 - t0
    user_s = ru1.ru_utime - ru0.ru_utime
    sys_s = ru1.ru_stime - ru0.ru_stime
    rss_kb = float(ru1.ru_maxrss)

    cpu_pct = 0.0
    if wall_s > 0:
        cpu_pct = ((user_s + sys_s) / wall_s) * 100.0

    avg_sample_wall = (
        sum(s.wall_s for s in sample_records) / len(sample_records) if sample_records else 0.0
    )

    entity_counts_all = [s.entity_count for s in sample_records if s.entity_count > 0]
    ec_min = min(entity_counts_all) if entity_counts_all else 0
    ec_max = max(entity_counts_all) if entity_counts_all else 0
    ec_last = entity_counts_all[-1] if entity_counts_all else 0

    samples_completed = len(sample_records) - len(collection_errors)

    measurements: dict[str, float] = {
        "wall_s": round(wall_s, 4),
        "user_s": round(user_s, 4),
        "sys_s": round(sys_s, 4),
        "rss_kb": rss_kb,
        "avg_sample_wall_s": round(avg_sample_wall, 4),
        "cpu_pct": round(cpu_pct, 2),
    }

    entity_counts: dict[str, int] = {
        "min": ec_min,
        "max": ec_max,
        "last": ec_last,
    }

    collection_ok = samples_completed == samples and not collection_errors

    cpu_ok = True
    if max_cpu_pct is not None and cpu_pct > max_cpu_pct:
        threshold_errors.append(
            f"CPU percent {cpu_pct:.2f}% exceeds threshold {max_cpu_pct:.2f}%"
        )
        cpu_ok = False

    rss_ok = True
    if max_rss_kb is not None and rss_kb > max_rss_kb:
        threshold_errors.append(
            f"RSS {rss_kb:.0f} KB exceeds threshold {max_rss_kb} KB"
        )
        rss_ok = False

    overall_ok = collection_ok and cpu_ok and rss_ok

    return SteadyResult(
        ok=overall_ok,
        version=__version__,
        python=sys.version,
        platform=platform.platform(),
        samples_requested=samples,
        samples_completed=samples_completed,
        measurements=measurements,
        entity_counts=entity_counts,
        collection_errors=collection_errors,
        threshold_errors=threshold_errors,
    )


# ---------------------------------------------------------------------------
# Core TUI smoke logic
# ---------------------------------------------------------------------------


def _parse_ui_smoke_line(line: str) -> dict[str, Any]:
    """Parse a "ui smoke ok ..." line into structured fields.

    Returns a dict with keys: frames, view, profile (or empty dict if unparseable).
    """
    result: dict[str, Any] = {}
    text = line.strip()
    if not text.startswith("ui smoke ok"):
        return result
    for part in text.split():
        if "=" in part:
            key, val = part.split("=", 1)
            if key == "frames":
                try:
                    result["frames"] = int(val)
                except ValueError:
                    pass
            elif key in ("view", "profile"):
                result[key] = val
    return result


def run_tui_smoke(
    replay_path: Path,
    *,
    config_path: Path | None = None,
    profile: str | None = None,
    timeout_s: float = 30.0,
) -> TuiSmokeResult:
    """Run the UI smoke child process and return a TuiSmokeResult.

    This function uses ``subprocess`` to invoke the CLI with ``--ui-smoke``,
    preserving the import contract: no Textual import in this module.
    """
    from groop import __version__

    cmd = [
        sys.executable, "-m", "groop.cli",
        "--replay", str(replay_path),
        "--step",
        "--ui-smoke",
    ]
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    if profile is not None:
        cmd.extend(["--profile", profile])

    # Capture child resource usage via RUSAGE_CHILDREN diff
    ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}

    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        t1 = time.monotonic()
        ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        wall_s = t1 - t0
        child_user_s = ru1.ru_utime - ru0.ru_utime
        child_sys_s = ru1.ru_stime - ru0.ru_stime
        child_rss_kb = float(ru1.ru_maxrss)
        return TuiSmokeResult(
            ok=False,
            exit_code=-1,
            version=__version__,
            python=sys.version,
            platform=platform.platform(),
            smoke_line=None,
            stdout_snippet="",
            stderr_snippet="(timeout)",
            frames=None,
            view=None,
            profile=None,
            measurements={
                "wall_s": round(wall_s, 4),
                "user_s": round(child_user_s, 4),
                "sys_s": round(child_sys_s, 4),
                "rss_kb": child_rss_kb,
            },
        )

    t1 = time.monotonic()
    ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)

    wall_s = t1 - t0
    child_user_s = ru1.ru_utime - ru0.ru_utime
    child_sys_s = ru1.ru_stime - ru0.ru_stime
    child_rss_kb = float(ru1.ru_maxrss)

    measurements: dict[str, float] = {
        "wall_s": round(wall_s, 4),
        "user_s": round(child_user_s, 4),
        "sys_s": round(child_sys_s, 4),
        "rss_kb": child_rss_kb,
    }

    exit_code = cp.returncode

    stdout_lines = cp.stdout.strip().split("\n")
    # Find the ui smoke line
    smoke_line: str | None = None
    for line in stdout_lines:
        if line.startswith("ui smoke ok"):
            smoke_line = line
            break

    parsed = _parse_ui_smoke_line(smoke_line) if smoke_line else {}

    frames: int | None = parsed.get("frames")
    view: str | None = parsed.get("view")
    prof: str | None = parsed.get("profile")

    # Truncate snippets for JSON output
    stdout_snippet = cp.stdout[:500] if len(cp.stdout) > 500 else cp.stdout
    stderr_snippet = cp.stderr[:500] if len(cp.stderr) > 500 else cp.stderr

    # Determine overall success
    ok = exit_code == 0 and smoke_line is not None

    return TuiSmokeResult(
        ok=ok,
        exit_code=exit_code,
        version=__version__,
        python=sys.version,
        platform=platform.platform(),
        smoke_line=smoke_line,
        stdout_snippet=stdout_snippet,
        stderr_snippet=stderr_snippet,
        frames=frames,
        view=view,
        profile=prof,
        measurements=measurements,
    )


# ---------------------------------------------------------------------------
# TUI smoke output formatting
# ---------------------------------------------------------------------------


def format_tui_smoke_json(result: TuiSmokeResult, *, pretty: bool = False) -> str:
    """Serialize a TUI smoke result as deterministic JSON."""
    indent = 2 if pretty else None
    obj: dict[str, Any] = {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "version": result.version,
        "python": result.python,
        "platform": result.platform,
        "smoke_line": result.smoke_line,
        "frames": result.frames,
        "view": result.view,
        "profile": result.profile,
        "measurements": result.measurements,
        "stdout_snippet": result.stdout_snippet,
        "stderr_snippet": result.stderr_snippet,
    }
    return json.dumps(
        obj,
        indent=indent,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
        default=str,
    )


def format_tui_smoke_text(result: TuiSmokeResult) -> str:
    """Format TUI smoke result as concise human-readable text."""
    lines: list[str] = []
    lines.append(f"groop acceptance tui-smoke  v{result.version}")
    lines.append(f"python: {result.python.split()[0]}  platform: {result.platform}")
    lines.append("")

    if result.smoke_line:
        lines.append(f"  UI smoke: {result.smoke_line}")
    else:
        lines.append("  UI smoke: line not found")

    lines.append(f"  exit code: {result.exit_code}")
    lines.append("")
    lines.append("  Measurements:")
    m = result.measurements
    lines.append(f"    wall:    {m.get('wall_s', '?'):>9.4f}s")
    lines.append(f"    user:    {m.get('user_s', '?'):>9.4f}s  (child)")
    lines.append(f"     sys:    {m.get('sys_s', '?'):>9.4f}s  (child)")
    lines.append(f"     RSS:    {m.get('rss_kb', '?'):>9.0f} KB  (child max)")

    if result.stderr_snippet:
        lines.append("")
        lines.append("  stderr:")
        for line in result.stderr_snippet.split("\n")[:5]:
            lines.append(f"    {line}")

    verdict = "ALL CHECKS PASSED" if result.ok else "SOME CHECKS FAILED"
    lines.append("")
    lines.append(f"  {verdict}  (exit code {'0' if result.ok else '1'})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP smoke logic
# ---------------------------------------------------------------------------


def _parse_tool_content(tool_result: Any) -> Any:
    """Extract content from a CallToolResult as a dict or string."""
    if hasattr(tool_result, "content") and tool_result.content:
        block = tool_result.content[0]
        if hasattr(block, "text") and block.text:
            try:
                return json.loads(block.text)
            except (json.JSONDecodeError, TypeError):
                return block.text
    return {}


def _tool_call_failure(tool_result: Any) -> str | None:
    """Return a reason string if a tool call failed, or None if it succeeded.

    ``CallToolResult.isError`` is NOT sufficient on its own: the MCP SDK only
    sets it when a tool *raises*, while groop's tools *return* their typed
    failures as an ordinary ``{"error": {"code": ...}}`` payload
    (``mcp/server.py`` ``_tool_error``).  Checking ``isError`` alone therefore
    reports success for every typed groop error -- an assertion that cannot
    fail.  The payload is the authority.
    """
    if getattr(tool_result, "isError", False):
        return "transport reported isError"
    content = _parse_tool_content(tool_result)
    if not isinstance(content, dict):
        return f"non-dict payload: {content!r}"
    error = content.get("error")
    if isinstance(error, dict):
        return f"typed error: {error.get('code')}"
    if error is not None:
        return f"malformed error field: {error!r}"
    return None


def _update_byte_size(
    tool_result: Any,
    tool_name: str,
    details: dict[str, Any],
) -> None:
    """Record the byte size of a tool response in the details dict."""
    size = 0
    if hasattr(tool_result, "content") and tool_result.content:
        for block in tool_result.content:
            if hasattr(block, "text") and block.text:
                size += len(block.text.encode("utf-8"))
    details[tool_name] = {"bytes": size}


def _terminate_process(proc: subprocess.Popen[bytes] | None) -> None:
    """Safely terminate a subprocess, with kill fallback.

    Does nothing when *proc* is ``None`` (no process owned).
    """
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _make_mcp_result(
    checks: list[Check],
    max_bytes: int | None,
    t0: float,
    ru0: Any,
) -> McpSmokeResult:
    """Build an McpSmokeResult from current state (helper for early exits)."""
    from groop import __version__

    t1 = time.perf_counter()
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    return McpSmokeResult(
        ok=all(c.ok for c in checks) if checks else False,
        version=__version__,
        python=sys.version,
        platform=platform.platform(),
        extra_installed=True,
        checks=checks,
        max_response_bytes=max_bytes,
        measurements={
            "wall_s": round(t1 - t0, 4),
            "user_s": round(ru1.ru_utime - ru0.ru_utime, 4),
            "sys_s": round(ru1.ru_stime - ru0.ru_stime, 4),
            "rss_kb": float(ru1.ru_maxrss),
        },
    )


def run_mcp_smoke(
    socket_path: Path | None = None,
    *,
    timeout_s: float = 30.0,
) -> McpSmokeResult:
    """Run MCP live-daemon acceptance checks.

    This function starts a real daemon on a temp socket, connects a real MCP
    server, drives all four MCP tools through the MCP client SDK, and records
    the maximum observed response size.  It handles daemon-loss mid-session
    and invalid-selector checks.

    If the ``mcp`` extra is absent, returns a typed skip result instead of
    failing (exit 0, ``extra_installed=False``, checks empty).
    """
    from groop import __version__

    t0 = time.perf_counter()
    ru0 = resource.getrusage(resource.RUSAGE_SELF)

    # --- Check if mcp extra is available (without importing it) ---
    import importlib.util  # noqa: E402

    if importlib.util.find_spec("mcp") is None:
        t1 = time.perf_counter()
        ru1 = resource.getrusage(resource.RUSAGE_SELF)
        return McpSmokeResult(
            ok=True,
            version=__version__,
            python=sys.version,
            platform=platform.platform(),
            extra_installed=False,
            checks=[],
            max_response_bytes=None,
            measurements={
                "wall_s": round(t1 - t0, 4),
                "user_s": round(ru1.ru_utime - ru0.ru_utime, 4),
                "sys_s": round(ru1.ru_stime - ru0.ru_stime, 4),
                "rss_kb": float(ru1.ru_maxrss),
            },
        )

    import asyncio  # noqa: E402
    import socket  # noqa: E402
    import tempfile  # noqa: E402
    import shutil  # noqa: E402

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}

    # --- Create temp dir for daemon socket ---
    tmpdir = Path(tempfile.mkdtemp(prefix="groop-mcp-smoke-"))
    daemon_socket: Path = socket_path if socket_path is not None else tmpdir / "groop.sock"

    # Track processes for teardown
    daemon_proc: subprocess.Popen[bytes] | None = None
    checks: list[Check] = []
    max_response_bytes: int | None = None

    try:
        # --- Start daemon ---
        daemon_cmd = [
            sys.executable, "-m", "groop.cli",
            "daemon", "serve",
            "--socket", str(daemon_socket),
        ]
        try:
            daemon_proc = subprocess.Popen(
                daemon_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError as exc:
            checks.append(Check(
                name="daemon_start",
                ok=False,
                message=f"Failed to start daemon: {exc}",
                details={"error": str(exc)},
            ))
            return _make_mcp_result(checks, max_response_bytes, t0, ru0)

        # --- Wait for the socket to ACCEPT A CONNECTION ---
        # A bound-but-not-listening socket already exists on disk, so polling
        # for the path can hand a not-yet-listening socket to the session and
        # fail with ECONNREFUSED.  Connecting is the only readiness signal that
        # means what it says.  Bail out early if the daemon is already dead
        # rather than burning the whole timeout.
        deadline = time.monotonic() + timeout_s
        socket_ready = False
        daemon_died = False
        while time.monotonic() < deadline:
            if daemon_proc.poll() is not None:
                daemon_died = True
                break
            if daemon_socket.exists():
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.settimeout(1.0)
                        probe.connect(str(daemon_socket))
                    socket_ready = True
                    break
                except OSError:
                    pass
            time.sleep(0.05)

        if not socket_ready:
            checks.append(Check(
                name="hello",
                ok=False,
                message=(
                    f"Daemon exited with code {daemon_proc.returncode} before serving"
                    if daemon_died
                    else f"Daemon socket did not accept a connection within {timeout_s}s"
                ),
                details={"daemon_exit_code": daemon_proc.returncode},
            ))

        # --- Run async MCP client session ---
        if socket_ready:
            # A failure to even establish the session (missing extra in the
            # child, server exiting non-zero at startup) must surface as a typed
            # failing check -- an escaping traceback would leave --json consumers
            # with unparseable output on the single most likely live failure.
            try:
                checks_list, max_bytes = asyncio.run(
                    _run_mcp_client_session(daemon_socket, env, daemon_proc)
                )
                checks.extend(checks_list)
                max_response_bytes = max_bytes
            except Exception as exc:
                checks.append(Check(
                    name="mcp_session",
                    ok=False,
                    message=f"MCP client session failed: {exc}",
                    details={"error": str(exc)},
                ))
    finally:
        # --- Teardown: kill daemon on any exit path ---
        if daemon_proc is not None:
            _terminate_process(daemon_proc)
        try:
            # Only remove a socket this process created.  Unlinking
            # unconditionally would delete the packaged system daemon's socket
            # when an operator points --socket at it.
            if socket_path is None:
                if daemon_socket.exists():
                    daemon_socket.unlink()
        except OSError:
            pass
        # tmpdir is created unconditionally, so it must be removed
        # unconditionally or every --socket run leaks an empty directory.
        shutil.rmtree(tmpdir, ignore_errors=True)

    # Determine overall pass: all checks must be OK
    all_ok = all(c.ok for c in checks) if checks else True

    t1 = time.perf_counter()
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    return McpSmokeResult(
        ok=all_ok,
        version=__version__,
        python=sys.version,
        platform=platform.platform(),
        extra_installed=True,
        checks=checks,
        max_response_bytes=max_response_bytes,
        measurements={
            "wall_s": round(t1 - t0, 4),
            "user_s": round(ru1.ru_utime - ru0.ru_utime, 4),
            "sys_s": round(ru1.ru_stime - ru0.ru_stime, 4),
            "rss_kb": float(ru1.ru_maxrss),
        },
    )


async def _run_mcp_client_session(
    daemon_socket: Path,
    env: dict[str, str],
    daemon_proc: subprocess.Popen[bytes] | None,
) -> tuple[list[Check], int | None]:
    """Run the async MCP client session against the daemon and server.

    Returns (checks, max_response_bytes).
    """
    _mcp_stdio = __import__("mcp.client.stdio", fromlist=["StdioServerParameters", "stdio_client"])
    StdioServerParameters = _mcp_stdio.StdioServerParameters
    stdio_client = _mcp_stdio.stdio_client
    _mcp_session = __import__("mcp.client.session", fromlist=["ClientSession"])
    ClientSession = _mcp_session.ClientSession

    _checks: list[Check] = []
    _max_bytes: int | None = None

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "groop.cli", "mcp", "serve", "--socket", str(daemon_socket)],
        env=env,
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # --- Check 1: hello probe via tool discovery ---
            try:
                tools_result = await session.list_tools()
                tool_names = {t.name for t in tools_result.tools}
                hello_ok = len(tool_names & _MCP_SMOKE_TOOLS) == len(_MCP_SMOKE_TOOLS)
                _checks.append(Check(
                    name="hello",
                    ok=hello_ok,
                    message=(
                        f"Discovered {len(tools_result.tools)} tool(s) with "
                        f"all expected tools present"
                        if hello_ok
                        else f"Expected tools {sorted(_MCP_SMOKE_TOOLS)}, found {sorted(tool_names)}"
                    ),
                    details={
                        "found_count": len(tools_result.tools),
                        "found": sorted(tool_names),
                        "expected": sorted(_MCP_SMOKE_TOOLS),
                    },
                ))
            except Exception as exc:
                _checks.append(Check(
                    name="hello",
                    ok=False,
                    message=f"Tool discovery failed: {exc}",
                    details={"error": str(exc)},
                ))
                return _checks, _max_bytes

            # --- Check 2: tool discovery lists exactly the four tools ---
            all_four = tool_names == _MCP_SMOKE_TOOLS if hello_ok else False
            _checks.append(Check(
                name="tool_discovery",
                ok=all_four,
                message=(
                    "Tool set is exactly the 4 expected tools"
                    if all_four
                    else f"Expected {sorted(_MCP_SMOKE_TOOLS)}, got {sorted(tool_names)}"
                ),
                details={
                    "expected": sorted(_MCP_SMOKE_TOOLS),
                    "found": sorted(tool_names),
                    "count": len(tool_names),
                },
            ))

            # --- Check 3: each tool returns valid result ---
            # Deterministic order: groop_overview must run before the two tools
            # that consume the entity key it discovers.
            overview_key: str | None = None
            tool_calls_ok = True
            tool_call_details: dict[str, Any] = {}

            for tool_name in _MCP_SMOKE_CALL_ORDER:
                try:
                    if tool_name == "groop_health":
                        result = await session.call_tool(tool_name, {})

                    elif tool_name == "groop_overview":
                        result = await session.call_tool(tool_name, {"sort_by": "ram", "limit": 5})

                    elif tool_name == "groop_entity":
                        if overview_key is None:
                            tool_calls_ok = False
                            tool_call_details[tool_name] = (
                                "skipped: groop_overview yielded no entity key to drive this tool"
                            )
                            continue
                        result = await session.call_tool(tool_name, {"selector": overview_key})

                    else:  # groop_history
                        if overview_key is None:
                            tool_calls_ok = False
                            tool_call_details[tool_name] = (
                                "skipped: groop_overview yielded no entity key to drive this tool"
                            )
                            continue
                        result = await session.call_tool(
                            tool_name,
                            {
                                "selector": overview_key,
                                "metric": "ram",
                                "window": "last:60",
                                "limit": 5,
                            },
                        )

                    _update_byte_size(result, tool_name, tool_call_details)
                    failure = _tool_call_failure(result)
                    if failure is not None:
                        tool_calls_ok = False
                        tool_call_details[tool_name]["failure"] = failure
                    elif tool_name == "groop_overview":
                        content_raw = _parse_tool_content(result)
                        parsed = content_raw if isinstance(content_raw, dict) else {}
                        rows = parsed.get("data", {}).get("rows", [])
                        if rows:
                            overview_key = rows[0].get("key")

                except Exception as exc:
                    tool_calls_ok = False
                    tool_call_details[tool_name] = str(exc)

            # Compute max bytes from details
            sizes = [
                v.get("bytes", 0) for v in tool_call_details.values()
                if isinstance(v, dict) and "bytes" in v
            ]
            _max_bytes = max(sizes) if sizes else None

            _checks.append(Check(
                name="tool_calls",
                ok=tool_calls_ok,
                message=(
                    "All 4 tools returned successful results"
                    if tool_calls_ok
                    else "One or more tool calls failed"
                ),
                details=tool_call_details,
            ))

            # --- Check 4: response under 4 MiB cap ---
            cap_ok = _max_bytes is None or _max_bytes <= 4 * 1024 * 1024
            _checks.append(Check(
                name="response_cap",
                ok=cap_ok,
                message=(
                    f"Largest response: {_max_bytes} bytes (cap: 4 MiB)"
                    if _max_bytes is not None
                    else "No responses measured"
                ),
                details={"max_response_bytes": _max_bytes, "cap_bytes": 4 * 1024 * 1024},
            ))

            # --- Check 5: bogus selector (live daemon) yields invalid-selector ---
            try:
                bogus_result = await session.call_tool("groop_entity", {"selector": "__nonexistent__"})
                bogus_content = _parse_tool_content(bogus_result)
                is_invalid_selector = (
                    isinstance(bogus_content, dict)
                    and isinstance(bogus_content.get("error"), dict)
                    and bogus_content["error"].get("code") == "invalid-selector"
                )
                _checks.append(Check(
                    name="invalid_selector",
                    ok=is_invalid_selector,
                    message=(
                        "Bogus selector produced typed invalid-selector error"
                        if is_invalid_selector
                        else f"Bogus selector did not produce invalid-selector: {bogus_content}"
                    ),
                    details={
                        "typed_error_code": bogus_content.get("error", {}).get("code") if isinstance(bogus_content, dict) else None,
                    },
                ))
            except Exception as exc:
                _checks.append(Check(
                    name="invalid_selector",
                    ok=False,
                    message=f"Invalid selector check raised: {exc}",
                    details={"error": str(exc)},
                ))

            # --- Check 6: daemon loss yields typed error, server still alive ---
            if daemon_proc is not None:
                try:
                    daemon_proc.terminate()
                    daemon_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    daemon_proc.kill()
                    daemon_proc.wait()

            try:
                loss_result = await session.call_tool("groop_overview", {"sort_by": "ram", "limit": 1})
                loss_content = _parse_tool_content(loss_result)
                is_daemon_unavailable = (
                    isinstance(loss_content, dict)
                    and isinstance(loss_content.get("error"), dict)
                    and loss_content["error"].get("code") == "daemon-unavailable"
                )
                # The contract is that losing the daemon does not take the MCP
                # server down with it, so this must be *observed*, not asserted:
                # a hardcoded True would report "server alive" for a server that
                # had already died.  Re-driving the session is the observation --
                # a dead server cannot answer list_tools().
                try:
                    post_loss_tools = await session.list_tools()
                    server_alive = {t.name for t in post_loss_tools.tools} >= _MCP_SMOKE_TOOLS
                except Exception:
                    server_alive = False
                daemon_loss_ok = is_daemon_unavailable and server_alive
                _checks.append(Check(
                    name="daemon_loss",
                    ok=daemon_loss_ok,
                    message=(
                        f"Daemon loss produced typed error (code=daemon-unavailable), "
                        f"server alive={server_alive}"
                        if daemon_loss_ok
                        else (
                            f"Daemon loss error not as expected: "
                            f"typed_error={is_daemon_unavailable}, server_alive={server_alive}"
                        )
                    ),
                    details={
                        "typed_error_code": loss_content.get("error", {}).get("code") if isinstance(loss_content, dict) else None,
                        "server_alive": server_alive,
                    },
                ))
            except Exception as exc:
                _checks.append(Check(
                    name="daemon_loss",
                    ok=False,
                    message=f"Daemon loss check raised: {exc}",
                    details={"error": str(exc)},
                ))

    return _checks, _max_bytes


# ---------------------------------------------------------------------------
# MCP smoke output formatting
# ---------------------------------------------------------------------------


def format_mcp_smoke_json(result: McpSmokeResult, *, pretty: bool = False) -> str:
    """Serialize an MCP smoke result as deterministic JSON."""
    indent = 2 if pretty else None
    obj: dict[str, Any] = {
        "ok": result.ok,
        "version": result.version,
        "python": result.python,
        "platform": result.platform,
        "extra_installed": result.extra_installed,
        "checks": [_check_to_dict(c) for c in result.checks],
        "max_response_bytes": result.max_response_bytes,
        "measurements": result.measurements,
    }
    return json.dumps(
        obj,
        indent=indent,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
        default=str,
    )


def format_mcp_smoke_text(result: McpSmokeResult) -> str:
    """Format MCP smoke result as concise human-readable text."""
    lines: list[str] = []
    lines.append(f"groop acceptance mcp-smoke  v{result.version}")
    lines.append(f"python: {result.python.split()[0]}  platform: {result.platform}")
    lines.append("")

    if not result.extra_installed:
        lines.append("  SKIPPED: groop[mcp] extra not installed")
        lines.append("")
        lines.append("  ALL CHECKS PASSED  (skipped)")
        return "\n".join(lines)

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

    if result.max_response_bytes is not None:
        lines.append("")
        lines.append(f"  Largest response: {result.max_response_bytes} bytes (cap: 4 MiB)")

    lines.append("")
    verdict = "ALL CHECKS PASSED" if result.ok else "SOME CHECKS FAILED"
    lines.append(f"  {verdict}  (exit code {'0' if result.ok else '1'})")
    return "\n".join(lines)


def _steady_to_jsonable(result: SteadyResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "version": result.version,
        "python": result.python,
        "platform": result.platform,
        "samples_requested": result.samples_requested,
        "samples_completed": result.samples_completed,
        "measurements": result.measurements,
        "entity_counts": result.entity_counts,
        "collection_errors": result.collection_errors,
        "threshold_errors": result.threshold_errors,
    }


def format_steady_json(result: SteadyResult, *, pretty: bool = False) -> str:
    """Serialize a steady result as deterministic JSON."""
    indent = 2 if pretty else None
    obj = _steady_to_jsonable(result)
    return json.dumps(
        obj,
        indent=indent,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
        default=str,
    )


def format_steady_text(result: SteadyResult) -> str:
    """Format steady result as concise human-readable text."""
    lines: list[str] = []
    lines.append(f"groop acceptance steady  v{result.version}")
    lines.append(f"python: {result.python.split()[0]}  platform: {result.platform}")
    lines.append("")
    lines.append(f"  Collection: {result.samples_completed}/{result.samples_requested} samples completed")
    ec = result.entity_counts
    lines.append(f"  Entity count: min={ec.get('min', '?')}, max={ec.get('max', '?')}, last={ec.get('last', '?')}")
    lines.append("")
    lines.append("  Measurements:")
    m = result.measurements
    lines.append(f"    wall:      {m.get('wall_s', '?'):>8.4f}s")
    lines.append(f"    user:      {m.get('user_s', '?'):>8.4f}s")
    lines.append(f"     sys:      {m.get('sys_s', '?'):>8.4f}s")
    lines.append(f"     RSS:      {m.get('rss_kb', '?'):>8.0f} KB")
    lines.append(f"    avg sample: {m.get('avg_sample_wall_s', '?'):>8.4f}s")
    lines.append(f"    cpu%:      {m.get('cpu_pct', '?'):>8.2f}%  (of one core)")

    if result.collection_errors:
        lines.append("")
        lines.append("  Collection failures:")
        for err in result.collection_errors:
            lines.append(f"    [FAIL] {err}")

    if result.threshold_errors:
        lines.append("")
        lines.append("  Threshold failures:")
        for err in result.threshold_errors:
            lines.append(f"    [FAIL] {err}")

    lines.append("")
    lines.append("  This is collector steady-state evidence, not full TUI steady-state acceptance.")

    verdict = "ALL CHECKS PASSED" if result.ok else "SOME CHECKS FAILED"
    lines.append("")
    lines.append(f"  {verdict}  (exit code {'0' if result.ok else '1'})")
    return "\n".join(lines)


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


# Entry point
# ---------------------------------------------------------------------------


def acceptance_main(argv: list[str] | None = None) -> int:
    """Parse args, dispatch to smoke or steady, print output, return exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "smoke":
        result = run_smoke(cgroup_root=args.cgroup_root, replay_path=args.replay)
        if args.json or args.pretty_json:
            output = format_json(result, pretty=args.pretty_json)
        else:
            output = format_text(result)
    elif args.command == "steady":
        if args.samples <= 0:
            print("error: --samples must be positive", file=sys.stderr)
            return 2
        if args.interval_s < 0:
            print("error: --interval-s must be non-negative", file=sys.stderr)
            return 2
        if args.max_cpu_pct is not None and args.max_cpu_pct < 0:
            print("error: --max-cpu-pct must be non-negative", file=sys.stderr)
            return 2
        if args.max_rss_kb is not None and args.max_rss_kb <= 0:
            print("error: --max-rss-kb must be positive", file=sys.stderr)
            return 2
        result = run_steady(
            cgroup_root=args.cgroup_root,
            samples=args.samples,
            interval_s=args.interval_s,
            max_cpu_pct=args.max_cpu_pct,
            max_rss_kb=args.max_rss_kb,
        )
        if args.json or args.pretty_json:
            output = format_steady_json(result, pretty=args.pretty_json)
        else:
            output = format_steady_text(result)
    elif args.command == "tui-smoke":
        if args.timeout_s <= 0:
            print("error: --timeout-s must be positive", file=sys.stderr)
            return 2
        result = run_tui_smoke(
            replay_path=args.replay,
            config_path=args.config,
            profile=args.profile,
            timeout_s=args.timeout_s,
        )
        if args.json or args.pretty_json:
            output = format_tui_smoke_json(result, pretty=args.pretty_json)
        else:
            output = format_tui_smoke_text(result)
    elif args.command == "mcp-smoke":
        if args.timeout_s <= 0:
            print("error: --timeout-s must be positive", file=sys.stderr)
            return 2
        result = run_mcp_smoke(
            socket_path=args.socket,
            timeout_s=args.timeout_s,
        )
        if args.json or args.pretty_json:
            output = format_mcp_smoke_json(result, pretty=args.pretty_json)
        else:
            output = format_mcp_smoke_text(result)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 2

    print(output)

    if result.ok:
        return 0
    return 1


def smoke_main(argv: list[str] | None = None) -> int:
    """Backward-compatible entry point for older callers."""
    return acceptance_main(argv)


def main() -> None:
    """Convenience entry point.  Called via ``python -m groop.acceptance``."""
    sys.exit(acceptance_main())


if __name__ == "__main__":
    main()
