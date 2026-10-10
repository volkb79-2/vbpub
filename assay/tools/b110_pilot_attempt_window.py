#!/usr/bin/env python3
"""Create the immutable start and expiry record for one B110 pilot attempt."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import time


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_WINDOW_NS = 90 * 60 * 1_000_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign")
    parser.add_argument("commit")
    parser.add_argument("--assay-fd", type=int, required=True)
    parser.add_argument("--expected-assay-device", type=int, required=True)
    parser.add_argument("--expected-assay-inode", type=int, required=True)
    args = parser.parse_args()
    if _HEX40.fullmatch(args.commit) is None:
        parser.error("commit must be a full lowercase Git commit ID")
    if args.expected_assay_device < 0 or args.expected_assay_inode < 1:
        parser.error("expected .assay device and inode are malformed")
    if args.campaign != f"b110-pilot-{args.commit[:12]}":
        parser.error("campaign must be derived from the source commit")

    try:
        state_info = os.fstat(args.assay_fd)
    except OSError as exc:
        parser.error(f"cannot inspect pinned .assay directory: {exc}")
    if (
        not stat.S_ISDIR(state_info.st_mode)
        or (state_info.st_dev, state_info.st_ino)
        != (args.expected_assay_device, args.expected_assay_inode)
    ):
        parser.error("pinned .assay directory differs from launcher admission")

    def check_visible_state_path() -> None:
        try:
            visible = os.stat(".assay", follow_symlinks=False)
        except OSError as exc:
            parser.error(f"cannot recheck .assay after host admission: {exc}")
        if (
            not stat.S_ISDIR(visible.st_mode)
            or (visible.st_dev, visible.st_ino)
            != (args.expected_assay_device, args.expected_assay_inode)
        ):
            parser.error(".assay path changed after host admission")

    check_visible_state_path()

    started = time.time_ns()
    document = {
        "schema": "assay-b110-pilot-attempt-window/1",
        "campaign": args.campaign,
        "commit": args.commit,
        "started_at_epoch_ns": started,
        "expires_at_epoch_ns": started + _WINDOW_NS,
    }
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(
            "b110-pilot-attempt-window.json",
            flags,
            0o600,
            dir_fd=args.assay_fd,
        )
    except OSError as exc:
        parser.error(f"cannot create this attempt's deadline record: {exc}")
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    check_visible_state_path()
    os.fsync(args.assay_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
