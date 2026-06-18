#!/usr/bin/env python3
"""Logged, end-to-end release driver — thin wrapper over ``release-all.py`` (the cmru CLI).

For an ad-hoc release just run ``./release-all.py release``. Use this wrapper when you
want the whole run captured to a timestamped file under ``logs/`` and (optionally) old
release assets / GHCR versions pruned afterwards.

  ./release-runner.py                       # cmru release (all orchestrated projects), logged
  ./release-runner.py --project cmru        # one project
  ./release-runner.py --dry-run             # preview tags only, no writes
  ./release-runner.py --remove-assets 30d   # release, then age-based cleanup

It runs two cmru verbs in sequence: ``release`` (detect → tag → push → build → publish)
and, when ``--remove-assets`` is given, ``cleanup``. The legacy ``--run-tests/--build/
--push/--validate`` flag interface was removed when cmru moved to a verb-based CLI.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run `cmru release` (+ optional cleanup) with log capture")
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Limit to the given project(s). Repeat for multiple.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview tags only; no writes")
    parser.add_argument("--no-build", action="store_true", help="Tag + push only; skip build/publish")
    parser.add_argument(
        "--remove-assets",
        metavar="AGE",
        help="After releasing, prune releases/GHCR versions older than AGE (e.g. 30d, 12h).",
    )
    return parser.parse_args()


def _tee(argv: list[str], cwd: Path, log_handle) -> int:
    """Run argv, streaming combined stdout/stderr to the console and the log file."""
    print(f"[INFO] Running: {' '.join(argv)}")
    log_handle.write(f"\n$ {' '.join(argv)}\n")
    log_handle.flush()
    process = subprocess.Popen(
        argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        log_handle.write(line)
    return process.wait()


def main() -> int:
    cli_args = parse_args()
    repo_root = Path(__file__).resolve().parent
    config_path = repo_root / "release.toml"
    sample_path = repo_root / "release.sample.toml"
    if not config_path.exists():
        if not sample_path.exists():
            print("[ERROR] release.sample.toml not found; cannot initialize release.toml")
            return 1
        shutil.copy(sample_path, config_path)
        print("[INFO] Created release.toml from release.sample.toml")
        print("[ERROR] Fill in secrets (GitHub token, usernames) before running the release pipeline.")
        return 1

    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"release-{timestamp}.log"
    release_all = str(repo_root / "release-all.py")

    release_cmd = [sys.executable, release_all, "release"]
    for project_name in cli_args.project:
        release_cmd.extend(["--project", project_name])
    if cli_args.dry_run:
        release_cmd.append("--dry-run")
    if cli_args.no_build:
        release_cmd.append("--no-build")

    print(f"[INFO] Logging to {log_file}")
    with log_file.open("a", encoding="utf-8") as handle:
        rc = _tee(release_cmd, repo_root, handle)
        if rc != 0:
            print(f"[ERROR] `cmru release` exited {rc}; skipping cleanup.")
            return rc
        if cli_args.remove_assets and not cli_args.dry_run:
            cleanup_cmd = [sys.executable, release_all, "cleanup", "--remove-assets", cli_args.remove_assets]
            rc = _tee(cleanup_cmd, repo_root, handle)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
