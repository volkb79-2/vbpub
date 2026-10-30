#!/usr/bin/env python3
"""Derive Wings image tags and append successful build identities."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from patchseries import SeriesError, __version__, parse, series_digest

SCRIPT_DIR = Path(__file__).resolve().parent
LEDGER_PATH = SCRIPT_DIR.parent / "image-ledger.json"
_SUFFIX = re.compile(r"^cgroup\.([1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESERVATION_ID = re.compile(r"^[0-9a-f]{32}$")
_UPSTREAM_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class ProvenanceError(ValueError):
    """The committed image ledger is invalid or inconsistent."""


def _read_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"cannot read image ledger {path}: {exc}") from exc
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int:
        raise ProvenanceError(f"image ledger {path} must declare schema_version = 2")
    if document["schema_version"] != 2:
        raise ProvenanceError(f"image ledger {path} must declare schema_version = 2")
    legacy = document.get("legacy_unrecorded")
    builds = document.get("builds")
    reservations = document.get("reservations")
    current_series = document.get("current_series")
    if (
        not isinstance(legacy, list)
        or not isinstance(builds, list)
        or not isinstance(reservations, list)
    ):
        raise ProvenanceError(
            f"image ledger {path} must contain legacy_unrecorded, builds and reservations arrays"
        )
    if current_series is not None and not isinstance(current_series, dict):
        raise ProvenanceError(f"image ledger {path} has an invalid current series record")
    if current_series is None:
        raise ProvenanceError(f"image ledger {path} must contain current_series")
    if current_series is not None:
        if not _valid_upstream(current_series.get("upstream_version")):
            raise ProvenanceError(f"image ledger {path} has an invalid current series version")
        if not isinstance(current_series.get("upstream_commit"), str) or not _UPSTREAM_COMMIT.fullmatch(
            current_series["upstream_commit"]
        ):
            raise ProvenanceError(f"image ledger {path} has an invalid current upstream commit")
        if not isinstance(current_series.get("series_digest"), str) or not _SHA256.fullmatch(
            current_series["series_digest"]
        ):
            raise ProvenanceError(f"image ledger {path} has an invalid current series digest")
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
        image_label_digest = item.get("image_label_series_digest")
        if image_label_digest is not None and (
            not isinstance(image_label_digest, str)
            or not _SHA256.fullmatch(image_label_digest)
        ):
            raise ProvenanceError(
                f"image ledger {path} has an invalid image-label series digest"
            )
        if not isinstance(item.get("image_id"), str) or not _IMAGE_ID.fullmatch(
            item["image_id"]
        ):
            raise ProvenanceError(f"image ledger {path} has an invalid image ID")
        if not isinstance(item.get("built_at"), str) or not item["built_at"]:
            raise ProvenanceError(f"image ledger {path} has an invalid build date")
    reservation_ids: set[str] = set()
    for item in reservations:
        if not isinstance(item, dict) or not _valid_upstream(item.get("upstream")):
            raise ProvenanceError(
                f"image ledger {path} has an invalid reservation upstream entry"
            )
        _suffix_number(item.get("suffix"))
        if not isinstance(item.get("series_digest"), str) or not _SHA256.fullmatch(
            item["series_digest"]
        ):
            raise ProvenanceError(f"image ledger {path} has an invalid reservation digest")
        reservation_id = item.get("reservation_id")
        if (
            not isinstance(reservation_id, str)
            or not _RESERVATION_ID.fullmatch(reservation_id)
        ):
            raise ProvenanceError(f"image ledger {path} has an invalid reservation ID")
        if reservation_id in reservation_ids:
            raise ProvenanceError(f"image ledger {path} has duplicate reservation IDs")
        reservation_ids.add(reservation_id)
    legacy_suffixes = {
        (item["upstream"], item["suffix"])
        for item in legacy
    }
    suffix_digests: dict[tuple[str, str], str] = {}
    for item in (*builds, *reservations):
        key = (item["upstream"], item["suffix"])
        if key in legacy_suffixes:
            raise ProvenanceError(
                f"image ledger {path} reuses legacy suffix {item['suffix']}"
            )
        previous = suffix_digests.setdefault(key, item["series_digest"])
        if previous != item["series_digest"]:
            raise ProvenanceError(
                f"image ledger {path} assigns suffix {item['suffix']} to different digests"
            )
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
        suffix = next(iter(matching))
        if any(
            entry["upstream"] == upstream
            and entry["suffix"] == suffix
            and entry["series_digest"] != digest
            for entry in ledger["builds"]
        ):
            raise ProvenanceError(
                f"suffix {suffix} is already recorded for a different digest"
            )
        return suffix
    numbers = [
        _suffix_number(entry["suffix"])
        for entry in (
            *ledger["legacy_unrecorded"],
            *ledger["builds"],
            *ledger["reservations"],
        )
        if entry["upstream"] == upstream
    ]
    return f"cgroup.{max(numbers, default=0) + 1}"


@contextmanager
def _locked_ledger(path: Path) -> Iterator[None]:
    """Serialize ledger read-modify-replace operations through a stable lock file."""
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _reserve(args: argparse.Namespace) -> int:
    changes = parse(args.series)
    digest = series_digest(changes, args.upstream_commit)
    with _locked_ledger(args.ledger):
        ledger = _read_ledger(args.ledger)
        if any(
            entry["upstream"] == args.upstream_version
            and entry["series_digest"] == digest
            for entry in ledger["reservations"]
        ):
            raise ProvenanceError(
                f"digest {digest} already has an active reservation for upstream "
                f"{args.upstream_version}"
            )
        suffix = _choose_suffix(ledger, args.upstream_version, digest)
        if any(
            entry["upstream"] == args.upstream_version
            and entry["suffix"] == suffix
            for entry in ledger["reservations"]
        ):
            raise ProvenanceError(
                f"suffix {suffix} already has an active reservation for upstream "
                f"{args.upstream_version}"
            )
        reservation_id = uuid.uuid4().hex
        ledger["reservations"].append(
            {
                "upstream": args.upstream_version,
                "suffix": suffix,
                "series_digest": digest,
                "reservation_id": reservation_id,
            }
        )
        _write_ledger(args.ledger, ledger)
    version = f"{args.upstream_version}-{suffix}"
    sys.stdout.write(
        f"{version}\t{suffix}\t{digest}\t{__version__}\t{args.upstream_commit}"
        f"\t{reservation_id}\n"
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
    with _locked_ledger(args.ledger):
        ledger = _read_ledger(args.ledger)
        matches = [
            entry
            for entry in ledger["reservations"]
            if entry["reservation_id"] == args.reservation_id
        ]
        if len(matches) != 1:
            raise ProvenanceError(
                f"reservation {args.reservation_id!r} is not active in the image ledger"
            )
        reservation = matches[0]
        if (
            reservation["upstream"] != args.upstream_version
            or reservation["suffix"] != args.suffix
            or reservation["series_digest"] != args.series_digest
        ):
            raise ProvenanceError("record arguments do not match the active reservation")
        if any(
            entry["upstream"] == args.upstream_version
            and entry["suffix"] == args.suffix
            and entry["series_digest"] != args.series_digest
            for entry in ledger["builds"]
        ):
            raise ProvenanceError(
                f"suffix {args.suffix} is already recorded for a different digest"
            )
        entry = {
            "upstream": args.upstream_version,
            "suffix": args.suffix,
            "series_digest": args.series_digest,
            "image_label_series_digest": args.series_digest,
            "image_id": args.image_id,
            "built_at": args.built_at,
        }
        ledger["builds"].append(entry)
        ledger["reservations"] = [
            item
            for item in ledger["reservations"]
            if item["reservation_id"] != args.reservation_id
        ]
        _write_ledger(args.ledger, ledger)
    sys.stdout.write(
        f"recorded {args.upstream_version}-{args.suffix} {args.image_id}\n"
    )
    return 0


def _release(args: argparse.Namespace) -> int:
    with _locked_ledger(args.ledger):
        ledger = _read_ledger(args.ledger)
        matches = [
            item
            for item in ledger["reservations"]
            if item["reservation_id"] == args.reservation_id
        ]
        if len(matches) != 1:
            raise ProvenanceError(
                f"reservation {args.reservation_id!r} is not active in the image ledger"
            )
        ledger["reservations"] = [
            item
            for item in ledger["reservations"]
            if item["reservation_id"] != args.reservation_id
        ]
        _write_ledger(args.ledger, ledger)
    sys.stdout.write(f"released reservation {args.reservation_id}\n")
    return 0


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    serialized = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    reserve = commands.add_parser(
        "reserve", help="reserve an image tag and derive the series identity"
    )
    reserve.add_argument("--series", type=Path, required=True)
    reserve.add_argument("--upstream-version", required=True)
    reserve.add_argument("--upstream-commit", required=True)
    reserve.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    record = commands.add_parser("record", help="record a successful image build")
    record.add_argument("--upstream-version", required=True)
    record.add_argument("--suffix", required=True)
    record.add_argument("--series-digest", required=True)
    record.add_argument("--reservation-id", required=True)
    record.add_argument("--image-id", required=True)
    record.add_argument("--built-at", required=True)
    record.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    release = commands.add_parser("release", help="release a failed build reservation")
    release.add_argument("--reservation-id", required=True)
    release.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "reserve":
            return _reserve(args)
        if args.command == "record":
            return _record(args)
        return _release(args)
    except (OSError, SeriesError, ProvenanceError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
