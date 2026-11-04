#!/usr/bin/env python3
"""Refresh the Netcup SCP OpenAPI snapshot from the published endpoint."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


OPENAPI_URL = "https://www.servercontrolpanel.de/scp-core/api/v1/openapi"
EXPECTED_TITLE = "SCP (Server Control Panel) REST API"
OUTPUT_PATH = Path(__file__).resolve().with_name("netcup-scp-openapi.json")


def _fetch() -> dict[str, Any]:
    request = urllib.request.Request(
        OPENAPI_URL,
        headers={"Accept": "application/json", "User-Agent": "vbpub-netcup-openapi-refresh/1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        content_type = response.headers.get_content_type()
        if content_type != "application/json" and not content_type.endswith("+json"):
            raise ValueError(f"expected JSON from Netcup, received {content_type}")
        payload = response.read()

    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Netcup returned invalid JSON ({type(exc).__name__})") from None
    if not isinstance(document, dict):
        raise ValueError("Netcup OpenAPI document must be a JSON object")
    info = document.get("info")
    paths = document.get("paths")
    if not isinstance(document.get("openapi"), str) or not document["openapi"].startswith("3."):
        raise ValueError("Netcup response is not an OpenAPI 3 document")
    if not isinstance(info, dict) or info.get("title") != EXPECTED_TITLE:
        raise ValueError("Netcup response has an unexpected API title")
    if not isinstance(info.get("version"), str) or not info["version"]:
        raise ValueError("Netcup OpenAPI document has no version")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("Netcup OpenAPI document has no paths")
    return document


def _replace_atomically(document: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(OUTPUT_PATH.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644

    encoded = (json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{OUTPUT_PATH.name}.", dir=OUTPUT_PATH.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, OUTPUT_PATH)
        directory_fd = os.open(OUTPUT_PATH.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    try:
        document = _fetch()
        _replace_atomically(document)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(f"ERROR: could not refresh Netcup OpenAPI snapshot: {exc}", file=sys.stderr)
        return 1

    print(
        f"Updated {OUTPUT_PATH} from {OPENAPI_URL} "
        f"(version {document['info']['version']}, {len(document['paths'])} paths)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
