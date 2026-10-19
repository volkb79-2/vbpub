#!/usr/bin/env python3
"""Small QMP driver for the headless TrueNAS installer VM."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import socket
import struct
import sys
import time
import zlib
from pathlib import Path


def qmp_command(path: str, execute: str, arguments: dict | None = None) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(path)
        stream = sock.makefile("rwb", buffering=0)
        greeting = json.loads(stream.readline())
        if "QMP" not in greeting:
            raise RuntimeError(f"not a QMP greeting: {greeting}")
        stream.write(b'{"execute":"qmp_capabilities"}\r\n')
        while True:
            response = json.loads(stream.readline())
            if "return" in response:
                break
            if "error" in response:
                raise RuntimeError(f"QMP capabilities failed: {response}")
        command = {"execute": execute}
        if arguments is not None:
            command["arguments"] = arguments
        stream.write((json.dumps(command) + "\r\n").encode())
        while True:
            response = json.loads(stream.readline())
            if "return" in response:
                return response["return"]
            if "error" in response:
                raise RuntimeError(f"QMP {execute} failed: {response['error']}")


def key_event(path: str, character: str) -> None:
    letters = "abcdefghijklmnopqrstuvwxyz"
    shifted_symbols = {
        "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
        "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
        "_": "minus", "+": "equal", "{": "bracket_left",
        "}": "bracket_right", ":": "semicolon", '"': "apostrophe",
        "~": "grave", "|": "backslash", "<": "comma", ">": "dot",
        "?": "slash",
    }
    plain = {
        " ": "spc", "-": "minus", "=": "equal", "[": "bracket_left",
        "]": "bracket_right", ";": "semicolon", "'": "apostrophe",
        "`": "grave", "\\": "backslash", ",": "comma", ".": "dot",
        "/": "slash", "\t": "tab", "\n": "ret",
    }
    if character in letters:
        keys = [character]
    elif character in letters.upper():
        keys = ["shift", character.lower()]
    elif character.isdigit():
        keys = [character]
    elif character in shifted_symbols:
        keys = ["shift", shifted_symbols[character]]
    elif character in plain:
        keys = [plain[character]]
    else:
        raise ValueError(f"unsupported keyboard character: {character!r}")
    qmp_command(path, "send-key", {"keys": [{"type": "qcode", "data": key} for key in keys]})
    time.sleep(0.025)


def type_text(path: str, value: str) -> None:
    for character in value:
        key_event(path, character)


def ppm_to_png(source: bytes) -> bytes:
    match = re.match(rb"P6[ \t\r\n]+(\d+)[ \t]+(\d+)[ \t\r\n]+(\d+)[ \t\r\n]", source)
    if match is None:
        raise ValueError("QMP screenshot was not a binary PPM image")
    width, height, maxval = (int(value) for value in match.groups())
    if maxval != 255:
        raise ValueError(f"unsupported PPM max value: {maxval}")
    pixels = source[match.end():]
    if len(pixels) != width * height * 3:
        raise ValueError("QMP screenshot has a truncated PPM pixel buffer")
    scanlines = b"".join(b"\0" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default="vm/qmp.sock", help="QMP Unix socket")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="query VM status")
    sub.add_parser("powerdown", help="request an ACPI guest shutdown")
    sub.add_parser("quit", help="terminate the QEMU process immediately")
    screen = sub.add_parser("screenshot", help="save VGA screenshot")
    screen.add_argument("path")
    key = sub.add_parser("key", help="send one QEMU qcode, e.g. ret, esc, ctrl-alt-f2")
    key.add_argument("value")
    typing = sub.add_parser("type", help="type US-keyboard text into the guest")
    typing.add_argument("text", nargs="?", help="text (avoid for secrets; use --password)")
    typing.add_argument("--password", action="store_true", help="read text without echo")
    typing.add_argument("--file", help="read text from a local file without echoing it")
    args = parser.parse_args()
    path = args.socket
    if args.command == "status":
        result = qmp_command(path, "query-status")
    elif args.command == "powerdown":
        result = qmp_command(path, "system_powerdown")
    elif args.command == "quit":
        result = qmp_command(path, "quit")
    elif args.command == "screenshot":
        output = Path(args.path)
        output.parent.mkdir(parents=True, exist_ok=True)
        raw_output = output.with_suffix(output.suffix + ".ppm") if output.suffix.lower() == ".png" else output
        result = qmp_command(path, "screendump", {"filename": str(raw_output.resolve())})
        if output.suffix.lower() == ".png":
            output.write_bytes(ppm_to_png(raw_output.read_bytes()))
            raw_output.unlink()
        print(f"saved {output}")
        return 0
    elif args.command == "key":
        keys = args.value.split("-")
        result = qmp_command(path, "send-key", {"keys": [{"type": "qcode", "data": k} for k in keys]})
    else:
        if args.password and args.file:
            parser.error("use only one of --password or --file")
        if args.password:
            value = getpass.getpass("Text to type into guest: ")
        elif args.file:
            value = Path(args.file).read_text().rstrip("\r\n")
        else:
            value = args.text
        if value is None:
            parser.error("provide text or --password")
        type_text(path, value)
        print(f"typed {len(value)} characters")
        return 0
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"qmp.py: {exc}", file=sys.stderr)
        raise SystemExit(1)
