#!/usr/bin/env python3
"""Plan or provision Path B on an explicitly named TrueNAS boot disk."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ACCEPTANCE = Path(__file__).resolve().parent / "acceptance"
sys.path.insert(0, str(ACCEPTANCE))
from path_b_provisioner import provision_path_b  # noqa: E402
from remote import RemoteError, Target  # noqa: E402
from path_b_provisioner import LayoutError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disk", required=True, help="whole boot disk, e.g. /dev/nvme0n1")
    parser.add_argument(
        "--boot-pool-partition", required=True,
        help="partition 3 that is the sole boot-pool vdev, e.g. /dev/nvme0n1p3",
    )
    parser.add_argument("--expected-version", required=True, help="exact system.info version")
    parser.add_argument(
        "--minimum-free-bytes", type=int, default=1024**3,
        help="minimum aligned GPT tail required (default: 1 GiB); partition 4 uses the full tail",
    )
    parser.add_argument("--apply", action="store_true", help="write GPT, create ZFS, and register it")
    parser.add_argument(
        "--confirm-disk",
        help="required with --apply; must exactly repeat --disk",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        target = Target.from_environment()
        result = provision_path_b(
            target,
            disk=args.disk,
            boot_pool_partition=args.boot_pool_partition,
            expected_version=args.expected_version,
            minimum_free_bytes=args.minimum_free_bytes,
            apply=args.apply,
            confirmed_disk=args.confirm_disk,
        )
    except (OSError, ValueError, LayoutError, RemoteError) as exc:
        print(f"provision_path_b.py: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
