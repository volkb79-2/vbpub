#!/usr/bin/env python3
"""CLI entry point for repeatable external TrueNAS update-survival acceptance."""

from pathlib import Path
import sys


ACCEPTANCE = Path(__file__).resolve().parent / "boot-pool-patch" / "acceptance"
sys.path.insert(0, str(ACCEPTANCE))

from accept_update_survival import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
