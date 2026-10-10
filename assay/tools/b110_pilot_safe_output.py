#!/usr/bin/env python3
"""Open B110 gate output files beneath the admitted .assay directory."""

from __future__ import annotations

import argparse
import os
import stat
import sys

_MAX_READ_BYTES = 128 * 1024 * 1024


def _open_output(name: str, assay_fd: int) -> int:
    _validate_name(name)
    return os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
        dir_fd=assay_fd,
    )


def _validate_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError("gate file name must be a single path component")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assay-fd", type=int, required=True)
    parser.add_argument("--expected-assay-device", type=int, required=True)
    parser.add_argument("--expected-assay-inode", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", help="create this output file beneath the pinned .assay directory")
    mode.add_argument("--read", help="safely read this regular file beneath the pinned .assay directory")
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("--stderr", help="write stderr to this separate .assay file")
    outputs.add_argument("--stderr-to-stdout", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.read and (command or args.stderr or args.stderr_to_stdout):
        parser.error("--read cannot be combined with an output command")
    if args.output and not command:
        parser.error("an output command is required after --")
    if args.expected_assay_device < 0 or args.expected_assay_inode < 1:
        parser.error("expected .assay device and inode are malformed")

    opened: list[int] = []
    try:
        assay_info = os.fstat(args.assay_fd)
        expected = (args.expected_assay_device, args.expected_assay_inode)
        if not stat.S_ISDIR(assay_info.st_mode) or (assay_info.st_dev, assay_info.st_ino) != expected:
            raise ValueError("pinned .assay directory differs from launcher admission")
        visible = os.stat(".assay", follow_symlinks=False)
        if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != expected:
            raise ValueError(".assay path changed after host admission")

        if args.read:
            _validate_name(args.read)
            source_fd = os.open(
                args.read,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=args.assay_fd,
            )
            opened.append(source_fd)
            source_info = os.fstat(source_fd)
            if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
                raise ValueError("pinned gate log is not a single-link regular file")
            if source_info.st_size > _MAX_READ_BYTES:
                raise ValueError(f"pinned gate log exceeds the {_MAX_READ_BYTES}-byte limit")
            content = bytearray()
            while len(content) <= _MAX_READ_BYTES:
                chunk = os.read(source_fd, min(1024 * 1024, _MAX_READ_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) > _MAX_READ_BYTES:
                raise ValueError(f"pinned gate log exceeds the {_MAX_READ_BYTES}-byte limit")
            visible_file = os.stat(args.read, dir_fd=args.assay_fd, follow_symlinks=False)
            after_info = os.fstat(source_fd)
            if (visible_file.st_dev, visible_file.st_ino) != (after_info.st_dev, after_info.st_ino):
                raise ValueError("pinned gate log path changed while it was read")
            visible = os.stat(".assay", follow_symlinks=False)
            if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != expected:
                raise ValueError(".assay path changed while reading a gate log")
            sys.stdout.buffer.write(content)
            return 0

        output_fd = _open_output(args.output, args.assay_fd)
        opened.append(output_fd)
        stderr_fd = None
        if args.stderr:
            stderr_fd = _open_output(args.stderr, args.assay_fd)
            opened.append(stderr_fd)

        visible = os.stat(".assay", follow_symlinks=False)
        if not stat.S_ISDIR(visible.st_mode) or (visible.st_dev, visible.st_ino) != expected:
            raise ValueError(".assay path changed while opening gate outputs")

        os.dup2(output_fd, 1)
        if args.stderr_to_stdout:
            os.dup2(output_fd, 2)
        elif stderr_fd is not None:
            os.dup2(stderr_fd, 2)
        for descriptor in opened:
            if descriptor not in {1, 2}:
                os.close(descriptor)
        opened.clear()
        os.execvpe(command[0], command, os.environ)
    except (OSError, ValueError) as exc:
        print(f"b110_pilot_safe_output: {exc}", file=sys.stderr)
        return 2
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
