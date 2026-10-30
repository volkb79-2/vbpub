#!/usr/bin/env python3
"""Derive Wings image tags and append successful build identities."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from patchseries import SeriesError, __version__, parse, series_digest

SCRIPT_DIR = Path(__file__).resolve().parent
LEDGER_PATH = SCRIPT_DIR.parent / "image-ledger.json"
_SUFFIX = re.compile(r"^cgroup\.([1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


class ProvenanceError(ValueError):
    """The committed image ledger is invalid or inconsistent."""


def _read_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read image ledger {path}: {exc}") from exc
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != 1
    ):
        raise ProvenanceError(f"image ledger {path} must declare schema_version = 1")
    legacy = document.get("legacy_unrecorded")
    builds = document.get("builds")
    if not isinstance(legacy, list) or not isinstance(builds, list):
        raise ProvenanceError(
            f"image ledger {path} must contain legacy_unrecorded and builds arrays"
        )
    for item in legacy:
        if not isinstance(item, dict) or not _valid_upstream(item.get("upstream")):
            raise ProvenanceError(
                f"image ledger {path} has an invalid legacy upstream entry"
            )
        if not isinstance(item.get("reason"), str) or not item["reason"]:
            raise ProvenanceError(
                f"image ledger {path} has an undocumented legacy suffix"
            )
        _suffix_number(item.get("suffix"))
    for item in builds:
        if not isinstance(item, dict) or not _valid_upstream(item.get("upstream")):
            raise ProvenanceError(
                f"image ledger {path} has an invalid build upstream entry"
            )
        _suffix_number(item.get("suffix"))
        if not isinstance(item.get("series_digest"), str) or not _SHA256.fullmatch(
            item["series_digest"]
        ):
            raise ProvenanceError(f"image ledger {path} has an invalid series digest")
        if not isinstance(item.get("image_id"), str) or not _IMAGE_ID.fullmatch(
            item["image_id"]
        ):
            raise ProvenanceError(f"image ledger {path} has an invalid image ID")
        if not isinstance(item.get("built_at"), str) or not item["built_at"]:
            raise ProvenanceError(f"image ledger {path} has an invalid build date")
    return document


def _valid_upstream(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _suffix_number(value: object) -> int:
    if not isinstance(value, str):
        raise ProvenanceError(f"invalid cgroup suffix: {value!r}")
    match = _SUFFIX.fullmatch(value)
    if match is None:
        raise ProvenanceError(f"invalid cgroup suffix: {value!r}")
    return int(match.group(1))


def _choose_suffix(ledger: dict[str, Any], upstream: str, digest: str) -> str:
    matching = {
        entry["suffix"]
        for entry in ledger["builds"]
        if entry["upstream"] == upstream and entry["series_digest"] == digest
    }
    if len(matching) > 1:
        raise ProvenanceError(
            f"digest {digest} has multiple suffixes for upstream {upstream}"
        )
    if matching:
        return next(iter(matching))
    numbers = [
        _suffix_number(entry["suffix"])
        for entry in (*ledger["legacy_unrecorded"], *ledger["builds"])
        if entry["upstream"] == upstream
    ]
    return f"cgroup.{max(numbers, default=0) + 1}"


def _prepare(args: argparse.Namespace) -> int:
    changes = parse(args.series)
    digest = series_digest(changes, args.upstream_commit, __version__)
    ledger = _read_ledger(args.ledger)
    suffix = _choose_suffix(ledger, args.upstream_version, digest)
    version = f"{args.upstream_version}-{suffix}"
    sys.stdout.write(
        f"{version}\t{suffix}\t{digest}\t{__version__}\t{args.upstream_commit}\n"
    )
    return 0


def _record(args: argparse.Namespace) -> int:
    if not _SHA256.fullmatch(args.series_digest):
        raise ProvenanceError("series digest must be a full lowercase SHA-256")
    if not _IMAGE_ID.fullmatch(args.image_id):
        raise ProvenanceError(
            "image ID must be sha256 followed by 64 lowercase hex characters"
        )
    if not args.built_at:
        raise ProvenanceError("build date must not be empty")
    ledger = _read_ledger(args.ledger)
    expected = _choose_suffix(ledger, args.upstream_version, args.series_digest)
    if args.suffix != expected:
        raise ProvenanceError(
            f"suffix {args.suffix!r} is not the ledger-derived suffix {expected!r}"
        )
    entry = {
        "upstream": args.upstream_version,
        "suffix": args.suffix,
        "series_digest": args.series_digest,
        "image_id": args.image_id,
        "built_at": args.built_at,
    }
    if entry not in ledger["builds"]:
        ledger["builds"].append(entry)
        _write_ledger(args.ledger, ledger)
    sys.stdout.write(
        f"recorded {args.upstream_version}-{args.suffix} {args.image_id}\n"
    )
    return 0


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    serialized = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        os.replace(temporary_name, path)
    except OSError:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="derive the image version and series digest"
    )
    prepare.add_argument("--series", type=Path, required=True)
    prepare.add_argument("--upstream-version", required=True)
    prepare.add_argument("--upstream-commit", required=True)
    prepare.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    record = commands.add_parser("record", help="record a successful image build")
    record.add_argument("--upstream-version", required=True)
    record.add_argument("--suffix", required=True)
    record.add_argument("--series-digest", required=True)
    record.add_argument("--image-id", required=True)
    record.add_argument("--built-at", required=True)
    record.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            return _prepare(args)
        return _record(args)
    except (OSError, SeriesError, ProvenanceError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
