#!/usr/bin/env python3
"""Run release pipeline with cleanup and tee-like logging."""
from __future__ import annotations

import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"release-{timestamp}.log"

    args = [
        sys.executable,
        str(repo_root / "release-all.py"),
        "--config",
        str(repo_root / "release.toml"),
        "--run-tests",
        "--build",
        "--push",
        "--validate",
        # "--remove-assets",
        # "10min",
    ]

    print(f"[INFO] Logging to {log_file}")
    print(f"[INFO] Running: {' '.join(args)}")

    with log_file.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(
            args,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            handle.write(line)
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
