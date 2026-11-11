#!/usr/bin/env python3
"""Build a bounded prose read-back from a session marker and Codex run records."""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since-marker", required=True)
    parser.add_argument("--session", required=True, help="session JSONL path")
    parser.add_argument("--runs-dir", required=True, help="controller runs directory")
    parser.add_argument("--out", required=True)
    parser.add_argument("--harness", default="nyxloom-harness")
    parser.add_argument("--budget-chars", type=int, default=20000)
    args = parser.parse_args()
    if args.budget_chars < 512:
        parser.error("--budget-chars must be at least 512")
    if not Path(args.session).is_file():
        parser.error(f"session file is missing: {args.session}")
    if not Path(args.runs_dir).is_dir():
        parser.error(f"runs directory is missing: {args.runs_dir}")
    return args


def find_cutoff(session: Path, marker: str) -> str:
    for line in session.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("uuid") == marker or marker in line[:400]:
            timestamp = event.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp:
                raise ValueError(f"marker has no timestamp: {marker}")
            return timestamp
    raise ValueError(f"session marker not found: {marker}")


def session_sections(session: Path, cutoff: str, marker: str, harness: str, budget: int) -> tuple[str, str, str]:
    result = subprocess.run(
        [
            harness,
            "extract",
            str(session),
            "--profile",
            "all",
            "--tool-calls",
            "intent-or-call",
            "--edit-calls",
            "collapse",
            "--read-calls",
            "collapse",
            "--timestamps",
            "gaps",
            "--since",
            marker,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"nyxloom-harness extract failed: {detail}")

    results: list[str] = []
    prompts: list[str] = []
    for line in session.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        timestamp = event.get("timestamp") or ""
        if timestamp and timestamp <= cutoff:
            continue
        message = event.get("message") or {}
        content = message.get("content")
        parts = [content] if isinstance(content, str) else content if isinstance(content, list) else []
        for part in parts:
            if isinstance(part, str):
                kind, text = "text", part
            elif isinstance(part, dict):
                kind, text = part.get("type"), part.get("text")
            else:
                continue
            if event.get("type") == "user" and text and "<task-notification>" in text:
                summary = re.search(r"<summary>(.*?)</summary>", text, re.S)
                returned = re.search(r"<result>(.*?)</result>", text, re.S)
                title = summary.group(1) if summary else ""
                if returned and "Monitor" not in title:
                    results.append(f"## {timestamp[11:16]}Z {title[:120]}\n{returned.group(1).strip()[:budget]}")
            if event.get("type") == "assistant" and kind == "tool_use" and isinstance(part, dict):
                value = part.get("input") or {}
                if part.get("name") == "Agent" and value.get("prompt"):
                    prompts.append(
                        f"## {timestamp[11:16]}Z Agent: {value.get('description', '')}\n{value['prompt'][:budget]}"
                    )
                if part.get("name") == "SendMessage" and value.get("message"):
                    prompts.append(
                        f"## {timestamp[11:16]}Z SendMessage to {value.get('to', '')}: "
                        f"{value.get('summary', '')}\n{value['message'][:budget]}"
                    )
    return result.stdout, "\n\n".join(results), "\n\n".join(prompts)


def run_sections(runs_dir: Path, cutoff: str, budget: int) -> str:
    cutoff_time = dt.datetime.fromisoformat(cutoff.replace("Z", "+00:00")).timestamp()
    entries: list[str] = []
    for done_path in sorted(glob.glob(str(runs_dir / "*.done")), key=os.path.getmtime):
        if os.path.getmtime(done_path) < cutoff_time:
            continue
        name = Path(done_path).name[:-5]
        final_path = runs_dir / f"{name}.last.txt"
        if final_path.is_file():
            final = final_path.read_text(encoding="utf-8", errors="replace").strip()
        else:
            final = "(no final message)"
        status = Path(done_path).read_text(encoding="utf-8", errors="replace").strip()[:60]
        stamp = dt.datetime.fromtimestamp(os.path.getmtime(done_path), dt.timezone.utc).strftime("%H:%MZ")
        entries.append(f"## {stamp} run {name} [{status}]\n{final[-budget:]}")
    return "\n\n".join(entries)


def bounded_output(sections: list[tuple[str, str]], budget: int) -> tuple[str, bool]:
    headings = [heading for heading, _ in sections]
    heading_size = sum(map(len, headings))
    marker = "\n[content trimmed to --budget-chars]\n"
    available = budget - heading_size
    content_size = sum(len(body) for _, body in sections)
    trimmed = content_size > available
    if trimmed:
        available -= len(marker)

    remaining = max(0, available)
    allocations = [0] * len(sections)
    active = {index for index, (_, body) in enumerate(sections) if body}
    while remaining > 0 and active:
        share = max(1, remaining // len(active))
        for index in tuple(active):
            length = len(sections[index][1])
            take = min(share, length - allocations[index], remaining)
            allocations[index] += take
            remaining -= take
            if allocations[index] >= length:
                active.remove(index)
            if remaining == 0:
                break

    rendered: list[str] = []
    for index, (heading, body) in enumerate(sections):
        limit = allocations[index]
        content = body[-limit:] if index == 0 and limit else body[:limit]
        rendered.append(heading + content)
    output = "".join(rendered)
    if trimmed:
        output += marker
    if len(output) > budget:
        raise ValueError("read-back budget enforcement failed")
    return output, trimmed


def main() -> int:
    args = arguments()
    session = Path(args.session)
    runs_dir = Path(args.runs_dir)
    try:
        cutoff = find_cutoff(session, args.since_marker)
        extract, results, prompts = session_sections(
            session, cutoff, args.since_marker, args.harness, args.budget_chars
        )
        runs = run_sections(runs_dir, cutoff, args.budget_chars)
        output, trimmed = bounded_output(
            [
                ("# PART 1: operator turns, session prose and intent lines\n", extract),
                ("\n# PART 2: results returned by subagents\n", results),
                ("\n# PART 3: messages sent to subagents\n", prompts),
                ("\n# PART 4: Codex run finals since the marker\n", runs),
            ],
            args.budget_chars,
        )
        Path(args.out).write_text(output, encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"build-readback: {exc}", file=sys.stderr)
        return 1
    print(f"chars={len(output)} trimmed={str(trimmed).lower()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
